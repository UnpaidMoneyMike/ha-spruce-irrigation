"""Sensors: water and status for the controller and each zone, readings for each moisture sensor."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfTemperature,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_PRICE,
    CONF_PRICE_ENTITY,
    CONF_REVIEW_TOLERANCE,
    DEFAULT_REVIEW_TOLERANCE,
    LITRES_PER_GALLON,
    SENSOR_STALE,
)
from .heads import design
from .entity import SpruceEntity, SpruceMoistureEntity, SpruceZoneEntity, outlet_device_info, planned_days

STATES = ["idle", "watering", "paused", "pump_delay", "cycle_soak", "hibernating", "offline"]
# Today's plan, most important first: a schedule watering beats one skipping
TODAY = ["watering", "hibernating", "rain", "rain_sensor", "freeze", "wind", "not_needed", "none"]
LPM = UnitOfVolumeFlowRate.LITERS_PER_MINUTE


@dataclass(frozen=True, kw_only=True)
class SpruceSensorDescription(SensorEntityDescription):
    value_fn: Callable[[Any], Any]
    attrs_fn: Callable[[Any], dict[str, Any] | None] | None = None
    quantity: str | None = None      # units.py quantity: shown in the unit the options choose
    spruce_unit: bool = False        # the value is in Spruce's unit (else litres / L/min)
    status: bool = False            # read from the 1-minute status coordinator (else the history one)


def _controller_state(rt) -> str:
    s, h = rt.status.data, rt.history.data
    if s.online is False:
        return "offline"
    if any(z.get("on") for z in s.zones.values()):
        return "watering"
    for key in ("pause", "pump_delay", "cycle_soak"):
        if s.run.get(key):
            return "paused" if key == "pause" else key
    return "hibernating" if h.hibernating else "idle"


def _running(rt) -> tuple[int, dict] | None:
    return next(((z, s) for z, s in sorted(rt.status.data.zones.items()) if s.get("on")), None)


def _active_zone(rt) -> str:
    run = _running(rt)
    if not run:
        return "none"
    zone = rt.history.data.zones.get(run[0])
    return zone.name if zone else f"Zone {run[0]}"


def _ends(rt):
    run = _running(rt)
    if not run or not run[1].get("started"):
        return None
    return run[1]["started"] + timedelta(seconds=run[1].get("duration") or 0)


def _next_watering(rt) -> tuple[dict, dict] | None:
    now = dt_util.utcnow()
    return next(((s, d) for s, d in planned_days(rt) if d["seconds"] and d["start"] > now), None)


def _next_attrs(rt) -> dict[str, Any]:
    nxt = _next_watering(rt)
    if not nxt:
        return {}
    schedule, day = nxt
    zones = rt.history.data.zones
    return {"schedule": schedule["name"], "minutes": round(day["seconds"] / 60),
            "ends": (day["start"] + timedelta(seconds=day["seconds"])).isoformat(), "message": day["message"],
            "zones": {zones[z].name if z in zones else f"Zone {z}": m for z, m in sorted(day["zones"].items())}}


def _today(rt) -> list[tuple[dict, dict]]:
    today = dt_util.now().date()
    return [(s, d) for s, d in planned_days(rt) if d["day"] == today]


def _today_state(rt) -> str:
    states = ["watering" if d["seconds"] else d["reason"] for _, d in _today(rt)]
    return min(states, key=TODAY.index) if states else "none"


def _today_attrs(rt) -> dict[str, Any]:
    return {"schedules": {s["name"]: {"minutes": round(d["seconds"] / 60), "starts": d["start"].isoformat(),
                                      "message": d["message"]} for s, d in _today(rt)}}


CONTROLLER_SENSORS = (
    SpruceSensorDescription(key="status", translation_key="status", device_class=SensorDeviceClass.ENUM, options=STATES,
                            value_fn=_controller_state, status=True),
    SpruceSensorDescription(key="active_zone", translation_key="active_zone", icon="mdi:sprinkler-variant",
                            value_fn=_active_zone, status=True),
    SpruceSensorDescription(key="watering_ends", translation_key="watering_ends", device_class=SensorDeviceClass.TIMESTAMP,
                            value_fn=_ends, status=True),
    SpruceSensorDescription(key="next_watering", translation_key="next_watering", device_class=SensorDeviceClass.TIMESTAMP,
                            icon="mdi:calendar-clock", value_fn=lambda rt: (_next_watering(rt) or (None, {}))[1].get("start"),
                            attrs_fn=_next_attrs, status=True),
    SpruceSensorDescription(key="watering_today", translation_key="watering_today", device_class=SensorDeviceClass.ENUM,
                            options=TODAY, icon="mdi:calendar-today", value_fn=_today_state, attrs_fn=_today_attrs,
                            status=True),
    SpruceSensorDescription(key="water_today", translation_key="water_today", device_class=SensorDeviceClass.WATER,
                            state_class=SensorStateClass.TOTAL_INCREASING, native_unit_of_measurement=UnitOfVolume.LITERS, quantity="volume",
                            suggested_display_precision=0, value_fn=lambda rt: round(sum(rt.history.data.water_today.values()), 1)),
    SpruceSensorDescription(key="water_month", translation_key="water_month", device_class=SensorDeviceClass.WATER,
                            state_class=SensorStateClass.TOTAL, native_unit_of_measurement=UnitOfVolume.LITERS, quantity="volume",
                            suggested_display_precision=0, value_fn=lambda rt: round(sum(rt.history.data.water_month.values()), 1)),
    SpruceSensorDescription(key="wifi_signal", translation_key="wifi_signal", device_class=SensorDeviceClass.SIGNAL_STRENGTH,
                            native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT, entity_category=EntityCategory.DIAGNOSTIC,
                            entity_registry_enabled_default=False, value_fn=lambda rt: rt.history.data.network.get("signal")),
    SpruceSensorDescription(key="last_seen", translation_key="last_seen", device_class=SensorDeviceClass.TIMESTAMP,
                            entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False, value_fn=lambda rt: rt.status.data.last_seen, status=True),
)

ZONE_SENSORS = (
    SpruceSensorDescription(key="water_today", translation_key="zone_water_today", device_class=SensorDeviceClass.WATER,
                            state_class=SensorStateClass.TOTAL_INCREASING, native_unit_of_measurement=UnitOfVolume.LITERS, quantity="volume",
                            suggested_display_precision=0, value_fn=lambda a: round(a[0].history.data.water_today.get(a[1], 0), 1)),
    SpruceSensorDescription(key="last_flow", translation_key="zone_last_flow", device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
                            native_unit_of_measurement=LPM, suggested_display_precision=1, quantity="flow",
                            value_fn=lambda a: _round(a[0].history.data.last_run.get(a[1], {}).get("lpm"))),
    SpruceSensorDescription(key="usual_flow", translation_key="zone_usual_flow", device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
                            native_unit_of_measurement=LPM, suggested_display_precision=1, entity_category=EntityCategory.DIAGNOSTIC,
                            quantity="flow", value_fn=lambda a: _round(a[0].history.data.usual_lpm.get(a[1]))),
    SpruceSensorDescription(key="last_run", translation_key="zone_last_run", device_class=SensorDeviceClass.TIMESTAMP,
                            value_fn=lambda a: a[0].history.data.last_run.get(a[1], {}).get("at")),
)

MOISTURE_SENSORS = (
    SpruceSensorDescription(key="moisture", device_class=SensorDeviceClass.MOISTURE, native_unit_of_measurement=PERCENTAGE,
                            state_class=SensorStateClass.MEASUREMENT, value_fn=lambda r: r.get("moisture"), status=True),
    SpruceSensorDescription(key="temperature", device_class=SensorDeviceClass.TEMPERATURE,
                            native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT, state_class=SensorStateClass.MEASUREMENT,
                            quantity="temperature", spruce_unit=True, suggested_display_precision=1,
                            value_fn=lambda r: r.get("temperature_f"), status=True),
    SpruceSensorDescription(key="battery", translation_key="battery_voltage", device_class=SensorDeviceClass.VOLTAGE,
                            native_unit_of_measurement=UnitOfElectricPotential.VOLT, state_class=SensorStateClass.MEASUREMENT,
                            entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=2,
                            value_fn=lambda r: r.get("battery_v"), status=True),
    SpruceSensorDescription(key="signal", translation_key="zigbee_signal", device_class=SensorDeviceClass.SIGNAL_STRENGTH,
                            native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT, entity_category=EntityCategory.DIAGNOSTIC,
                            entity_registry_enabled_default=False, value_fn=lambda r: r.get("signal"), status=True),
    SpruceSensorDescription(key="last_reading", translation_key="last_reading", device_class=SensorDeviceClass.TIMESTAMP,
                            entity_category=EntityCategory.DIAGNOSTIC, value_fn=lambda r: r.get("latest_at"), status=True),
)


def _round(v):
    return round(v, 2) if v is not None else None


# ---- flow baseline, design flow and flow check -------------------------------------------------------------------

CHECKS = ["ok", "high", "low", "no_flow", "no_reference", "no_run"]


def _flow(rt, lpm):
    return round(rt.units.from_metric("flow", lpm), 2) if lpm is not None else None


def _zone_name(rt, z) -> str:
    zones = rt.history.data.zones
    return zones[z].name if z in zones else f"Zone {z}"


def _design(rt, z):
    d = design(rt.history.entry).get(z)
    return d if d and d.lpm > 0 else None


def _reference(rt, z) -> tuple[float | None, str | None]:
    """What a zone's flow is checked against: its design flow (from its heads), else its accepted baseline."""
    if d := _design(rt, z):
        return d.lpm, "design"
    ref = rt.baselines.reference(z) if rt.baselines else None
    return (ref, "baseline") if ref else (None, None)


