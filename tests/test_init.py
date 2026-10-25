"""Set-up, entities, control and services against the fake account."""
from __future__ import annotations

from datetime import timedelta

import pytest

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.spruce_irrigation.const import CONF_API_KEY, CONF_CONTROLLER, DOMAIN
from custom_components.spruce_irrigation.statistics import statistic_id

from .conftest import CONTROLLER, OUTLET


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """The recorder (for the water statistics) has to start before Home Assistant does."""
    yield


@pytest.fixture(autouse=True)
async def drain(hass: HomeAssistant):
    """Let background statistics imports started by a test finish before the recorder shuts down."""
    yield
    await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)


@pytest.fixture
def expected_lingering_timers() -> bool:
    """Commands schedule two confirming refreshes a few seconds later."""
    return True


@pytest.fixture
async def entry(hass: HomeAssistant, mock_api) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=CONTROLLER, title="Test Spruce",
                            data={CONF_API_KEY: "k", CONF_CONTROLLER: CONTROLLER})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)   # the water-history import into statistics
    await async_wait_recording_done(hass)
    return entry


def _entity(hass: HomeAssistant, unique: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(_domain(unique), DOMAIN, f"{CONTROLLER}_{unique}")
    assert entity_id, unique
    return entity_id


def _domain(unique: str) -> str:
    return {"zone_1_valve": "valve", "zone_2_valve": "valve", "online": "binary_sensor", "watering": "binary_sensor",
            "zone_1_flow_fault": "binary_sensor", "zone_2_flow_fault": "binary_sensor", "hibernate": "switch",
            "stop": "button", "zone_1_run_time": "number"}.get(unique, "sensor")


async def test_setup_and_unload(hass: HomeAssistant, entry) -> None:
    assert entry.state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_states(hass: HomeAssistant, entry) -> None:
    assert hass.states.get(_entity(hass, "zone_1_valve")).state == "open"
    assert hass.states.get(_entity(hass, "zone_2_valve")).state == "closed"
    assert er.async_get(hass).async_get_entity_id("valve", DOMAIN, f"{CONTROLLER}_zone_3_valve") is None   # disabled zone
    assert hass.states.get(_entity(hass, "status")).state == "watering"
    assert hass.states.get(_entity(hass, "active_zone")).state == "Front Lawn"
    assert hass.states.get(_entity(hass, "watering")).state == "on"
    assert hass.states.get(_entity(hass, "online")).state == "on"
    assert float(hass.states.get(_entity(hass, "water_today")).state) == pytest.approx(80 * 3.785411784, rel=1e-3)
    assert hass.states.get(_entity(hass, "zone_1_flow_fault")).state == "on"     # latest run measured no flow
    assert hass.states.get(_entity(hass, "zone_2_flow_fault")).state == "off"
    assert float(hass.states.get(_entity(hass, "000D6F0000ABCDEF_moisture")).state) == 31


async def test_valve_control(hass: HomeAssistant, entry, mock_api) -> None:
    valve = _entity(hass, "zone_2_valve")
    await hass.services.async_call("valve", "open_valve", {"entity_id": valve}, blocking=True)
    mock_api.zone_on.assert_awaited_with(CONTROLLER, 2, 600)
    await hass.services.async_call(DOMAIN, "run_zone", {"entity_id": valve, "minutes": 3}, blocking=True)
    mock_api.zone_on.assert_awaited_with(CONTROLLER, 2, 180)
    await hass.services.async_call("valve", "close_valve", {"entity_id": valve}, blocking=True)
    mock_api.zone_off.assert_awaited_with(CONTROLLER, 2)


async def test_run_time_number(hass: HomeAssistant, entry, mock_api) -> None:
    await hass.services.async_call("number", "set_value", {"entity_id": _entity(hass, "zone_1_run_time"), "value": 7},
                                   blocking=True)
    await hass.services.async_call("valve", "open_valve", {"entity_id": _entity(hass, "zone_1_valve")}, blocking=True)
    mock_api.zone_on.assert_awaited_with(CONTROLLER, 1, 420)


async def test_services(hass: HomeAssistant, entry, mock_api) -> None:
    await hass.services.async_call(DOMAIN, "run_schedule", {"schedule": "morning"}, blocking=True)
    mock_api.schedule_on.assert_awaited_with(CONTROLLER, 11)
    await hass.services.async_call(DOMAIN, "run_all_zones", {"minutes": 2}, blocking=True)
    mock_api.run_all.assert_awaited_with(CONTROLLER, 120)
    await hass.services.async_call(DOMAIN, "stop", {}, blocking=True)
    mock_api.stop.assert_awaited()
    await hass.services.async_call("switch", "turn_on", {"entity_id": _entity(hass, "hibernate")}, blocking=True)
    mock_api.hibernate.assert_awaited_with(CONTROLLER, True)


async def test_weather_statistics(hass: HomeAssistant, entry) -> None:
    rain, temp = statistic_id(CONTROLLER, None, "rain"), statistic_id(CONTROLLER, None, "temperature")
    start = dt_util.start_of_local_day(dt_util.now().date()) - timedelta(days=1)
    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, start, None, {rain, temp}, "hour", None, {"state", "sum", "min", "max"})
    assert [(r["state"], r["sum"]) for r in stats[rain]] == [(2.5, 2.5), (5.1, 7.6)]   # mm; today's later entry
    assert (stats[temp][0]["min"], stats[temp][0]["max"]) == (10.0, 30.0)               # °F -> °C


