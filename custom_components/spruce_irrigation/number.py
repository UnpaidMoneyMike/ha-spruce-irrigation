"""Numbers: each zone's run time when its valve is opened from Home Assistant (kept in Home Assistant only), each
schedule's rain-skip threshold, and the controller's freeze-skip temperature (both saved in Spruce)."""
from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode, RestoreNumber
from homeassistant.const import EntityCategory, UnitOfLength, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import SpruceError
from .const import CONF_DEFAULT_RUN_MINUTES, DEFAULT_RUN_MINUTES
from .entity import SpruceEntity, SpruceScheduleEntity, SpruceZoneEntity, watering_schedules


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    rt = entry.runtime_data
    entities: list[NumberEntity] = [SpruceRunTime(entry, z) for z, zone in rt.history.data.zones.items() if zone.enabled]
    entities += [SpruceRainThreshold(entry, sid) for sid in watering_schedules(entry)]
    entities.append(SpruceFreezeTemperature(entry))
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


class SpruceRainThreshold(SpruceScheduleEntity, NumberEntity):
    """Forecast rain (inches) that makes the schedule skip watering, when its rain skip is on."""

    _attr_translation_key = "schedule_rain_threshold"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = NumberDeviceClass.PRECIPITATION
    _attr_native_unit_of_measurement = UnitOfLength.INCHES
    _attr_native_min_value = 0
    _attr_native_max_value = 1
    _attr_native_step = 0.05
    _attr_mode = NumberMode.BOX

    def __init__(self, entry, schedule_id: int) -> None:
        super().__init__(entry, schedule_id, "rain_threshold")

    @property
    def native_value(self) -> float | None:
        return self.schedule.get("rain_inches")

    async def async_set_native_value(self, value: float) -> None:
        await self.rt.async_set_schedule(self.schedule_id, {"rain_delay_inches": f"{value:.2f}"},
                                         local={"rain_inches": round(value, 2)})


class SpruceFreezeTemperature(SpruceEntity, NumberEntity):
    """Forecast temperature below which schedules with freeze skip on don't water."""

    _attr_translation_key = "freeze_temperature"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_native_min_value = 25
    _attr_native_max_value = 60
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, entry) -> None:
        super().__init__(entry, entry.runtime_data.history, "freeze_temperature")

    @property
    def native_value(self) -> float | None:
        return self.history.freeze_skip_f

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self.rt.api.save_controller_settings(self.controller, {"freeze_temperature": str(int(round(value)))})
        except SpruceError as err:
            raise HomeAssistantError(f"Couldn't change the freeze temperature: {err}") from err
        self.history.freeze_skip_f = float(round(value))
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
