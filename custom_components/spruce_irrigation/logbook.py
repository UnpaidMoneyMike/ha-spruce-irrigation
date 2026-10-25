"""Logbook lines for Spruce's activity log entries."""
from __future__ import annotations

from homeassistant.components.logbook import LOGBOOK_ENTRY_MESSAGE, LOGBOOK_ENTRY_NAME
from homeassistant.core import Event, HomeAssistant, callback

from .const import DOMAIN, EVENT_SPRUCE


@callback
def async_describe_events(hass: HomeAssistant, async_describe_event) -> None:
    @callback
    def describe(event: Event) -> dict[str, str]:
        return {LOGBOOK_ENTRY_NAME: "Spruce", LOGBOOK_ENTRY_MESSAGE: event.data.get("message") or event.data.get("type") or ""}

    async_describe_event(DOMAIN, EVENT_SPRUCE, describe)
