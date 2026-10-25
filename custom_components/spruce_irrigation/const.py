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
CONF_REVIEW_TOLERANCE = "review_tolerance"
DEFAULT_REVIEW_TOLERANCE = 0.25     # a new baseline more than 25% from design / the accepted one needs review

# Options: units and water price
CONF_UNITS = "units"                # metric (default) / us / custom
CONF_UNIT_VOLUME = "unit_volume"
CONF_UNIT_FLOW = "unit_flow"
CONF_UNIT_RAIN = "unit_rain"
CONF_UNIT_TEMPERATURE = "unit_temperature"
CONF_UNIT_WIND = "unit_wind"
CONF_PRICE = "water_price"          # per kL (metric volume) or per 1000 gal (US volume)
CONF_PRICE_ENTITY = "water_price_entity"
UNITS_METRIC, UNITS_US, UNITS_CUSTOM = "metric", "us", "custom"

HISTORY_DAYS = 7                    # water per day re-read each history poll (Spruce fills in late)
EVENT_LOOKBACK = timedelta(days=2)
SENSOR_STALE = timedelta(days=2)    # a moisture sensor silent this long is shown unavailable
WORKING_GPM = 0.05
LITRES_PER_GALLON = 3.785411784
MM_PER_INCH = 25.4
FLOW_PORTS = (101, 102)             # flow-meter ports appear as zones 101 and 102 in Spruce's settings
OUTLET_SILENT = timedelta(days=7)
BASELINE_STALE = timedelta(days=365)  # a flow baseline older than this is reported   # a Zigbee outlet / repeater not heard from for this long is reported

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
SERVICE_SET_SCHEDULE_ZONE = "set_schedule_zone"
SERVICE_PAUSE_SCHEDULES = "pause_schedules"
SERVICE_CREATE_SCHEDULE = "create_schedule"
SERVICE_DELETE_SCHEDULE = "delete_schedule"
SERVICE_RENAME_SCHEDULE = "rename_schedule"
SERVICE_SET_SCHEDULE_DAYS = "set_schedule_days"
SERVICE_RUN_BASELINE = "run_baseline"
SERVICE_SCHEDULE_DAY = "get_schedule_day"
SERVICE_RECORD_BASELINE = "record_baseline"
SERVICE_REVIEW_BASELINE = "review_baseline"
SERVICE_SET_ZONE_HEADS = "set_zone_heads"

STRATEGY_FILE = "spruce-irrigation-strategy.js"   # the dashboard, in frontend/
STRATEGY_URL = f"/{DOMAIN}/{STRATEGY_FILE}"
