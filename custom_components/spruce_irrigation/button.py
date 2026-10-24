"""Buttons: stop everything, run each schedule, and pair a moisture sensor."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import SpruceError
from .entity import SpruceEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    rt = entry.runtime_data
    entities: list[ButtonEntity] = [SpruceStop(entry), SprucePair(entry)]
    entities += [SpruceRunSchedule(entry, sid) for sid in rt.history.data.schedules]
    async_add_entities(entities)


class _Button(SpruceEntity, ButtonEntity):
    async def _do(self, action) -> None:
        try:
            await action
        except SpruceError as err:
            raise HomeAssistantError(str(err)) from err
        await self.rt.status.async_request_refresh()


class SpruceStop(_Button):
    _attr_translation_key = "stop"
    _attr_icon = "mdi:stop-circle-outline"

    def __init__(self, entry) -> None:
        super().__init__(entry, entry.runtime_data.status, "stop")

    async def async_press(self) -> None:
        await self._do(self.rt.api.stop(self.controller))


class SprucePair(_Button):
    """Opens the controller's Zigbee network so a reset moisture sensor can join (as 'Add device' in the app)."""

    _attr_translation_key = "pair_sensor"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:access-point-plus"

    def __init__(self, entry) -> None:
        super().__init__(entry, entry.runtime_data.status, "pair_sensor")

    async def async_press(self) -> None:
        await self._do(self.rt.api.pair_sensor(self.controller))


class SpruceRunSchedule(_Button):
    _attr_translation_key = "run_schedule"
    _attr_icon = "mdi:calendar-start"

    def __init__(self, entry, schedule_id: int) -> None:
        super().__init__(entry, entry.runtime_data.history, f"schedule_{schedule_id}_run")
        self.schedule_id = schedule_id
        self._attr_translation_placeholders = {"schedule": self.history.schedules[schedule_id]["name"]}

    @property
    def available(self) -> bool:
        return super().available and self.schedule_id in self.history.schedules

    async def async_press(self) -> None:
        await self._do(self.rt.api.schedule_on(self.controller, self.schedule_id))
