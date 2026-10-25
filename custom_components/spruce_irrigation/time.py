"""Each schedule's start time."""
from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from homeassistant.const import EntityCategory

from .entity import SpruceScheduleEntity, connected_schedules, watering_schedules


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    entities: list[TimeEntity] = [SpruceStartTime(entry, sid) for sid in watering_schedules(entry)]
    entities += [SpruceWindowTime(entry, sid, end) for sid in connected_schedules(entry) for end in (False, True)]
    async_add_entities(entities)


def _time(text) -> time | None:
    try:
        hours, minutes = str(text or "").split(":")[:2]
        return time(int(hours), int(minutes))
    except ValueError:
        return None


class SpruceWindowTime(SpruceScheduleEntity, TimeEntity):
    """Start or end of a connected schedule's watering window (used when the window switch is on)."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry, schedule_id: int, end: bool) -> None:
        super().__init__(entry, schedule_id, "window_end" if end else "window_start")
        self._field = "window_end" if end else "window_start"
        self._setting = "time_restr1_end" if end else "time_restr1_start"
        self._attr_translation_key = f"schedule_{self._field}"
        self._attr_icon = "mdi:clock-end" if end else "mdi:clock-start"

    @property
    def native_value(self) -> time | None:
        return _time(self.schedule.get(self._field))

    async def async_set_value(self, value: time) -> None:
        text = f"{value.hour:02d}:{value.minute:02d}"
        await self.rt.async_set_schedule(self.schedule_id, {self._setting: text}, local={self._field: text})


class SpruceStartTime(SpruceScheduleEntity, TimeEntity):
    _attr_translation_key = "schedule_start"
    _attr_icon = "mdi:clock-start"

    def __init__(self, entry, schedule_id: int) -> None:
        super().__init__(entry, schedule_id, "start_time")

    @property
    def native_value(self) -> time | None:
        return _time(self.schedule.get("start_time"))

    async def async_set_value(self, value: time) -> None:
        text = f"{value.hour:02d}:{value.minute:02d}"
        await self.rt.async_set_schedule(self.schedule_id, {"start_time": text, "start_time_mode": "set"},
                                         local={"start_time": text})