async def test_watering_plan(hass: HomeAssistant, entry) -> None:
    reg = er.async_get(hass)
    plan = reg.async_get_entity_id("calendar", DOMAIN, f"{CONTROLLER}_plan")
    state = hass.states.get(plan)
    assert state.state == "off"                                   # the next watering is tomorrow
    assert state.attributes["message"] == "Morning · 20 min"
    response = await hass.services.async_call(
        "calendar", "get_events", {"entity_id": plan, "duration": {"days": 3}}, blocking=True, return_response=True)
    summaries = [e["summary"] for e in response[plan]["events"]]
    assert summaries == ["Morning: skipped (predicted rain)", "Morning · 20 min"]   # "no watering needed" left out
    assert "Front Lawn: 12 min" in response[plan]["events"][1]["description"]

    nxt = hass.states.get(reg.async_get_entity_id("sensor", DOMAIN, f"{CONTROLLER}_next_watering"))
    assert nxt.attributes["schedule"] == "Morning" and nxt.attributes["minutes"] == 20
    assert nxt.attributes["zones"] == {"Front Lawn": 12, "Garden Beds": 8}
    assert hass.states.get(reg.async_get_entity_id("sensor", DOMAIN, f"{CONTROLLER}_watering_today")).state == "rain"
    enabled = reg.async_get_entity_id("switch", DOMAIN, f"{CONTROLLER}_schedule_11_enabled")
    assert hass.states.get(enabled).attributes["watering_days"] == "Every day"


async def test_rename_reloads(hass: HomeAssistant, entry, mock_api) -> None:
    valve = _entity(hass, "zone_2_valve")
    status = entry.runtime_data.status
    await status.async_refresh()                   # nothing changed: no reload
    assert entry.runtime_data.status is status
    mock_api.sensor_status.return_value = {}       # a sensor missing from one read: no reload
    await status.async_refresh()
    await hass.async_block_till_done()
    assert entry.runtime_data.status is status

    mock_api.controller_settings.return_value["zone"]["2"]["zone_name"] = "Vegetables"
    await entry.runtime_data.history.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.status is not status
    assert _entity(hass, "zone_2_valve") == valve   # same entity, new name
    assert "Vegetables" in hass.states.get(valve).attributes["friendly_name"]


