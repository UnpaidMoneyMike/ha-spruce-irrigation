"""Flow baseline history and review. Spruce keeps only its latest baseline and applies a new one straight away, so a
baseline taken while something was wrong (a leak, a missing nozzle, a blocked dripper) silently becomes "normal".
The integration keeps every baseline it sees (and older ones entered by hand), and reviews each new one zone by
zone against the zone's design flow (from its heads) or else the last baseline the user accepted. A zone that moved
more than the tolerance waits for the user to accept the new value or keep the previous one; until then flow checks
use the previous accepted value."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

STORAGE_VERSION = 1
SAVE_DELAY = 10
NO_FLOW_LPM = 0.5            # a zone measuring under this at baseline has no real flow
ACCEPTED, PENDING, KEPT = "accepted", "needs_review", "kept_previous"


class BaselineStore:
    """Snapshots (newest last): {id, at, source: spruce|manual, zones: {zone: L/min}, review: {zone: state}};
    accepted: {zone: {lpm, id}} — what flow checks compare against when a zone has no design flow."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.baselines")
        self.snapshots: list[dict[str, Any]] = []
        self.accepted: dict[int, dict[str, Any]] = {}
        self._dirty = False

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.snapshots = [{**s, "zones": {int(z): v for z, v in s["zones"].items()},
                           "review": {int(z): v for z, v in (s.get("review") or {}).items()}}
                          for s in data.get("snapshots", [])]
        self.accepted = {int(z): v for z, v in (data.get("accepted") or {}).items()}

    def _data(self) -> dict[str, Any]:
        return {"snapshots": [{**s, "zones": {str(z): v for z, v in s["zones"].items()},
                               "review": {str(z): v for z, v in s["review"].items()}} for s in self.snapshots],
                "accepted": {str(z): v for z, v in self.accepted.items()}}

    def _save(self) -> None:
        self._dirty = True
        self._store.async_delay_save(self._data, SAVE_DELAY)

    async def async_flush(self) -> None:
        """Write now (on unload: a reload must not read the file before a delayed write has happened)."""
        if self._dirty:
            await self._store.async_save(self._data())
            self._dirty = False

    # ---- reading ---------------------------------------------------------------------------------------------------

    @property
    def latest(self) -> dict[str, Any] | None:
        spruce = [s for s in self.snapshots if s["source"] == "spruce"]
        return spruce[-1] if spruce else (self.snapshots[-1] if self.snapshots else None)

    def pending(self) -> dict[int, dict[str, Any]]:
        """Zones waiting for review: zone -> {new, previous, id}."""
        latest = self.latest
        if not latest:
            return {}
        return {z: {"new": latest["zones"].get(z), "previous": (self.accepted.get(z) or {}).get("lpm"), "id": latest["id"]}
                for z, state in latest["review"].items() if state == PENDING}

    def reference(self, zone: int) -> float | None:
        """The accepted baseline flow of a zone (L/min)."""
        return (self.accepted.get(zone) or {}).get("lpm")

    # ---- changing --------------------------------------------------------------------------------------------------

    def observe(self, current: dict[int, dict[str, Any]], design: dict[int, float], tolerance: float,
                skip: set[int] | None = None) -> dict | None:
        """Look at Spruce's current baseline ({zone: {id, lpm, at}}); a baseline not seen before is stored and
        reviewed. Returns the new snapshot, or None."""
        if not current:
            return None
        baseline_id = Counter(str(v["id"]) for v in current.values()).most_common(1)[0][0]
        if any(s["id"] == baseline_id and s["source"] == "spruce" for s in self.snapshots):
            return None
        zones = {z: round(v["lpm"], 3) for z, v in current.items() if str(v["id"]) == baseline_id}
        stamps = [v["at"] for z, v in current.items() if z in zones and v.get("at")]
        at = max(stamps) if stamps else datetime.now(timezone.utc)
        snapshot = {"id": baseline_id, "at": at.astimezone(timezone.utc).isoformat(),
                    "source": "spruce", "zones": zones, "review": {}}
        for zone, lpm in zones.items():
            ref = design.get(zone) or self.reference(zone)
            # below the meter's range a change says nothing: take it as it is
            if ref is None or ref <= 0 or zone in (skip or ()) or abs(lpm - ref) / ref <= tolerance:
                snapshot["review"][zone] = ACCEPTED
                self.accepted[zone] = {"lpm": lpm, "id": baseline_id}
            else:
                snapshot["review"][zone] = PENDING
        self.snapshots.append(snapshot)
        self.snapshots.sort(key=lambda s: s["at"])
        self._save()
        return snapshot

    def record(self, at: datetime, zones: dict[int, float], note: str | None = None, timed: bool = False,
               suspect: bool = False) -> dict:
        """Add an older baseline by hand (e.g. from paper or old screenshots). It goes into the history; it doesn't
        change what is accepted, except for zones that have nothing accepted yet — and never when it is marked
        suspect (taken while a pipe was leaking, a nozzle missing…)."""
        stamp = at.strftime("%Y-%m-%dT%H%M") if timed else at.date().isoformat()
        snapshot = {"id": f"manual-{stamp}", "at": at.astimezone(timezone.utc).isoformat(), "source": "manual",
                    "zones": {z: round(v, 3) for z, v in zones.items()}, "review": {}, "note": note,
                    "suspect": suspect}
        self.snapshots = [s for s in self.snapshots if s["id"] != snapshot["id"]] + [snapshot]
        self.snapshots.sort(key=lambda s: s["at"])
        if not suspect:
            for zone, lpm in snapshot["zones"].items():
                self.accepted.setdefault(zone, {"lpm": lpm, "id": snapshot["id"]})
        self._save()
        return snapshot

    def decide(self, zones: list[int] | None, accept: bool) -> list[int]:
        """Accept the latest baseline for zones waiting for review (all by default), or keep their previous value."""
        latest = self.latest
        waiting = self.pending()
        done = [z for z in (list(waiting) if zones is None else zones) if z in waiting]
        for zone in done:
            latest["review"][zone] = ACCEPTED if accept else KEPT
            if accept:
                self.accepted[zone] = {"lpm": latest["zones"][zone], "id": latest["id"]}
        if done:
            self._save()
        return done
