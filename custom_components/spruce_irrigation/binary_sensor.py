"""Binary sensors: controller online, watering now, and a flow-fault flag per zone."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import SpruceEntity, SpruceZoneEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    rt = entry.runtime_data
    entities: list[BinarySensorEntity] = [SpruceOnline(entry), SpruceWatering(entry)]
    entities += [SpruceFlowFault(entry, z) for z, zone in rt.history.data.zones.items() if zone.enabled]
    async_add_entities(entities)


class SpruceOnline(SpruceEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "online"

    def __init__(self, entry) -> None:
        super().__init__(entry, entry.runtime_data.status, "online")

    @property
    def is_on(self) -> bool | None:
        return self.status.online


class SpruceWatering(SpruceEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_translation_key = "watering"

    def __init__(self, entry) -> None:
        super().__init__(entry, entry.runtime_data.status, "watering")

    @property
    def is_on(self) -> bool:
        return any(z.get("on") for z in self.status.zones.values())


class SpruceFlowFault(SpruceZoneEntity, BinarySensorEntity):
    """On when the zone's latest run measured far less flow than usual (or none): a broken meter, valve or pipe."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "flow_fault"

    def __init__(self, entry, zone: int) -> None:
        super().__init__(entry, entry.runtime_data.history, zone, "flow_fault")

    @property
    def is_on(self) -> bool | None:
        return self.history.flow_fault.get(self.zone)

    @property
    def extra_state_attributes(self):
        run = self.history.last_run.get(self.zone, {})
        return {"last_run": run.get("at").isoformat() if run.get("at") else None,
                "last_run_flow_lpm": round(run["lpm"], 2) if "lpm" in run else None,
                "usual_flow_lpm": round(self.history.usual_lpm[self.zone], 2) if self.zone in self.history.usual_lpm else None}
