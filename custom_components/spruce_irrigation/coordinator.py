"""Data coordinators: a fast one for live status and a slow one for settings and history."""
from __future__ import annotations

import logging
import statistics as stats
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import SpruceApi, SpruceAuthError, SpruceError
from .baselines import NO_FLOW_LPM, BaselineStore
from .const import (
    BASELINE_STALE,
    CONF_FAULT_SHARE,
    CONF_HISTORY_INTERVAL,
    CONF_METER_MIN,
    CONF_REVIEW_TOLERANCE,
    CONF_STATUS_INTERVAL,
    DEFAULT_FAULT_SHARE,
    DEFAULT_HISTORY_INTERVAL,
    DEFAULT_REVIEW_TOLERANCE,
    DEFAULT_STATUS_INTERVAL,
    DOMAIN,
    EVENT_LOOKBACK,
    EVENT_SPRUCE,
    HISTORY_DAYS,
    LITRES_PER_GALLON,
    MM_PER_INCH,
    OUTLET_SILENT,
    WORKING_GPM,
)
from .heads import design

_LOGGER = logging.getLogger(__name__)
WEEKS_KEPT = 14                 # days of water and rain kept for this week vs last week
FLOW_RUNS_FOR_USUAL = 40        # a zone's usual flow is the median of its last working runs


@dataclass
class Zone:
    number: int
    name: str
    enabled: bool
    nozzle: str | None = None
    landscape: str | None = None
    soil: str | None = None
    configured_gpm: float | None = None
    sensor_id: str | None = None
    group: tuple[int, ...] = ()          # the zones of its zone group ("Place") incl. itself; () when not grouped


@dataclass
class StatusData:
    zones: dict[int, dict] = field(default_factory=dict)     # zone -> {on, started, duration, schedule_id}
    run: dict[str, int] = field(default_factory=dict)        # pause / pump_delay / cycle_soak seconds left
    online: bool | None = None
    last_seen: datetime | None = None
    sensors: dict[str, dict] = field(default_factory=dict)   # moisture sensor id -> latest readings


@dataclass
class HistoryData:
    name: str = "Spruce"
    model: str | None = None
    firmware: str | None = None
    serial: str | None = None
    timezone: str | None = None
    zones: dict[int, Zone] = field(default_factory=dict)
    schedules: dict[int, dict] = field(default_factory=dict)   # id -> {name, type, enabled, start_time}
    hibernating: bool = False
    hibernate_until: date | None = None
    rain_skip_inches: float | None = None
    freeze_skip_f: float | None = None
    wind_skip_mph: float | None = None
    manual_max_minutes: float | None = None
    pump_delay_seconds: float | None = None
    concurrent_zones: bool = False
    num_zones: int = 0
    zone_groups: dict[int, dict] = field(default_factory=dict)        # group id -> {name, zones, sensor_zone}
    flow_meter: dict[str, Any] = field(default_factory=dict)          # port 101's settings (empty: no flow meter)
    outlets: dict[str, dict] = field(default_factory=dict)            # Zigbee outlet id -> {name, model, state…}
    water_today: dict[int, float] = field(default_factory=dict)        # litres per zone today (as Spruce counts)
    water_month: dict[int, float] = field(default_factory=dict)
    minutes_today: dict[int, float] = field(default_factory=dict)
    daily: list[dict] = field(default_factory=list)                    # last days: day, zone, minutes, litres
    last_run: dict[int, dict] = field(default_factory=dict)            # zone -> {at, lpm}
    usual_lpm: dict[int, float] = field(default_factory=dict)          # zone -> median measured flow
    flow_fault: dict[int, bool] = field(default_factory=dict)          # zone's last run measured ~no flow
    network: dict[str, Any] = field(default_factory=dict)
    baseline: dict[int, dict] = field(default_factory=dict)            # Spruce's current baseline: zone -> {id, lpm, at}
    below_range: set[int] = field(default_factory=set)                 # zones whose flow is under the meter's minimum
    forecast: dict[str, list] = field(default_factory=dict)            # WeatherKit: {daily: [...], hourly: [...]}
    rain_daily: dict[date, float] = field(default_factory=dict)        # recorded rain, mm per day (last 2 weeks)
    climate_rules: list[int] = field(default_factory=list)              # climate adjust: 12 monthly percentages