def _check(a) -> str:
    rt, z = a
    ref, _ = _reference(rt, z)
    last = rt.history.data.last_run.get(z, {}).get("lpm")
    if ref is None:
        return "no_reference"
    if last is None:
        return "no_run"
    tolerance = rt.history.entry.options.get(CONF_REVIEW_TOLERANCE, DEFAULT_REVIEW_TOLERANCE)
    if last < 0.1 * ref:
        return "no_flow"
    deviation = (last - ref) / ref
    return "high" if deviation > tolerance else "low" if deviation < -tolerance else "ok"


def _check_attrs(a) -> dict[str, Any]:
    rt, z = a
    ref, source = _reference(rt, z)
    last = rt.history.data.last_run.get(z, {}).get("lpm")
    d = _design(rt, z)
    return {"reference": source, "reference_flow": _flow(rt, ref), "last_flow": _flow(rt, last),
            "deviation_percent": round((last - ref) / ref * 100) if ref and last is not None else None,
            "design_flow": _flow(rt, d.lpm if d else None),
            "baseline_flow": _flow(rt, rt.history.data.baseline.get(z, {}).get("lpm")),
            "unit": rt.units.unit["flow"]}


def _baseline_attrs(a) -> dict[str, Any]:
    rt, z = a
    b = rt.history.data.baseline.get(z, {})
    latest = rt.baselines.latest if rt.baselines else None
    return {"baseline_id": b.get("id"), "baselined": b["at"].isoformat() if b.get("at") else None,
            "accepted_flow": _flow(rt, rt.baselines.reference(z) if rt.baselines else None),
            "review": (latest or {}).get("review", {}).get(z)}


