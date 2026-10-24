"""Calendar: Spruce's watering plan. A watering is an event from its start to its finish; a day a schedule skips
for the weather (or hibernation) is an all-day note. Days it skips because the soil has enough water are left out."""
from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .entity import SpruceEntity, planned_days

SKIPS = {"rain": "predicted rain", "rain_sensor": "rain sensor", "freeze": "freeze", "wind": "wind",
         "hibernating": "hibernating"}


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([SpruceCalendar(entry)])


class SpruceCalendar(SpruceEntity, CalendarEntity):
    _attr_translation_key = "plan"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, entry) -> None:
        super().__init__(entry, entry.runtime_data.history, "plan")

    def _events(self) -> list[CalendarEvent]:
        zones = self.history.zones
        events = []
        for schedule, day in planned_days(self.rt):
            uid = f"{self.controller}_{schedule['name']}_{day['day'].isoformat()}"
            if day["seconds"]:
                lines = [day["message"] or ""] + [
                    f"{zones[z].name if z in zones else f'Zone {z}'}: {m:g} min" for z, m in sorted(day["zones"].items())]
                events.append(CalendarEvent(
                    start=day["start"], end=day["start"] + timedelta(seconds=day["seconds"]),
                    summary=f"{schedule['name']} · {round(day['seconds'] / 60)} min",
                    description="\n".join(line for line in lines if line), uid=uid))
            elif day["reason"] in SKIPS:
                events.append(CalendarEvent(
                    start=day["day"], end=day["day"] + timedelta(days=1),
                    summary=f"{schedule['name']}: skipped ({SKIPS[day['reason']]})",
                    description=day["message"], uid=uid))
        return events

    @property
    def event(self) -> CalendarEvent | None:
        """The watering under way, or the next one."""
        now = dt_util.utcnow()
        return next((e for e in self._events() if isinstance(e.start, datetime) and e.end > now), None)

    async def async_get_events(self, hass: HomeAssistant, start_date: datetime, end_date: datetime) -> list[CalendarEvent]:
        return [e for e in self._events() if e.start_datetime_local < end_date and e.end_datetime_local > start_date]
