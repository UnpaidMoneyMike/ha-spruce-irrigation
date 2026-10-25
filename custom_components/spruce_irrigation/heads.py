"""Sprinkler heads and emitters: a small built-in library plus the user's own models (config subentries), and each
zone's installed heads (a subentry per zone). From them comes a zone's design flow: a reference a leak can't move,
unlike a baseline taken while something was already wrong.

Flows are kept in metric: L/min for heads, L/h for emitters and drip line (per emitter / per metre)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry

SUBENTRY_HEAD_MODEL = "head_model"
SUBENTRY_ZONE_HEADS = "zone_heads"

# How a model's flow is given, and so what "quantity" and "arc" mean for it
KIND_ARC = "per_360"         # sprays and rotating nozzles: L/min at a full circle, flow scales with the arc
KIND_HEAD = "per_head"       # rotors (a nozzle's flow whatever the arc): L/min per head
KIND_EMITTER = "per_emitter"  # drip emitters: L/h each
KIND_METRE = "per_metre"     # drip line: L/h per metre, quantity = metres
KINDS = [KIND_ARC, KIND_HEAD, KIND_EMITTER, KIND_METRE]
HOURLY = (KIND_EMITTER, KIND_METRE)   # flow given per hour


@dataclass(frozen=True)
class HeadModel:
    key: str
    brand: str
    model: str
    kind: str
    flow: float                       # L/min (arc: at 360°; head) or L/h (emitter, per metre)
    pressure_bar: float | None = None
    radius_m: float | None = None
    builtin: bool = False

    @property
    def label(self) -> str:
        return f"{self.brand} {self.model}".strip()

    def lpm(self, quantity: float, arc: float | None) -> float:
        """The flow of `quantity` of these (heads, emitters or metres) at `arc` degrees, in L/min."""
        if self.kind == KIND_ARC:
            return self.flow * (arc or 360) / 360 * quantity
        if self.kind in HOURLY:
            return self.flow / 60 * quantity
        return self.flow * quantity

    def area_m2(self, quantity: float, arc: float | None) -> float | None:
        """Ground watered with head-to-head spacing (a full-circle head covers radius² of it), for the precipitation
        rate. Only sprays, rotating nozzles and rotors with a radius."""
        if self.kind not in (KIND_ARC, KIND_HEAD) or not self.radius_m:
            return None
        return self.radius_m ** 2 * (arc or 360) / 360 * quantity


def _b(key, brand, model, kind, flow, pressure=None, radius=None) -> HeadModel:
    return HeadModel(f"builtin:{key}", brand, model, kind, flow, pressure, radius, True)


# Typical published figures at the stated pressure; check the maker's chart for your nozzle and pressure (copy a
# model to make your own with the right values).
BUILTIN: dict[str, HeadModel] = {m.key: m for m in (
    _b("hunter_mp1000", "Hunter", "MP Rotator MP1000", KIND_ARC, 2.7, 2.8, 4.0),
    _b("hunter_mp2000", "Hunter", "MP Rotator MP2000", KIND_ARC, 5.6, 2.8, 5.8),
    _b("hunter_mp3000", "Hunter", "MP Rotator MP3000", KIND_ARC, 13.0, 2.8, 9.1),
    _b("rainbird_8f", "Rain Bird", "8 series spray nozzle", KIND_ARC, 4.0, 2.1, 2.4),
    _b("rainbird_10f", "Rain Bird", "10 series spray nozzle", KIND_ARC, 6.0, 2.1, 3.0),
    _b("rainbird_12f", "Rain Bird", "12 series spray nozzle", KIND_ARC, 9.8, 2.1, 3.7),
    _b("rainbird_15f", "Rain Bird", "15 series spray nozzle", KIND_ARC, 14.0, 2.1, 4.6),
    _b("rainbird_xb05", "Rain Bird", "XB-05 emitter (2 L/h)", KIND_EMITTER, 1.9, 1.7),
    _b("rainbird_xb10", "Rain Bird", "XB-10 emitter (4 L/h)", KIND_EMITTER, 3.8, 1.7),
    _b("rainbird_xb20", "Rain Bird", "XB-20 emitter (8 L/h)", KIND_EMITTER, 7.6, 1.7),
    _b("netafim_techline_16_30", "Netafim", "Techline HCVXR 1.6 L/h, 30 cm", KIND_METRE, 5.3, 1.0),
    _b("netafim_techline_16_50", "Netafim", "Techline HCVXR 1.6 L/h, 50 cm", KIND_METRE, 3.2, 1.0),
    _b("netafim_techline_23_30", "Netafim", "Techline HCVXR 2.3 L/h, 30 cm", KIND_METRE, 7.7, 1.0),
    _b("generic_emitter_2", "Generic", "2 L/h dripper", KIND_EMITTER, 2.0),
    _b("generic_emitter_4", "Generic", "4 L/h dripper", KIND_EMITTER, 4.0),
    _b("generic_emitter_8", "Generic", "8 L/h dripper", KIND_EMITTER, 8.0),
)}


def custom_models(entry: ConfigEntry) -> dict[str, HeadModel]:
    """The user's own models (one config subentry each)."""
    models = {}
    for sub_id, sub in entry.subentries.items():
        if sub.subentry_type != SUBENTRY_HEAD_MODEL:
            continue
        d = sub.data
        models[f"custom:{sub_id}"] = HeadModel(
            f"custom:{sub_id}", d.get("brand", ""), d.get("model", sub.title), d.get("kind", KIND_HEAD),
            float(d.get("flow") or 0), _opt(d.get("pressure_bar")), _opt(d.get("radius_m")))
    return models


