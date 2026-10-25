"""Diagnostics download, with the API key, location and network details removed."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_API_KEY
from .heads import design, zone_heads

REDACT = {CONF_API_KEY, "serial", "ip", "latitude", "longitude", "controller_id"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry) -> dict[str, Any]:
    rt = entry.runtime_data
    history = asdict(rt.history.data)
    history.pop("daily", None)
    history["forecast"] = {part: len(items) for part, items in history.get("forecast", {}).items()}
    return async_redact_data({
        "entry": {"data": dict(entry.data), "options": dict(entry.options)},
        "status": asdict(rt.status.data) if rt.status.data else None,
        "history": history,
        "baselines": {"snapshots": rt.baselines.snapshots, "accepted": rt.baselines.accepted} if rt.baselines else None,
        "zone_heads": {z: rows for z, (_, rows) in zone_heads(entry).items()},
        "design_flow": {z: asdict(d) for z, d in design(entry).items()},
    }, REDACT)