def _design_attrs(a) -> dict[str, Any] | None:
    rt, z = a
    d = design(rt.history.entry).get(z)
    if not d:
        return None
    return {"heads": [f"{r['heads']}: {_flow(rt, r['flow_lpm'])} {rt.units.unit['flow']}" for r in d.rows],
            "precipitation_rate_mm_h": d.precipitation_mm_h, "missing_models": d.missing or None}


def _last_baselined(rt):
    stamps = [b["at"] for b in rt.history.data.baseline.values() if b.get("at")]
    return max(stamps) if stamps else None


def _history_attrs(rt) -> dict[str, Any]:
    """The baselines kept (newest first, up to 12), each zone's flow in the chosen unit."""
    if not rt.baselines:
        return {}
    return {"unit": rt.units.unit["flow"], "baselines": [
        {"id": snap["id"], "date": dt_util.as_local(datetime.fromisoformat(snap["at"])).date().isoformat(),
         "source": snap["source"], "note": snap.get("note"),
         "zones": {_zone_name(rt, z): _flow(rt, v) for z, v in sorted(snap["zones"].items())},
         "review": {_zone_name(rt, z): v for z, v in sorted(snap["review"].items()) if v != "accepted"} or None}
        for snap in reversed(rt.baselines.snapshots[-12:])]}