async def test_schedule_controls(hass: HomeAssistant, entry, mock_api) -> None:
    reg = er.async_get(hass)
    enabled = reg.async_get_entity_id("switch", DOMAIN, f"{CONTROLLER}_schedule_11_enabled")
    assert hass.states.get(enabled).state == "on"
    assert reg.async_get_entity_id("switch", DOMAIN, f"{CONTROLLER}_schedule_12_enabled") is None   # manual: no settings
    # Connected schedule: skips are per zone; the switch is on only when every zone has it
    freeze = reg.async_get_entity_id("switch", DOMAIN, f"{CONTROLLER}_schedule_11_freeze_skip")
    assert hass.states.get(freeze).state == "on"
    wind = reg.async_get_entity_id("switch", DOMAIN, f"{CONTROLLER}_schedule_11_wind_skip")
    assert hass.states.get(wind).state == "off"
    assert hass.states.get(wind).attributes["zones_off"] == ["Garden Beds"]
    await hass.services.async_call("switch", "turn_on", {"entity_id": wind}, blocking=True)
    mock_api.save_schedule_zones.assert_awaited_with(
        CONTROLLER, 11, {1: {"wind_delay_enabled": "1"}, 2: {"wind_delay_enabled": "1"}})

    await hass.services.async_call("switch", "turn_off", {"entity_id": enabled}, blocking=True)
    mock_api.save_schedule_settings.assert_awaited_with(CONTROLLER, 11, {"sched_enabled": "0"}, None)
    mock_api.sync_schedule.assert_awaited_with(CONTROLLER, 11, False)

    start = reg.async_get_entity_id("time", DOMAIN, f"{CONTROLLER}_schedule_11_start_time")
    assert hass.states.get(start).state == "06:00:00"
    await hass.services.async_call("time", "set_value", {"entity_id": start, "time": "05:30"}, blocking=True)
    mock_api.save_schedule_settings.assert_awaited_with(
        CONTROLLER, 11, {"start_time": "05:30", "start_time_mode": "set"}, None)

    # Garden Beds is in the "Lawns" zone group with Front Lawn: the group changes together
    await hass.services.async_call(DOMAIN, "set_schedule_zone",
                                   {"schedule": "Morning", "zone": "Garden Beds", "watering_percent": 120,
                                    "rain_skip": False}, blocking=True)
    mock_api.save_schedule_zones.assert_awaited_with(
        CONTROLLER, 11, {1: {"duration_scaling": "120", "rain_delay_enabled": "0"},
                         2: {"duration_scaling": "120", "rain_delay_enabled": "0"}})

    # Weather thresholds are the controller's, shown in metric
    assert reg.async_get_entity_id("number", DOMAIN, f"{CONTROLLER}_schedule_11_rain_threshold") is None
    rain = reg.async_get_entity_id("number", DOMAIN, f"{CONTROLLER}_rain_skip_threshold")
    assert hass.states.get(rain).state == "3.81"                   # 0.15 in
    assert hass.states.get(rain).attributes["unit_of_measurement"] == "mm"
    await hass.services.async_call("number", "set_value", {"entity_id": rain, "value": 5}, blocking=True)
    mock_api.save_controller_settings.assert_awaited_with(CONTROLLER, {"rain_delay_inches": "0.197"})

    wind_limit = reg.async_get_entity_id("number", DOMAIN, f"{CONTROLLER}_wind_skip_threshold")
    assert hass.states.get(wind_limit).attributes["unit_of_measurement"] == "km/h"
    await hass.services.async_call("number", "set_value", {"entity_id": wind_limit, "value": 32}, blocking=True)
    mock_api.save_controller_settings.assert_awaited_with(CONTROLLER, {"wind_mph": "20"})

    freeze_temp = reg.async_get_entity_id("number", DOMAIN, f"{CONTROLLER}_freeze_temperature")
    await hass.services.async_call("number", "set_value", {"entity_id": freeze_temp, "value": 1}, blocking=True)
    mock_api.save_controller_settings.assert_awaited_with(CONTROLLER, {"freeze_temperature": "34"})   # 1 °C = 33.8 °F


