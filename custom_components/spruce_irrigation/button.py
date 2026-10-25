"""Buttons: stop everything, pause schedules until tomorrow, run each schedule, and pair (or stop pairing) a
moisture sensor."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from datetime import timedelta

from homeassistant.util import dt as dt_util

from .api import SpruceError
from .entity import SpruceEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    rt = entry.runtime_data
    entities: list[ButtonEntity] = [SpruceStop(entry), SprucePair(entry), SpruceCancelPair(entry), SprucePause(entry),
                                    SpruceRunBaseline(entry), SpruceReviewBaseline(entry, True),
                                    SpruceReviewBaseline(entry, False)]
    entities += [SpruceRunSchedule(entry, sid) for sid in rt.history.data.schedules]
    async_add_entities(entities)


class _Button(SpruceEntity, ButtonEntity):
    async def _do(self, action, all_off: bool = False) -> None:
        try:
            await action
        except SpruceError as err:
            raise HomeAssistantError(str(err)) from err
        self.rt.optimistic(None, {"on": False} if all_off else None)


class SpruceStop(_Button):
    _attr_translation_key = "stop"
    _attr_icon = "mdi:stop-circle-outline"

    def __init__(self, entry) -> None:
        super().__init__(entry, entry.runtime_data.status, "stop")

    async def async_press(self) -> None:
        await self._do(self.rt.api.stop(self.controller), all_off=True)


class SprucePair(_Button):
    """Opens the controller's Zigbee network so a reset moisture sensor can join (as 'Add device' in the app)."""

    _attr_translation_key = "pair_sensor"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:access-point-plus"

    def __init__(self, entry) -> None:
        super().__init__(entry, entry.runtime_data.status, "pair_sensor")

    async def async_press(self) -> None:
        await self._do(self.rt.api.pair_sensor(self.controller))


class SpruceCancelPair(_Button):
    """Stops the controller listening for a new Zigbee device."""

    _attr_translation_key = "cancel_pairing"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:access-point-remove"

    def __init__(self, entry) -> None:
        super().__init__(entry, entry.runtime_data.status, "cancel_pairing")

    async def async_press(self) -> None:
        await self._do(self.rt.api.cancel_pairing(self.controller))


class SprucePause(_Button):
    """Skips scheduled watering until tomorrow: hibernate with an end date, as the app's Hibernate page does
    (Spruce resumes the schedules on that day). Manual watering still works."""

    _attr_translation_key = "pause_until_tomorrow"
    _attr_icon = "mdi:calendar-remove"

    def __init__(self, entry) -> None:
        super().__init__(entry, entry.runtime_data.history, "pause_until_tomorrow")

    async def async_press(self) -> None:
        until = dt_util.now().date() + timedelta(days=1)

        def local(data) -> None:
            data.hibernating, data.hibernate_until = True, until
        await self.rt.async_save("the schedules", self.rt.api.hibernate(self.controller, True, until), local)


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


class SpruceRunBaseline(_Button):
    """Runs a flow baseline: every enabled zone waters for 60 s (plus a 10 s pause) while its flow is measured;
    Spruce applies the new values when it finishes, and the integration reviews them."""

    _attr_translation_key = "run_baseline"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:chart-bell-curve-cumulative"

    def __init__(self, entry) -> None:
        super().__init__(entry, entry.runtime_data.history, "run_baseline")

    async def async_press(self) -> None:
        await self._do(self.rt.api.start_baseline(self.controller))


class SpruceReviewBaseline(_Button):
    """Accept the latest baseline for every zone waiting for review, or keep their previous values."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry, accept: bool) -> None:
        key = "accept_baseline" if accept else "keep_previous_baseline"
        super().__init__(entry, entry.runtime_data.history, key)
        self._accept = accept
        self._attr_translation_key = key
        self._attr_icon = "mdi:check-all" if accept else "mdi:undo-variant"

    @property
    def available(self) -> bool:
        return super().available and bool(self.rt.baselines and self.rt.baselines.pending())

    async def async_press(self) -> None:
        if not self.rt.baselines.decide(None, self._accept):
            raise HomeAssistantError("No zones are waiting for a baseline review")
        self.rt.baselines_changed()
