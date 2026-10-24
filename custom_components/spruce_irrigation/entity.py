"""Base entities: the controller device and one device per moisture sensor."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .const import DOMAIN, MANUFACTURER

# Home Assistant 2026.8+ links a device to its parent by device id (via_device, by identifier, ends in 2027.8)
VIA_DEVICE_ID = "via_device_id" in DeviceInfo.__annotations__


class SpruceEntity(CoordinatorEntity[DataUpdateCoordinator]):
    """An entity of the Spruce controller."""

    _attr_has_entity_name = True

    def __init__(self, entry, coordinator: DataUpdateCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self.rt = entry.runtime_data
        self.controller: str = coordinator.controller
        self._attr_unique_id = f"{self.controller}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, self.controller)})

    @property
    def history(self):
        return self.rt.history.data

    @property
    def status(self):
        return self.rt.status.data


class SpruceZoneEntity(SpruceEntity):
    """An entity for one zone, named after it."""

    def __init__(self, entry, coordinator, zone: int, key: str) -> None:
        super().__init__(entry, coordinator, f"zone_{zone}_{key}")
        self.zone = zone
        self._attr_translation_placeholders = {"zone": self.zone_name}

    @property
    def zone_name(self) -> str:
        z = self.rt.history.data.zones.get(self.zone)
        return z.name if z else f"Zone {self.zone}"


class SpruceMoistureEntity(SpruceEntity):
    """An entity of one wireless moisture sensor (its own device, linked to the controller)."""

    def __init__(self, entry, coordinator, sensor_id: str, key: str) -> None:
        super().__init__(entry, coordinator, f"{sensor_id}_{key}")
        self.sensor_id = sensor_id
        info = coordinator.data.sensors.get(sensor_id, {})
        zone = next((z for z in entry.runtime_data.history.data.zones.values() if z.sensor_id == sensor_id), None)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, sensor_id)}, manufacturer=MANUFACTURER,
            model="Spruce moisture sensor", name=info.get("name") or f"Spruce sensor {sensor_id[-4:]}",
            suggested_area=zone.name if zone else None)
        if VIA_DEVICE_ID and self.rt.device_id:
            self._attr_device_info["via_device_id"] = self.rt.device_id
        else:
            self._attr_device_info["via_device"] = (DOMAIN, self.controller)

    @property
    def reading(self) -> dict:
        return self.status.sensors.get(self.sensor_id, {})


class SpruceScheduleEntity(SpruceEntity):
    """An entity for one Spruce schedule (on the controller device), named after it."""

    def __init__(self, entry, schedule_id: int, key: str) -> None:
        super().__init__(entry, entry.runtime_data.history, f"schedule_{schedule_id}_{key}")
        self.schedule_id = schedule_id
        self._attr_translation_placeholders = {"schedule": self.schedule.get("name", f"Schedule {schedule_id}")}

    @property
    def schedule(self) -> dict:
        return self.rt.history.data.schedules.get(self.schedule_id, {})

    @property
    def available(self) -> bool:
        return super().available and self.schedule_id in self.rt.history.data.schedules


def watering_schedules(entry) -> list[int]:
    """Schedules with settings to show: everything but the manual one."""
    return [sid for sid, s in entry.runtime_data.history.data.schedules.items() if s.get("type") != "manual"]