def _review_attrs(rt) -> dict[str, Any]:
    pending = rt.baselines.pending() if rt.baselines else {}
    return {"unit": rt.units.unit["flow"], "zones": {
        _zone_name(rt, z): {"previous": _flow(rt, p["previous"]), "new": _flow(rt, p["new"])}
        for z, p in sorted(pending.items())}}


def _zone_days(rt, z) -> list[tuple[dict, dict, dict]]:
    """(schedule, day, the zone's detail) for every plan day that waters this zone, soonest first."""
    return [(s, d, d["detail"].get(z, {})) for s, d in planned_days(rt) if d["seconds"] and d["zones"].get(z)]


def _zone_next(a):
    rt, z = a
    now = dt_util.utcnow()
    return next((d["start"] for _, d, _ in _zone_days(rt, z) if d["start"] > now), None)


def _zone_next_attrs(a) -> dict[str, Any]:
    """The next watering of a zone, Spruce's projection for the coming week, and its water budget (mm)."""
    rt, z = a
    now = dt_util.utcnow()
    upcoming = [(s, d, detail) for s, d, detail in _zone_days(rt, z) if d["start"] > now]
    week = [d for _, d, _ in upcoming if d["start"] < now + timedelta(days=7)]
    attrs: dict[str, Any] = {
        "days_next_7": len({d["day"] for d in week}),
        "average_minutes": round(sum(d["zones"][z] for d in week) / len(week)) if week else 0,
    }
    if upcoming:
        schedule, day, detail = upcoming[0]
        attrs.update({"schedule": schedule["name"], "minutes": day["zones"][z], "cycles": detail.get("cycles"),
                      "message": detail.get("message") or day["message"], "water_budget": detail.get("budget")})
    today = dt_util.now().date()
    todays = [d["detail"].get(z) for _, d in planned_days(rt) if d["day"] == today and d["detail"].get(z)]
    if todays:
        attrs["today"] = todays[0].get("message")
        attrs["today_skip"] = todays[0].get("skip")
    return attrs


def _climate(rt) -> int | None:
    rules = rt.history.data.climate_rules
    return rules[dt_util.now().month - 1] if rules else None


def _climate_attrs(rt) -> dict[str, Any]:
    names = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
    return dict(zip(names, rt.history.data.climate_rules))


CLIMATE_SENSORS = (
    SpruceSensorDescription(key="climate_adjust", translation_key="climate_adjust", native_unit_of_measurement=PERCENTAGE,
                            icon="mdi:sun-thermometer", value_fn=_climate, attrs_fn=_climate_attrs),
)


FLOW_RATE = SpruceSensorDescription(
    key="flow_rate", translation_key="flow_rate", device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
    native_unit_of_measurement=LPM, suggested_display_precision=1, quantity="flow", state_class=SensorStateClass.MEASUREMENT,
    value_fn=lambda rt: rt.live.flow_lpm if rt.live else None,
    attrs_fn=lambda rt: {"average_10_s": rt.units.from_metric("flow", rt.live.flow_avg_lpm) if rt.live and rt.live.flow_avg_lpm is not None else None,
                         "read_at": rt.live.flow_at.isoformat() if rt.live and rt.live.flow_at else None},
    status=True)


PLAN_ZONE_SENSORS = (
    SpruceSensorDescription(key="next_watering", translation_key="zone_next_watering", device_class=SensorDeviceClass.TIMESTAMP,
                            icon="mdi:calendar-clock", value_fn=_zone_next, attrs_fn=_zone_next_attrs),
)


