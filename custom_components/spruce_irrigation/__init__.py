"""The Spruce Irrigation integration: a Spruce Gen2 Wi-Fi controller through Spruce's cloud."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from types import MappingProxyType

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import Platform
from homeassistant.core import HassJob, HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from .api import SpruceApi, SpruceError
from .baselines import BaselineStore
from .live import Live
from .const import (
    CONF_API_KEY,
    CONF_CONTROLLER,
    CONF_INSTANT,
    DOMAIN,
    LITRES_PER_GALLON,
    SERVICE_HIBERNATE,
    SERVICE_IMPORT_HISTORY,
    SERVICE_PAIR_SENSOR,
    SERVICE_RUN_ALL,
    SERVICE_RUN_SCHEDULE,
    SERVICE_CREATE_SCHEDULE,
    SERVICE_DELETE_SCHEDULE,
    SERVICE_PAUSE_SCHEDULES,
    SERVICE_RECORD_BASELINE,
    SERVICE_RENAME_SCHEDULE,
    SERVICE_SET_CLIMATE_ADJUST,
    SERVICE_SET_SCHEDULE_DAYS,
    SERVICE_REVIEW_BASELINE,
    SERVICE_RUN_BASELINE,
    SERVICE_SCHEDULE_DAY,
    SERVICE_SET_SCHEDULE_ZONE,
    SERVICE_SET_ZONE_HEADS,
    SERVICE_STOP,
    SERVICE_STOP_SCHEDULE,
    STRATEGY_FILE,
    STRATEGY_URL,
)
from .coordinator import SpruceHistoryCoordinator, SpruceStatusCoordinator
from .heads import SUBENTRY_ZONE_HEADS, library, zone_heads
from .units import Units
from .statistics import async_import_history, async_update_recent_statistics

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.CALENDAR, Platform.EVENT, Platform.NUMBER, Platform.SELECT, Platform.SENSOR,
             Platform.SWITCH, Platform.TIME, Platform.VALVE, Platform.WEATHER]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
HISTORY_IMPORTED = "history_imported"
HISTORY_VERSION = 4   # 2: per zone per schedule; 3: rain and temperature; 4: flow per zone; a lower version re-imports


@dataclass
class SpruceRuntime:
    api: SpruceApi
    controller: str
    status: SpruceStatusCoordinator
    history: SpruceHistoryCoordinator
    run_minutes: dict[int, float] = field(default_factory=dict)   # per-zone manual run time (number entities)
    device_id: str | None = None   # the controller's device registry id (moisture sensor devices link to it)
    units: Units = field(default_factory=lambda: Units({}))
    baselines: BaselineStore | None = None
    live: Live | None = None
    loaded_with: tuple = ()

    def baselines_changed(self) -> None:
        """After a baseline is recorded, accepted or kept: re-check the review notice and update the entities."""
        self.history.review_baseline(self.history.data)
        self.history.async_update_listeners()

    def optimistic(self, zone: int | None, change: dict | None) -> None:
        """Show a command's effect straight away (Spruce's cloud takes a few seconds to catch up), then confirm it
        with refreshes 5 and 20 seconds later. zone None = every zone."""
        if change is not None and self.status.data:
            zones = self.status.data.zones
            for z in ([zone] if zone is not None else list(zones)):
                zones[z] = {**zones.get(z, {}), **change}
                self.status.hold(z, change)
            self.status.async_update_listeners()
        for delay in (5, 20):
            async_call_later(self.status.hass, delay, HassJob(self._refresh, cancel_on_shutdown=True))

    async def _refresh(self, _now) -> None:
        await self.status.async_request_refresh()

    async def async_set_schedule(self, schedule_id: int, values: dict[str, str], zone: int | None = None,
                                 local: dict | None = None) -> None:
        """Change a schedule as the Spruce app does: save the settings in Spruce's cloud, then (for a schedule that
        runs on the controller) send the controller its new run list; disabling removes it from the controller.
        `local` updates what Home Assistant shows straight away."""
        schedule = self.history.data.schedules.get(schedule_id)
        if schedule is None:
            raise ServiceValidationError(f"No Spruce schedule {schedule_id}")
        try:
            await self.api.save_schedule_settings(self.controller, schedule_id, values, zone)
            enabled = values.get("sched_enabled", "1" if schedule["enabled"] else "0") == "1"
            if schedule.get("type") != "manual" and ("sched_enabled" in values or enabled):
                await self.api.sync_schedule(self.controller, schedule_id, enabled)
        except SpruceError as err:
            raise HomeAssistantError(f"Couldn't change {schedule['name']}: {err}") from err
        if local:
            target = schedule["zones"].setdefault(zone, {}) if zone is not None else schedule
            target.update(local)
            self.history.async_update_listeners()
        await self.history.async_request_refresh()


    async def async_save(self, what: str, action, local=None, sync: bool = False) -> None:
        """Save something in Spruce (`action`: an awaitable), then show it straight away (`local`: a callable that
        updates the cached data) and, for settings the controller itself uses, tell it to fetch them."""
        try:
            await action
            if sync:
                await self.api.refresh_settings(self.controller)
        except SpruceError as err:
            raise HomeAssistantError(f"Couldn't change {what}: {err}") from err
        if local:
            local(self.history.data)
            self.history.async_update_listeners()
        await self.history.async_request_refresh()

    async def async_set_schedule_zones(self, schedule_id: int, zones: list[int], values: dict[str, str],
                                       local: dict) -> None:
        """Change settings of zones within a schedule (a whole zone group at once), then sync it to the controller."""
        schedule = self.history.data.schedules.get(schedule_id)
        if schedule is None:
            raise ServiceValidationError(f"No Spruce schedule {schedule_id}")

        def apply(data) -> None:
            for z in zones:
                data.schedules[schedule_id]["zones"].setdefault(z, {}).update(local)
        await self.async_save(schedule["name"], self.api.save_schedule_zones(
            self.controller, schedule_id, {z: dict(values) for z in zones}), apply)
        if schedule.get("enabled") and schedule.get("type") != "manual":
            try:
                await self.api.sync_schedule(self.controller, schedule_id, True)
            except SpruceError as err:
                raise HomeAssistantError(f"Saved, but couldn't send {schedule['name']} to the controller: {err}") from err

    async def async_set_zone_skips(self, schedule_id: int, skip: str, on: bool, zones: list[int] | None = None) -> None:
        """Turn a weather skip (rain / freeze / wind) on or off for zones of a connected schedule (all its zones by
        default) — Spruce keeps these per zone, as its Weather Watch page shows."""
        schedule = self.history.data.schedules.get(schedule_id)
        if schedule is None:
            raise ServiceValidationError(f"No Spruce schedule {schedule_id}")
        targets = zones or list(schedule["zones"])
        setting = {"rain_skip": "rain_delay_enabled", "freeze_skip": "freeze_delay_enabled",
                   "wind_skip": "wind_delay_enabled"}[skip]

        def local(data) -> None:
            for z in targets:
                data.schedules[schedule_id]["zones"].setdefault(z, {})[skip] = on
        await self.async_save(f"{schedule['name']}'s {skip.replace('_', ' ')}", self.api.save_schedule_zones(
            self.controller, schedule_id, {z: {setting: "1" if on else "0"} for z in targets}), local)


type SpruceConfigEntry = ConfigEntry[SpruceRuntime]


async def async_setup_entry(hass: HomeAssistant, entry: SpruceConfigEntry) -> bool:
    api = SpruceApi(async_get_clientsession(hass), entry.data[CONF_API_KEY])
    controller = entry.data[CONF_CONTROLLER]
    status = SpruceStatusCoordinator(hass, entry, api, controller)
    history = SpruceHistoryCoordinator(hass, entry, api, controller)
    baselines = BaselineStore(hass, entry.entry_id)
    await baselines.async_load()
    history.baselines = baselines
    await history.async_config_entry_first_refresh()
    await status.async_config_entry_first_refresh()
    entry.runtime_data = SpruceRuntime(api, controller, status, history, units=Units(entry.options), baselines=baselines)
    entry.runtime_data.live = Live(hass, entry.runtime_data)

    entry.runtime_data.device_id = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, controller)}, manufacturer="Plaid Systems (Spruce)",
        name=history.data.name, model=history.data.model, sw_version=history.data.firmware,
        serial_number=history.data.serial, configuration_url="https://app.spruceirrigation.com/").id
    _remove_retired_entities(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Water history into long-term statistics: everything once, then the last week after each history update
    @callback
    def _history_updated() -> None:
        if history.last_update_success and entry.data.get(HISTORY_IMPORTED) == HISTORY_VERSION:
            entry.async_create_background_task(hass, async_update_recent_statistics(hass, entry), "spruce statistics")
    entry.async_on_unload(history.async_add_listener(_history_updated))

    # Names (and zones, schedules and sensors) are fixed when the entities are made: reload when Spruce's change
    layout = _layout(entry.runtime_data)

    @callback
    def _check_layout() -> None:
        if history.last_update_success and status.last_update_success and _layout_changed(layout, _layout(entry.runtime_data)):
            _LOGGER.info("Spruce names or zones changed; reloading %s", entry.title)
            hass.config_entries.async_schedule_reload(entry.entry_id)
    entry.async_on_unload(history.async_add_listener(_check_layout))
    entry.async_on_unload(status.async_add_listener(_check_layout))
    if entry.data.get(HISTORY_IMPORTED) != HISTORY_VERSION:
        entry.async_create_background_task(hass, _first_import(hass, entry), "spruce history import")
    entry.runtime_data.loaded_with = _settings(entry)
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    if entry.options.get(CONF_INSTANT):
        entry.runtime_data.live.start_stream(entry)
    return True


def _layout(rt: SpruceRuntime) -> tuple:
    """What the entities and devices are built from: the controller's, zones', schedules' and sensors' names."""
    h = rt.history.data
    return (h.name,
            {z.number: (z.name, z.enabled, z.sensor_id) for z in h.zones.values()},
            {sid: (s["name"], s.get("type")) for sid, s in h.schedules.items()},
            {sid: s.get("name") for sid, s in rt.status.data.sensors.items()})


def _layout_changed(old: tuple, new: tuple) -> bool:
    """A moisture sensor missing from one status read isn't a change (that would reload over and over)."""
    sensors = {sid: name for sid, name in new[3].items() if sid not in old[3] or name != old[3][sid]}
    return old[:3] != new[:3] or bool(sensors)


