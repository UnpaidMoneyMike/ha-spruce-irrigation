"""Async client for the Spruce irrigation cloud (api.spruceirrigation.com).

Spruce controllers (Gen2 Wi-Fi) have no local API; the Spruce web app and this client talk to Spruce's cloud with
the account API key, sent as-is in the Authorization header. Three families of endpoints are used:

  v2/...          documented partner API: controllers, settings, zone and sensor status
  app/api/...     the web app's data API: history (water_summary, gpm_data, events), forecast, flow models
  app/device/...  commands relayed to the controller: run/stop zones and schedules, hibernate, sensor pairing
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://api.spruceirrigation.com/"
TIMEOUT = aiohttp.ClientTimeout(total=60)
STREAM_TIMEOUT = aiohttp.ClientTimeout(total=90)
RETRIES = 3


class SpruceError(Exception):
    """Spruce's cloud could not be reached or answered with an error."""


class SpruceAuthError(SpruceError):
    """The API key was refused."""


class SpruceCommandError(SpruceError):
    """The controller did not accept a command."""


class SpruceApi:
    """One Spruce account."""

    def __init__(self, session: aiohttp.ClientSession, api_key: str) -> None:
        self._session = session
        self._key = api_key.strip()

    async def _request(self, method: str, path: str, params: dict[str, Any] | None = None) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        headers = {"Authorization": self._key}
        for attempt in range(RETRIES):
            try:
                async with self._session.request(
                    method,
                    BASE_URL + path,
                    params=params if method == "GET" else None,
                    data=params if method != "GET" else None,
                    headers=headers,
                    timeout=TIMEOUT,
                ) as resp:
                    if resp.status in (401, 403):
                        raise SpruceAuthError("Spruce refused the API key")
                    if resp.status >= 500 and attempt < RETRIES - 1:   # Spruce sometimes answers 504 on slow requests
                        await asyncio.sleep(5 * (attempt + 1))
                        continue
                    if resp.status >= 400:
                        raise SpruceError(f"{path}: HTTP {resp.status}")
                    text = await resp.text()
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                if attempt < RETRIES - 1:
                    await asyncio.sleep(5 * (attempt + 1))
                    continue
                raise SpruceError(f"{path}: {err}") from err
            if not text.strip():
                return None
            try:
                data = await asyncio.get_running_loop().run_in_executor(None, _json, text)
            except ValueError as err:
                raise SpruceError(f"{path}: not JSON") from err
            if isinstance(data, dict) and data.get("error") is True and "message" in data:
                if "authoriz" in str(data["message"]).lower():
                    raise SpruceAuthError(data["message"])
                raise SpruceError(f"{path}: {data['message']}")
            return data
        raise SpruceError(f"{path}: no answer")

    # ---- account and settings ------------------------------------------------------------------------------------

    async def controllers(self) -> dict[str, dict]:
        """Controllers on the account, keyed by device id."""
        return await self._request("GET", "v2/controllers") or {}

    async def controller_settings(self, controller: str) -> dict:
        """Name, model, firmware, zones (name, nozzle, soil, configured gpm, sensor), schedules."""
        return await self._request("GET", "v2/controller_settings", {"controller_id": controller}) or {}

    async def settings(self) -> dict:
        """Everything the web app knows: controllers (incl. hibernate, rain/freeze skip), schedules, sensors."""
        return await self._request("GET", "app/api/settings") or {}

    async def flow_meter_models(self) -> list[dict]:
        return await self._request("GET", "app/api/flow_meter_models") or []

    # ---- live status ---------------------------------------------------------------------------------------------

    async def zone_status(self, controller: str) -> dict[str, dict]:
        """Per zone: zone_state (1 = watering), epoch_time (start), duration (s), schedule_id."""
        return await self._request("GET", "v2/zone_status", {"controller_id": controller}) or {}

    async def sensor_status(self, controller: str) -> dict[str, dict]:
        """Per moisture sensor: moisture %, temperature (F), battery (V), signal (dB), each with its epoch."""
        return await self._request("GET", "v2/sensor_status", {"controller_id": controller}) or {}

    async def controller_status(self, controller: str) -> dict:
        """current_state 1 = online, epoch_time of the last message."""
        return await self._request("GET", "app/api/controller_status", {"controller_id": controller}) or {}

    async def run_status(self, controller: str) -> dict[str, int]:
        """Seconds left of pause, pump delay and cycle-soak, asked of the controller itself."""
        data = await self.device("GET", "run_status", controller)
        parts = [int(float(x or 0)) for x in str(data.get("result", "")).split(",")[:3]] + [0, 0, 0]
        return {"pause": parts[0], "pump_delay": parts[1], "cycle_soak": parts[2]}

    async def network(self, controller: str) -> dict[str, Any]:
        """The controller's Wi-Fi channel, signal and address."""
        data = await self.device("GET", "network_var", controller)
        f = str(data.get("result", "")).split(",") + [""] * 6
        return {"channel": f[2], "signal": _int(f[3]), "ip": f[4]}

    async def forecast(self, controller: str) -> dict:
        return await self._request("GET", "app/api/weather_forecast", {"controller_id": controller}) or {}

    # ---- history -------------------------------------------------------------------------------------------------

    async def water_summary(self, controller: str, start: date, end: date) -> list[dict]:
        """Water per zone per day per schedule: ymd, zone, schedule_id, schedule, minutes, gallons."""
        data = await self._request("GET", "app/api/water_summary",
                                   {"deviceid": controller, "start_date": start.isoformat(), "end_date": end.isoformat()})
        return _columns_to_rows(data, ("ymd", "zone", "schedule_id", "schedule", "schedule_type", "minutes", "gallons"))

    async def weather_history(self, controller: str, start: date, end: date) -> list[dict]:
        """The weather Spruce recorded at the controller's location, per day: ymd, precip_in, temp_min, temp_max (°F).
        A day can appear twice and some days are missing."""
        data = await self._request("GET", "app/api/weather_history",
                                   {"deviceid": controller, "start_date": start.isoformat(), "end_date": end.isoformat()})
        return _columns_to_rows(data, ("ymd", "precip_in", "temp_min", "temp_max"))

    async def gpm_data(self, controller: str, start: date, end: date) -> list[dict]:
        """Measured flow of each zone run: DTepoch (run start), zone, gpm (0 if the meter counted nothing)."""
        data = await self._request("GET", "app/api/gpm_data",
                                   {"deviceid": controller, "start_date": start.isoformat(), "end_date": end.isoformat()})
        return _columns_to_rows(data, ("DTepoch", "zone", "gpm"))

    async def events(self, controller: str, start_epoch: int, end_epoch: int) -> list[dict]:
        """Activity log entries: id, type, zone, message, DTepoch."""
        data = await self._request("GET", "app/api/events",
                                   {"deviceid": controller, "start_epoch": start_epoch, "end_epoch": end_epoch})
        # Spruce also returns old entries of some types (e.g. outlet_manual) whatever the window; keep the window's
        return [e for e in data if start_epoch <= (e.get("DTepoch") or 0) <= end_epoch] if isinstance(data, list) else []

    # ---- commands ------------------------------------------------------------------------------------------------

    async def device(self, method: str, command: str, controller: str, **params: Any) -> dict:
        data = await self._request(method, f"app/device/{command}",
                                   {"device_id": controller, "gateway": "wifi", **params})
        return data if isinstance(data, dict) else {}

    async def _spruceset(self, controller: str, command: str) -> None:
        data = await self.device("POST", "spruce", controller, spruceset=command)
        if data.get("return_value") != 1:
            raise SpruceCommandError(f"The controller did not accept '{command.split(',')[1] if ',' in command else command}'")

    async def zone_on(self, controller: str, zone: int, seconds: int, schedule_id: int = 0) -> None:
        await self._spruceset(controller, f"run,zon,{zone},{int(seconds)},{schedule_id}")

    async def zone_off(self, controller: str, zone: int, schedule_id: int = 0) -> None:
        await self._spruceset(controller, f"run,zoff,{zone},{schedule_id}")

    async def run_all(self, controller: str, seconds: int) -> None:
        await self._spruceset(controller, f"run,zall,{int(seconds)}")

    async def schedule_on(self, controller: str, schedule_id: int) -> None:
        await self._spruceset(controller, f"run,son,{schedule_id}")

    async def schedule_off(self, controller: str, schedule_id: int) -> None:
        await self._spruceset(controller, f"run,soff,{schedule_id}")

    async def stop(self, controller: str) -> None:
        await self._spruceset(controller, "stop")

    async def _save_settings(self, records: dict[str, dict]) -> None:
        await self._request("POST", "app/api/settings", {"settings": _dumps(records)})

    async def save_controller_settings(self, controller: str, values: dict[str, str]) -> None:
        """Save controller settings in Spruce's cloud, as the web app does (one record per setting)."""
        await self._save_settings({
            name: {"name": name, "value": value, "device_id": controller, "zone": 99, "schedule_id": 0,
                   "settings_key_type": "controller", "settings_key": f"controller.{controller}"}
            for name, value in values.items()})

    async def save_schedule_settings(self, controller: str, schedule_id: int, values: dict[str, str],
                                     zone: int | None = None) -> None:
        """Save settings of a schedule (or of one zone within it), as the web app does."""
        key = f"schedule.{schedule_id}" + (f".zone.{zone}" if zone is not None else "")
        await self._save_settings({
            name: {"name": name, "value": value, "device_id": controller, "zone": zone if zone is not None else 99,
                   "schedule_id": schedule_id, "settings_key_type": "schedule_zone" if zone is not None else "schedule",
                   "settings_key": key}
            for name, value in values.items()})

    async def save_schedule_zones(self, controller: str, schedule_id: int, values: dict[int, dict[str, str]]) -> None:
        """Save settings of several zones within a schedule in one go (a zone group, or a skip for every zone), as the
        app does: one record per zone and setting."""
        records: dict[str, dict] = {}
        for zone, zone_values in values.items():
            for name, value in zone_values.items():
                records[f"{name},{zone}"] = {
                    "name": name, "value": value, "device_id": controller, "zone": zone, "schedule_id": schedule_id,
                    "settings_key_type": "schedule_zone", "settings_key": f"schedule.{schedule_id}.zone.{zone}"}
        await self._save_settings(records)

    async def save_zone_settings(self, controller: str, values: dict[int, dict[str, str]]) -> None:
        """Save settings of zones of the controller (incl. the flow-meter ports 101/102), as the app does."""
        records: dict[str, dict] = {}
        for zone, zone_values in values.items():
            for name, value in zone_values.items():
                records[f"{name},{zone}"] = {
                    "name": name, "value": value, "device_id": controller, "zone": zone, "schedule_id": 0,
                    "settings_key_type": "zone", "settings_key": f"controller.{controller}.zone.{zone}"}
        await self._save_settings(records)

    async def refresh_settings(self, controller: str) -> None:
        """Tell the controller to fetch its settings again (the app does this after saving controller or zone
        settings the controller itself uses)."""
        await self._spruceset(controller, "settings")

    async def sync_schedule(self, controller: str, schedule_id: int, enabled: bool) -> None:
        """Tell the controller about a changed schedule, as the web app does after saving: an enabled schedule's run
        list is worked out again in the cloud and sent to the controller; a disabled one is removed from the
        controller (it stays on the account)."""
        if not enabled:
            await self._spruceset(controller, f"delete,{schedule_id}")
            return
        runlist = await self._request("GET", "schedule/runlist",
                                      {"deviceid": controller, "scheduleid": schedule_id, "run": "false"}) or {}
        await self._spruceset(controller, f"schedule,{schedule_id},{runlist.get('start_time') or 0}")

    async def hibernate(self, controller: str, enabled: bool, until: date | None = None) -> None:
        """Hibernate (winter mode): schedules don't run until turned off again, or until `until` (Spruce resumes them
        on that day). Like the app: save the setting, then tell the controller."""
        await self.save_controller_settings(controller, {
            "hibernate_enabled": "1" if enabled else "0",
            "hibernate_end_date": until.isoformat() if enabled and until else ("0" if enabled else "")})
        await self._spruceset(controller, f"hibernate,{1 if enabled else 0}")

    async def pause(self, pause_seconds: int) -> None:
        """Pause watering for a while (account-wide, v2 API)."""
        await self._request("POST", "v2/pause", {"pausetime": int(pause_seconds)})

    async def resume(self) -> None:
        await self._request("POST", "v2/resume", {"pausetime": 0})

    async def outlet_status(self, controller: str) -> dict[str, dict]:
        """Zigbee outlets / repeaters: current_state (1 = on), epoch_time (last report), duration, signal_strength."""
        data = await self._request("GET", "app/api/outlet_status", {"controller_id": controller})
        return data if isinstance(data, dict) else {}

    async def outlet_on(self, controller: str, device: str, seconds: int = 0) -> None:
        """Switch a Zigbee outlet on, for `seconds` (0 = until switched off)."""
        await self._spruceset(controller, f"run,oon,{device},{int(seconds)}")

    async def outlet_off(self, controller: str, device: str) -> None:
        await self._spruceset(controller, f"run,ooff,{device}")

    async def pair_sensor(self, controller: str) -> None:
        """Put the controller's Zigbee network in listen mode so a reset moisture sensor can join."""
        await self._spruceset(controller, "device,add,sensor")

    async def cancel_pairing(self, controller: str) -> None:
        await self._spruceset(controller, "device,add,cancel")

    # ---- schedules ----------------------------------------------------------------------------------------------

    async def create_schedule(self, controller: str, schedule_type: str, name: str) -> int:
        """Add a schedule (connected / basic / manual), as the app's "Add a new schedule", and name it."""
        data = await self._request("POST", "app/api/schedule",
                                   {"schedule_type": schedule_type, "master_controller": controller}) or {}
        schedule_id = _int(data.get("id")) if isinstance(data, dict) else None
        if not schedule_id:
            raise SpruceError("Spruce didn't create the schedule")
        await self.save_schedule_settings(controller, schedule_id, {"schedule_name": name})
        return schedule_id

    async def schedule_day(self, schedule_id: int, day: date) -> dict:
        """What a schedule actually did on a past day, per zone: start times, durations, gpm, notes."""
        data = await self._request("GET", "app/api/schedule_history_zone_state",
                                   {"schedule_id": schedule_id, "date": day.isoformat()})
        return data if isinstance(data, dict) else {}

    async def delete_schedule(self, controller: str, schedule_id: int) -> None:
        """Delete a schedule from the account, then from the controller (as the app does)."""
        await self._request("DELETE", f"app/api/schedule/{schedule_id}")
        await self._spruceset(controller, f"delete,{schedule_id}")

    # ---- live streams (server-sent events read as a long-poll) ----------------------------------------------------

    async def _stream(self, path: str, params: dict[str, Any]) -> str:
        try:
            async with self._session.get(BASE_URL + path, params=params, headers={"Authorization": self._key},
                                         timeout=STREAM_TIMEOUT) as resp:
                if resp.status in (401, 403):
                    raise SpruceAuthError("Spruce refused the API key")
                if resp.status >= 400:
                    raise SpruceError(f"{path}: HTTP {resp.status}")
                return await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise SpruceError(f"{path}: {err}") from err

    async def zone_status_stream(self, controller: str) -> str:
        """Waits for a zone change (or Spruce closing the stream with nothing new); the raw event text."""
        return await self._stream("app/stream/zone-status-stream.php", {"devid": controller, "auth": self._key})

    async def start_live_flow(self, controller: str, port: int) -> int:
        """Start logging the flow meter every 2 s (the app's live logging); returns the log id."""
        data = await self._request("POST", "app/api/live_log_flow", {"deviceid": controller, "port": port}) or {}
        log_id = _int(data.get("live_log_id")) if isinstance(data, dict) else None
        if not log_id:
            raise SpruceError("Spruce didn't start live flow logging")
        await self.keep_live_flow(controller, port, log_id)
        return log_id

    async def keep_live_flow(self, controller: str, port: int, log_id: int) -> None:
        await self._spruceset(controller, f"flowlog,1,{port},{log_id}")

    async def stop_live_flow(self, controller: str, port: int) -> None:
        await self._spruceset(controller, f"flowlog,0,{port}")

    async def live_flow(self, log_id: int) -> list[dict]:
        """The live log's readings so far, oldest first: {gpm, elapsed_seconds, DTepoch}."""
        text = await self._stream("app/stream/live-logging-flow.php", {"id": log_id})
        points: list[dict] = []
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                data = _json(line[5:].strip())
            except ValueError:
                continue
            if isinstance(data, dict):
                points = [p for k, p in data.items() if str(k).isdigit() and isinstance(p, dict)] or points
        return sorted(points, key=lambda p: p.get("DTepoch") or 0)

    # ---- flow baseline -------------------------------------------------------------------------------------------

    async def current_baseline(self, controller: str) -> dict[str, dict]:
        """Spruce's flow baseline per zone: {zone: {id, zone, gpm, DTepoch}}. Spruce keeps only the latest one."""
        data = await self._request("GET", "app/api/current_baseline", {"deviceid": controller, "type": "flow"})
        return data if isinstance(data, dict) else {}

    async def start_baseline(self, controller: str) -> int:
        """Run a flow baseline, as the app's "Baseline Now": every enabled zone runs 60 s (plus a 10 s pause) while
        the flow is measured; Spruce applies the new values itself when it finishes."""
        data = await self._request("POST", "app/api/baseline",
                                   {"deviceid": controller, "current": "false", "flow": "true", "notify": 0}) or {}
        baseline_id = _int(data.get("flow_baseline_id")) if isinstance(data, dict) else None
        if not baseline_id:
            raise SpruceError("Spruce didn't start a baseline")
        await self._spruceset(controller, f"baseline,0,{baseline_id}")
        return baseline_id