BASELINE_CONTROLLER_SENSORS = (
    SpruceSensorDescription(key="last_baselined", translation_key="last_baselined", device_class=SensorDeviceClass.TIMESTAMP,
                            icon="mdi:chart-bell-curve", value_fn=_last_baselined, attrs_fn=_history_attrs),
    SpruceSensorDescription(key="baseline_review", translation_key="baseline_review", icon="mdi:clipboard-check-outline",
                            native_unit_of_measurement="zones",
                            value_fn=lambda rt: len(rt.baselines.pending()) if rt.baselines else 0, attrs_fn=_review_attrs),
)

BASELINE_ZONE_SENSORS = (
    SpruceSensorDescription(key="baseline_flow", translation_key="zone_baseline_flow", device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
                            native_unit_of_measurement=LPM, suggested_display_precision=1, quantity="flow",
                            state_class=SensorStateClass.MEASUREMENT,
                            value_fn=lambda a: _round(a[0].history.data.baseline.get(a[1], {}).get("lpm")),
                            attrs_fn=_baseline_attrs),
    SpruceSensorDescription(key="design_flow", translation_key="zone_design_flow", device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
                            native_unit_of_measurement=LPM, suggested_display_precision=1, quantity="flow",
                            value_fn=lambda a: _round(getattr(_design(*a), "lpm", None)), attrs_fn=_design_attrs),
    SpruceSensorDescription(key="flow_check", translation_key="zone_flow_check", device_class=SensorDeviceClass.ENUM,
                            options=CHECKS, icon="mdi:water-check", value_fn=_check, attrs_fn=_check_attrs),
)


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    rt = entry.runtime_data
    entities: list[SensorEntity] = [SpruceControllerSensor(entry, d)
                                    for d in CONTROLLER_SENSORS + BASELINE_CONTROLLER_SENSORS + CLIMATE_SENSORS]
    for z, zone in rt.history.data.zones.items():
        if zone.enabled:
            entities += [SpruceZoneSensor(entry, z, d) for d in ZONE_SENSORS + BASELINE_ZONE_SENSORS + PLAN_ZONE_SENSORS]
    for sid in rt.status.data.sensors:
        entities += [SpruceMoistureSensor(entry, sid, d) for d in MOISTURE_SENSORS]
    entities += [SpruceCostSensor(entry, key) for key in ("water_cost_today", "water_cost_month")]
    if rt.history.data.flow_meter:
        entities.append(SpruceControllerSensor(entry, FLOW_RATE))
    for did in rt.history.data.outlets:
        entities += [SpruceOutletSensor(entry, did, d) for d in OUTLET_SENSORS]
    async_add_entities(entities)


class _UnitsMixin:
    """Shows a value in the unit chosen in the options (Spruce works in gallons / gpm / °F; the integration keeps
    litres and L/min)."""

    entity_description: SpruceSensorDescription

    def _apply_units(self) -> None:
        q = self.entity_description.quantity
        if q:
            # Suggested too, so Home Assistant doesn't convert it to its own unit system when the entity is created
            self._attr_native_unit_of_measurement = self._attr_suggested_unit_of_measurement = self.rt.units.unit[q]

    def _convert(self, value):
        d = self.entity_description
        if not d.quantity or value is None:
            return value
        value = self.rt.units.from_spruce(d.quantity, value) if d.spruce_unit else self.rt.units.from_metric(d.quantity, value)
        return round(value, 2)


class SpruceControllerSensor(_UnitsMixin, SpruceEntity, SensorEntity):
    entity_description: SpruceSensorDescription

    def __init__(self, entry, description: SpruceSensorDescription) -> None:
        rt = entry.runtime_data
        super().__init__(entry, rt.status if description.status else rt.history, description.key)
        self.entity_description = description
        self._apply_units()

    @property
    def native_value(self):
        return self._convert(self.entity_description.value_fn(self.rt))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.rt) if fn else None

    @property
    def last_reset(self):
        if self.entity_description.key == "water_month":
            return dt_util.start_of_local_day(dt_util.now().date().replace(day=1))
        return None


