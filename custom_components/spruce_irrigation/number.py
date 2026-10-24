"""Per-zone run time used when a zone's valve is opened from Home Assistant (kept in Home Assistant only)."""
from __future__ import annotations

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_DEFAULT_RUN_MINUTES, DEFAULT_RUN_MINUTES
from .entity import SpruceZoneEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    rt = entry.runtime_data
    async_add_entities(SpruceRunTime(entry, z) for z, zone in rt.history.data.zones.items() if zone.enabled)


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
