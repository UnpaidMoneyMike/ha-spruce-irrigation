# Spruce web app reference

Everything the Spruce web app (app.spruceirrigation.com, Gen2 Wi-Fi controller) can show and do, collected
October 2026 from the app's own code (`app.js`) and its live screens, and compared with this integration. It is
the basis for planning later releases; nothing here is a commitment.

Status column: **Have** = in the integration today; **Part** = some of it; **No** = not yet.

---

## 1. How the app talks to Spruce

Base URL `https://api.spruceirrigation.com/`. Every request carries the account's API key in the
`Authorization` header (no `Bearer`). Form-encoded bodies for POST/PUT, query strings for GET/DELETE.

| Path prefix | Used for |
|---|---|
| `app/api/` | Almost everything ("internal" requests) |
| `app/device/` | Talking to the controller itself (relayed through the cloud); adds `device_id` and `gateway` |
| `app/auth/auth.php/` | Account: login, profile, password, registration |
| `app/admin/` | Spruce staff only (look up users) — not for us |
| `v1/` | Older public API |
| `v2/` | Public API (`v2/controllers`, `v2/controller_settings`, used by this integration) |
| `schedule/runlist` | Calculated run list for a schedule (`deviceid`, `scheduleid`, `run=false`) |

Answers are JSON. Errors come back as `{"error": true, "message": "..."}` with HTTP 200; slow history
requests sometimes give 504 (retry, smaller date ranges).

### 1.1 `app/api/` endpoints

