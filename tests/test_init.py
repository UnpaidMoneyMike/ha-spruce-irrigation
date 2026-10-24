"""Set-up, entities, control and services against the fake account."""
from __future__ import annotations

import pytest

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.spruce_irrigation.const import CONF_API_KEY, CONF_CONTROLLER, DOMAIN

from .conftest import CONTROLLER


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """The recorder (for the water statistics) has to start before Home Assistant does."""
    yield


@pytest.fixture
async def entry(hass: HomeAssistant, mock_api) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=CONTROLLER, title="Test Spruce",
                            data={CONF_API_KEY: "k", CONF_CONTROLLER: CONTROLLER})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _entity(hass: HomeAssistant, unique: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(_domain(unique), DOMAIN, f"{CONTROLLER}_{unique}")
    assert entity_id, unique
    return entity_id


def _domain(unique: str) -> str:
    return {"zone_1_valve": "valve", "zone_2_valve": "valve", "online": "binary_sensor", "watering": "binary_sensor",
            "zone_1_flow_fault": "binary_sensor", "zone_2_flow_fault": "binary_sensor", "hibernate": "switch",
            "stop": "button", "zone_1_run_time": "number"}.get(unique, "sensor")


async def test_setup_and_unload(hass: HomeAssistant, entry) -> None:
    assert entry.state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_states(hass: HomeAssistant, entry) -> None:
    assert hass.states.get(_entity(hass, "zone_1_valve")).state == "open"
    assert hass.states.get(_entity(hass, "zone_2_valve")).state == "closed"
    assert er.async_get(hass).async_get_entity_id("valve", DOMAIN, f"{CONTROLLER}_zone_3_valve") is None   # disabled zone
    assert hass.states.get(_entity(hass, "status")).state == "watering"
    assert hass.states.get(_entity(hass, "active_zone")).state == "Front Lawn"
    assert hass.states.get(_entity(hass, "watering")).state == "on"
    assert hass.states.get(_entity(hass, "online")).state == "on"
    assert float(hass.states.get(_entity(hass, "water_today")).state) == pytest.approx(80 * 3.785411784, rel=1e-3)
    assert hass.states.get(_entity(hass, "zone_1_flow_fault")).state == "on"     # latest run measured no flow
    assert hass.states.get(_entity(hass, "zone_2_flow_fault")).state == "off"
    assert float(hass.states.get(_entity(hass, "000D6F0000ABCDEF_moisture")).state) == 31


async def test_valve_control(hass: HomeAssistant, entry, mock_api) -> None:
    valve = _entity(hass, "zone_2_valve")
    await hass.services.async_call("valve", "open_valve", {"entity_id": valve}, blocking=True)
    mock_api.zone_on.assert_awaited_with(CONTROLLER, 2, 600)
    await hass.services.async_call(DOMAIN, "run_zone", {"entity_id": valve, "minutes": 3}, blocking=True)
    mock_api.zone_on.assert_awaited_with(CONTROLLER, 2, 180)
    await hass.services.async_call("valve", "close_valve", {"entity_id": valve}, blocking=True)
    mock_api.zone_off.assert_awaited_with(CONTROLLER, 2)


async def test_run_time_number(hass: HomeAssistant, entry, mock_api) -> None:
    await hass.services.async_call("number", "set_value", {"entity_id": _entity(hass, "zone_1_run_time"), "value": 7},
                                   blocking=True)
    await hass.services.async_call("valve", "open_valve", {"entity_id": _entity(hass, "zone_1_valve")}, blocking=True)
    mock_api.zone_on.assert_awaited_with(CONTROLLER, 1, 420)


async def test_services(hass: HomeAssistant, entry, mock_api) -> None:
    await hass.services.async_call(DOMAIN, "run_schedule", {"schedule": "morning"}, blocking=True)
    mock_api.schedule_on.assert_awaited_with(CONTROLLER, 11)
    await hass.services.async_call(DOMAIN, "run_all_zones", {"minutes": 2}, blocking=True)
    mock_api.run_all.assert_awaited_with(CONTROLLER, 120)
    await hass.services.async_call(DOMAIN, "stop", {}, blocking=True)
    mock_api.stop.assert_awaited()
    await hass.services.async_call("switch", "turn_on", {"entity_id": _entity(hass, "hibernate")}, blocking=True)
    mock_api.hibernate.assert_awaited_with(CONTROLLER, True)
