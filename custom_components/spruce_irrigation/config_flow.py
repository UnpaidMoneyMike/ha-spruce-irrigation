"""Set up Spruce Irrigation from the UI: sign in with the Spruce account (the integration fetches the API key) or
paste an API key, then (if the account has several) choose the controller. The same choice is offered when the key
stops working (reauth) and from Reconfigure. Only the API key is stored, never the password."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import SpruceApi, SpruceAuthError, SpruceError, sign_in
from .const import (
    CONF_API_KEY,
    CONF_CONTROLLER,
    CONF_DEFAULT_RUN_MINUTES,
    CONF_FAULT_SHARE,
    CONF_HISTORY_INTERVAL,
    CONF_STATUS_INTERVAL,
    DEFAULT_FAULT_SHARE,
    DEFAULT_HISTORY_INTERVAL,
    DEFAULT_RUN_MINUTES,
    DEFAULT_STATUS_INTERVAL,
    DOMAIN,
    MIN_STATUS_INTERVAL,
)

KEY_SCHEMA = vol.Schema({vol.Required(CONF_API_KEY): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))})
SIGN_IN_SCHEMA = vol.Schema({
    vol.Required("username"): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT, autocomplete="username")),
    vol.Required("password"): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD,
                                                               autocomplete="current-password")),
})


class SpruceConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._key: str | None = None
        self._controllers: dict[str, dict] = {}

    # ---- the choice ------------------------------------------------------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["sign_in", "api_key"])

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return self.async_show_menu(step_id="reauth", menu_options=["sign_in", "api_key"])

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="reconfigure", menu_options=["sign_in", "api_key"])

    # ---- getting a key ---------------------------------------------------------------------------------------------

    async def async_step_sign_in(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            # A separate session that keeps cookies, only for the sign-in (Spruce's login is a web session)
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar()) as session:
                try:
                    key = await sign_in(session, user_input["username"], user_input["password"])
                except SpruceAuthError:
                    errors["base"] = "invalid_sign_in"
                except SpruceError:
                    errors["base"] = "cannot_connect"
            if not errors:
                errors = await self._check(key)
                if not errors:
                    return await self._done()
        return self.async_show_form(step_id="sign_in", data_schema=SIGN_IN_SCHEMA, errors=errors)

    async def async_step_api_key(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._check(user_input[CONF_API_KEY])
            if not errors:
                return await self._done()
        return self.async_show_form(step_id="api_key", data_schema=KEY_SCHEMA, errors=errors)

    async def _check(self, key: str) -> dict[str, str]:
        try:
            self._controllers = await SpruceApi(async_get_clientsession(self.hass), key).controllers()
        except SpruceAuthError:
            return {"base": "invalid_auth"}
        except SpruceError:
            return {"base": "cannot_connect"}
        if not self._controllers:
            return {"base": "no_controllers"}
        self._key = key.strip()
        return {}

    # ---- finishing -------------------------------------------------------------------------------------------------

    async def _done(self) -> ConfigFlowResult:
        if self.source in (SOURCE_REAUTH, SOURCE_RECONFIGURE):
            entry = self._get_reauth_entry() if self.source == SOURCE_REAUTH else self._get_reconfigure_entry()
            if entry.data[CONF_CONTROLLER] not in self._controllers:
                return self.async_abort(reason="wrong_account")
            return self.async_update_reload_and_abort(entry, data_updates={CONF_API_KEY: self._key})
        if len(self._controllers) == 1:
            return await self._create(next(iter(self._controllers)))
        return await self.async_step_controller()

    async def async_step_controller(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return await self._create(user_input[CONF_CONTROLLER])
        options = [{"value": cid, "label": c.get("controller_name") or cid} for cid, c in self._controllers.items()]
        return self.async_show_form(step_id="controller", data_schema=vol.Schema(
            {vol.Required(CONF_CONTROLLER): SelectSelector(SelectSelectorConfig(options=options))}))

    async def _create(self, controller: str) -> ConfigFlowResult:
        await self.async_set_unique_id(controller)
        self._abort_if_unique_id_configured()
        name = self._controllers.get(controller, {}).get("controller_name") or "Spruce"
        return self.async_create_entry(title=name, data={CONF_API_KEY: self._key, CONF_CONTROLLER: controller})

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return SpruceOptionsFlow()


class SpruceOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={k: int(v) if k != CONF_FAULT_SHARE else float(v) for k, v in user_input.items()})
        o = self.config_entry.options
        box = lambda lo, hi, step=1, unit=None: NumberSelector(NumberSelectorConfig(  # noqa: E731
            min=lo, max=hi, step=step, mode=NumberSelectorMode.BOX, unit_of_measurement=unit))
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Required(CONF_DEFAULT_RUN_MINUTES, default=o.get(CONF_DEFAULT_RUN_MINUTES, DEFAULT_RUN_MINUTES)): box(1, 240, 1, "min"),
            vol.Required(CONF_STATUS_INTERVAL, default=o.get(CONF_STATUS_INTERVAL, DEFAULT_STATUS_INTERVAL)): box(MIN_STATUS_INTERVAL, 900, 5, "s"),
            vol.Required(CONF_HISTORY_INTERVAL, default=o.get(CONF_HISTORY_INTERVAL, DEFAULT_HISTORY_INTERVAL)): box(300, 3600, 60, "s"),
            vol.Required(CONF_FAULT_SHARE, default=o.get(CONF_FAULT_SHARE, DEFAULT_FAULT_SHARE)): box(0.05, 0.9, 0.05),
        }))