def _remove_retired_entities(hass: HomeAssistant, entry: SpruceConfigEntry) -> None:
    """0.6: the rain skip threshold is the controller's (one for all schedules), not a number per schedule."""
    registry = er.async_get(hass)
    for item in er.async_entries_for_config_entry(registry, entry.entry_id):
        if item.unique_id.endswith("_rain_threshold") and "_schedule_" in item.unique_id:
            registry.async_remove(item.entity_id)


async def _first_import(hass: HomeAssistant, entry: SpruceConfigEntry) -> None:
    try:
        await async_import_history(hass, entry, None)
    except SpruceError as err:
        _LOGGER.warning("Spruce water history not imported yet (will retry at the next restart): %s", err)
        return
    hass.config_entries.async_update_entry(entry, data={**entry.data, HISTORY_IMPORTED: HISTORY_VERSION})


def _settings(entry: SpruceConfigEntry) -> tuple:
    """What a reload is for: the options, and the head models and zone heads (subentries)."""
    return (dict(entry.options), {sid: (sub.title, dict(sub.data)) for sid, sub in entry.subentries.items()})


async def _options_updated(hass: HomeAssistant, entry: SpruceConfigEntry) -> None:
    """Reload for changed options or subentries; not for the integration's own notes in the entry's data (such as
    the history import version), which would otherwise reload it after every import."""
    if _settings(entry) != entry.runtime_data.loaded_with:
        await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: SpruceConfigEntry) -> None:
    """Removing the integration also removes its stored baseline history."""
    await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.baselines").async_remove()


