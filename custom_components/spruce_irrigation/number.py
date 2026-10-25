"""Numbers: each zone's run time when its valve is opened from Home Assistant (kept in Home Assistant only); the
controller's Weather Watch thresholds (rain, freeze, wind — one each for every schedule and zone, as the Spruce
app says), its maximum manual run time and pump delay; and the flow meter's deviation limits."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
    RestoreNumber,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_DEFAULT_RUN_MINUTES, DEFAULT_RUN_MINUTES, FLOW_PORTS
from .entity import SpruceEntity, SpruceScheduleEntity, SpruceZoneEntity, connected_schedules


class SpruceStartOffset(SpruceScheduleEntity, NumberEntity):
    """Minutes before (−) or after (+) sunrise / sunset, for a connected schedule started relative to the sun."""

    _attr_translation_key = "schedule_start_offset"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:sun-clock"
    _attr_native_min_value, _attr_native_max_value, _attr_native_step = -180, 180, 5
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_mode = NumberMode.BOX

    def __init__(self, entry, schedule_id: int) -> None:
        super().__init__(entry, schedule_id, "start_offset")

    @property
    def native_value(self) -> float | None:
        return self.schedule.get("start_offset")

    async def async_set_value(self, value: float) -> None:
        await self.rt.async_set_schedule(self.schedule_id, {"start_time_rel": str(int(value))},
                                         local={"start_offset": float(int(value))})


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    rt = entry.runtime_data
    entities: list[NumberEntity] = [SpruceRunTime(entry, z) for z, zone in rt.history.data.zones.items() if zone.enabled]
    entities += [SpruceWeatherThreshold(entry, d) for d in WEATHER]
    entities += [SpruceControllerNumber(entry, d) for d in CONTROLLER]
    if rt.history.data.flow_meter:
        entities += [SpruceFlowLimit(entry, d) for d in FLOW_LIMITS]
    entities += [SpruceStartOffset(entry, sid) for sid in connected_schedules(entry)]
    async_add_entities(entities)


class SpruceRunTime(SpruceZoneEntity, RestoreNumber):
    _attr_translation_key = "run_time"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_native_min_value = 1
    _attr_native_max_value = 240
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:timer-outline"

    def __init__(self, entry, zone: int) -> None:
        super().__init__(entry, entry.runtime_data.history, zone, "run_time")
        self._attr_native_value = float(entry.options.get(CONF_DEFAULT_RUN_MINUTES, DEFAULT_RUN_MINUTES))

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_number_data()) and last.native_value:
            self._attr_native_value = last.native_value
        self.rt.run_minutes[self.zone] = self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.rt.run_minutes[self.zone] = value
        self.async_write_ha_state()


@dataclass(frozen=True, kw_only=True)
class WeatherDescription(NumberEntityDescription):
    quantity: str                      # units.py quantity
    setting: str                       # Spruce controller setting
    field: str                         # HistoryData field, in Spruce's unit
    spruce_range: tuple[float, float]  # min, max in Spruce's unit
    steps: dict[str, float]            # step per displayed unit
    as_text: Callable[[float], str]    # how Spruce stores the value


WEATHER = (
    WeatherDescription(key="rain_skip_threshold", translation_key="rain_skip_threshold", icon="mdi:weather-pouring",
                       device_class=NumberDeviceClass.PRECIPITATION, quantity="rain", setting="rain_delay_inches",
                       field="rain_skip_inches", spruce_range=(0.01, 3), steps={"mm": 0.5, "in": 0.01},
                       as_text=lambda v: f"{round(v, 3):g}"),
    WeatherDescription(key="freeze_temperature", translation_key="freeze_temperature", icon="mdi:snowflake-thermometer",
                       device_class=NumberDeviceClass.TEMPERATURE, quantity="temperature", setting="freeze_temperature",
                       field="freeze_skip_f", spruce_range=(25, 60), steps={"°C": 0.5, "°F": 1},
                       as_text=lambda v: str(int(round(v)))),
    WeatherDescription(key="wind_skip_threshold", translation_key="wind_skip_threshold", icon="mdi:weather-windy",
                       device_class=NumberDeviceClass.WIND_SPEED, quantity="wind", setting="wind_mph",
                       field="wind_skip_mph", spruce_range=(1, 50), steps={"km/h": 1, "mph": 1},
                       as_text=lambda v: str(int(round(v)))),
)


class SpruceWeatherThreshold(SpruceEntity, NumberEntity):
    """A Weather Watch threshold. Spruce has one of each for the whole controller; whether a schedule (or, in a
    connected schedule, each zone) skips for it is set by the skip switches."""

    entity_description: WeatherDescription
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(self, entry, description: WeatherDescription) -> None:
        super().__init__(entry, entry.runtime_data.history, description.key)
        self.entity_description = description
        units, q = self.rt.units, description.quantity
        self._attr_native_unit_of_measurement = units.unit[q]
        lo, hi = (units.from_spruce(q, v) for v in description.spruce_range)
        step = description.steps.get(units.unit[q], 1)
        low = round(lo / step) * step
        self._attr_native_min_value = step if lo > 0 >= low else low   # rain can't go to 0; freeze can be below 0 °C
        self._attr_native_max_value = round(hi / step) * step
        self._attr_native_step = step

    @property
    def native_value(self) -> float | None:
        d = self.entity_description
        value = self.rt.units.from_spruce(d.quantity, getattr(self.history, d.field))
        return None if value is None else round(value, 2)

    async def async_set_native_value(self, value: float) -> None:
        d = self.entity_description
        text = d.as_text(self.rt.units.to_spruce(d.quantity, value))
        await self.rt.async_save(d.key.replace("_", " "), self.rt.api.save_controller_settings(
            self.controller, {d.setting: text}), lambda data: setattr(data, d.field, float(text)))


@dataclass(frozen=True, kw_only=True)
class ControllerNumberDescription(NumberEntityDescription):
    setting: str
    field: str


CONTROLLER = (
    ControllerNumberDescription(key="manual_max_time", translation_key="manual_max_time", icon="mdi:timer-cog-outline",
                                entity_category=EntityCategory.CONFIG, native_unit_of_measurement=UnitOfTime.MINUTES,
                                native_min_value=1, native_max_value=240, native_step=1, mode=NumberMode.BOX,
                                setting="manual_max_time", field="manual_max_minutes"),
    ControllerNumberDescription(key="pump_delay", translation_key="pump_delay", icon="mdi:pump",
                                entity_category=EntityCategory.CONFIG, native_unit_of_measurement=UnitOfTime.SECONDS,
                                native_min_value=0, native_max_value=600, native_step=1, mode=NumberMode.BOX,
                                setting="pump_delay", field="pump_delay_seconds"),
)


class SpruceControllerNumber(SpruceEntity, NumberEntity):
    """A controller setting the controller itself uses: saved in Spruce, then the controller is told to fetch it."""

    entity_description: ControllerNumberDescription

    def __init__(self, entry, description: ControllerNumberDescription) -> None:
        super().__init__(entry, entry.runtime_data.history, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        value = getattr(self.history, self.entity_description.field)
        return 0 if value is None and self.entity_description.key == "pump_delay" else value

    async def async_set_native_value(self, value: float) -> None:
        d = self.entity_description
        await self.rt.async_save(d.key.replace("_", " "), self.rt.api.save_controller_settings(
            self.controller, {d.setting: str(int(value))}), lambda data: setattr(data, d.field, float(int(value))),
            sync=True)


@dataclass(frozen=True, kw_only=True)
class FlowLimitDescription(NumberEntityDescription):
    setting: str
    field: str


FLOW_LIMITS = (
    FlowLimitDescription(key="flow_limit_high", translation_key="flow_limit_high", icon="mdi:arrow-up-bold-box-outline",
                         entity_category=EntityCategory.CONFIG, native_unit_of_measurement=PERCENTAGE,
                         native_min_value=1, native_max_value=200, native_step=1, mode=NumberMode.BOX,
                         setting="flow_limit_high", field="limit_high"),
    FlowLimitDescription(key="flow_limit_low", translation_key="flow_limit_low", icon="mdi:arrow-down-bold-box-outline",
                         entity_category=EntityCategory.CONFIG, native_unit_of_measurement=PERCENTAGE,
                         native_min_value=-99, native_max_value=-1, native_step=1, mode=NumberMode.BOX,
                         setting="flow_limit_low", field="limit_low"),
)


class SpruceFlowLimit(SpruceEntity, NumberEntity):
    """How far a zone's flow may differ from its baseline before Spruce reports it (high: also stops the zone when
    'stop on high flow' is on). Saved on both flow-meter ports, as the app does."""

    entity_description: FlowLimitDescription

    def __init__(self, entry, description: FlowLimitDescription) -> None:
        super().__init__(entry, entry.runtime_data.history, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        return self.history.flow_meter.get(self.entity_description.field)

    async def async_set_native_value(self, value: float) -> None:
        d = self.entity_description
        await self.rt.async_save(d.key.replace("_", " "), self.rt.api.save_zone_settings(
            self.controller, {port: {d.setting: str(int(value))} for port in FLOW_PORTS}),
            lambda data: data.flow_meter.__setitem__(d.field, float(int(value))))
