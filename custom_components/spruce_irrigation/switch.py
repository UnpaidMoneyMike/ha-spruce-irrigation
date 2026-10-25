"""Switches: hibernate (winter mode); per schedule, enabled and its weather skips (per zone in connected schedules);
concurrent manual zones; each zone enabled; the flow meter's protections; and the Zigbee outlets."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import FLOW_PORTS
from .entity import SpruceEntity, SpruceScheduleEntity, connected_schedules, outlet_device_info, watering_schedules

# key -> (Spruce setting, field in the schedule data, icon)
SCHEDULE_SWITCHES = {
    "enabled": ("sched_enabled", "enabled", "mdi:calendar-check"),
    "rain_skip": ("rain_delay_enabled", "rain_skip", "mdi:weather-rainy"),
    "freeze_skip": ("freeze_delay_enabled", "freeze_skip", "mdi:snowflake-thermometer"),
    "wind_skip": ("wind_delay_enabled", "wind_skip", "mdi:weather-windy"),
}


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    rt = entry.runtime_data
    data = rt.history.data
    entities: list[SwitchEntity] = [SpruceHibernate(entry), SpruceConcurrentZones(entry)]
    entities += [SpruceScheduleSwitch(entry, sid, key) for sid in watering_schedules(entry) for key in SCHEDULE_SWITCHES]
    entities += [SpruceWindow(entry, sid) for sid in connected_schedules(entry)]
    entities += [SpruceZoneEnabled(entry, z) for z in range(1, data.num_zones + 1)]
    if data.flow_meter:
        entities += [SpruceFlowSwitch(entry, d) for d in FLOW_SWITCHES]
    entities += [SpruceOutlet(entry, did) for did in data.outlets]
    async_add_entities(entities)


class SpruceHibernate(SpruceEntity, SwitchEntity):
    """While on, the controller runs no schedules (winter mode), until switched off or until its end date."""

    _attr_translation_key = "hibernate"
    _attr_icon = "mdi:snowflake"

    def __init__(self, entry) -> None:
        super().__init__(entry, entry.runtime_data.history, "hibernate")

    @property
    def is_on(self) -> bool:
        return self.history.hibernating

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        until = self.history.hibernate_until
        return {"until": until.isoformat() if until else None}

    async def _set(self, on: bool) -> None:
        def local(data) -> None:
            data.hibernating, data.hibernate_until = on, None
        await self.rt.async_save("hibernate", self.rt.api.hibernate(self.controller, on), local)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)


class SpruceConcurrentZones(SpruceEntity, SwitchEntity):
    """Allow up to 3 zones to be turned on by hand at the same time."""

    _attr_translation_key = "concurrent_zones"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:format-list-group"

    def __init__(self, entry) -> None:
        super().__init__(entry, entry.runtime_data.history, "concurrent_zones")

    @property
    def is_on(self) -> bool:
        return self.history.concurrent_zones

    async def _set(self, on: bool) -> None:
        await self.rt.async_save("concurrent zones", self.rt.api.save_controller_settings(
            self.controller, {"concurrent_zones_enabled": "1" if on else "0"}),
            lambda data: setattr(data, "concurrent_zones", on), sync=True)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)


class SpruceScheduleSwitch(SpruceScheduleEntity, SwitchEntity):
    """A schedule's enabled flag, or one of its weather skips. In a connected schedule Spruce keeps the skips per
    zone (its Weather Watch page has a tick per zone): the switch is on when every zone has it, and sets every zone;
    `set_schedule_zone` sets single zones."""

    def __init__(self, entry, schedule_id: int, key: str) -> None:
        super().__init__(entry, schedule_id, key)
        self._key = key
        self._setting, self._field, self._attr_icon = SCHEDULE_SWITCHES[key]
        self._attr_translation_key = f"schedule_{key}"

    @property
    def _per_zone(self) -> bool:
        return self._key != "enabled" and self.schedule.get("type") == "connected" and bool(self.schedule.get("zones"))

    @property
    def is_on(self) -> bool:
        if self._per_zone:
            return all(z.get(self._field, True) for z in self.schedule["zones"].values())
        return bool(self.schedule.get(self._field))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self._key == "enabled":
            return {"start_time": self.schedule.get("start_time"), "next_date": self.schedule.get("next_date"),
                    "next_minutes": self.schedule.get("next_minutes"), "type": self.schedule.get("type"),
                    "watering_days": self.schedule.get("days")}
        if self._per_zone:
            zones = self.history.zones
            names = lambda on: [zones[z].name if z in zones else f"Zone {z}"  # noqa: E731
                                for z, v in sorted(self.schedule["zones"].items()) if v.get(self._field, True) == on]
            return {"zones_on": names(True), "zones_off": names(False)}
        return None

    async def _set(self, on: bool) -> None:
        if self._per_zone:
            await self.rt.async_set_zone_skips(self.schedule_id, self._key, on)
        else:
            await self.rt.async_set_schedule(self.schedule_id, {self._setting: "1" if on else "0"}, local={self._field: on})

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)


class SpruceWindow(SpruceScheduleEntity, SwitchEntity):
    """A connected schedule's watering window: when on, watering happens only between its start and end times."""

    _attr_translation_key = "schedule_window"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:clock-time-four-outline"

    def __init__(self, entry, schedule_id: int) -> None:
        super().__init__(entry, schedule_id, "window")

    @property
    def is_on(self) -> bool:
        return bool(self.schedule.get("window"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"start": self.schedule.get("window_start"), "end": self.schedule.get("window_end")}

    async def _set(self, on: bool) -> None:
        values = {"time_restr1_enabled": "1" if on else "0"}
        if on:   # the app needs both times; keep the stored ones or use a night window
            values["time_restr1_start"] = self.schedule.get("window_start") or "22:00"
            values["time_restr1_end"] = self.schedule.get("window_end") or "06:00"
        await self.rt.async_set_schedule(self.schedule_id, values, local={
            "window": on, "window_start": values.get("time_restr1_start", self.schedule.get("window_start")),
            "window_end": values.get("time_restr1_end", self.schedule.get("window_end"))})

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)