class SpruceStatusCoordinator(DataUpdateCoordinator[StatusData]):
    """Zone status, pause/cycle-soak, moisture sensors: every minute by default."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: SpruceApi, controller: str) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=f"{DOMAIN} status",
                         update_interval=timedelta(seconds=entry.options.get(CONF_STATUS_INTERVAL, DEFAULT_STATUS_INTERVAL)))
        self.api, self.controller = api, controller
        self.overrides: dict[int, tuple[dict, datetime]] = {}   # zone -> (state just commanded, until when)

    def hold(self, zone: int, change: dict, seconds: int = 30) -> None:
        """Keep showing a commanded zone state until Spruce's cloud reports it (it lags a few seconds)."""
        self.overrides[zone] = (change, dt_util.utcnow() + timedelta(seconds=seconds))

    async def _async_update_data(self) -> StatusData:
        try:
            zone_status = await self.api.zone_status(self.controller)
            sensors = await self.api.sensor_status(self.controller)
            controller = await self.api.controller_status(self.controller)
            try:
                run = await self.api.run_status(self.controller)
            except SpruceError:          # the controller itself may be briefly unreachable
                run = {}
        except SpruceAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SpruceError as err:
            raise UpdateFailed(str(err)) from err
        zones = {}
        for key, z in zone_status.items():
            if not str(key).isdigit() or int(key) == 0 or int(key) >= 100:   # 0 = all zones, 100+ = flow port
                continue
            started = z.get("epoch_time")
            zones[int(key)] = {
                "on": z.get("zone_state") == 1,
                "started": datetime.fromtimestamp(started, timezone.utc) if started else None,
                "duration": z.get("duration") or 0,
                "schedule_id": z.get("schedule_id"),
            }
        now = dt_util.utcnow()
        for zone, (change, until) in list(self.overrides.items()):
            if now >= until or zones.get(zone, {}).get("on") == change.get("on"):
                del self.overrides[zone]          # Spruce has caught up (or it's taking too long)
            else:
                zones[zone] = {**zones.get(zone, {}), **change}
        seen = controller.get("epoch_time")
        return StatusData(
            zones=zones, run=run,
            online=controller.get("current_state") == 1 if controller else None,
            last_seen=datetime.fromtimestamp(seen, timezone.utc) if seen else None,
            sensors={sid: _sensor(s) for sid, s in sensors.items()},
        )


