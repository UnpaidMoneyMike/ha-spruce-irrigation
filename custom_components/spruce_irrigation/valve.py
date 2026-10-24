"""A water valve per zone: open runs the zone for its run time, close stops it."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

import voluptuous as vol

from homeassistant.components.valve import ValveDeviceClass, ValveEntity, ValveEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import SpruceError
from .const import CONF_DEFAULT_RUN_MINUTES, DEFAULT_RUN_MINUTES, SERVICE_RUN_ZONE, SERVICE_STOP_ZONE
from .entity import SpruceZoneEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    rt = entry.runtime_data
    async_add_entities(SpruceValve(entry, z) for z, zone in rt.history.data.zones.items() if zone.enabled)
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_RUN_ZONE, {vol.Optional("minutes"): vol.All(vol.Coerce(float), vol.Range(min=0.5, max=240))}, "async_run")
    platform.async_register_entity_service(SERVICE_STOP_ZONE, {}, "async_close_valve")


class SpruceValve(SpruceZoneEntity, ValveEntity):
    _attr_device_class = ValveDeviceClass.WATER
    _attr_supported_features = ValveEntityFeature.OPEN | ValveEntityFeature.CLOSE
    _attr_reports_position = False
    _attr_translation_key = "zone"

    def __init__(self, entry, zone: int) -> None:
        super().__init__(entry, entry.runtime_data.status, zone, "valve")
        self._default = entry.options.get(CONF_DEFAULT_RUN_MINUTES, DEFAULT_RUN_MINUTES)

    @property
    def _state(self) -> dict:
        return self.status.zones.get(self.zone, {})

    @property
    def is_closed(self) -> bool | None:
        return not self._state.get("on") if self._state else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s, z = self._state, self.history.zones.get(self.zone)
        attrs: dict[str, Any] = {"zone": self.zone}
        if s.get("on") and s.get("started"):
            attrs["started"] = s["started"].isoformat()
            attrs["ends"] = (s["started"] + timedelta(seconds=s.get("duration") or 0)).isoformat()
            attrs["schedule"] = self.history.schedules.get(s.get("schedule_id"), {}).get("name", "manual")
        if z:
            attrs.update(nozzle=z.nozzle, landscape=z.landscape, soil=z.soil)
        return attrs

    async def async_open_valve(self) -> None:
        await self.async_run()

    async def async_run(self, minutes: float | None = None) -> None:
        minutes = minutes or self.rt.run_minutes.get(self.zone) or self._default
        try:
            await self.rt.api.zone_on(self.controller, self.zone, int(minutes * 60))
        except SpruceError as err:
            raise HomeAssistantError(f"Couldn't start {self.zone_name}: {err}") from err
        self.rt.optimistic(self.zone, {"on": True, "started": dt_util.utcnow(), "duration": int(minutes * 60), "schedule_id": 0})

    async def async_close_valve(self) -> None:
        try:
            await self.rt.api.zone_off(self.controller, self.zone)
        except SpruceError as err:
            raise HomeAssistantError(f"Couldn't stop {self.zone_name}: {err}") from err
        self.rt.optimistic(self.zone, {"on": False})