async def async_unload_entry(hass: HomeAssistant, entry: SpruceConfigEntry) -> bool:
    if entry.runtime_data.baselines:
        await entry.runtime_data.baselines.async_flush()
    if entry.runtime_data.live:
        await entry.runtime_data.live.async_shutdown()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


# ---- services --------------------------------------------------------------------------------------------------

DEVICE = vol.Optional("device_id")
WEEKDAY_KEYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]   # Spruce's order
# set_schedule_zone field -> (Spruce setting, key in the schedule's zone data)
ZONE_FIELDS = {
    "watering_percent": ("duration_scaling", "scaling"), "enabled": ("zonesched_enabled", "enabled"),
    "rain_skip": ("rain_delay_enabled", "rain_skip"), "freeze_skip": ("freeze_delay_enabled", "freeze_skip"),
    "wind_skip": ("wind_delay_enabled", "wind_skip"), "mode": ("schedule_mode", "mode"), "cycles": ("cycles", "cycles"),
    "weekly_min": ("weekly_safeguard_min", "weekly_min"), "weekly_max": ("weekly_safeguard_max", "weekly_max"),
    "climate_adjust": ("climate_adjust_enabled", "climate_adjust"),
    "moisture_threshold": ("moisture_setpoint", "moisture_threshold"),
}
SCHEMAS = {
    SERVICE_RUN_ALL: vol.Schema({DEVICE: cv.string, vol.Required("minutes"): vol.All(vol.Coerce(float), vol.Range(min=1, max=240))}),
    SERVICE_RUN_SCHEDULE: vol.Schema({DEVICE: cv.string, vol.Required("schedule"): cv.string}),
    SERVICE_STOP_SCHEDULE: vol.Schema({DEVICE: cv.string, vol.Required("schedule"): cv.string}),
    SERVICE_STOP: vol.Schema({DEVICE: cv.string}),
    SERVICE_HIBERNATE: vol.Schema({DEVICE: cv.string, vol.Required("enabled"): cv.boolean, vol.Optional("until"): cv.date}),
    SERVICE_PAUSE_SCHEDULES: vol.Schema({DEVICE: cv.string, vol.Exclusive("days", "until"): vol.All(vol.Coerce(int), vol.Range(min=1, max=365)),
                                         vol.Exclusive("until", "until"): cv.date}),
    SERVICE_PAIR_SENSOR: vol.Schema({DEVICE: cv.string}),
    SERVICE_IMPORT_HISTORY: vol.Schema({DEVICE: cv.string, vol.Optional("start_date"): cv.date}),
    SERVICE_RUN_BASELINE: vol.Schema({DEVICE: cv.string}),
    SERVICE_RECORD_BASELINE: vol.Schema({
        DEVICE: cv.string, vol.Required("date"): cv.date, vol.Optional("time"): cv.time,
        vol.Required("flows"): vol.Schema({cv.string: vol.All(vol.Coerce(float), vol.Range(min=0, max=1000))}),
        vol.Optional("unit"): vol.In(["L/min", "gal/min"]), vol.Optional("note"): cv.string,
        vol.Optional("suspect", default=False): cv.boolean}),
    SERVICE_REVIEW_BASELINE: vol.Schema({DEVICE: cv.string, vol.Required("accept"): cv.boolean,
                                         vol.Optional("zones"): vol.All(cv.ensure_list, [cv.string])}),
    SERVICE_SET_ZONE_HEADS: vol.Schema({
        DEVICE: cv.string, vol.Required("zone"): cv.string,
        vol.Required("heads"): vol.All(cv.ensure_list, [vol.Schema({
            vol.Required("model"): cv.string,
            vol.Optional("arc"): vol.All(vol.Coerce(float), vol.Range(min=1, max=360)),
            vol.Required("quantity"): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=10000))})])}),
    SERVICE_SET_SCHEDULE_ZONE: vol.Schema({
        DEVICE: cv.string, vol.Required("schedule"): cv.string, vol.Required("zone"): cv.string,
        vol.Optional("watering_percent"): vol.All(vol.Coerce(int), vol.Range(min=1, max=300)),
        vol.Optional("enabled"): cv.boolean, vol.Optional("rain_skip"): cv.boolean, vol.Optional("freeze_skip"): cv.boolean,
        vol.Optional("wind_skip"): cv.boolean, vol.Optional("mode"): vol.In(["et_days", "et_duration", "sensor", "fixed"]),
        vol.Optional("cycles"): vol.In(["auto", "1", "2", "3", "4"]),
        vol.Optional("weekly_min"): vol.All(vol.Coerce(float), vol.Range(min=0, max=7)),
        vol.Optional("weekly_max"): vol.All(vol.Coerce(float), vol.Range(min=0, max=7)),
        vol.Optional("climate_adjust"): cv.boolean,
        vol.Optional("moisture_threshold"): vol.All(vol.Coerce(int), vol.Range(min=1, max=59))}),
    SERVICE_SET_CLIMATE_ADJUST: vol.Schema({
        DEVICE: cv.string,
        vol.Exclusive("months", "climate"): vol.All(cv.ensure_list, [vol.All(vol.Coerce(int), vol.Range(min=0, max=200))],
                                                    vol.Length(min=12, max=12)),
        vol.Exclusive("month", "climate"): vol.All(vol.Coerce(int), vol.Range(min=1, max=12)),
        vol.Optional("percent"): vol.All(vol.Coerce(int), vol.Range(min=0, max=200))}),
    SERVICE_CREATE_SCHEDULE: vol.Schema({DEVICE: cv.string, vol.Required("name"): cv.string,
                                         vol.Optional("type", default="connected"): vol.In(["connected", "basic", "manual"])}),
    SERVICE_DELETE_SCHEDULE: vol.Schema({DEVICE: cv.string, vol.Required("schedule"): cv.string}),
    SERVICE_RENAME_SCHEDULE: vol.Schema({DEVICE: cv.string, vol.Required("schedule"): cv.string, vol.Required("name"): cv.string}),
    SERVICE_SET_SCHEDULE_DAYS: vol.Schema({
        DEVICE: cv.string, vol.Required("schedule"): cv.string,
        vol.Exclusive("weekdays", "days"): vol.All(cv.ensure_list, [vol.In(WEEKDAY_KEYS)]),
        vol.Exclusive("every", "days"): vol.All(vol.Coerce(int), vol.Range(min=1, max=15)),
        vol.Exclusive("even_odd", "days"): vol.In(["even", "odd"])}),
}


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async def handle(call: ServiceCall) -> None:
        entry = _entry_for(hass, call.data.get("device_id"))
        rt: SpruceRuntime = entry.runtime_data
        api, ctrl = rt.api, rt.controller
        try:
            if call.service == SERVICE_RUN_ALL:
                await api.run_all(ctrl, int(call.data["minutes"] * 60))
            elif call.service in (SERVICE_RUN_SCHEDULE, SERVICE_STOP_SCHEDULE):
                sid = _schedule_id(rt, call.data["schedule"])
                await (api.schedule_on if call.service == SERVICE_RUN_SCHEDULE else api.schedule_off)(ctrl, sid)
            elif call.service == SERVICE_STOP:
                await api.stop(ctrl)
            elif call.service == SERVICE_HIBERNATE:
                await api.hibernate(ctrl, call.data["enabled"], call.data.get("until"))
            elif call.service == SERVICE_PAUSE_SCHEDULES:
                until = call.data.get("until") or (dt_util.now().date() + timedelta(days=call.data.get("days", 1)))
                await api.hibernate(ctrl, True, until)
            elif call.service == SERVICE_PAIR_SENSOR:
                await api.pair_sensor(ctrl)
            elif call.service == SERVICE_SET_SCHEDULE_ZONE:
                # A zone in a zone group ("Place") is set with the rest of its group, as the app does
                sid, zones = _schedule_id(rt, call.data["schedule"]), _zones(rt, call.data["zone"])
                values, local = {}, {}
                for field, (setting, key) in ZONE_FIELDS.items():
                    if field not in call.data:
                        continue
                    value = call.data[field]
                    if isinstance(value, bool):
                        values[setting], local[key] = ("1" if value else "0"), value
                    elif isinstance(value, float | int):
                        values[setting], local[key] = f"{value:g}", float(value)
                    else:
                        values[setting], local[key] = value, value
                if not values:
                    raise ServiceValidationError(f"Give at least one of {', '.join(ZONE_FIELDS)}")
                await rt.async_set_schedule_zones(sid, zones, values, local)
                return
            elif call.service == SERVICE_SET_CLIMATE_ADJUST:
                rules = list(call.data.get("months") or rt.history.data.climate_rules or [100] * 12)
                if "month" in call.data:
                    if "percent" not in call.data:
                        raise ServiceValidationError("Give the percent for the month")
                    rules[call.data["month"] - 1] = call.data["percent"]
                elif "months" not in call.data:
                    raise ServiceValidationError("Give months (12 percentages) or a month and its percent")

                def local(data) -> None:
                    data.climate_rules = rules
                await rt.async_save("climate adjust", api.save_controller_settings(
                    ctrl, {"climate_adjust_rules": ",".join(str(r) for r in rules)}), local, sync=True)
                return
            elif call.service == SERVICE_CREATE_SCHEDULE:
                await api.create_schedule(ctrl, call.data["type"], call.data["name"].strip())
            elif call.service == SERVICE_DELETE_SCHEDULE:
                await api.delete_schedule(ctrl, _schedule_id(rt, call.data["schedule"]))
            elif call.service == SERVICE_RENAME_SCHEDULE:
                sid = _schedule_id(rt, call.data["schedule"])
                await rt.async_set_schedule(sid, {"schedule_name": call.data["name"].strip()},
                                            local={"name": call.data["name"].strip()})
                return
            elif call.service == SERVICE_SET_SCHEDULE_DAYS:
                sid = _schedule_id(rt, call.data["schedule"])
                if "weekdays" in call.data:   # Spruce keeps 7 flags, Sunday first
                    select, var = "days", "".join("1" if d in call.data["weekdays"] else "0" for d in WEEKDAY_KEYS)
                elif "every" in call.data:
                    select, var = "everyx", str(call.data["every"])
                elif "even_odd" in call.data:
                    select, var = "even_odd", call.data["even_odd"]
                else:
                    raise ServiceValidationError("Give weekdays, every (days) or even_odd")
                await rt.async_set_schedule(sid, {"days_select": select, "days_var": var},
                                            local={"days_select": select, "days_var": var})
                return
            elif call.service == SERVICE_RUN_BASELINE:
                await api.start_baseline(ctrl)
            elif call.service == SERVICE_RECORD_BASELINE:
                gallons = call.data.get("unit", "gal/min" if rt.units.unit["flow"] == "gal/min" else "L/min") == "gal/min"
                flows = {_zone(rt, z): v * (LITRES_PER_GALLON if gallons else 1) for z, v in call.data["flows"].items()}
                at = dt_util.start_of_local_day(call.data["date"])
                if call.data.get("time"):
                    at = at.replace(hour=call.data["time"].hour, minute=call.data["time"].minute)
                rt.baselines.record(at, flows, call.data.get("note"), timed="time" in call.data,
                                    suspect=call.data["suspect"])
                rt.baselines_changed()
                return
            elif call.service == SERVICE_REVIEW_BASELINE:
                zones = [_zone(rt, z) for z in call.data["zones"]] if call.data.get("zones") else None
                if not rt.baselines.decide(zones, call.data["accept"]):
                    raise ServiceValidationError("No zones are waiting for a baseline review")
                rt.baselines_changed()
                return
            elif call.service == SERVICE_SET_ZONE_HEADS:
                set_zone_heads(hass, entry, _zone(rt, call.data["zone"]), call.data["heads"])
                return
            elif call.service == SERVICE_IMPORT_HISTORY:
                start: date | None = call.data.get("start_date")
                await async_import_history(hass, entry, start)
                return
        except SpruceError as err:
            raise HomeAssistantError(str(err)) from err
        rt.optimistic(None, {"on": False} if call.service == SERVICE_STOP else None)
        if call.service in (SERVICE_HIBERNATE, SERVICE_PAUSE_SCHEDULES, SERVICE_RUN_SCHEDULE, SERVICE_STOP_SCHEDULE,
                            SERVICE_RUN_BASELINE, SERVICE_CREATE_SCHEDULE, SERVICE_DELETE_SCHEDULE):
            await rt.history.async_request_refresh()

    for name, schema in SCHEMAS.items():
        hass.services.async_register(DOMAIN, name, handle, schema=schema)

    async def schedule_day(call: ServiceCall) -> ServiceResponse:
        """A schedule's day: Spruce's plan (per zone: minutes, cycles, why, water budget) and, for a past day, what
        actually ran."""
        rt: SpruceRuntime = _entry_for(hass, call.data.get("device_id")).runtime_data
        sid, day = _schedule_id(rt, call.data["schedule"]), call.data["date"]
        schedule = rt.history.data.schedules[sid]
        zones = rt.history.data.zones
        name = lambda z: zones[z].name if z in zones else f"Zone {z}"   # noqa: E731
        plan = next((d for d in schedule.get("plan", {}).values() if d["day"] == day), None)
        result: dict[str, Any] = {"schedule": schedule["name"], "date": day.isoformat()}
        if plan:
            result.update({"start": plan["start"].isoformat(), "minutes": round(plan["seconds"] / 60),
                           "skipped": plan["reason"], "message": plan["message"],
                           "zones": {name(z): v for z, v in sorted(plan["detail"].items())}})
        if day < dt_util.now().date():
            try:
                ran = await rt.api.schedule_day(sid, day)
            except SpruceError as err:
                raise HomeAssistantError(str(err)) from err
            result["ran"] = {name(int(z)) if str(z).isdigit() else str(z): v for z, v in ran.items()}
        return result

    hass.services.async_register(DOMAIN, SERVICE_SCHEDULE_DAY, schedule_day, schema=vol.Schema({
        DEVICE: cv.string, vol.Required("schedule"): cv.string, vol.Required("date"): cv.date}),
        supports_response=SupportsResponse.ONLY)
    await _async_register_dashboard(hass)
    return True