async def test_controller_settings(hass: HomeAssistant, entry, mock_api) -> None:
    reg = er.async_get(hass)
    max_time = reg.async_get_entity_id("number", DOMAIN, f"{CONTROLLER}_manual_max_time")
    assert hass.states.get(max_time).state == "10.0"
    await hass.services.async_call("number", "set_value", {"entity_id": max_time, "value": 16}, blocking=True)
    mock_api.save_controller_settings.assert_awaited_with(CONTROLLER, {"manual_max_time": "16"})
    mock_api.refresh_settings.assert_awaited_with(CONTROLLER)   # the controller uses it: told to fetch settings

    leak = reg.async_get_entity_id("switch", DOMAIN, f"{CONTROLLER}_flow_leak_burst")
    assert hass.states.get(leak).state == "on"
    await hass.services.async_call("switch", "turn_off", {"entity_id": leak}, blocking=True)
    mock_api.save_zone_settings.assert_awaited_with(
        CONTROLLER, {101: {"flow_idle_leak_big_enabled": "0"}, 102: {"flow_idle_leak_big_enabled": "0"}})

    high = reg.async_get_entity_id("number", DOMAIN, f"{CONTROLLER}_flow_limit_high")
    await hass.services.async_call("number", "set_value", {"entity_id": high, "value": 45}, blocking=True)
    mock_api.save_zone_settings.assert_awaited_with(
        CONTROLLER, {101: {"flow_limit_high": "45"}, 102: {"flow_limit_high": "45"}})

    # Last: enabling a zone changes the zone layout, so the integration reloads itself
    zone3 = reg.async_get_entity_id("switch", DOMAIN, f"{CONTROLLER}_zone_3_enabled")
    assert hass.states.get(zone3).state == "off"
    await hass.services.async_call("switch", "turn_on", {"entity_id": zone3}, blocking=True)
    mock_api.save_zone_settings.assert_awaited_with(CONTROLLER, {3: {"zenabled": "1"}})


async def test_outlet_and_pause(hass: HomeAssistant, entry, mock_api) -> None:
    reg = er.async_get(hass)
    outlet = reg.async_get_entity_id("switch", DOMAIN, f"{CONTROLLER}_{OUTLET}_outlet")
    assert hass.states.get(outlet).state == "off"
    await hass.services.async_call("switch", "turn_on", {"entity_id": outlet}, blocking=True)
    mock_api.outlet_on.assert_awaited_with(CONTROLLER, OUTLET)
    silent = reg.async_get_entity_id("binary_sensor", DOMAIN, f"{CONTROLLER}_{OUTLET}_silent")
    assert hass.states.get(silent).state == "on"                 # last report 30 days ago

    pause = reg.async_get_entity_id("button", DOMAIN, f"{CONTROLLER}_pause_until_tomorrow")
    await hass.services.async_call("button", "press", {"entity_id": pause}, blocking=True)
    mock_api.hibernate.assert_awaited_with(CONTROLLER, True, dt_util.now().date() + timedelta(days=1))
    hibernate = reg.async_get_entity_id("switch", DOMAIN, f"{CONTROLLER}_hibernate")
    assert hass.states.get(hibernate).attributes["until"] == (dt_util.now().date() + timedelta(days=1)).isoformat()


async def test_us_units_and_cost(hass: HomeAssistant, mock_api) -> None:
    from custom_components.spruce_irrigation.const import CONF_PRICE, CONF_UNITS

    entry = MockConfigEntry(domain=DOMAIN, unique_id=CONTROLLER, title="Test Spruce",
                            data={CONF_API_KEY: "k", CONF_CONTROLLER: CONTROLLER},
                            options={CONF_UNITS: "us", CONF_PRICE: 5.0})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)
    reg = er.async_get(hass)
    today = hass.states.get(reg.async_get_entity_id("sensor", DOMAIN, f"{CONTROLLER}_water_today"))
    assert today.attributes["unit_of_measurement"] == "gal" and float(today.state) == pytest.approx(80, rel=1e-3)
    rain = hass.states.get(reg.async_get_entity_id("number", DOMAIN, f"{CONTROLLER}_rain_skip_threshold"))
    assert rain.attributes["unit_of_measurement"] == "in" and float(rain.state) == 0.15
    cost = hass.states.get(reg.async_get_entity_id("sensor", DOMAIN, f"{CONTROLLER}_water_cost_today"))
    assert float(cost.state) == pytest.approx(80 / 1000 * 5, abs=0.01)   # $5 per 1000 gal


