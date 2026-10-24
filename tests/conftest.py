"""Fixtures: a fake Spruce account with one controller, two zones and one moisture sensor (made-up data)."""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.util import dt as dt_util

CONTROLLER = "aa00bb11cc22dd33ee44ff55"
SENSOR = "000D6F0000ABCDEF"


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
        "controller": {CONTROLLER: {"hibernate_enabled": "0", "rain_delay_inches": "0.15", "freeze_temperature": "35"}},
        "schedule": {
            "11": {"schedule_name": "Morning", "schedule_type": "connected", "sched_enabled": "1",
                   "start_time": "06:00", "master_controller": CONTROLLER, "rain_delay_inches": "0.15",
                   "freeze_delay_enabled": "0", "days_select": "everyx", "days_var": "1",
                   "zone": {"1": {"duration_scaling": "100", "zonesched_enabled": "1"},
                            "2": {"duration_scaling": "80", "zonesched_enabled": "1"}},
                   "history": {(today - timedelta(days=1)).isoformat(): {
                       "is_scheduled": False, "message": "No watering needed today.", "epoch_time": epoch - 86400}},
                   "prediction": {
                       today.isoformat(): {"is_scheduled": False, "is_rain_delay": True, "epoch_time": epoch + 60,
                                           "message": "Schedule is skipping due to predicted rain."},
                       (today + timedelta(days=1)).isoformat(): {
                           "is_scheduled": True, "total_duration_seconds": 1200, "epoch_time": epoch + 86400,
                           "message": "Watering for 20 minutes.",
                           "zone": {"1": {"water_duration_minutes": 12}, "2": {"water_duration_minutes": 8}}},
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
    api.events.return_value = []
    return api


@pytest.fixture
def mock_api():
    api = fake_api()
    with patch("custom_components.spruce_irrigation.SpruceApi", return_value=api), \
         patch("custom_components.spruce_irrigation.config_flow.SpruceApi", return_value=api):
        yield api