async def _async_register_dashboard(hass: HomeAssistant) -> None:
    """Serve the dashboard strategy and load it in the frontend, so "Spruce Irrigation" is offered under
    Settings → Dashboards → Add dashboard → Community dashboards."""
    if hass.http is None or "frontend" not in hass.config.components:
        return
    from homeassistant.components.frontend import add_extra_js_url
    from homeassistant.components.http import StaticPathConfig

    version = (await async_get_integration(hass, DOMAIN)).version
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STRATEGY_URL, str(Path(__file__).parent / "frontend" / STRATEGY_FILE), True)])
    url = f"{STRATEGY_URL}?v={version}"
    add_extra_js_url(hass, url)   # for the Add dashboard dialog
    await _async_register_resource(hass, url)


async def _async_register_resource(hass: HomeAssistant, url: str) -> None:
    """Also list the strategy as a dashboard resource (storage mode only). Dashboards load their resources each
    time they open, so the dashboard works even in a browser or app still running the page from before the
    integration was installed or updated, which doesn't load extra_js_url."""
    try:
        from homeassistant.components.lovelace.const import LOVELACE_DATA

        lovelace = hass.data.get(LOVELACE_DATA)
        if lovelace is None or lovelace.resource_mode != "storage":
            return
        resources = lovelace.resources
        await resources.async_get_info()   # loads the stored resources
        ours = [r for r in resources.async_items() if str(r.get("url", "")).split("?")[0] == STRATEGY_URL]
        if not ours:
            await resources.async_create_item({"res_type": "module", "url": url})
        elif ours[0].get("url") != url:
            await resources.async_update_item(ours[0]["id"], {"res_type": "module", "url": url})
    except Exception:   # an internal Home Assistant API; the dashboard still loads through extra_js_url
        _LOGGER.debug("Couldn't register the dashboard resource", exc_info=True)