class SpruceZoneEnabled(SpruceEntity, SwitchEntity):
    """Whether a zone is in use. Off hides it in Spruce (and its valve and sensors here, after the reload that
    follows)."""

    _attr_translation_key = "zone_enabled"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:sprinkler-variant"

    def __init__(self, entry, zone: int) -> None:
        super().__init__(entry, entry.runtime_data.history, f"zone_{zone}_enabled")
        self.zone = zone
        z = self.history.zones.get(zone)
        self._attr_translation_placeholders = {"zone": z.name if z and z.name else f"Zone {zone}"}

    @property
    def is_on(self) -> bool:
        z = self.history.zones.get(self.zone)
        return bool(z and z.enabled)

    async def _set(self, on: bool) -> None:
        def local(data) -> None:
            if self.zone in data.zones:
                data.zones[self.zone].enabled = on
        await self.rt.async_save(f"zone {self.zone}", self.rt.api.save_zone_settings(
            self.controller, {self.zone: {"zenabled": "1" if on else "0"}}), local, sync=True)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)


@dataclass(frozen=True, kw_only=True)
class FlowSwitchDescription(SwitchEntityDescription):
    setting: str
    field: str


FLOW_SWITCHES = (
    FlowSwitchDescription(key="flow_stop_on_high", translation_key="flow_stop_on_high", icon="mdi:water-off",
                          entity_category=EntityCategory.CONFIG, setting="flow_stop_enabled", field="stop_on_high"),
    FlowSwitchDescription(key="flow_pump_protect", translation_key="flow_pump_protect", icon="mdi:pump-off",
                          entity_category=EntityCategory.CONFIG, setting="flow_pump_protect_enabled", field="pump_protect"),
    FlowSwitchDescription(key="flow_leak_sustained", translation_key="flow_leak_sustained", icon="mdi:pipe-leak",
                          entity_category=EntityCategory.CONFIG, setting="flow_idle_leak_small_enabled", field="leak_small"),
    FlowSwitchDescription(key="flow_leak_burst", translation_key="flow_leak_burst", icon="mdi:water-alert",
                          entity_category=EntityCategory.CONFIG, setting="flow_idle_leak_big_enabled", field="leak_big"),
)


class SpruceFlowSwitch(SpruceEntity, SwitchEntity):
    """One of the flow meter's protections (saved on both flow-meter ports, as the app does)."""

    entity_description: FlowSwitchDescription

    def __init__(self, entry, description: FlowSwitchDescription) -> None:
        super().__init__(entry, entry.runtime_data.history, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        return bool(self.history.flow_meter.get(self.entity_description.field))

    async def _set(self, on: bool) -> None:
        d = self.entity_description
        await self.rt.async_save(d.key.replace("_", " "), self.rt.api.save_zone_settings(
            self.controller, {port: {d.setting: "1" if on else "0"} for port in FLOW_PORTS}),
            lambda data: data.flow_meter.__setitem__(d.field, on))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)


class SpruceOutlet(SpruceEntity, SwitchEntity):
    """A Zigbee smart outlet joined to the controller (often also a repeater for the moisture sensors): its own
    device, linked to the controller."""

    _attr_device_class = SwitchDeviceClass.OUTLET
    _attr_translation_key = "outlet"

    def __init__(self, entry, device: str) -> None:
        super().__init__(entry, entry.runtime_data.history, f"{device}_outlet")
        self.device = device
        self._attr_device_info = outlet_device_info(self.rt, device)

    @property
    def is_on(self) -> bool | None:
        o = self.history.outlets.get(self.device)
        return o.get("on") if o else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.rt.async_save("the outlet", self.rt.api.outlet_on(self.controller, self.device),
                                 lambda data: data.outlets[self.device].__setitem__("on", True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.rt.async_save("the outlet", self.rt.api.outlet_off(self.controller, self.device),
                                 lambda data: data.outlets[self.device].__setitem__("on", False))
