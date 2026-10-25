"""Fixtures: a fake Spruce account with one controller, two zones and one moisture sensor (made-up data)."""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.util import dt as dt_util

CONTROLLER = "aa00bb11cc22dd33ee44ff55"
SENSOR = "000D6F0000ABCDEF"
OUTLET = "000D6FFFFE000001"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def fake_api() -> AsyncMock:
    today = dt_util.now().date()
    epoch = int(dt_util.utcnow().timestamp())
    api = AsyncMock()
    api.controllers.return_value = {CONTROLLER: {"controller_name": "Test Spruce"}}
    api.controller_settings.return_value = {
        "controller_name": "Test Spruce", "model_number": "PS-SPRWIFI16-01", "firmware": "2.740",
        "serial_number": "CA0000000", "date_added": (today - timedelta(days=10)).isoformat() + " 00:00:00",
        "zone": {
            "1": {"zone_name": "Front Lawn", "zenabled": "1", "gpm": "8", "nozzle_type": "rotary_nozzle",
                  "landscape_type": "grass", "soil_type": "loam", "sensor": SENSOR},
            "2": {"zone_name": "Garden Beds", "zenabled": "1", "gpm": "2", "nozzle_type": "drip"},
            "3": {"zone_name": "Unused", "zenabled": "0"},
        },
    }
    api.settings.return_value = {
        "controller": {CONTROLLER: {
            "hibernate_enabled": "0", "rain_delay_inches": "0.15", "freeze_temperature": "35", "wind_mph": "15",
            "manual_max_time": "10", "num_zones": "3",
            "climate_adjust_rules": "100,100,90,80,60,50,50,60,70,80,90,100",
            "zone_group": {"1": {"grouped_zones": "1;2;", "zone_group_name": "Lawns", "sensor_in_zone": "1"}},
            "zone": {"101": {"custom_flow_enabled": "0", "flow_limit_high": "40", "flow_limit_low": "-30",
                             "flow_stop_enabled": "0", "flow_pump_protect_enabled": "1",
                             "flow_idle_leak_small_enabled": "1", "flow_idle_leak_big_enabled": "1"}}}},
        "device": {OUTLET: {"device_name": "Porch plug", "device_type": "Outlet", "model_number": "TRADFRI signal repeater",
                            "gateway": CONTROLLER}},
        "schedule": {
            "11": {"schedule_name": "Morning", "schedule_type": "connected", "sched_enabled": "1",
                   "start_time": "06:00", "master_controller": CONTROLLER, "rain_delay_inches": "0.15",
                   "freeze_delay_enabled": "0", "days_select": "everyx", "days_var": "1",
                   "zone": {"1": {"duration_scaling": "100", "zonesched_enabled": "1"},
                            "2": {"duration_scaling": "80", "zonesched_enabled": "1", "wind_delay_enabled": "0"}},
                   "history": {(today - timedelta(days=1)).isoformat(): {
                       "is_scheduled": False, "message": "No watering needed today.", "epoch_time": epoch - 86400}},
                   "prediction": {
                       today.isoformat(): {"is_scheduled": False, "is_rain_delay": True, "epoch_time": epoch + 60,
                                           "message": "Schedule is skipping due to predicted rain."},
                       (today + timedelta(days=1)).isoformat(): {
                           "is_scheduled": True, "total_duration_seconds": 1200, "epoch_time": epoch + 86400,
                           "message": "Watering for 20 minutes.",
                           "zone": {"1": {"water_duration_minutes": 12, "cycles": 2, "message": "Watering for 12 minutes.",
                                          "water_budget": {"projected_water_balance": 0.5, "water_needed_inches": 0.8,
                                                           "is_invalid": False}},
                                    "2": {"water_duration_minutes": 8}}},
                       "average": {}}},
            "12": {"schedule_name": "Manual", "schedule_type": "manual", "sched_enabled": "1",
                   "master_controller": CONTROLLER},
        },
    }
    api.zone_status.return_value = {
        "0": {"zone_state": 0},
        "1": {"zone_state": 1, "epoch_time": epoch - 60, "duration": 600, "schedule_id": 0},
        "2": {"zone_state": 0, "epoch_time": epoch - 86400, "duration": 300, "schedule_id": 11},
        "101": {"zone_state": 1},
    }
    api.sensor_status.return_value = {
        SENSOR: {"sensor_name": "Front Lawn", "moisture": 31, "temperature": 70, "battery": "2.9", "signal": -60,
                 "moisture_DTepoch": epoch - 600, "latest_DTepoch": epoch - 600},
    }
    api.controller_status.return_value = {"current_state": 1, "epoch_time": epoch - 30}
    api.run_status.return_value = {"pause": 0, "pump_delay": 0, "cycle_soak": 0}
    api.network.return_value = {"channel": "6", "signal": -50, "ip": "192.0.2.10"}
    api.water_summary.return_value = [
        {"ymd": today.isoformat(), "zone": 1, "schedule_id": 0, "schedule": "Manual", "schedule_type": "Manual",
         "minutes": 10, "gallons": 80},
        {"ymd": (today - timedelta(days=1)).isoformat(), "zone": 2, "schedule_id": 11, "schedule": "Morning",
         "schedule_type": "Connected", "minutes": 5, "gallons": 10},
    ]
    api.gpm_data.return_value = (
        [{"DTepoch": epoch - 86400 * d, "zone": 1, "gpm": 8.0} for d in range(10, 2, -1)]
        + [{"DTepoch": epoch - 3600, "zone": 1, "gpm": 0.0}]                 # latest run of zone 1: no flow
        + [{"DTepoch": epoch - 86400, "zone": 2, "gpm": 2.0}]
    )
    api.weather_history.return_value = [   # yesterday, and today listed twice
        {"ymd": (today - timedelta(days=1)).isoformat(), "precip_in": 0.1, "temp_min": 50, "temp_max": 86},
        {"ymd": today.isoformat(), "precip_in": 0.1, "temp_min": 59, "temp_max": 77},
        {"ymd": today.isoformat(), "precip_in": 0.2, "temp_min": 59, "temp_max": 77},
    ]
    api.events.return_value = []
    api.current_baseline.return_value = {   # Spruce's latest flow baseline, 5 days ago
        "1": {"id": "7", "zone": "1", "gpm": 8.0, "DTepoch": str(epoch - 86400 * 5)},
        "2": {"id": "7", "zone": "2", "gpm": 2.0, "DTepoch": str(epoch - 86400 * 5)},
    }
    api.start_baseline.return_value = 8
    api.start_live_flow.return_value = 3313
    api.live_flow.return_value = [{"gpm": 0, "DTepoch": epoch - 4}, {"gpm": 4.0, "DTepoch": epoch - 2},
                                  {"gpm": 4.2, "DTepoch": epoch}]
    api.zone_status_stream.return_value = 'retry: 400' + chr(10) + 'data: {"error":false}' + chr(10)
    api.forecast.return_value = {
        "api": "weatherkit",
        "daily": {"data": [{"forecastStart": "2026-10-26T13:00:00Z", "conditionCode": "Rain", "temperatureMax": 25.0,
                            "temperatureMin": 12.0, "precipitationAmount": 6.5, "precipitationChance": 0.8}]},
        "hourly": {"data": [{"forecastStart": "2026-10-26T01:00:00Z", "conditionCode": "MostlyClear", "daylight": True,
                             "temperature": 21.5, "humidity": 0.55, "windSpeed": 12.0, "pressure": 1013.0}]},
    }
    api.outlet_status.return_value = {OUTLET: {"device_id": OUTLET, "current_state": "0", "epoch_time": str(epoch - 86400 * 30),
                                               "duration": "0", "schedule_id": "0", "signal_strength": "-70"}}
    return api


@pytest.fixture
def mock_api():
    api = fake_api()
    with patch("custom_components.spruce_irrigation.SpruceApi", return_value=api), \
         patch("custom_components.spruce_irrigation.config_flow.SpruceApi", return_value=api):
        yield api
