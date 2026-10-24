"""Spruce's water history as Home Assistant long-term statistics.

Spruce keeps water used per zone per schedule per day (as the flow meter counted it) back to when the controller
was added. Each zone gets two external statistics, litres and minutes run, and so does each zone within each
schedule that has watered it (zone_<n>_schedule_<id>_...); plus a controller total. Each day's value is placed at
the start of that local day. Usable in the Energy dashboard (water) and in statistics graphs, and read
back by anything that pulls Home Assistant statistics.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMeanType, StatisticMetaData
from homeassistant.components.recorder.statistics import async_add_external_statistics, statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import DOMAIN, HISTORY_DAYS, LITRES_PER_GALLON

_LOGGER = logging.getLogger(__name__)
FIRST_DAY = date(2015, 1, 1)


def statistic_id(controller: str, zone: int | None, kind: str, schedule: int | None = None) -> str:
    short = "".join(c for c in controller.lower() if c.isalnum())[-8:]
    zone_part = f"zone_{zone}_" if zone else ""
    schedule_part = f"schedule_{schedule}_" if schedule is not None else ""
    return f"{DOMAIN}:{short}_{zone_part}{schedule_part}{kind}"


async def async_import_history(hass: HomeAssistant, entry, start: date | None) -> None:
    """Import days from start (default: since the controller was added) to today."""
    rt = entry.runtime_data
    today = dt_util.now().date()
    if start is None:
        added = (await rt.api.controller_settings(rt.controller)).get("date_added")
        start = date.fromisoformat(str(added)[:10]) if added else FIRST_DAY
    await _import(hass, entry, start, today)


async def async_update_recent_statistics(hass: HomeAssistant, entry) -> None:
    """Re-import the last week, which Spruce may still fill in or correct."""
    today = dt_util.now().date()
    await _import(hass, entry, today - timedelta(days=HISTORY_DAYS), today)


async def _import(hass: HomeAssistant, entry, start: date, end: date) -> None:
    rt = entry.runtime_data
    rows = []
    chunk = start
    while chunk <= end:   # a year per request keeps Spruce's answers quick
        upto = min(chunk + timedelta(days=365), end + timedelta(days=1))
        rows += await rt.api.water_summary(rt.controller, chunk, upto)
        chunk = upto + timedelta(days=1)
    # (zone, schedule) -> day -> [litres, minutes]; zone None = the whole controller, schedule None = all schedules
    per_day: dict[tuple[int | None, int | None], dict[date, list[float]]] = {}
    schedule_names: dict[int, str] = {}
    for r in rows:
        day = date.fromisoformat(r["ymd"])
        if not start <= day <= end:
            continue
        zone, schedule = int(r["zone"]), int(r.get("schedule_id") or 0)
        schedule_names[schedule] = r.get("schedule") or f"Schedule {schedule}"
        litres = float(r.get("gallons") or 0) * LITRES_PER_GALLON
        minutes = float(r.get("minutes") or 0)
        for key in ((zone, schedule), (zone, None), (None, None)):
            acc = per_day.setdefault(key, {}).setdefault(day, [0.0, 0.0])
            acc[0] += litres
            acc[1] += minutes

    zones = rt.history.data.zones
    series = [(None, None)] + [(z, None) for z in sorted(zones)] + sorted(k for k in per_day if k[1] is not None)
    for zone, schedule in series:
        label = "All zones" if zone is None else (zones[zone].name if zone in zones else f"Zone {zone}")
        if schedule is not None:
            label += f" · {schedule_names.get(schedule, f'Schedule {schedule}')}"
        days = per_day.get((zone, schedule), {})
        for kind, idx, unit, unit_class in (("water", 0, "L", "volume"), ("minutes", 1, "min", "duration")):
            if zone is None and kind == "minutes":
                continue
            sid = statistic_id(rt.controller, zone, kind, schedule)
            base = await _sum_before(hass, sid, dt_util.start_of_local_day(start))
            total, points = base, []
            day = start
            while day <= end:
                value = days.get(day, [0.0, 0.0])[idx]
                total += value
                points.append(StatisticData(start=dt_util.start_of_local_day(day), state=round(value, 2), sum=round(total, 2)))
                day += timedelta(days=1)
            meta = StatisticMetaData(
                mean_type=StatisticMeanType.NONE, has_sum=True, name=f"{rt.history.data.name} {label} {kind}",
                source=DOMAIN, statistic_id=sid, unit_class=unit_class, unit_of_measurement=unit)
            async_add_external_statistics(hass, meta, points)
    _LOGGER.debug("Spruce statistics imported %s to %s (%d rows)", start, end, len(rows))


async def _sum_before(hass: HomeAssistant, sid: str, before: datetime) -> float:
    """The running total just before a day, so a partial re-import continues the series. Looks back 60 days first
    (the usual case), then further for a series that hasn't watered for a while."""
    for days in (60, 4000):
        result = await get_instance(hass).async_add_executor_job(
            statistics_during_period, hass, before - timedelta(days=days), before, {sid}, "hour", None, {"sum"})
        rows = result.get(sid) or []
        if rows:
            return float(rows[-1].get("sum") or 0)
    return 0.0