| Method | Endpoint | Parameters | What it returns / does | Status |
|---|---|---|---|---|
| GET | `settings` | – | **Everything**: `global`, `controller`, `schedule`, `sensor`, `device` (see §3) | Have |
| POST | `settings` | `settings` = JSON of changed records | Saves any setting (controller, zone, schedule, sensor, notifications) | Part (schedules, freeze, hibernate) |
| GET | `zone_status` | `deviceid` | Per zone: state, start epoch, duration, schedule id | Have |
| POST | `zone_state` | `deviceid`, `zone_states` | App pushes zone states it read from the controller | No (not needed) |
| GET | `controller_status` | `controller_id` | Online state, last message epoch | Have |
| GET | `outlet_status` | `controller_id` | Zigbee smart outlets / extenders: `current_state`, `epoch_time`, `duration`, `schedule_id`, `signal_strength` | No |
| GET | `is_online` | – | Spruce cloud reachable | No |
| GET | `sensor_status` | `device_id` | Per moisture sensor: moisture, temperature, battery, signal, each with its own epoch | Have |
| GET | `sensor_data` | `sensor_id`, `sensor_id2`, `start_date`, `end_date`, `timezone`, `controller_id`, `zone` | One or two sensors' history: `moisture` {`moisture`[], `DTepoch`[]}, `temperature` {…}, plus min/max of each | No |
| GET | `chart_data` | `controller_id`, `start_epoch`, `end_epoch`, `zone` (`%` = all), `sensor_id` (`%` = all) | The Places charts. Keyed by sensor id: `moisture`, `temperature` points (`x` ms, `y`). Keyed by zone: `et` (daily soil-water balance points, subtypes `weather` / `zone` / `initialize`), `watering` (`watering_on` / `watering_off` with `seconds`, subtype = schedule id), `aggregated_watering`. Plus `weather`: `precipitation` and `weather_temperature` (`y` high, `y0` low) per day | No |
| GET | `water_summary` | `deviceid`, `start_date`, `end_date` | Water per day per zone per schedule (minutes, gallons) | Have |
| GET | `gpm_data` | `deviceid`, `start_date`, `end_date` | Measured flow of each zone run | Have |
| GET | `weather_history` | `deviceid`, `start_date`, `end_date` | Daily rain (in), low/high temperature (°F) | Have (0.5.0) |
| GET | `weather_forecast` | `controller_id` | Apple WeatherKit forecast: hourly and daily (rain amount/chance, temperature, wind, humidity, UV, sunrise/sunset, condition) | No |
| GET | `events` | `deviceid`, `start_epoch`, `end_epoch` | Activity log (see §6) | Have (as HA events) |
| GET | `schedule_history_zone_state` | `schedule_id`, `date` | What actually ran on a past schedule day, per zone: `start_time`[], `zone_label`, `duration`[], `gpm`[], `text`[], `zone_total_duration`, `zone_total_gallons` | No |
| POST | `schedule` | `schedule_type`, `master_controller` | Create a schedule (returns its id) | No |
| DELETE | `schedule/<id>` | – | Delete a schedule (then device `delete,<id>`) | No |
| PUT | `zone_group` | `deviceid`, `old_zg`, `new_zg`, `zg_name`, `sensor_in_zone` | Create / change a zone group ("Place") | No |
| GET | `default_climate_rules` | `latitude` | Recommended monthly climate-adjust percentages for the location | No |
| GET | `flow_meter_models` | – | Flow meter models with k / offset | Have (not used) |
| POST | `baseline` | `deviceid`, `current`, `flow`, `notify` | Start a flow baseline (then device `baseline,0,<id>`) | No |
| GET | `current_baseline` | `deviceid`, `type=flow` | Per zone: baseline `id`, `gpm`, `DTepoch` (when baselined); limits = gpm × deviation % | No |
| POST | `live_log_flow` | `deviceid`, `port` | Start live flow logging (then device `flowlog,0,<id>`) | No |
| POST | `start_learn` / `stop_learn` | `deviceid` | Sensor "learn mode" (learns a zone's moisture response) | No |
| POST | `claim_device` | `deviceid`, `uid`, `nickname`, `serial_number` | Add a controller to the account | No (setup only) |
| DELETE | `device/<id>` | `delete_now` | Remove a controller / sensor / device from the account | No (deliberately) |
| GET | `user` (auth) | – | Profile: user id, username, email, names | No |
| POST | `update_profile`, `update_password`, `register`, `forgot_password`, `resend_activation` (auth) | | Account management | No (out of scope) |
| POST | `record_login`, `error_report` | | App telemetry | No |

`schedule/runlist` (`deviceid`, `scheduleid`, `run=false`) returns today's run list, `run_today`,
`run_length`, `status_message`, and the whole plan keyed by date (same day fields as `prediction`).

### 1.2 `app/device/` (the controller itself)

Every request needs `device_id` and `gateway` (`wifi` for Gen2), or it answers "gateway is missing". Results are
comma-separated strings in `result`.

| Method | Endpoint | Returns / does | Status |
|---|---|---|---|
| POST | `spruce` with `spruceset=<command>` | Sends a command (§2); `return_value` 1 = accepted | Have (several) |
| GET | `run_status` | `pause,pump_delay,cycle_soak,…` seconds left | Have |
| GET | `zone_status` | Live zone state from the controller | No (cloud copy used) |
| GET | `network_var` | Wi-Fi: SSID, channel, power, IP, MAC | Have (signal, IP) |
| GET | `mesh_var` | Zigbee mesh: status, mode, channel, PAN, power, firmware | No |
| GET | `wifi_var` | Wi-Fi networks stored on the controller | No |
| POST | `initialize`, `verifyAPI` | Set-up wizard checks | No (setup only) |

### 1.3 Live streams (`app/stream/`, server-sent events)

The app opens these with `EventSource` while a screen is visible, and also polls every 60 s as a fallback.
Each answers `retry: 400` and `data: {...}`; with nothing changing it sends `{"error":false}` and closes
(the browser reconnects), so it behaves as a fast long-poll.

| Stream | Pushes | Used by | Status |
|---|---|---|---|
| `zone-status-stream.php` | `zone_data` per zone when a zone changes | Control / running screen | No — would let HA see zone changes within a second instead of a 60 s poll |
| `outlet-status-stream.php` | `status` per outlet | Other Devices | No |
| `live-logging-flow.php` | Live flow readings | Flow Meter → live logging | No |
| `listen-device-stream.php` | A device found while pairing | Add Device | No |

## 2. Controller commands (`spruceset`)

From the app's command table (Wi-Fi gateway form). `sync` = the app treats it as a settings sync and shows
"Synced with controller!".

| Command | Format | Meaning | Status |
|---|---|---|---|
| Zone on | `run,zon,<zone>,<seconds>,<schedule_id>` | Run a zone | Have |
| Zone off | `run,zoff,<zone>,<schedule_id>` | Stop a zone | Have |
| Run all zones | `run,zall,<seconds>` | Every enabled zone in turn | Have |
| Schedule run | `run,son,<schedule_id>` | Run a schedule now | Have |
| Schedule stop | `run,soff,<schedule_id>` | Stop a running schedule | Have |
| Outlet on / off | `run,oon,<device>,<seconds>` / `run,ooff,<device>` | Zigbee smart outlet | No |
| Stop | `stop` | Stop all watering | Have |
| Schedule settings | `schedule,<schedule_id>,<start_time>` | Send a schedule's run list to the controller | Have |
| Delete schedule | `delete,<schedule_id>` | Remove from controller (also sent when a schedule is disabled) | Have |
| Hibernate | `hibernate,<0/1>` | Winter mode | Have |
| Pause / resume | (pause command) | Pause watering for a time | Have (API, no entity) |
| Settings | `settings` | Make the controller re-fetch its settings | No |
| Sensor poll interval | `poll,<device>,<minutes>` | How often a moisture sensor reports | No |
| Pair sensor | `device,add,sensor` / `device,add,cancel` | Listen for a new Zigbee device | Have (pair) |
| Listen | `listen` | Listen mode | No |
| Baseline | `baseline,0,<baseline_id>` | Run the flow baseline | No |
| Flow live log | `flowlog,0,<log_id>` | Stream live flow readings | No |
| Remove device | `remove,<device>` | Drop a Zigbee device | No (deliberately) |
| Clear Wi-Fi | `clearwifi` | Forget stored networks | No (deliberately) |
| Factory reset | `factoryreset` | | No (deliberately) |

## 3. The data (`GET app/api/settings`)

Keys only; values are the account's own.

**`global`**: `default_controller`, `push_delivery` (notification switches: `controller_offline`,
`schedule_execution`, `manual_zone_toggle_off`, `rain_sensor_on`, `flow_deviation`).

**`controller.<id>`**: `controller_name`, `model_number`, `serial_number`, `firmware`, `gateway`, `num_zones`,
`date_added`, `device_id`, `latitude`, `longitude`, `location_name`, `timezone`, `tz_offset`,
`hibernate_enabled`, `manual_max_time`, `rain_delay_inches`, `freeze_temperature`, `wind_mph`,
`climate_adjust_rules` (12 monthly %), `schedules` (comma list), `forecast` (embedded WeatherKit forecast),
`zone` (per zone, below), `zone_group` (Places, below).

**`controller.<id>.zone.<n>`** (1–16; 101/102 = flow meter ports): `zone_name`, `zenabled`, `gpm`,
`landscape_type`, `nozzle_type`, `soil_type`, `sun`, `slope`, `sensor`, `member_of_zone_group`,
`override_et_coefficients`, `recommendations`, and flow-meter settings: `flow_port`, `custom_flow_enabled`,
`flow_k`, `flow_offset`, `flow_limit_low`, `flow_limit_high`, `flow_stop_enabled`, `flow_pump_protect_enabled`,
`flow_idle_leak_small_enabled`, `flow_idle_leak_big_enabled`.

**`controller.<id>.zone_group.<n>`** ("Places"): `zone_group_name`, `grouped_zones`, `sensor_in_zone`.

**`schedule.<id>`**: `schedule_name`, `schedule_type` (`connected` / `basic` / `manual`), `sched_enabled`,
`controller`, `schedule_created`, `start_time`, `start_time_mode` (`set` / `rel-sunrise` / `rel-sunset`),
`start_time_rel` (±180 min), `days_select` (`days` / `even_odd` / `everyx`), `days_var`, `days_mode`,
`rain_delay_enabled`, `rain_delay_inches`, `freeze_delay_enabled`, `wind_delay_enabled`,
`time_restr1_enabled` / `_start` / `_end` (watering window), `use_independent_et`, `climate_array`,
`run_list` (basic: per weekday), `next_date`, `next_minutes`, `pred_start_time`, `pred_processed`,
**`prediction`** and **`history`** (per day, below), and **`zone.<n>`** per zone in the schedule:
`zonesched_enabled`, `duration_scaling` (%), `schedule_mode` (`et_days` / `et_duration` / `sensor` / `fixed`),
`cycles` (1–4 / auto), `base_duration`, `base_duration_min`, `base_duration_max`, `weekly_safeguard_min`,
`weekly_safeguard_max` (days of watering per week), `moisture_setpoint`, `sensor`, `sensor_enabled`,
`sensor_mode`, `learning_enabled`, `climate_adjust_enabled`, `rain_delay_enabled`, `freeze_delay_enabled`,
`wind_delay_enabled`, `guideline`.

**Schedule day (`prediction` / `history`)**: `local_date`, `epoch_time` (start), `start_time`, `end_time`,
`is_scheduled`, `total_duration_seconds`, `total_run_seconds`, `total_delay_seconds`, `message`, `icon`,
`is_allowed_day`, `is_hibernating`, `is_rain_delay`, `is_rain_sensor_delay`, `is_freeze_delay`,
`is_wind_delay`, `is_low_et`, `is_high_rain`, `precip_inches`, `weather_scaling`, `time_multiplier`,
`checks`, `runlist`, `flags` (per zone and `all`, §5), and per zone: `water_duration_minutes`/`_seconds`,
`cycles`, `message`, skip flags, and a **water budget**: `previous_water_balance`,
`projected_water_balance`, `projected_water_balance_after_watering`, `water_needed_inches`,
`water_scheduled_inches`, `allowed_depletion_inches`, `is_invalid`. Status: **Have** (plan calendar,
next watering, today) — water budget and per-zone messages **No**.

**`sensor.<id>`**: `sensor_name`, `model_number`, `serial_number`, `firmware`, `gateway`, `date_added`,
`device_id`, `assigned_controller`, `assigned_zone`, `moisture_setpoint`, `poll_interval`, `learning_enabled`.

**`device.<id>`** (other Zigbee devices: smart outlets, signal repeaters): `device_name`, `device_type`,
`model_number`, `serial_number`, `firmware`, `gateway`, `date_added`.

## 4. Settings the app can change

Titles and limits as the app defines them (units are Spruce's: inches, °F, mph, gpm).

| Setting | Where | Limits / default | Status |
|---|---|---|---|
| Controller display name | Controller | | No |
| Enabled zones | Controller | per zone | No |
| Maximum manual run time | Controller | min, default 10 | No (HA has its own run time) |
| Pump delay | Controller | sec | No |
| Allow concurrent zones (manual) | Controller | up to 3 zones | No |
| Hibernate | Controller | | Have |
| Location | Weather & location | lat −90…90, lon −180…180 | No |
| Rain skip threshold | Weather (controller) and per schedule | 0.001–3 in, default 0.15 | Part (per schedule) |
| Freeze skip threshold | Weather | °F, default 35 | Have |
| Wind skip threshold | Weather | mph, default 10 | No |
| Climate adjust rules | Weather | 12 monthly %, recommended from location | No |
| Zone name / enabled / gpm | Zone | gpm 0.01–50 | No |
| Landscape, nozzle, soil, sun, slope | Zone or zone group | lists | No (read only in data) |
| Custom ET coefficients | Zone | precipitation rate, crop coefficient, allowed depletion, root depth, available water %, absorption rate | No |
| Zone groups ("Places") | Zones | name, member zones, sensor | No |
| Flow meter port, model / custom k + offset | Flow meter | k 0.001–100, offset 0–2 | No |
| Flow deviation limits | Flow meter | low / high %, default 15 | No |
| Stop on high flow deviation | Flow meter | default on | No |
| Pump protect | Flow meter | default on | No |
| Sustained / burst leak detection | Flow meter | default on | No |
| Sensor name | Sensor | | No |
| Moisture threshold | Sensor / schedule zone | 1–59 %, default 30 | No |
| Sensor polling interval | Sensor | min, default 20 | No |
| Schedule name, enabled | Schedule | | Part (enabled) |
| Start time and mode | Schedule | set / sunrise / sunset ±180 min | Part (set time) |
| Days | Schedule | days / even-odd / every 1–15 days | No (shown only) |
| Watering window | Schedule | start / end | No |
| Rain / freeze / wind / rain-sensor skip | Schedule (and per zone) | | Part (schedule rain/freeze/wind) |
| Per zone: enabled, watering per day % | Schedule zone | 1–300 % | Have (service) |
| Per zone: mode, frequency, cycles | Schedule zone | et_days / et_duration / sensor / fixed; 1–6 or always; 1–4 / auto | No |
| Per zone: weekly min / max | Schedule zone | days per week, defaults 0.5 / 4 | No |
| Per zone: climate adjust on/off | Schedule zone | | No |
| Create / delete schedule | Schedules | connected / basic / manual | No |
| Push notifications | Account | 5 switches + quiet-hours window | No |

## 5. Plan flags (why a zone waters or not)

`IS_WATERING`, `SKIP_ET_SUFFICIENT` (soil has enough water), `RAIN_SATURATED`, `RAIN_DELAY`,
`HIGH_RAIN_SCALED`, `SENSOR_DELAY`, `SKIP_SENSOR_PROJECTION`, `MINIMUM_WEEKLY_TIME`,
`MAXIMUM_WEEKLY_TIME_SKIP`, `MAXIMUM_WEEKLY_TIME_ADJUST`, and sensor faults `SENSOR_ERROR_OLD`,
`SENSOR_ERROR_ZERO`, `SENSOR_ERROR_NO_DATA`, `SENSOR_ERROR_REPEATS`. Status: **Part** (only the
schedule-level skip reason is shown).

## 6. Activity log event types

Types seen in practice: `zone_scheduled` and `zone_manual` ("Zone N finished watering for X
minutes (Y gallons, Z gpm)"), `schedule` (daily plan summary per zone), `flow` (e.g. "Pump protect
activated. Turned off zone N due to low flow", "Burst leak of X gpm was detected when no zones were running"), `network` (device added), `reset` (controller auto-reset,
with Wi-Fi network and signal), `other` (hibernate), `outlet_manual` (smart outlet). Fields: `id`, `type`,
`zone`, `message`, `DTepoch`, `device_name`. Status: **Have** (fired as `spruce_irrigation_event`) — no
log view or notifications built on them yet.

## 7. Screens

| Screen | What it shows / does | Status in HA |
|---|---|---|
| Home → Dashboard | Last watering, weather card (5-day forecast, location), each enabled schedule's last and next activity with picture and reason | Part (no forecast card, no "last activity") |
| Home → Schedules | Calendar of past and planned days for all schedules (filter by schedule and zone); tap a day for its detail | Have (calendar) — no zone filter |
| Schedule day detail | Per zone: minutes, cycles, why (flags), water budget, weather blobs (rain/freeze/wind/sensor), for past days a plot of what actually ran | No |
| Home → Places | One card per zone or zone group: current moisture (or tank fill level), last and next watering, sensor temperature and signal, threshold line | Part (moisture sensors only) |
| Control (play button) | Pick zones and minutes, run; running view with countdown, pause, stop | Have (valves, run time) — no pause entity |
| Device Settings → Controller | Name, enabled zones, max manual time, pump delay, concurrent zones, hibernate, remove | Part |
| → Weather and Location | Location map, rain / freeze / wind thresholds, climate adjust rules (monthly sliders) | Part |
| → Flow Meter | Port, model, live logging, leak and fault detection, deviation limits, baseline table (base gpm and limits per zone, "Baseline Now") | No |
| → Zones / Zone groups | Name, enabled, gpm; group composition, sensor, landscape / nozzle / soil / sun / slope, custom ET | No |
| → Spruce Sensors | Name, moisture threshold, zone association, remove | No |
| → Other Devices | Smart outlets, repeaters | No |
| Add Device | Pair controller / sensor / flow meter / rain sensor wizards | Part (pair sensor button) |
| Schedule setup | Connected: days, start (incl. sunrise/sunset), window, per-zone mode/frequency/cycles/limits, skips. Basic: run list per weekday | Part |
| Analytics → Controller | Water by schedule or zone (minutes or gallons, day/week/month), pie, flow (gpm) per zone, weather (rain and temperature) | Have (statistics charts) |
| Analytics → Sensor | Moisture and temperature history of one or two sensors, watering overlaid | No |
| Log | Activity log, filterable | No (events only) |
| Account | Profile, password, push notification switches and quiet hours | No |
| Integrations | Alexa, Google Assistant, SmartThings, Hubitat | n/a |

### 7.1 Screen details (second pass, every tab, menu and dropdown that doesn't change anything)

**Home → Dashboard.** Banner "Last watered manually, Zone N: X hours ago". Weather card: today's high/low,
condition, location, 5-day forecast, "Last updated". One card per enabled schedule with Last Activity and
Next Activity (date, minutes, message, picture chosen by reason: rain, night, sprinkler…). Tapping a schedule
card flips it to: Back, **Edit Schedule**, **Details for <last day>**, **Details for <next day>**.

**Schedule day detail.** "Summary for <day>", the day's message, total minutes, weather blobs (Rain Delay,
Freeze Delay, Wind Delay, Real Gallons / Dual, Alert / Sensor Data), then one card per zone: message
(e.g. "Moisture data is not current, ignoring sensor data. Water not yet needed."), Smart Watering
(SKIP / minutes), Sensor Alert, and "Zone N not enabled in schedule". Gear icon top right = edit schedule.

**Home → Schedules.** Calendar (5 weeks, today marked) with a filter "All Schedules, All Zones"; the
selected day lists each schedule's start and message; below it the same Places cards.

**Home → Places.** One card per zone or zone group (ZG): name, sensor name, current moisture (or **tank
fill level**, with "Lowest Allowed Level", Full / Empty), last watering (minutes, when), next watering
(minutes, when, or "None scheduled in next 15 days" / "No schedules found"), sensor temperature and signal,
and the moisture threshold drawn as a line. Sensors not in a zone get their own card.

**Place details** (tap a Places card). Tabs **Chart / Activity / Schedules**; menu ⋮: Go to Zone
Settings, Go to Sensor Settings, Switch to Zone N (zone groups). Chart: **Moisture and Watering** (moisture
%, watering minutes, rain, threshold line, optional **Universal Et** line), **Temperature** (sensor
temperature over the weather high/low band), **Punchcard** (watering by weekday and hour, last 30 days);
range 3 / 7 / 14 / 30 days (each change calls `chart_data`). Schedules tab: the schedules the zone is
active in, and manual schedules.

**Connected Schedule Setup** (Edit Schedule). Name, Schedule Enabled; **Day and Time Settings** (Watering
Days: Select Days / Even-Odd / Every N days with a slider; Start Time with mode Set Time / Relative to
Sunrise / Relative to Sunset; Enable Time Restriction = watering window); **Weather Watch** (the
controller's rain / freeze / wind thresholds, and a tick per zone for each skip, with Toggle All); then one
row per zone or zone group with its mode and the projection ("Projected to water 2 days per week for an
average of 40 minutes per day. Reduced watering due to predicted rain."), and Disabled Zones.

**Schedule zone settings.** Zone Enabled in this schedule; **Mode**: Weather Balance – Vary Days
(recommended; water to refill when calculated ET passes a threshold, days vary), Weather Balance – Vary Time
(fixed days per week, time from ET), Sensor (recommended when a sensor is fitted; water below a moisture
value), Fixed (set time and days, optionally scaled by month); **Watering per Day** (% and minutes);
**Watering Frequency** (sensor mode: moisture threshold and days/week); **Cycles**; **Safeguards**: minimum
and maximum watering amount (days/week and minutes); Edit Zone Settings (affects all schedules).

**Analytics.** Summary strip: date range, gallons, hours, 10-day change in water use and watering time.
Four slides: water usage by schedule (bars), the same as a pie, flow meter gpm by zone, weather
(precipitation bars, low/high temperature). **Customize Charts**: start and end date, Watering Units
(Minutes / Gallons), Y-Axis Grouping (None / Schedule / Schedule Type / Zone / Landscape Type / Nozzle
Type), X-Axis Grouping (Day / Month / Year), Zone Filtering.

**Log.** Paged table (5–100 rows), last 7 days, "Load more"; colour legend and filters: Manual Zone Toggle,
Scheduled Zone Toggle, Schedule, Flow alerts, Network Events, Controller Resets, Firmware, Other. Messages
include each night's plan per zone, zone finished (minutes, gallons, gpm), flow above/below baseline by %,
**burst leak detected when no zones were running**, sensor added, hibernate started/ended.

**Add Device.** Spruce Sensor (Gen 1/2/3 sensors or Zigbee outlets), Flow Meter, Rain Sensor, Spruce
Controller (add another, or re-join Wi-Fi). Each starts a pairing / wiring wizard (not opened: it puts the
controller in listen mode).

**Other Devices.** Zigbee smart outlets and extenders (repeaters). Each: name, "Outlet is off / on",
**Turn On / Turn Off**, history, Remove Device.

**Account.** About you (profile, password); Notification Settings: Controller Status, Schedules, Zone
Finished Watering, Flow Alerts, Notification Time Window (quiet hours); Save Notification Settings.

## 8. Things noticed while collecting

- The web app labels the last reading of every sensor "Current", however old it is. The integration's
  "unavailable after 2 days" is the more honest view.
- Flow baselines are a one-off measurement (`current_baseline`, with its date); the integration's "usual flow"
  (median of recent working runs) can drift away from an old baseline, and flow alerts are relative to the
  baseline, so a stale baseline gives misleading alerts.
- Zones 101 and 102 are flow-meter ports, not watering zones.
- Climate adjust is a controller-wide, per-month percentage applied to basic schedules and connected
  schedules in "fixed" mode.
- The rain skip threshold exists at controller level and per schedule. In a connected schedule the app's
  Weather Watch shows only the **controller's** thresholds (rain, freeze, wind) with skips ticked per zone. The
  schedule-level `rain_delay_inches` that the integration's "rain skip threshold" edits never appears in the
  app, so it may not be the value Spruce uses. Likewise the integration's per-schedule rain/freeze/wind switches set the schedule-level flags,
  while the app ticks them per zone. **To check before relying on those entities.**
- A zone in **Sensor** mode whose sensor has stopped reporting keeps watering: Spruce says "Moisture data is
  not current, ignoring sensor data" and falls back to the weather balance.
- App display glitches: Watering Frequency shows "undefined%" in sensor mode; the log header briefly shows
  "Showing data since Nov 16th, 5138" while loading. The Places "Current" moisture is the last reading,
  however old.
- The schedule zone page's back button is labelled "Save" (leaving saves); only reloading discards changes.

## 9. Ideas beyond the web app

- **Water budget per zone**: Spruce already calculates soil water balance, need and allowed depletion for
  each zone each day — sensors and a chart of soil water vs. watering and rain.
- **Forecast entity**: expose Spruce's WeatherKit forecast as an HA `weather` entity (no extra API).
- **Rain gauge comparison**: forecast rain vs. recorded rain vs. a local gauge / BOM, to tune the threshold.
- **Leak and fault alerts**: Repairs issues and notifications from `flow` events and idle-flow leak
  detection; daily "water used vs. plan" check.
- **Cost**: water used × the council tariff, per zone and month, alongside the bills database.
- **Zone groups (Places)** as HA areas or groups, with the group's sensor.
- **Flow baseline** as a button with the baseline table as diagnostics.
- **Notifications** via HA instead of Spruce push (and the quiet-hours window).
- **Sensor history** imported as statistics, like water and weather.
- **Pause** as a number/button pair (pause for N minutes, resume).
- **Smart outlets / repeaters** as switches and diagnostic devices.
- **Push updates**: listen to `zone-status-stream` so valves change state in HA within a second.
- **Places charts in HA**: import `chart_data` moisture and the per-zone ET balance as statistics, so the
  dashboard can show moisture vs watering vs rain like the Place details chart.
- **Projection per zone** ("2 days per week, 40 minutes per day") as sensors, from the schedule data.
- **Burst leak alert** as a binary sensor / Repairs issue from the `flow` log entries.