class SpruceHistoryCoordinator(DataUpdateCoordinator[HistoryData]):
    """Settings, water per day, measured flow and the activity log: every 15 minutes by default."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: SpruceApi, controller: str) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=f"{DOMAIN} history",
                         update_interval=timedelta(seconds=entry.options.get(CONF_HISTORY_INTERVAL, DEFAULT_HISTORY_INTERVAL)))
        self.api, self.controller, self.entry = api, controller, entry
        self._seen_events: set[str] = set()
        self._events_primed = False
        self.baselines: BaselineStore | None = None    # set up before the first refresh

    async def _async_update_data(self) -> HistoryData:
        today = dt_util.now().date()
        try:
            settings = await self.api.controller_settings(self.controller)
            app_settings = await self.api.settings()
            since = min(today.replace(day=1), today - timedelta(days=WEEKS_KEPT))
            water = await self.api.water_summary(self.controller, since, today + timedelta(days=1))
            runs = await self.api.gpm_data(self.controller, today - timedelta(days=365), today + timedelta(days=1))
            try:
                outlet_status = await self.api.outlet_status(self.controller)
            except SpruceError:
                outlet_status = {}
            try:
                network = await self.api.network(self.controller)
            except SpruceError:
                network = {}
            try:
                baseline = await self.api.current_baseline(self.controller)
            except SpruceError:
                baseline = {}
            try:
                forecast = await self.api.forecast(self.controller)
            except SpruceError:
                forecast = {}
            try:
                rain = await self.api.weather_history(self.controller, today - timedelta(days=WEEKS_KEPT),
                                                      today + timedelta(days=1))
            except SpruceError:
                rain = []
            try:
                await self._fire_events()
            except SpruceError as err:   # the activity log is slow; it shouldn't hold up the rest
                _LOGGER.debug("Activity log not read this time: %s", err)
        except SpruceAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SpruceError as err:
            raise UpdateFailed(str(err)) from err

        data = HistoryData(
            name=settings.get("controller_name") or "Spruce",
            model=settings.get("model_number"), firmware=settings.get("firmware"),
            serial=settings.get("serial_number"), timezone=settings.get("timezone"), network=network,
        )
        for key, z in (settings.get("zone") or {}).items():
            data.zones[int(key)] = Zone(
                number=int(key), name=z.get("zone_name") or f"Zone {key}", enabled=z.get("zenabled") == "1",
                nozzle=z.get("nozzle_type"), landscape=z.get("landscape_type"), soil=z.get("soil_type"),
                configured_gpm=_float(z.get("gpm")), sensor_id=z.get("sensor") or None)
        controller_app = (app_settings.get("controller") or {}).get(self.controller, {})
        data.hibernating = str(controller_app.get("hibernate_enabled")) == "1"
        until = str(controller_app.get("hibernate_end_date") or "")
        data.hibernate_until = date.fromisoformat(until[:10]) if data.hibernating and _is_date(until[:10]) else None
        data.rain_skip_inches = _float(controller_app.get("rain_delay_inches"))
        data.freeze_skip_f = _float(controller_app.get("freeze_temperature"))
        data.wind_skip_mph = _float(controller_app.get("wind_mph"))
        data.manual_max_minutes = _float(controller_app.get("manual_max_time"))
        data.pump_delay_seconds = _float(controller_app.get("pump_delay"))
        data.concurrent_zones = str(controller_app.get("concurrent_zones_enabled")) == "1"
        rules = [_int(v) for v in str(controller_app.get("climate_adjust_rules") or "").split(",")]
        data.climate_rules = rules if len(rules) == 12 and None not in rules else []
        for row in rain if isinstance(rain, list) else []:
            if _is_date(str(row.get("ymd"))) and _float(row.get("precip_in")) is not None:
                data.rain_daily[date.fromisoformat(row["ymd"])] = round(_float(row["precip_in"]) * MM_PER_INCH, 1)
        if isinstance(forecast, dict):
            data.forecast = {part: (forecast.get(part) or {}).get("data") or [] for part in ("daily", "hourly")
                             if isinstance(forecast.get(part), dict)}
        data.num_zones = int(_float(controller_app.get("num_zones")) or max([z for z in data.zones if z < 100], default=0))
        for gid, g in (controller_app.get("zone_group") or {}).items():
            members = tuple(int(z) for z in str(g.get("grouped_zones") or "").split(";") if z.strip().isdigit())
            if str(gid).isdigit() and members:
                data.zone_groups[int(gid)] = {"name": g.get("zone_group_name") or f"Zones {' + '.join(map(str, members))}",
                                              "zones": members, "sensor_zone": _int(g.get("sensor_in_zone"))}
        for group in data.zone_groups.values():
            for z in group["zones"]:
                if z in data.zones:
                    data.zones[z].group = group["zones"]
        port = (controller_app.get("zone") or {}).get("101") or {}
        if port.get("custom_flow_enabled") not in (None, "") or port.get("flow_limit_high") not in (None, ""):
            data.flow_meter = {
                "limit_high": _float(port.get("flow_limit_high")), "limit_low": _float(port.get("flow_limit_low")),
                "stop_on_high": str(port.get("flow_stop_enabled")) == "1",
                "pump_protect": str(port.get("flow_pump_protect_enabled", "1")) == "1",
                "leak_small": str(port.get("flow_idle_leak_small_enabled", "1")) == "1",
                "leak_big": str(port.get("flow_idle_leak_big_enabled", "1")) == "1",
                "custom": str(port.get("custom_flow_enabled")) == "1",
                "k": _float(port.get("flow_k")), "offset": _float(port.get("flow_offset")),
            }
        for did, d in (app_settings.get("device") or {}).items():
            if str(d.get("gateway")) != self.controller or str(d.get("device_type", "")).lower() != "outlet":
                continue
            st = outlet_status.get(did) or {}
            seen = _int(st.get("epoch_time"))
            data.outlets[did] = {
                "name": d.get("device_name") or f"Outlet {did[-4:]}", "model": d.get("model_number"),
                "firmware": d.get("firmware"), "on": _int(st.get("current_state")) == 1,
                "last_seen": datetime.fromtimestamp(seen, timezone.utc) if seen else None,
                "signal": _float(st.get("signal_strength")),
            }
        for sid, s in (app_settings.get("schedule") or {}).items():
            if str(s.get("master_controller", self.controller)) not in (self.controller, "None", ""):
                continue
            data.schedules[int(sid)] = {
                "name": s.get("schedule_name") or f"Schedule {sid}", "type": s.get("schedule_type"),
                "enabled": str(s.get("sched_enabled")) == "1", "start_time": s.get("start_time"),
                # Skips default to on in the Spruce app when a schedule doesn't store them
                "rain_skip": str(s.get("rain_delay_enabled", "1")) == "1",
                "freeze_skip": str(s.get("freeze_delay_enabled", "1")) == "1",
                "wind_skip": str(s.get("wind_delay_enabled", "1")) == "1",
                "rain_inches": _float(s.get("rain_delay_inches")) if s.get("rain_delay_inches") not in (None, "") else 0.15,
                "start_mode": s.get("start_time_mode") or "set", "start_offset": _float(s.get("start_time_rel")) or 0.0,
                "days_select": s.get("days_select"), "days_var": str(s.get("days_var") or ""),
                "window": str(s.get("time_restr1_enabled")) == "1",
                "window_start": s.get("time_restr1_start"), "window_end": s.get("time_restr1_end"),
                "zones": {int(z): {"enabled": str(v.get("zonesched_enabled", "1")) == "1",
                                   "scaling": _float(v.get("duration_scaling")) or 100.0,
                                   # connected schedules keep the weather skips per zone (default on, as the app)
                                   "rain_skip": str(v.get("rain_delay_enabled", "1")) == "1",
                                   "freeze_skip": str(v.get("freeze_delay_enabled", "1")) == "1",
                                   "wind_skip": str(v.get("wind_delay_enabled", "1")) == "1",
                                   "mode": v.get("schedule_mode"), "cycles": v.get("cycles"),
                                   "weekly_min": _float(v.get("weekly_safeguard_min")),
                                   "weekly_max": _float(v.get("weekly_safeguard_max")),
                                   "climate_adjust": str(v.get("climate_adjust_enabled", "0")) == "1"}
                          for z, v in _literal(s.get("zone")).items() if str(z).isdigit() and isinstance(v, dict)},
                "next_date": s.get("next_date"), "next_minutes": _float(s.get("next_minutes")),
                "days": _watering_days(s),
                # Spruce's plan: its last few days and its forecast for the next ~18 (connected schedules re-plan daily)
                "plan": {day: _plan_day(day, d, data.timezone)
                         for part in ("prediction", "history") for day, d in (s.get(part) or {}).items()
                         if isinstance(d, dict) and _is_date(day)},
            }

        month = today.replace(day=1)
        for row in water:
            day, zone = date.fromisoformat(row["ymd"]), int(row["zone"])
            litres = float(row.get("gallons") or 0) * LITRES_PER_GALLON
            minutes = float(row.get("minutes") or 0)
            data.daily.append({"day": day, "zone": zone, "minutes": minutes, "litres": litres})
            if day == today:
                data.water_today[zone] = data.water_today.get(zone, 0) + litres
                data.minutes_today[zone] = data.minutes_today.get(zone, 0) + minutes
            if day >= month:
                data.water_month[zone] = data.water_month.get(zone, 0) + litres

        share = self.entry.options.get(CONF_FAULT_SHARE, DEFAULT_FAULT_SHARE)
        by_zone: dict[int, list[tuple[datetime, float]]] = {}
        for r in runs:
            if r.get("DTepoch") is None or r.get("zone") is None:
                continue
            by_zone.setdefault(int(r["zone"]), []).append(
                (datetime.fromtimestamp(int(r["DTepoch"]), timezone.utc), float(r.get("gpm") or 0)))
        for zone, zr in by_zone.items():
            zr.sort()
            at, gpm = zr[-1]
            data.last_run[zone] = {"at": at, "lpm": gpm * LITRES_PER_GALLON}
            good = [g for _, g in zr if g > WORKING_GPM][-FLOW_RUNS_FOR_USUAL:]
            if good:
                data.usual_lpm[zone] = stats.median(good) * LITRES_PER_GALLON
                data.flow_fault[zone] = gpm * LITRES_PER_GALLON < share * data.usual_lpm[zone]
        for key, b in baseline.items():
            if isinstance(b, dict) and str(key).isdigit() and _float(b.get("gpm")) is not None:
                seen = _int(b.get("DTepoch"))
                data.baseline[int(key)] = {"id": str(b.get("id")), "lpm": _float(b["gpm"]) * LITRES_PER_GALLON,
                                           "at": datetime.fromtimestamp(seen, timezone.utc) if seen else None}
        self._below_meter_range(data)
        self._raise_flow_issue(data)
        self.review_baseline(data)
        self._raise_health_issues(data)
        return data

    def _below_meter_range(self, data: HistoryData) -> None:
        """Zones whose flow is under the meter's minimum (its Q1, an option): a Hall-effect meter misses pulses there,
        so their readings (often 0) say nothing about leaks or faults. A zone counts as below when every estimate of
        its flow — design (heads), accepted baseline, Spruce's baseline, usual measured flow — is under the minimum."""
        minimum = float(self.entry.options.get(CONF_METER_MIN) or 0)
        if not minimum:
            return
        designs = {z: d.lpm for z, d in design(self.entry).items() if d.lpm > 0}
        for z in data.zones:
            estimates = [v for v in (designs.get(z), self.baselines.reference(z) if self.baselines else None,
                                     data.baseline.get(z, {}).get("lpm"), data.usual_lpm.get(z)) if v]
            if not estimates or max(estimates) < minimum:
                data.below_range.add(z)
        for z in data.below_range:
            data.flow_fault[z] = False

    def review_baseline(self, data: HistoryData) -> None:
        """Store a baseline not seen before and review it; tell the user when one lands (not the first one seen)."""
        if self.baselines is None:
            return
        first = not self.baselines.snapshots
        designs = {z: d.lpm for z, d in design(self.entry).items() if d.lpm > 0}
        snapshot = self.baselines.observe(data.baseline, designs,
                                          self.entry.options.get(CONF_REVIEW_TOLERANCE, DEFAULT_REVIEW_TOLERANCE),
                                          skip=data.below_range)
        pending = self.baselines.pending()
        issue_id = f"baseline_review_{self.controller}"
        name = lambda z: data.zones[z].name if z in data.zones else f"Zone {z}"   # noqa: E731
        if pending:
            ir.async_create_issue(
                self.hass, DOMAIN, issue_id, is_fixable=True, severity=ir.IssueSeverity.WARNING,
                translation_key="baseline_review", data={"entry_id": self.entry.entry_id},
                translation_placeholders={"zones": ", ".join(name(z) for z in sorted(pending))})
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        if snapshot is None or first:
            return
        previous = [s for s in self.baselines.snapshots if s is not snapshot]
        before = previous[-1]["zones"] if previous else {}
        changes = sorted(((z, before.get(z), v) for z, v in snapshot["zones"].items()),
                         key=lambda c: -abs((c[2] - c[1]) / c[1]) if c[1] else 0)
        lines = [f"- {name(z)}: {old:.1f} → {new:.1f} L/min" if old else f"- {name(z)}: {new:.1f} L/min"
                 for z, old, new in changes[:6]]
        review = f"\n\n{len(pending)} zone(s) need review (Settings → Repairs)." if pending else ""
        persistent_notification.async_create(
            self.hass, "Biggest changes from the previous baseline:\n" + "\n".join(lines) + review,
            title=f"{data.name}: new flow baseline", notification_id=f"{DOMAIN}_baseline_{self.controller}")

    def _raise_health_issues(self, data: HistoryData) -> None:
        """Repairs notices for things the Spruce app only hints at."""
        name = lambda z: data.zones[z].name if z in data.zones else f"Zone {z}"   # noqa: E731
        enabled = {z for z, zone in data.zones.items() if zone.enabled}
        no_flow = sorted(z for z, b in data.baseline.items()
                         if z in enabled and z not in data.below_range and b["lpm"] < NO_FLOW_LPM)
        offset = data.flow_meter.get("offset")
        stamps = [b["at"] for b in data.baseline.values() if b.get("at")]
        stale = bool(stamps) and dt_util.utcnow() - max(stamps) > BASELINE_STALE
        silent = [o["name"] for o in data.outlets.values()
                  if o.get("last_seen") is None or dt_util.utcnow() - o["last_seen"] > OUTLET_SILENT]
        checks = {
            "no_flow_at_baseline": (bool(no_flow), {"zones": ", ".join(name(z) for z in no_flow)}),
            "flow_calibration": (bool(data.flow_meter.get("custom")) and offset is not None and not 0 <= offset <= 2,
                                 {"offset": f"{offset:g}" if offset is not None else ""}),
            "stale_baseline": (stale, {"date": max(stamps).date().isoformat() if stamps else ""}),
            "repeaters_silent": (bool(silent), {"outlets": ", ".join(silent)}),
        }
        for key, (raise_it, placeholders) in checks.items():
            issue_id = f"{key}_{self.controller}"
            if raise_it:
                ir.async_create_issue(self.hass, DOMAIN, issue_id, is_fixable=False, severity=ir.IssueSeverity.WARNING,
                                      translation_key=key, translation_placeholders=placeholders)
            else:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    async def _fire_events(self) -> None:
        """Fire spruce_irrigation_event for each new activity-log entry (not the backlog at start-up)."""
        now = int(dt_util.utcnow().timestamp())
        entries = await self.api.events(self.controller, now - int(EVENT_LOOKBACK.total_seconds()), now)
        for e in sorted(entries, key=lambda e: e.get("DTepoch") or 0):
            eid = str(e.get("id"))
            if eid in self._seen_events:
                continue
            self._seen_events.add(eid)
            if self._events_primed:
                self.hass.bus.async_fire(EVENT_SPRUCE, {
                    "controller_id": self.controller, "id": eid, "type": e.get("type"),
                    "zone": int(e["zone"]) if str(e.get("zone") or "").isdigit() else None,
                    "message": e.get("message"),
                    "time": datetime.fromtimestamp(e["DTepoch"], timezone.utc).isoformat() if e.get("DTepoch") else None})
        self._events_primed = True

    def _raise_flow_issue(self, data: HistoryData) -> None:
        """A Repairs notice when several zones' latest runs measured no flow: the flow meter has likely failed."""
        faulty = sorted(z for z, f in data.flow_fault.items() if f and data.zones.get(z, Zone(z, "", True)).enabled)
        issue_id = f"flow_meter_{self.controller}"
        if len(faulty) >= 3:
            ir.async_create_issue(
                self.hass, DOMAIN, issue_id, is_fixable=False, severity=ir.IssueSeverity.WARNING,
                translation_key="flow_meter_fault",
                translation_placeholders={"zones": ", ".join(data.zones[z].name if z in data.zones else str(z) for z in faulty)})
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)


