"""Base entities: the controller device and one device per moisture sensor."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .const import DOMAIN, MANUFACTURER


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
            identifiers={(DOMAIN, sensor_id)}, via_device=(DOMAIN, self.controller), manufacturer=MANUFACTURER,
            model="Spruce moisture sensor", name=info.get("name") or f"Spruce sensor {sensor_id[-4:]}",
            suggested_area=zone.name if zone else None)

    @property
    def reading(self) -> dict:
        return self.status.sensors.get(self.sensor_id, {})
