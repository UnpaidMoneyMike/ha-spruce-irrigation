"""Each schedule's start time."""
from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import SpruceScheduleEntity, watering_schedules


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities(SpruceStartTime(entry, sid) for sid in watering_schedules(entry))


class SpruceStartTime(SpruceScheduleEntity, TimeEntity):
    _attr_translation_key = "schedule_start"
    _attr_icon = "mdi:clock-start"

    def __init__(self, entry, schedule_id: int) -> None:
        super().__init__(entry, schedule_id, "start_time")

    @property
    def native_value(self) -> time | None:
        try:
            hours, minutes = str(self.schedule.get("start_time") or "").split(":")[:2]
            return time(int(hours), int(minutes))
        except ValueError:
            return None

    async def async_set_value(self, value: time) -> None:
        text = f"{value.hour:02d}:{value.minute:02d}"
        await self.rt.async_set_schedule(self.schedule_id, {"start_time": text, "start_time_mode": "set"},
                                         local={"start_time": text})
