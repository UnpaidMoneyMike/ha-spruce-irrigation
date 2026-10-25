"""Per connected schedule: how its start time is set (a set time, or relative to sunrise / sunset) and which days
it may water."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import SpruceScheduleEntity, connected_schedules

START_MODES = {"set": "set", "sunrise": "rel-sunrise", "sunset": "rel-sunset"}   # option -> Spruce's value
# Watering days presets -> (days_select, days_var); "weekdays" = chosen days of the week (set_schedule_days)
DAYS = {"every_day": ("everyx", "1"), "every_2_days": ("everyx", "2"), "every_3_days": ("everyx", "3"),
        "every_4_days": ("everyx", "4"), "every_5_days": ("everyx", "5"), "every_7_days": ("everyx", "7"),
        "even_days": ("even_odd", "even"), "odd_days": ("even_odd", "odd")}


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    sids = connected_schedules(entry)
    async_add_entities([SpruceStartMode(entry, sid) for sid in sids] + [SpruceDays(entry, sid) for sid in sids])


class SpruceStartMode(SpruceScheduleEntity, SelectEntity):
    _attr_translation_key = "schedule_start_mode"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:weather-sunset-up"
    _attr_options = list(START_MODES)

    def __init__(self, entry, schedule_id: int) -> None:
        super().__init__(entry, schedule_id, "start_mode")

    @property
    def current_option(self) -> str | None:
        mode = self.schedule.get("start_mode")
        return next((k for k, v in START_MODES.items() if v == mode), "set")

    async def async_select_option(self, option: str) -> None:
        value = START_MODES[option]
        await self.rt.async_set_schedule(self.schedule_id, {"start_time_mode": value}, local={"start_mode": value})


class SpruceDays(SpruceScheduleEntity, SelectEntity):
    """Common watering-day patterns; days of the week are set with the set_schedule_days service."""

    _attr_translation_key = "schedule_days"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:calendar-week"
    _attr_options = [*DAYS, "weekdays", "other"]

    def __init__(self, entry, schedule_id: int) -> None:
        super().__init__(entry, schedule_id, "days")

    @property
    def current_option(self) -> str | None:
        select, var = self.schedule.get("days_select"), self.schedule.get("days_var")
        if select == "days":
            return "weekdays"
        return next((k for k, v in DAYS.items() if v == (select, var)), "other")

    @property
    def extra_state_attributes(self) -> dict:
        return {"watering_days": self.schedule.get("days")}

    async def async_select_option(self, option: str) -> None:
        if option not in DAYS:
            raise ServiceValidationError("Choose the days of the week with the spruce_irrigation.set_schedule_days action")
        select, var = DAYS[option]
        await self.rt.async_set_schedule(self.schedule_id, {"days_select": select, "days_var": var},
                                         local={"days_select": select, "days_var": var})
