"""Switches: hibernate (winter mode), and for each schedule whether it is enabled and which weather skips it uses."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import SpruceError
from .entity import SpruceEntity, SpruceScheduleEntity, watering_schedules

# key -> (Spruce setting, field in the schedule data, icon)
SCHEDULE_SWITCHES = {
    "enabled": ("sched_enabled", "enabled", "mdi:calendar-check"),
    "rain_skip": ("rain_delay_enabled", "rain_skip", "mdi:weather-rainy"),
    "freeze_skip": ("freeze_delay_enabled", "freeze_skip", "mdi:snowflake-thermometer"),
    "wind_skip": ("wind_delay_enabled", "wind_skip", "mdi:weather-windy"),
}


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    entities: list[SwitchEntity] = [SpruceHibernate(entry)]
    entities += [SpruceScheduleSwitch(entry, sid, key) for sid in watering_schedules(entry) for key in SCHEDULE_SWITCHES]
    async_add_entities(entities)


class SpruceHibernate(SpruceEntity, SwitchEntity):
    """While on, the controller runs no schedules (winter mode)."""

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


class SpruceScheduleSwitch(SpruceScheduleEntity, SwitchEntity):
    def __init__(self, entry, schedule_id: int, key: str) -> None:
        super().__init__(entry, schedule_id, key)
        self._setting, self._field, self._attr_icon = SCHEDULE_SWITCHES[key]
        self._attr_translation_key = f"schedule_{key}"

    @property
    def is_on(self) -> bool:
        return bool(self.schedule.get(self._field))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self._field != "enabled":
            return None
        return {"start_time": self.schedule.get("start_time"), "next_date": self.schedule.get("next_date"),
                "next_minutes": self.schedule.get("next_minutes"), "type": self.schedule.get("type")}

    async def _set(self, on: bool) -> None:
        await self.rt.async_set_schedule(self.schedule_id, {self._setting: "1" if on else "0"}, local={self._field: on})

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)
