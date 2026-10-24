"""Sensors: water and status for the controller and each zone, readings for each moisture sensor."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
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

from .const import SENSOR_STALE
from .entity import SpruceEntity, SpruceMoistureEntity, SpruceZoneEntity, planned_days

STATES = ["idle", "watering", "paused", "pump_delay", "cycle_soak", "hibernating", "offline"]
# Today's plan, most important first: a schedule watering beats one skipping
TODAY = ["watering", "hibernating", "rain", "rain_sensor", "freeze", "wind", "not_needed", "none"]
LPM = UnitOfVolumeFlowRate.LITERS_PER_MINUTE


@dataclass(frozen=True, kw_only=True)
class SpruceSensorDescription(SensorEntityDescription):
    value_fn: Callable[[Any], Any]
    attrs_fn: Callable[[Any], dict[str, Any] | None] | None = None
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
                            state_class=SensorStateClass.TOTAL_INCREASING, native_unit_of_measurement=UnitOfVolume.LITERS,
                            suggested_display_precision=0, value_fn=lambda rt: round(sum(rt.history.data.water_today.values()), 1)),
    SpruceSensorDescription(key="water_month", translation_key="water_month", device_class=SensorDeviceClass.WATER,
                            state_class=SensorStateClass.TOTAL, native_unit_of_measurement=UnitOfVolume.LITERS,
                            suggested_display_precision=0, value_fn=lambda rt: round(sum(rt.history.data.water_month.values()), 1)),
    SpruceSensorDescription(key="wifi_signal", translation_key="wifi_signal", device_class=SensorDeviceClass.SIGNAL_STRENGTH,
                            native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT, entity_category=EntityCategory.DIAGNOSTIC,
                            entity_registry_enabled_default=False, value_fn=lambda rt: rt.history.data.network.get("signal")),
    SpruceSensorDescription(key="last_seen", translation_key="last_seen", device_class=SensorDeviceClass.TIMESTAMP,
                            entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False, value_fn=lambda rt: rt.status.data.last_seen, status=True),
)

ZONE_SENSORS = (
    SpruceSensorDescription(key="water_today", translation_key="zone_water_today", device_class=SensorDeviceClass.WATER,
                            state_class=SensorStateClass.TOTAL_INCREASING, native_unit_of_measurement=UnitOfVolume.LITERS,
                            suggested_display_precision=0, value_fn=lambda a: round(a[0].history.data.water_today.get(a[1], 0), 1)),
    SpruceSensorDescription(key="last_flow", translation_key="zone_last_flow", device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
                            native_unit_of_measurement=LPM, suggested_display_precision=1,
                            value_fn=lambda a: _round(a[0].history.data.last_run.get(a[1], {}).get("lpm"))),
    SpruceSensorDescription(key="usual_flow", translation_key="zone_usual_flow", device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
                            native_unit_of_measurement=LPM, suggested_display_precision=1, entity_category=EntityCategory.DIAGNOSTIC,
                            value_fn=lambda a: _round(a[0].history.data.usual_lpm.get(a[1]))),
    SpruceSensorDescription(key="last_run", translation_key="zone_last_run", device_class=SensorDeviceClass.TIMESTAMP,
                            value_fn=lambda a: a[0].history.data.last_run.get(a[1], {}).get("at")),
)

MOISTURE_SENSORS = (
    SpruceSensorDescription(key="moisture", device_class=SensorDeviceClass.MOISTURE, native_unit_of_measurement=PERCENTAGE,
                            state_class=SensorStateClass.MEASUREMENT, value_fn=lambda r: r.get("moisture"), status=True),
    SpruceSensorDescription(key="temperature", device_class=SensorDeviceClass.TEMPERATURE,
                            native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT, state_class=SensorStateClass.MEASUREMENT,
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


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    rt = entry.runtime_data
    entities: list[SensorEntity] = [SpruceControllerSensor(entry, d) for d in CONTROLLER_SENSORS]
    for z, zone in rt.history.data.zones.items():
        if zone.enabled:
            entities += [SpruceZoneSensor(entry, z, d) for d in ZONE_SENSORS]
    for sid in rt.status.data.sensors:
        entities += [SpruceMoistureSensor(entry, sid, d) for d in MOISTURE_SENSORS]
    async_add_entities(entities)


class SpruceControllerSensor(SpruceEntity, SensorEntity):
    entity_description: SpruceSensorDescription

    def __init__(self, entry, description: SpruceSensorDescription) -> None:
        rt = entry.runtime_data
        super().__init__(entry, rt.status if description.status else rt.history, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        return self.entity_description.value_fn(self.rt)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.rt) if fn else None

    @property
    def last_reset(self):
        if self.entity_description.key == "water_month":
            return dt_util.start_of_local_day(dt_util.now().date().replace(day=1))
        return None


class SpruceZoneSensor(SpruceZoneEntity, SensorEntity):
    entity_description: SpruceSensorDescription

    def __init__(self, entry, zone: int, description: SpruceSensorDescription) -> None:
        super().__init__(entry, entry.runtime_data.history, zone, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        return self.entity_description.value_fn((self.rt, self.zone))


class SpruceMoistureSensor(SpruceMoistureEntity, SensorEntity):
    entity_description: SpruceSensorDescription

    def __init__(self, entry, sensor_id: str, description: SpruceSensorDescription) -> None:
        super().__init__(entry, entry.runtime_data.status, sensor_id, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """A sensor that hasn't reported for two days shows unavailable (its last reading is kept as a diagnostic)."""
        if self.entity_description.key == "last_reading":
            return super().available
        at = self.reading.get("latest_at")
        return super().available and at is not None and dt_util.utcnow() - at < SENSOR_STALE

    @property
    def native_value(self):
        return self.entity_description.value_fn(self.reading)