class SpruceZoneSensor(_UnitsMixin, SpruceZoneEntity, SensorEntity):
    entity_description: SpruceSensorDescription

    def __init__(self, entry, zone: int, description: SpruceSensorDescription) -> None:
        super().__init__(entry, entry.runtime_data.history, zone, description.key)
        self.entity_description = description
        self._apply_units()

    @property
    def native_value(self):
        return self._convert(self.entity_description.value_fn((self.rt, self.zone)))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn((self.rt, self.zone)) if fn else None


class SpruceMoistureSensor(_UnitsMixin, SpruceMoistureEntity, SensorEntity):
    entity_description: SpruceSensorDescription

    def __init__(self, entry, sensor_id: str, description: SpruceSensorDescription) -> None:
        super().__init__(entry, entry.runtime_data.status, sensor_id, description.key)
        self.entity_description = description
        self._apply_units()

    @property
    def available(self) -> bool:
        """A sensor that hasn't reported for two days shows unavailable (its last reading is kept as a diagnostic)."""
        if self.entity_description.key == "last_reading":
            return super().available
        at = self.reading.get("latest_at")
        return super().available and at is not None and dt_util.utcnow() - at < SENSOR_STALE

    @property
    def native_value(self):
        return self._convert(self.entity_description.value_fn(self.reading))


class SpruceCostSensor(SpruceEntity, SensorEntity):
    """What the water used today / this month cost, at the price in the options (a fixed price, or an entity that
    holds the tariff): per kL with metric volume, per 1000 gal with US volume. Unknown until a price is set."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:cash"

    def __init__(self, entry, key: str) -> None:
        super().__init__(entry, entry.runtime_data.history, key)
        self._key = key
        self._attr_translation_key = key
        self._options = entry.options

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._attr_native_unit_of_measurement = self.hass.config.currency
        if entity := self._options.get(CONF_PRICE_ENTITY):
            self.async_on_remove(async_track_state_change_event(
                self.hass, [entity], lambda _event: self.async_write_ha_state()))

    def _price(self) -> float | None:
        if entity := self._options.get(CONF_PRICE_ENTITY):
            state = self.hass.states.get(entity)
            try:
                return float(state.state) if state else None
            except ValueError:
                return None
        price = self._options.get(CONF_PRICE)
        return float(price) if price not in (None, "") else None

    @property
    def native_value(self) -> float | None:
        price = self._price()
        if price is None:
            return None
        used = self.history.water_today if self._key == "water_cost_today" else self.history.water_month
        thousand = 1000 if self.rt.units.metric_volume else 1000 * LITRES_PER_GALLON   # per kL or per 1000 gal
        return round(sum(used.values()) / thousand * price, 2)

    @property
    def last_reset(self):
        today = dt_util.now().date()
        return dt_util.start_of_local_day(today if self._key == "water_cost_today" else today.replace(day=1))


OUTLET_SENSORS = (
    SpruceSensorDescription(key="last_seen", translation_key="outlet_last_seen", device_class=SensorDeviceClass.TIMESTAMP,
                            entity_category=EntityCategory.DIAGNOSTIC, value_fn=lambda o: o.get("last_seen")),
    SpruceSensorDescription(key="signal", translation_key="zigbee_signal", device_class=SensorDeviceClass.SIGNAL_STRENGTH,
                            native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
                            entity_category=EntityCategory.DIAGNOSTIC, value_fn=lambda o: o.get("signal")),
)


class SpruceOutletSensor(SpruceEntity, SensorEntity):
    """When a Zigbee outlet / repeater last reported, and its signal."""

    entity_description: SpruceSensorDescription

    def __init__(self, entry, device: str, description: SpruceSensorDescription) -> None:
        super().__init__(entry, entry.runtime_data.history, f"{device}_{description.key}")
        self.entity_description = description
        self.device = device
        self._attr_device_info = outlet_device_info(self.rt, device)

    @property
    def native_value(self):
        return self.entity_description.value_fn(self.history.outlets.get(self.device, {}))
