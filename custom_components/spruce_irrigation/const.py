"""Constants for the Spruce Irrigation integration."""
from datetime import timedelta

DOMAIN = "spruce_irrigation"
MANUFACTURER = "Plaid Systems (Spruce)"

CONF_API_KEY = "api_key"
CONF_CONTROLLER = "controller_id"

# Options
CONF_DEFAULT_RUN_MINUTES = "default_run_minutes"
CONF_STATUS_INTERVAL = "status_interval"
CONF_HISTORY_INTERVAL = "history_interval"
CONF_FAULT_SHARE = "fault_share"
DEFAULT_RUN_MINUTES = 10
DEFAULT_STATUS_INTERVAL = 60        # seconds: zone status, run status, moisture
DEFAULT_HISTORY_INTERVAL = 900      # seconds: water per day, measured flow, activity log, settings
DEFAULT_FAULT_SHARE = 0.2           # a run measuring under 20% of the zone's usual flow is a flow fault
MIN_STATUS_INTERVAL = 30

HISTORY_DAYS = 7                    # water per day re-read each history poll (Spruce fills in late)
EVENT_LOOKBACK = timedelta(days=2)
SENSOR_STALE = timedelta(days=2)    # a moisture sensor silent this long is shown unavailable
WORKING_GPM = 0.05
LITRES_PER_GALLON = 3.785411784

EVENT_SPRUCE = f"{DOMAIN}_event"    # fired for each new activity-log entry

SERVICE_RUN_ZONE = "run_zone"
SERVICE_STOP_ZONE = "stop_zone"
SERVICE_RUN_ALL = "run_all_zones"
SERVICE_RUN_SCHEDULE = "run_schedule"
SERVICE_STOP_SCHEDULE = "stop_schedule"
SERVICE_STOP = "stop"
SERVICE_HIBERNATE = "set_hibernate"
SERVICE_PAIR_SENSOR = "pair_sensor"
SERVICE_IMPORT_HISTORY = "import_history"