def _sensor(s: dict) -> dict:
    stamp = lambda key: datetime.fromtimestamp(s[key], timezone.utc) if s.get(key) else None
    return {
        "name": s.get("sensor_name"), "moisture": _float(s.get("moisture")), "temperature_f": _float(s.get("temperature")),
        "battery_v": _float(s.get("battery")), "signal": _float(s.get("signal")),
        "moisture_at": stamp("moisture_DTepoch"), "latest_at": stamp("latest_DTepoch"),
    }


WEEKDAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")   # Spruce's order


def _is_date(text: str) -> bool:
    try:
        date.fromisoformat(text)
    except (TypeError, ValueError):
        return False
    return True


def _plan_day(day: str, d: dict, tz: str | None) -> dict:
    """One day of a schedule's plan: when it starts, how long it runs (0 = it doesn't) and why not."""
    if d.get("epoch_time"):
        start = datetime.fromtimestamp(int(d["epoch_time"]), timezone.utc)
    else:   # no time given: the schedule's start time on that day, in the controller's time zone
        hours, _, minutes = str(d.get("start_time") or "00:00").partition(":")
        local = dt_util.get_time_zone(tz) if tz else None
        start = datetime.combine(date.fromisoformat(day), datetime.min.time()).replace(
            hour=int(hours or 0) % 24, minute=int(minutes[:2] or 0), tzinfo=local or dt_util.get_default_time_zone())
    seconds = int(_float(d.get("total_duration_seconds")) or 0) if d.get("is_scheduled") else 0
    if seconds:
        reason = None
    elif d.get("is_hibernating"):
        reason = "hibernating"
    elif d.get("is_rain_sensor_delay"):
        reason = "rain_sensor"
    elif d.get("is_rain_delay"):
        reason = "rain"
    elif d.get("is_freeze_delay"):
        reason = "freeze"
    elif d.get("is_wind_delay"):
        reason = "wind"
    else:
        reason = "not_needed"
    zones = {int(z): round(_float(v.get("water_duration_minutes")) or 0, 1)
             for z, v in (d.get("zone") or {}).items() if str(z).isdigit() and isinstance(v, dict)}
    return {"day": date.fromisoformat(day), "start": start, "seconds": seconds, "reason": reason,
            "message": d.get("message"), "zones": {z: m for z, m in zones.items() if m},
            "detail": {int(z): _zone_day(v) for z, v in (d.get("zone") or {}).items()
                       if str(z).isdigit() and isinstance(v, dict)}}