def _sensor(hass: HomeAssistant, unique: str):
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{CONTROLLER}_{unique}")
    assert entity_id, unique
    return hass.states.get(entity_id)


async def test_baselines(hass: HomeAssistant, entry, mock_api) -> None:
    from custom_components.spruce_irrigation.repairs import BaselineReviewFlow

    issues = ir.async_get(hass)
    # The first baseline seen is taken as it is
    assert float(_sensor(hass, "zone_1_baseline_flow").state) == pytest.approx(30.28, abs=0.01)   # 8 gpm
    history = _sensor(hass, "last_baselined").attributes["baselines"]
    assert [b["id"] for b in history] == ["7"] and history[0]["zones"]["Front Lawn"] == pytest.approx(30.28, abs=0.01)
    assert _sensor(hass, "baseline_review").state == "0"
    assert _sensor(hass, "zone_1_flow_check").state == "no_flow"     # last run measured nothing
    assert _sensor(hass, "zone_2_flow_check").state == "ok"
    assert _sensor(hass, "zone_1_design_flow").state == "unknown"     # no heads entered yet
    assert issues.async_get_issue(DOMAIN, f"repeaters_silent_{CONTROLLER}")   # the outlet: 30 days silent
    assert issues.async_get_issue(DOMAIN, f"no_flow_at_baseline_{CONTROLLER}") is None

    # Heads in zone 1: 8 MP2000 at 90° = 8 × 5.6 / 4 = 11.2 L/min design flow
    await hass.services.async_call(DOMAIN, "set_zone_heads", {"zone": "Front Lawn", "heads": [
        {"model": "Hunter MP Rotator MP2000", "arc": 90, "quantity": 8}]}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    design = _sensor(hass, "zone_1_design_flow")
    assert float(design.state) == pytest.approx(11.2)
    assert design.attributes["heads"] == ["8 × Hunter MP Rotator MP2000 at 90°: 11.2 L/min"]
    assert design.attributes["precipitation_rate_mm_h"] == pytest.approx(10.0, abs=0.1)

    # A new baseline: zone 1 is 35% over its design flow (needs review), zone 2 within 25% of its last one
    mock_api.current_baseline.return_value = {
        "1": {"id": "8", "zone": "1", "gpm": 4.0, "DTepoch": str(int(dt_util.utcnow().timestamp()))},
        "2": {"id": "8", "zone": "2", "gpm": 2.2, "DTepoch": str(int(dt_util.utcnow().timestamp()))},
    }
    rt = hass.config_entries.async_get_entry(entry.entry_id).runtime_data
    await rt.history.async_refresh()
    await hass.async_block_till_done()
    review = _sensor(hass, "baseline_review")
    assert review.state == "1" and list(review.attributes["zones"]) == ["Front Lawn"]
    assert issues.async_get_issue(DOMAIN, f"baseline_review_{CONTROLLER}").is_fixable
    assert rt.baselines.reference(2) == pytest.approx(2.2 * 3.785411784, abs=0.001)   # accepted
    assert rt.baselines.reference(1) == pytest.approx(30.28, abs=0.01)                # still the previous one

    # Repairs: keep the previous value for zone 1 (nothing ticked)
    flow = BaselineReviewFlow(entry.entry_id)
    flow.hass, flow.flow_id, flow.handler = hass, "test", DOMAIN
    form = await flow.async_step_init()
    assert form["type"] is FlowResultType.FORM and "Front Lawn" in form["description_placeholders"]["zones"]
    done = await flow.async_step_init({"accept": []})
    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert _sensor(hass, "baseline_review").state == "0"
    assert issues.async_get_issue(DOMAIN, f"baseline_review_{CONTROLLER}") is None
    assert rt.baselines.latest["review"][1] == "kept_previous"

    # An old baseline from paper (in gpm), and a new baseline run
    await hass.services.async_call(DOMAIN, "record_baseline", {
        "date": "2025-03-14", "flows": {"1": 16.073, "Garden Beds": 4.365}, "unit": "gal/min", "note": "paper"},
        blocking=True)
    history = _sensor(hass, "last_baselined").attributes["baselines"]
    assert [b["id"] for b in history] == ["8", "7", "manual-2025-03-14"]
    assert history[2]["source"] == "manual" and history[2]["zones"]["Front Lawn"] == pytest.approx(60.84, abs=0.01)

    run = er.async_get(hass).async_get_entity_id("button", DOMAIN, f"{CONTROLLER}_run_baseline")
    await hass.services.async_call("button", "press", {"entity_id": run}, blocking=True)
    mock_api.start_baseline.assert_awaited_with(CONTROLLER)


async def test_zone_heads_flow(hass: HomeAssistant, entry) -> None:
    """Add a head model of your own, then a zone's heads through the subentry forms."""
    model = await hass.config_entries.subentries.async_init((entry.entry_id, "head_model"), context={"source": "user"})
    assert model["type"] is FlowResultType.FORM
    form = await hass.config_entries.subentries.async_configure(model["flow_id"], {"start_from": "builtin:rainbird_xb10"})
    assert form["step_id"] == "model"
    made = await hass.config_entries.subentries.async_configure(form["flow_id"], {
        "brand": "Acme", "model": "Dripper 6", "kind": "per_emitter", "flow": 6})
    assert made["type"] is FlowResultType.CREATE_ENTRY and made["title"] == "Acme Dripper 6"
    await hass.async_block_till_done(wait_background_tasks=True)
    entry = hass.config_entries.async_get_entry(entry.entry_id)
    custom = next(k for k, s in entry.subentries.items() if s.subentry_type == "head_model")

    flow = await hass.config_entries.subentries.async_init((entry.entry_id, "zone_heads"), context={"source": "user"})
    menu = await hass.config_entries.subentries.async_configure(flow["flow_id"], {"zone": "2"})
    assert menu["type"] is FlowResultType.MENU
    form = await hass.config_entries.subentries.async_configure(menu["flow_id"], {"next_step_id": "add_head"})
    menu = await hass.config_entries.subentries.async_configure(form["flow_id"], {
        "model": f"custom:{custom}", "arc": 360, "quantity": 20})
    assert "2.00" in menu["description_placeholders"]["total"]        # 20 × 6 L/h = 2 L/min
    saved = await hass.config_entries.subentries.async_configure(menu["flow_id"], {"next_step_id": "save"})
    assert saved["type"] is FlowResultType.CREATE_ENTRY and saved["title"] == "Garden Beds"
    await hass.async_block_till_done(wait_background_tasks=True)
    assert float(_sensor(hass, "zone_2_design_flow").state) == pytest.approx(2.0)


async def test_schedule_editing(hass: HomeAssistant, entry, mock_api) -> None:
    reg = er.async_get(hass)
    uid = lambda domain, key: reg.async_get_entity_id(domain, DOMAIN, f"{CONTROLLER}_schedule_11_{key}")  # noqa: E731

    days = uid("select", "days")
    assert hass.states.get(days).state == "every_day"
    await hass.services.async_call("select", "select_option", {"entity_id": days, "option": "even_days"}, blocking=True)
    mock_api.save_schedule_settings.assert_awaited_with(CONTROLLER, 11, {"days_select": "even_odd", "days_var": "even"}, None)
    mock_api.sync_schedule.assert_awaited_with(CONTROLLER, 11, True)

    mode = uid("select", "start_mode")
    assert hass.states.get(mode).state == "set"
    await hass.services.async_call("select", "select_option", {"entity_id": mode, "option": "sunrise"}, blocking=True)
    mock_api.save_schedule_settings.assert_awaited_with(CONTROLLER, 11, {"start_time_mode": "rel-sunrise"}, None)
    await hass.services.async_call("number", "set_value", {"entity_id": uid("number", "start_offset"), "value": -30},
                                   blocking=True)
    mock_api.save_schedule_settings.assert_awaited_with(CONTROLLER, 11, {"start_time_rel": "-30"}, None)

    window = uid("switch", "window")
    assert hass.states.get(window).state == "off"
    await hass.services.async_call("switch", "turn_on", {"entity_id": window}, blocking=True)
    mock_api.save_schedule_settings.assert_awaited_with(
        CONTROLLER, 11, {"time_restr1_enabled": "1", "time_restr1_start": "22:00", "time_restr1_end": "06:00"}, None)

    await hass.services.async_call(DOMAIN, "set_schedule_days", {"schedule": "Morning", "weekdays": ["mon", "wed", "fri"]},
                                   blocking=True)
    mock_api.save_schedule_settings.assert_awaited_with(CONTROLLER, 11, {"days_select": "days", "days_var": "0101010"}, None)

    await hass.services.async_call(DOMAIN, "set_schedule_zone", {
        "schedule": "Morning", "zone": "Lawns", "mode": "fixed", "cycles": "2", "weekly_max": 3}, blocking=True)
    values = {"schedule_mode": "fixed", "cycles": "2", "weekly_safeguard_max": "3"}
    mock_api.save_schedule_zones.assert_awaited_with(CONTROLLER, 11, {1: values, 2: values})

    await hass.services.async_call(DOMAIN, "create_schedule", {"name": "Test", "type": "manual"}, blocking=True)
    mock_api.create_schedule.assert_awaited_with(CONTROLLER, "manual", "Test")
    await hass.services.async_call(DOMAIN, "delete_schedule", {"schedule": "Morning"}, blocking=True)
    mock_api.delete_schedule.assert_awaited_with(CONTROLLER, 11)
    await hass.services.async_call(DOMAIN, "rename_schedule", {"schedule": "Morning", "name": "Dawn"}, blocking=True)
    mock_api.save_schedule_settings.assert_awaited_with(CONTROLLER, 11, {"schedule_name": "Dawn"}, None)
    await hass.async_block_till_done(wait_background_tasks=True)   # a new name reloads the integration


async def test_zone_plan_and_schedule_day(hass: HomeAssistant, entry, mock_api) -> None:
    nxt = _sensor(hass, "zone_1_next_watering")
    assert nxt.state not in ("unknown", "unavailable")
    assert nxt.attributes["schedule"] == "Morning" and nxt.attributes["minutes"] == 12
    assert nxt.attributes["cycles"] == 2 and nxt.attributes["message"] == "Watering for 12 minutes."
    assert nxt.attributes["water_budget"]["needed_mm"] == 20.3 and nxt.attributes["water_budget"]["soil_water_mm"] == 12.7

    day = (dt_util.now().date() - timedelta(days=1))
    mock_api.schedule_day.return_value = {"1": {"zone_label": "Front Lawn", "duration": [600], "gpm": [8.0]}}
    result = await hass.services.async_call(DOMAIN, "get_schedule_day", {"schedule": "Morning", "date": day.isoformat()},
                                            blocking=True, return_response=True)
    assert result["schedule"] == "Morning" and result["message"] == "No watering needed today."
    assert result["ran"]["Front Lawn"]["duration"] == [600]
    mock_api.schedule_day.assert_awaited_with(11, day)

    await hass.services.async_call(DOMAIN, "set_schedule_zone", {"schedule": "Morning", "zone": "1",
                                                                 "moisture_threshold": 28}, blocking=True)
    mock_api.save_schedule_zones.assert_awaited_with(CONTROLLER, 11, {1: {"moisture_setpoint": "28"},
                                                                     2: {"moisture_setpoint": "28"}})
