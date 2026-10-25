"""Spruce's activity log as an event entity: zone finished, flow alerts, schedule summaries… for automations and the
history of the dashboard. Each new log entry (also fired as spruce_irrigation_event) triggers it."""
from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import EVENT_SPRUCE
from .entity import SpruceEntity

# Spruce log types -> event types
TYPES = {"zone_scheduled": "zone_finished", "zone_manual": "zone_finished", "flow": "flow_alert",
         "schedule": "schedule_summary", "hibernate": "hibernate", "network": "network", "reset": "reset"}
EVENT_TYPES = sorted(set(TYPES.values())) + ["other"]


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([SpruceActivity(entry)])


class SpruceActivity(SpruceEntity, EventEntity):
    _attr_translation_key = "activity"
    _attr_icon = "mdi:text-box-outline"
    _attr_event_types = EVENT_TYPES

    def __init__(self, entry) -> None:
        super().__init__(entry, entry.runtime_data.history, "activity")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.hass.bus.async_listen(EVENT_SPRUCE, self._log_entry))

    @callback
    def _log_entry(self, event: Event) -> None:
        if event.data.get("controller_id") != self.controller:
            return
        zones = self.history.zones
        zone = event.data.get("zone")
        self._trigger_event(TYPES.get(event.data.get("type"), "other"), {
            "message": event.data.get("message"), "zone": zones[zone].name if zone in zones else zone,
            "spruce_type": event.data.get("type"), "time": event.data.get("time")})
        self.async_write_ha_state()
