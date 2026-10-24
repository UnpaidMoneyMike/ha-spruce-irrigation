"""Hibernate (winter mode): while on, the controller runs no schedules."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import SpruceError
from .entity import SpruceEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([SpruceHibernate(entry)])


class SpruceHibernate(SpruceEntity, SwitchEntity):
    _attr_translation_key = "hibernate"
    _attr_icon = "mdi:snowflake"

    def __init__(self, entry) -> None:
        super().__init__(entry, entry.runtime_data.history, "hibernate")

    @property
    def is_on(self) -> bool:
        return self.history.hibernating

    async def _set(self, on: bool) -> None:
        try:
            await self.rt.api.hibernate(self.controller, on)
        except SpruceError as err:
            raise HomeAssistantError(f"Couldn't change hibernate: {err}") from err
        self.history.hibernating = on   # Spruce's settings catch up on the next refresh
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)
