"""Faster than polling: Spruce's live streams (server-sent events, which answer and close, so they are read as a
long-poll).

- Instant updates (option, off by default): listen to the zone status stream and refresh the zone status as soon as
  Spruce reports a change, instead of waiting for the next 60 s poll. Reconnects no faster than every few seconds.
- Live flow (a switch): tell the controller to log the flow meter every 2 s, read the readings into a flow rate
  sensor, and stop after 10 minutes or when switched off (as the app's Flow Meter → live logging)."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .api import SpruceError
from .const import LITRES_PER_GALLON

_LOGGER = logging.getLogger(__name__)
STREAM_GAP = 5             # seconds between reconnects to the zone status stream
STREAM_BACKOFF = 60        # after an error
LIVE_FLOW_PORT = 101
LIVE_FLOW_READ = 4         # seconds between reads of the live flow log
LIVE_FLOW_KEEPALIVE = 15   # the controller stops logging after ~15–20 s unless told again
LIVE_FLOW_MAX = timedelta(minutes=10)


class Live:
    def __init__(self, hass: HomeAssistant, rt) -> None:
        self.hass, self.rt = hass, rt
        self._stream: asyncio.Task | None = None
        self._flow: asyncio.Task | None = None
        self.flow_lpm: float | None = None
        self.flow_avg_lpm: float | None = None
        self.flow_at: datetime | None = None

    # ---- instant zone updates --------------------------------------------------------------------------------------

    def start_stream(self, entry) -> None:
        self._stream = entry.async_create_background_task(self.hass, self._listen(), "spruce zone status stream")

    async def _listen(self) -> None:
        while True:
            try:
                text = await self.rt.api.zone_status_stream(self.rt.controller)
                if "zone_data" in text:   # a zone changed: read the status now
                    await self.rt.status.async_request_refresh()
                await asyncio.sleep(STREAM_GAP)
            except SpruceError as err:
                _LOGGER.debug("Zone status stream: %s", err)
                await asyncio.sleep(STREAM_BACKOFF)

    # ---- live flow -------------------------------------------------------------------------------------------------

    @property
    def flowing(self) -> bool:
        return self._flow is not None and not self._flow.done()

    def start_flow(self, entry) -> None:
        if not self.flowing:
            self._flow = entry.async_create_background_task(self.hass, self._log_flow(), "spruce live flow")

    async def stop_flow(self) -> None:
        if self._flow:
            self._flow.cancel()
        self._flow = None
        await self._stopped()

    async def _stopped(self) -> None:
        """Tell the controller to stop logging, and clear the reading."""
        self.flow_lpm = self.flow_avg_lpm = None
        try:
            await self.rt.api.stop_live_flow(self.rt.controller, LIVE_FLOW_PORT)
        except SpruceError as err:
            _LOGGER.debug("Stopping live flow: %s", err)
        self.rt.status.async_update_listeners()

    async def _log_flow(self) -> None:
        api, ctrl = self.rt.api, self.rt.controller
        started = dt_util.utcnow()
        try:
            log_id = await api.start_live_flow(ctrl, LIVE_FLOW_PORT)
            kept = dt_util.utcnow()
            while dt_util.utcnow() - started < LIVE_FLOW_MAX:
                await asyncio.sleep(LIVE_FLOW_READ)
                if (dt_util.utcnow() - kept).total_seconds() >= LIVE_FLOW_KEEPALIVE:
                    await api.keep_live_flow(ctrl, LIVE_FLOW_PORT, log_id)
                    kept = dt_util.utcnow()
                points = await api.live_flow(log_id)
                if points:
                    last = points[-1]
                    recent = [p["gpm"] for p in points if p.get("DTepoch") and p["DTepoch"] >= last.get("DTepoch", 0) - 10]
                    self.flow_lpm = round(float(last.get("gpm") or 0) * LITRES_PER_GALLON, 2)
                    self.flow_avg_lpm = round(sum(recent) / len(recent) * LITRES_PER_GALLON, 2) if recent else None
                    self.flow_at = datetime.fromtimestamp(last["DTepoch"], timezone.utc) if last.get("DTepoch") else None
                    self.rt.status.async_update_listeners()
        except SpruceError as err:
            _LOGGER.warning("Live flow stopped: %s", err)
        # Ended by itself (time up or an error); a cancel from stop_flow doesn't get here
        self._flow = None
        await self._stopped()

    async def async_shutdown(self) -> None:
        for task in (self._stream, self._flow):
            if task:
                task.cancel()
        if self._flow:
            await self.stop_flow()