APP_URL = "https://app.spruceirrigation.com/"


async def sign_in(session: aiohttp.ClientSession, username: str, password: str) -> str:
    """Sign in as the Spruce web app does and return the account's API key. The session must keep cookies (Spruce's
    login is a PHP session); the password is sent the way the web app sends it (MD5) and is not kept."""
    import hashlib
    import time

    md5 = lambda text: hashlib.md5(text.encode()).hexdigest()  # noqa: E731 - Spruce's own scheme
    try:
        async with session.post(APP_URL + "ub_login_user.php", timeout=TIMEOUT, data={
            "ge_username": username.strip(), "ge_password": md5(password), "ge_loc": "user",
            "ge_rem_real": "0", "localStorage": "false",
        }) as resp:
            login = _json(await resp.text())
        if not isinstance(login, dict) or login.get("Ack") != "success":
            raise SpruceAuthError((login or {}).get("Msg") or "Spruce did not accept the username or password")
        verification = md5(f"nk9r6{60 * round(time.time() / 60)}")   # as the web app's session check
        async with session.get(APP_URL + "session.php", params={"verification": verification}, timeout=TIMEOUT) as resp:
            account = _json(await resp.text())
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
        raise SpruceError(f"Couldn't sign in to Spruce: {err}") from err
    key = (account or {}).get("api_key") if isinstance(account, dict) else None
    if not key:
        raise SpruceError("Signed in, but Spruce didn't return an API key")
    return key


def _json(text: str) -> Any:
    import json

    return json.loads(text)


def _dumps(value: Any) -> str:
    import json

    return json.dumps(value, separators=(",", ":"))


def _int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _columns_to_rows(data: Any, keys: tuple[str, ...]) -> list[dict]:
    """Spruce's history endpoints return one list per column; turn them into rows."""
    if not isinstance(data, dict) or not isinstance(data.get(keys[0]), list):
        return []
    columns = [data.get(k) or [None] * len(data[keys[0]]) for k in keys]
    return [dict(zip(keys, row)) for row in zip(*columns)]