def library(entry: ConfigEntry) -> dict[str, HeadModel]:
    """Every model to choose from: the user's own first, then the built-in ones."""
    return {**custom_models(entry), **BUILTIN}


def zone_heads(entry: ConfigEntry) -> dict[int, tuple[str, list[dict]]]:
    """zone -> (subentry id, its rows of {model, arc, quantity})."""
    return {int(sub.data["zone"]): (sub_id, list(sub.data.get("heads") or []))
            for sub_id, sub in entry.subentries.items()
            if sub.subentry_type == SUBENTRY_ZONE_HEADS and str(sub.data.get("zone", "")).isdigit()}


@dataclass
class ZoneDesign:
    lpm: float                           # design flow
    precipitation_mm_h: float | None     # with head-to-head spacing, when the heads have a radius
    rows: list[dict[str, Any]]           # readable: "2 × Hunter MP Rotator MP2000 at 90°" and its L/min
    missing: list[str]                   # rows whose model has been deleted


def design(entry: ConfigEntry) -> dict[int, ZoneDesign]:
    """Each zone with heads entered: its design flow from them."""
    models = library(entry)
    result = {}
    for zone, (_, rows) in zone_heads(entry).items():
        total, area, readable, missing = 0.0, 0.0, [], []
        for row in rows:
            model = models.get(row.get("model", ""))
            if model is None:
                missing.append(row.get("model", "?"))
                continue
            qty, arc = float(row.get("quantity") or 0), _opt(row.get("arc"))
            lpm = model.lpm(qty, arc)
            total += lpm
            area += model.area_m2(qty, arc) or 0
            readable.append({"heads": describe(model, qty, arc), "flow_lpm": round(lpm, 2)})
        result[zone] = ZoneDesign(total, round(total * 60 / area, 1) if area else None, readable, missing)
    return result


def describe(model: HeadModel, quantity: float, arc: float | None) -> str:
    qty = f"{quantity:g}"
    if model.kind == KIND_METRE:
        return f"{qty} m of {model.label}"
    text = f"{qty} × {model.label}"
    return f"{text} at {arc:g}°" if model.kind == KIND_ARC and arc else text


def _opt(v: Any) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None