def _entry_for(hass: HomeAssistant, device_id: str | None) -> SpruceConfigEntry:
    entries = [e for e in hass.config_entries.async_loaded_entries(DOMAIN)]
    if device_id:
        device = dr.async_get(hass).async_get(device_id)
        entries = [e for e in entries if device and e.entry_id in device.config_entries]
    if len(entries) != 1:
        raise ServiceValidationError("Choose the Spruce controller (device) to use" if entries else "Spruce is not set up")
    return entries[0]


def set_zone_heads(hass: HomeAssistant, entry: SpruceConfigEntry, zone: int, rows: list[dict]) -> None:
    """Set a zone's installed heads (its zone-heads subentry); no rows removes them."""
    models = library(entry)
    by_label = {m.label.lower(): key for key, m in models.items()}
    heads = []
    for row in rows:
        key = row["model"] if row["model"] in models else by_label.get(row["model"].strip().lower())
        if key is None:
            raise ServiceValidationError(f"No head model '{row['model']}' (add it under the integration's "
                                         "Add head model, or use a built-in one)")
        heads.append({"model": key, "arc": row.get("arc", 360), "quantity": row["quantity"]})
    existing = zone_heads(entry).get(zone)
    name = entry.runtime_data.history.data.zones[zone].name
    if existing and not heads:
        hass.config_entries.async_remove_subentry(entry, existing[0])
    elif existing:
        hass.config_entries.async_update_subentry(entry, entry.subentries[existing[0]], data={"zone": zone, "heads": heads})
    elif heads:
        hass.config_entries.async_add_subentry(entry, ConfigSubentry(
            data=MappingProxyType({"zone": zone, "heads": heads}), subentry_type=SUBENTRY_ZONE_HEADS,
            title=name, unique_id=f"zone_{zone}"))


def _zones(rt: SpruceRuntime, zone: str) -> list[int]:
    """A zone (number or name) and the other zones of its group, or a zone group by name."""
    data = rt.history.data
    for group in data.zone_groups.values():
        if group["name"].lower() == zone.strip().lower():
            return list(group["zones"])
    number = _zone(rt, zone)
    return list(data.zones[number].group or (number,))


def _zone(rt: SpruceRuntime, zone: str) -> int:
    zones = rt.history.data.zones
    if zone.strip().isdigit() and int(zone) in zones:
        return int(zone)
    for number, z in zones.items():
        if z.name.lower() == zone.strip().lower():
            return number
    raise ServiceValidationError(f"No Spruce zone called '{zone}' (have: {', '.join(z.name for z in zones.values())})")


def _schedule_id(rt: SpruceRuntime, schedule: str) -> int:
    schedules = rt.history.data.schedules
    if schedule.isdigit() and int(schedule) in schedules:
        return int(schedule)
    for sid, s in schedules.items():
        if s["name"].lower() == schedule.strip().lower():
            return sid
    raise ServiceValidationError(f"No Spruce schedule called '{schedule}' (have: {', '.join(s['name'] for s in schedules.values())})")
