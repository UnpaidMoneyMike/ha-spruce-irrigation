"""The Spruce Irrigation integration: a Spruce Gen2 Wi-Fi controller through Spruce's cloud."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HassJob, HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .api import SpruceApi, SpruceError
from .const import (
    CONF_API_KEY,
    CONF_CONTROLLER,
    DOMAIN,
    SERVICE_HIBERNATE,
    SERVICE_IMPORT_HISTORY,
    SERVICE_PAIR_SENSOR,
    SERVICE_RUN_ALL,
    SERVICE_RUN_SCHEDULE,
    SERVICE_SET_SCHEDULE_ZONE,
    SERVICE_STOP,
    SERVICE_STOP_SCHEDULE,
    STRATEGY_FILE,
    STRATEGY_URL,
)
from .coordinator import SpruceHistoryCoordinator, SpruceStatusCoordinator
from .statistics import async_import_history, async_update_recent_statistics

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.NUMBER, Platform.SENSOR, Platform.SWITCH, Platform.TIME,
             Platform.VALVE]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
HISTORY_IMPORTED = "history_imported"
HISTORY_VERSION = 2   # 2: statistics per zone per schedule as well; a lower stored version re-imports everything


@dataclass
class SpruceRuntime:
    api: SpruceApi
    controller: str
    status: SpruceStatusCoordinator
    history: SpruceHistoryCoordinator
    run_minutes: dict[int, float] = field(default_factory=dict)   # per-zone manual run time (number entities)
    device_id: str | None = None   # the controller's device registry id (moisture sensor devices link to it)

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


type SpruceConfigEntry = ConfigEntry[SpruceRuntime]


async def async_setup_entry(hass: HomeAssistant, entry: SpruceConfigEntry) -> bool:
    api = SpruceApi(async_get_clientsession(hass), entry.data[CONF_API_KEY])
    controller = entry.data[CONF_CONTROLLER]
    status = SpruceStatusCoordinator(hass, entry, api, controller)
    history = SpruceHistoryCoordinator(hass, entry, api, controller)
    await history.async_config_entry_first_refresh()
    await status.async_config_entry_first_refresh()
    entry.runtime_data = SpruceRuntime(api, controller, status, history)

    entry.runtime_data.device_id = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, controller)}, manufacturer="Plaid Systems (Spruce)",
        name=history.data.name, model=history.data.model, sw_version=history.data.firmware,
        serial_number=history.data.serial, configuration_url="https://app.spruceirrigation.com/").id
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
    entry.async_on_unload(entry.add_update_listener(_options_updated))
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


async def _first_import(hass: HomeAssistant, entry: SpruceConfigEntry) -> None:
    try:
        await async_import_history(hass, entry, None)
    except SpruceError as err:
        _LOGGER.warning("Spruce water history not imported yet (will retry at the next restart): %s", err)
        return
    hass.config_entries.async_update_entry(entry, data={**entry.data, HISTORY_IMPORTED: HISTORY_VERSION})


async def _options_updated(hass: HomeAssistant, entry: SpruceConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: SpruceConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


# ---- services --------------------------------------------------------------------------------------------------

DEVICE = vol.Optional("device_id")
SCHEMAS = {
    SERVICE_RUN_ALL: vol.Schema({DEVICE: cv.string, vol.Required("minutes"): vol.All(vol.Coerce(float), vol.Range(min=1, max=240))}),
    SERVICE_RUN_SCHEDULE: vol.Schema({DEVICE: cv.string, vol.Required("schedule"): cv.string}),
    SERVICE_STOP_SCHEDULE: vol.Schema({DEVICE: cv.string, vol.Required("schedule"): cv.string}),
    SERVICE_STOP: vol.Schema({DEVICE: cv.string}),
    SERVICE_HIBERNATE: vol.Schema({DEVICE: cv.string, vol.Required("enabled"): cv.boolean}),
    SERVICE_PAIR_SENSOR: vol.Schema({DEVICE: cv.string}),
    SERVICE_IMPORT_HISTORY: vol.Schema({DEVICE: cv.string, vol.Optional("start_date"): cv.date}),
    SERVICE_SET_SCHEDULE_ZONE: vol.Schema({
        DEVICE: cv.string, vol.Required("schedule"): cv.string, vol.Required("zone"): cv.string,
        vol.Optional("watering_percent"): vol.All(vol.Coerce(int), vol.Range(min=1, max=300)),
        vol.Optional("enabled"): cv.boolean}),
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
                await api.hibernate(ctrl, call.data["enabled"])
            elif call.service == SERVICE_PAIR_SENSOR:
                await api.pair_sensor(ctrl)
            elif call.service == SERVICE_SET_SCHEDULE_ZONE:
                sid, zone = _schedule_id(rt, call.data["schedule"]), _zone(rt, call.data["zone"])
                values, local = {}, {}
                if "watering_percent" in call.data:
                    values["duration_scaling"] = str(call.data["watering_percent"])
                    local["scaling"] = float(call.data["watering_percent"])
                if "enabled" in call.data:
                    values["zonesched_enabled"] = "1" if call.data["enabled"] else "0"
                    local["enabled"] = call.data["enabled"]
                if not values:
                    raise ServiceValidationError("Give watering_percent and/or enabled")
                await rt.async_set_schedule(sid, values, zone=zone, local=local)
                return
            elif call.service == SERVICE_IMPORT_HISTORY:
                start: date | None = call.data.get("start_date")
                await async_import_history(hass, entry, start)
                return
        except SpruceError as err:
            raise HomeAssistantError(str(err)) from err
        rt.optimistic(None, {"on": False} if call.service == SERVICE_STOP else None)
        if call.service in (SERVICE_HIBERNATE, SERVICE_RUN_SCHEDULE, SERVICE_STOP_SCHEDULE):
            await rt.history.async_request_refresh()

    for name, schema in SCHEMAS.items():
        hass.services.async_register(DOMAIN, name, handle, schema=schema)
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
