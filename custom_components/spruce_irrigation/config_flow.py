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
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
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
    CONF_REVIEW_TOLERANCE,
    CONF_STATUS_INTERVAL,
    DEFAULT_FAULT_SHARE,
    DEFAULT_HISTORY_INTERVAL,
    DEFAULT_REVIEW_TOLERANCE,
    DEFAULT_RUN_MINUTES,
    DEFAULT_STATUS_INTERVAL,
    DOMAIN,
    MIN_STATUS_INTERVAL,
    CONF_PRICE,
    CONF_PRICE_ENTITY,
    CONF_UNITS,
    UNITS_CUSTOM,
    UNITS_METRIC,
    UNITS_US,
)
from .const import LITRES_PER_GALLON
from .heads import (
    BUILTIN,
    HOURLY,
    KIND_ARC,
    KIND_METRE,
    KINDS,
    SUBENTRY_HEAD_MODEL,
    SUBENTRY_ZONE_HEADS,
    describe,
    library,
    zone_heads,
)
from .units import QUANTITIES

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

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_HEAD_MODEL: HeadModelFlow, SUBENTRY_ZONE_HEADS: ZoneHeadsFlow}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return SpruceOptionsFlow()


class SpruceOptionsFlow(OptionsFlow):
    """Configure: general (run time, polling, flow-fault share), units (metric by default), water price."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["general", "units", "price"])

    def _save(self, values: dict[str, Any]) -> ConfigFlowResult:
        return self.async_create_entry(data={**self.config_entry.options, **values})

    async def async_step_general(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self._save({k: float(v) if k in (CONF_FAULT_SHARE, CONF_REVIEW_TOLERANCE) else int(v)
                               for k, v in user_input.items()})
        o = self.config_entry.options
        box = lambda lo, hi, step=1, unit=None: NumberSelector(NumberSelectorConfig(  # noqa: E731
            min=lo, max=hi, step=step, mode=NumberSelectorMode.BOX, unit_of_measurement=unit))
        return self.async_show_form(step_id="general", data_schema=vol.Schema({
            vol.Required(CONF_DEFAULT_RUN_MINUTES, default=o.get(CONF_DEFAULT_RUN_MINUTES, DEFAULT_RUN_MINUTES)): box(1, 240, 1, "min"),
            vol.Required(CONF_STATUS_INTERVAL, default=o.get(CONF_STATUS_INTERVAL, DEFAULT_STATUS_INTERVAL)): box(MIN_STATUS_INTERVAL, 900, 5, "s"),
            vol.Required(CONF_HISTORY_INTERVAL, default=o.get(CONF_HISTORY_INTERVAL, DEFAULT_HISTORY_INTERVAL)): box(300, 3600, 60, "s"),
            vol.Required(CONF_FAULT_SHARE, default=o.get(CONF_FAULT_SHARE, DEFAULT_FAULT_SHARE)): box(0.05, 0.9, 0.05),
            vol.Required(CONF_REVIEW_TOLERANCE, default=o.get(CONF_REVIEW_TOLERANCE, DEFAULT_REVIEW_TOLERANCE)): box(0.05, 1, 0.05),
        }))

    async def async_step_units(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Metric (the default), US, or a unit per quantity (used when Custom is chosen)."""
        if user_input is not None:
            return self._save(user_input)
        o = self.config_entry.options
        choose = lambda options: SelectSelector(SelectSelectorConfig(options=options))  # noqa: E731
        schema: dict = {vol.Required(CONF_UNITS, default=o.get(CONF_UNITS, UNITS_METRIC)): SelectSelector(
            SelectSelectorConfig(options=[UNITS_METRIC, UNITS_US, UNITS_CUSTOM], translation_key="units"))}
        for quantity, (key, metric, us) in QUANTITIES.items():
            schema[vol.Required(key, default=o.get(key, metric))] = choose([str(metric), str(us)])
        return self.async_show_form(step_id="units", data_schema=vol.Schema(schema))

    async def async_step_price(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Water price per kL (per 1000 gal with US volume), or an entity holding the tariff."""
        if user_input is not None:
            values: dict[str, Any] = {CONF_PRICE: user_input.get(CONF_PRICE), CONF_PRICE_ENTITY: user_input.get(CONF_PRICE_ENTITY)}
            return self._save(values)
        o = self.config_entry.options
        return self.async_show_form(step_id="price", data_schema=vol.Schema({
            vol.Optional(CONF_PRICE, description={"suggested_value": o.get(CONF_PRICE)}): NumberSelector(NumberSelectorConfig(
                min=0, max=1000, step=0.001, mode=NumberSelectorMode.BOX)),
            vol.Optional(CONF_PRICE_ENTITY, description={"suggested_value": o.get(CONF_PRICE_ENTITY)}): EntitySelector(
                EntitySelectorConfig(domain=["sensor", "input_number", "number"])),
        }))


# ---- head library and zone heads (config subentries) -------------------------------------------------------------

def _us_flow(entry: ConfigEntry) -> bool:
    rt = getattr(entry, "runtime_data", None)
    return bool(rt) and rt.units.unit["flow"] == "gal/min"


def _flow_unit(kind: str, us: bool) -> str:
    return ("gal/h" if us else "L/h") if kind in HOURLY else ("gal/min" if us else "L/min")


class HeadModelFlow(ConfigSubentryFlow):
    """A sprinkler head, nozzle, emitter or drip line of your own: brand, model, how its flow is given, the flow,
    and optionally the pressure it was rated at and its radius. Start blank or from a built-in model."""

    def __init__(self) -> None:
        self._start: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if user_input is not None:
            model = BUILTIN.get(user_input.get("start_from", ""))
            if model:
                self._start = {"brand": model.brand, "model": f"{model.model} (mine)", "kind": model.kind,
                               "flow": model.flow, "pressure_bar": model.pressure_bar, "radius_m": model.radius_m}
            return await self.async_step_model()
        options = [{"value": "blank", "label": "Blank"}] + [{"value": k, "label": m.label} for k, m in BUILTIN.items()]
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Optional("start_from", default="blank"): SelectSelector(SelectSelectorConfig(options=options))}))

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        self._start = dict(self._get_reconfigure_subentry().data)
        return await self.async_step_model()

    async def async_step_model(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        entry = self._get_entry()
        us = _us_flow(entry)
        start = self._start
        if user_input is not None:
            kind = user_input["kind"]
            flow = float(user_input["flow"]) * (LITRES_PER_GALLON if us else 1)
            data = {"brand": user_input["brand"].strip(), "model": user_input["model"].strip(), "kind": kind,
                    "flow": round(flow, 4), "pressure_bar": user_input.get("pressure_bar"),
                    "radius_m": user_input.get("radius_m")}
            title = f"{data['brand']} {data['model']}".strip()
            if self.source == SOURCE_RECONFIGURE:
                return self.async_update_and_abort(entry, self._get_reconfigure_subentry(), title=title, data=data)
            return self.async_create_entry(title=title, data=data)
        flow = start.get("flow")
        shown = round(flow / LITRES_PER_GALLON, 3) if us and flow else flow
        box = lambda lo, hi, step, unit=None: NumberSelector(NumberSelectorConfig(  # noqa: E731
            min=lo, max=hi, step=step, mode=NumberSelectorMode.BOX, unit_of_measurement=unit))
        return self.async_show_form(step_id="model", data_schema=vol.Schema({
            vol.Required("brand", default=start.get("brand", "")): TextSelector(),
            vol.Required("model", default=start.get("model", "")): TextSelector(),
            vol.Required("kind", default=start.get("kind", KIND_ARC)): SelectSelector(SelectSelectorConfig(
                options=KINDS, translation_key="head_kind")),
            vol.Required("flow", default=shown or 0): box(0, 2000, 0.001),
            vol.Optional("pressure_bar", description={"suggested_value": start.get("pressure_bar")}): box(0, 20, 0.1, "bar"),
            vol.Optional("radius_m", description={"suggested_value": start.get("radius_m")}): box(0, 50, 0.1, "m"),
        }), description_placeholders={"per_360": _flow_unit(KIND_ARC, us), "per_hour": _flow_unit(KIND_METRE, us)})


class ZoneHeadsFlow(ConfigSubentryFlow):
    """The heads installed in a zone: rows of quantity × model (× arc). Gives the zone's design flow."""

    def __init__(self) -> None:
        self._zone: int | None = None
        self._heads: list[dict[str, Any]] = []

    def _zones(self) -> dict[int, str]:
        rt = getattr(self._get_entry(), "runtime_data", None)
        return {z: zone.name for z, zone in sorted(rt.history.data.zones.items()) if zone.enabled} if rt else {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        zones = self._zones()
        if not zones:
            return self.async_abort(reason="not_loaded")
        taken = zone_heads(self._get_entry())
        free = {z: n for z, n in zones.items() if z not in taken}
        if not free:
            return self.async_abort(reason="all_zones_done")
        if user_input is not None:
            self._zone = int(user_input["zone"])
            return await self.async_step_heads()
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required("zone"): SelectSelector(SelectSelectorConfig(
                options=[{"value": str(z), "label": n} for z, n in free.items()]))}))

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        sub = self._get_reconfigure_subentry()
        self._zone, self._heads = int(sub.data["zone"]), [dict(h) for h in sub.data.get("heads") or []]
        return await self.async_step_heads()

    async def async_step_heads(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        models = library(self._get_entry())
        rows, total = [], 0.0
        for h in self._heads:
            m = models.get(h["model"])
            if m:
                lpm = m.lpm(float(h["quantity"]), h.get("arc"))
                total += lpm
                rows.append(f"- {describe(m, float(h['quantity']), h.get('arc'))}: {lpm:.2f} L/min")
            else:
                rows.append(f"- {h['model']} (model deleted)")
        menu = ["add_head"] + (["remove_head"] if self._heads else []) + ["save"]
        return self.async_show_menu(step_id="heads", menu_options=menu, description_placeholders={
            "zone": self._zones().get(self._zone, f"Zone {self._zone}"),
            "rows": "\n".join(rows) or "(none yet)", "total": f"{total:.2f}"})

    async def async_step_add_head(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if user_input is not None:
            self._heads.append({"model": user_input["model"], "arc": float(user_input.get("arc") or 360),
                                "quantity": float(user_input["quantity"])})
            return await self.async_step_heads()
        models = library(self._get_entry())
        options = [{"value": k, "label": m.label + ("" if m.builtin else " ★")} for k, m in models.items()]
        return self.async_show_form(step_id="add_head", data_schema=vol.Schema({
            vol.Required("model"): SelectSelector(SelectSelectorConfig(options=options)),
            vol.Optional("arc", default=360): NumberSelector(NumberSelectorConfig(
                min=1, max=360, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="°")),
            vol.Required("quantity", default=1): NumberSelector(NumberSelectorConfig(
                min=0.1, max=10000, step=0.1, mode=NumberSelectorMode.BOX)),
        }))

    async def async_step_remove_head(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        models = library(self._get_entry())
        if user_input is not None:
            drop = {int(i) for i in user_input.get("rows", [])}
            self._heads = [h for i, h in enumerate(self._heads) if i not in drop]
            return await self.async_step_heads()
        options = [{"value": str(i), "label": describe(models[h["model"]], float(h["quantity"]), h.get("arc"))
                    if h["model"] in models else h["model"]} for i, h in enumerate(self._heads)]
        return self.async_show_form(step_id="remove_head", data_schema=vol.Schema({
            vol.Optional("rows", default=[]): SelectSelector(SelectSelectorConfig(options=options, multiple=True))}))

    async def async_step_save(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        data = {"zone": self._zone, "heads": self._heads}
        title = self._zones().get(self._zone, f"Zone {self._zone}")
        if self.source == SOURCE_RECONFIGURE:
            return self.async_update_and_abort(self._get_entry(), self._get_reconfigure_subentry(), title=title, data=data)
        return self.async_create_entry(title=title, data=data, unique_id=f"zone_{self._zone}")
