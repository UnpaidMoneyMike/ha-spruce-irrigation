"""Config flow: key accepted, refused, unreachable, duplicate, and reauth."""
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


@pytest.fixture(autouse=True)
def no_setup():
    """The flow is tested on its own; the created entry isn't set up."""
    with patch("custom_components.spruce_irrigation.async_setup_entry", return_value=True):
        yield


async def _start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})


async def test_create_entry(hass: HomeAssistant, mock_api) -> None:
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: " abc123 "})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Spruce"
    assert result["data"] == {CONF_API_KEY: "abc123", CONF_CONTROLLER: CONTROLLER}
    assert result["result"].unique_id == CONTROLLER


async def test_invalid_key(hass: HomeAssistant, mock_api) -> None:
    mock_api.controllers.side_effect = SpruceAuthError("no")
    result = await hass.config_entries.flow.async_configure((await _start(hass))["flow_id"], {CONF_API_KEY: "x"})
    assert result["errors"] == {"base": "invalid_auth"}


async def test_cannot_connect(hass: HomeAssistant, mock_api) -> None:
    mock_api.controllers.side_effect = SpruceError("down")
    result = await hass.config_entries.flow.async_configure((await _start(hass))["flow_id"], {CONF_API_KEY: "x"})
    assert result["errors"] == {"base": "cannot_connect"}


async def test_already_configured(hass: HomeAssistant, mock_api) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=CONTROLLER, data={CONF_API_KEY: "k", CONF_CONTROLLER: CONTROLLER}).add_to_hass(hass)
    result = await hass.config_entries.flow.async_configure((await _start(hass))["flow_id"], {CONF_API_KEY: "k"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(hass: HomeAssistant, mock_api) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=CONTROLLER, data={CONF_API_KEY: "old", CONF_CONTROLLER: CONTROLLER})
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "new"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_KEY] == "new"
