"""Config flow: sign in or paste a key; refused, unreachable, duplicate; reauth and reconfigure."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.spruce_irrigation.api import SpruceAuthError, SpruceError
from custom_components.spruce_irrigation.const import CONF_API_KEY, CONF_CONTROLLER, DOMAIN

from .conftest import CONTROLLER

SIGN_IN = "custom_components.spruce_irrigation.config_flow.sign_in"


@pytest.fixture(autouse=True)
def no_setup():
    """The flow is tested on its own; the created entry isn't set up."""
    with patch("custom_components.spruce_irrigation.async_setup_entry", return_value=True):
        yield


async def _choose(hass: HomeAssistant, step: str, flow=None):
    """Pick a way in from the opening menu (a new entry unless a reauth/reconfigure flow is given)."""
    if flow is None:
        flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert flow["type"] is FlowResultType.MENU
    return await hass.config_entries.flow.async_configure(flow["flow_id"], {"next_step_id": step})


async def test_api_key(hass: HomeAssistant, mock_api) -> None:
    result = await _choose(hass, "api_key")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: " abc123 "})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Spruce"
    assert result["data"] == {CONF_API_KEY: "abc123", CONF_CONTROLLER: CONTROLLER}
    assert result["result"].unique_id == CONTROLLER


async def test_sign_in(hass: HomeAssistant, mock_api) -> None:
    with patch(SIGN_IN, return_value="fetchedkey") as signed:
        result = await _choose(hass, "sign_in")
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"username": "me", "password": "pw"})
    assert signed.await_args.args[1:] == ("me", "pw")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_API_KEY: "fetchedkey", CONF_CONTROLLER: CONTROLLER}   # the password isn't kept


async def test_sign_in_refused(hass: HomeAssistant, mock_api) -> None:
    with patch(SIGN_IN, side_effect=SpruceAuthError("no")):
        result = await _choose(hass, "sign_in")
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"username": "me", "password": "x"})
    assert result["errors"] == {"base": "invalid_sign_in"}


async def test_invalid_key(hass: HomeAssistant, mock_api) -> None:
    mock_api.controllers.side_effect = SpruceAuthError("no")
    result = await _choose(hass, "api_key")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "x"})
    assert result["errors"] == {"base": "invalid_auth"}


async def test_cannot_connect(hass: HomeAssistant, mock_api) -> None:
    mock_api.controllers.side_effect = SpruceError("down")
    result = await _choose(hass, "api_key")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "x"})
    assert result["errors"] == {"base": "cannot_connect"}


async def test_already_configured(hass: HomeAssistant, mock_api) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=CONTROLLER, data={CONF_API_KEY: "k", CONF_CONTROLLER: CONTROLLER}).add_to_hass(hass)
    result = await _choose(hass, "api_key")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "k"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(hass: HomeAssistant, mock_api) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=CONTROLLER, data={CONF_API_KEY: "old", CONF_CONTROLLER: CONTROLLER})
    entry.add_to_hass(hass)
    result = await _choose(hass, "api_key", await entry.start_reauth_flow(hass))
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "new"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_KEY] == "new"


async def test_reconfigure_sign_in(hass: HomeAssistant, mock_api) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=CONTROLLER, data={CONF_API_KEY: "old", CONF_CONTROLLER: CONTROLLER})
    entry.add_to_hass(hass)
    with patch(SIGN_IN, return_value="fresh"):
        result = await _choose(hass, "sign_in", await entry.start_reconfigure_flow(hass))
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"username": "me", "password": "pw"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_API_KEY] == "fresh"


async def test_options_units_and_price(hass: HomeAssistant, mock_api) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=CONTROLLER, data={CONF_API_KEY: "k", CONF_CONTROLLER: CONTROLLER},
                            options={"default_run_minutes": 10})
    entry.add_to_hass(hass)
    menu = await hass.config_entries.options.async_init(entry.entry_id)
    assert menu["type"] is FlowResultType.MENU
    form = await hass.config_entries.options.async_configure(menu["flow_id"], {"next_step_id": "units"})
    assert form["step_id"] == "units"
    result = await hass.config_entries.options.async_configure(form["flow_id"], {
        "units": "custom", "unit_volume": "L", "unit_flow": "L/min", "unit_rain": "in", "unit_temperature": "°C",
        "unit_wind": "km/h"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["units"] == "custom" and entry.options["unit_rain"] == "in"
    assert entry.options["default_run_minutes"] == 10          # other options kept

    menu = await hass.config_entries.options.async_init(entry.entry_id)
    form = await hass.config_entries.options.async_configure(menu["flow_id"], {"next_step_id": "price"})
    await hass.config_entries.options.async_configure(form["flow_id"], {"water_price": 2.33})
    assert entry.options["water_price"] == 2.33 and entry.options["units"] == "custom"