def _zone_day(v: dict) -> dict:
    """One zone on one plan day: minutes, cycles, why (Spruce's message) and its water budget in mm."""
    budget = v.get("water_budget") if isinstance(v.get("water_budget"), dict) else {}
    mm = lambda key: round(_float(budget.get(key)) * MM_PER_INCH, 1) if _float(budget.get(key)) is not None else None  # noqa: E731
    return {
        "minutes": round(_float(v.get("water_duration_minutes")) or 0, 1), "cycles": _int(v.get("cycles")),
        "message": v.get("message"),
        "skip": next((r for r, flag in (("rain", "is_rain_delay"), ("rain_sensor", "is_rain_sensor_delay"),
                                         ("freeze", "is_freeze_delay"), ("wind", "is_wind_delay")) if v.get(flag)), None),
        "budget": {"soil_water_mm": mm("projected_water_balance"), "after_watering_mm": mm("projected_water_balance_after_watering"),
                   "needed_mm": mm("water_needed_inches"), "scheduled_mm": mm("water_scheduled_inches"),
                   "allowed_depletion_mm": mm("allowed_depletion_inches")} if budget and not budget.get("is_invalid") else None,
    }


def _watering_days(s: dict) -> str | None:
    """Which days a schedule may water, in words."""
    select, var = s.get("days_select"), str(s.get("days_var") or "")
    if select == "everyx" and var.isdigit():
        return "Every day" if int(var) <= 1 else f"Every {int(var)} days"
    if select == "even_odd" and var in ("even", "odd"):
        return f"{var.capitalize()} days"
    if select == "days" and len(var) == 7 and set(var) <= {"0", "1"}:
        if var == "1111111":
            return "Every day"
        return ", ".join(name[:3] for name, on in zip(WEEKDAYS, var) if on == "1") or "No days"
    return None


def _int(v: Any) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _literal(v: Any) -> dict:
    """Spruce stores a schedule's zone settings as a Python-style dict string; read it safely."""
    if isinstance(v, dict):
        return v
    try:
        import ast

        value = ast.literal_eval(v) if isinstance(v, str) else {}
    except (ValueError, SyntaxError):
        return {}
    return value if isinstance(value, dict) else {}


def _float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
