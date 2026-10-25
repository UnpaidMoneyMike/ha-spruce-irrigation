"""Repairs: decide on a new flow baseline that moved some zones a lot (accept it, or keep their previous values)."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig, SelectSelectorMode


class BaselineReviewFlow(RepairsFlow):
    """Shows each zone waiting for review (previous → new) and lets the user accept or keep each one."""

    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> data_entry_flow.FlowResult:
        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        rt = entry.runtime_data if entry else None
        if rt is None or rt.baselines is None:
            return self.async_abort(reason="not_loaded")
        pending = rt.baselines.pending()
        if not pending:
            return self.async_create_entry(data={})
        zones = rt.history.data.zones
        name = lambda z: zones[z].name if z in zones else f"Zone {z}"   # noqa: E731
        flow = lambda v: "–" if v is None else f"{rt.units.from_metric('flow', v):.1f} {rt.units.unit['flow']}"  # noqa: E731
        if user_input is not None:
            accept = [int(z) for z in user_input.get("accept", [])]
            rt.baselines.decide(accept, True)
            rt.baselines.decide([z for z in pending if z not in accept], False)
            rt.baselines_changed()
            return self.async_create_entry(data={})
        options = [{"value": str(z), "label": f"{name(z)}: {flow(p['previous'])} → {flow(p['new'])}"}
                   for z, p in sorted(pending.items())]
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Optional("accept", default=[]): SelectSelector(SelectSelectorConfig(
                options=options, multiple=True, mode=SelectSelectorMode.LIST))}),
            description_placeholders={"zones": "\n".join(f"- {o['label']}" for o in options)})


async def async_create_fix_flow(hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None) -> RepairsFlow:
    return BaselineReviewFlow((data or {})["entry_id"])
