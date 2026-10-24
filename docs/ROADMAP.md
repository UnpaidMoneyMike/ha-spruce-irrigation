# Roadmap

Where the integration goes next: everything the Spruce web app can do, done in Home Assistant, plus what Home
Assistant can add. Built on [`spruce-app-reference.md`](spruce-app-reference.md) (what the app shows and calls)
and on write tests made through the app in October 2026 (the exact request each Save sends — §6 below).

Status today (0.5.x): zone valves and run times, run / stop schedules, run all, stop, hibernate, pair sensor;
schedule enable, start time, rain/freeze/wind skip, rain threshold, freeze temperature, per-schedule-zone
watering %; live status, water today / month, flow faults, moisture sensors; water, minutes, rain and
temperature history as statistics; watering plan calendar, next watering, watering today; activity log as events;
a built-in dashboard; reloads itself when names change in Spruce.

---

## 1. Fix first (correctness)

| Issue | Evidence | Fix |
|---|---|---|
| **Weather thresholds are controller-wide**, not per schedule | The app: "The threshold set here is global for all zones and schedules on this Spruce Controller". It saves `rain_delay_inches` / `freeze_temperature` / `wind_mph` as controller records (zone 99) | Replace the per-schedule *rain skip threshold* number with one controller **Rain skip threshold** (mm/in), keep **Freeze skip temperature**, add **Wind skip threshold** (fixed steps: 5/10/15/20/25/30 mph — check the full list). Remove the old entity with a note in the release |
| **Skips are per zone in connected schedules** | "Enabling skip behavior is done … independently for each schedule and zone (Connected Schedules)". The app saves `rain_delay_enabled` etc. as `schedule_zone` records | For connected schedules, the rain / freeze / wind switches write every zone in the schedule (and show *on* only if all are on, with per-zone attributes); add a service for a single zone. Basic schedules keep schedule-level flags |
| **Zone groups** | Changing a zone group's watering % writes every member zone (`duration_scaling` for zone 1 *and* 2) | `set_schedule_zone` and any per-zone schedule setting expand a group to its members (`controller.zone.N.member_of_zone_group`, `zone_group.N.grouped_zones`) |
| Controller-setting syncs | The app sends `spruceset=settings` after saving controller settings that live on the controller (e.g. max manual run time) | Send it after controller settings that the controller uses; weather thresholds are cloud-only (no command) |

## 2. Parity with the web app

Grouped as the app groups them. *Entity* = a Home Assistant entity; *service* = an action for automations.

### Controller
- **Maximum manual run time** (number, min), **Pump delay** (number, s), **Allow concurrent zones** (switch).
- **Zone enabled** per zone (switch, config) — hides/shows the zone in Spruce.
- Rename controller / zones from Home Assistant (service) — optional; names already follow Spruce.

### Weather and location
- Thresholds: see §1.
- **Climate adjust rules**: 12 monthly percentages (`climate_adjust_rules` "Jan,…,Dec") — a service to set
  all 12 and a sensor showing this month's factor; `default_climate_rules?latitude=` gives Spruce's
  recommendation for a "reset to recommended" service.
- Forecast: a **weather entity** from `weather_forecast` (Apple WeatherKit: hourly and daily rain amount and
  chance, temperature, wind, humidity, UV, sunrise/sunset) — no extra API key.

### Flow meter
- **Flow deviation upper / lower limit** (numbers, %), **Stop watering on high deviation**, **Pump protect**,
  **Sustained leak detection**, **Burst leak detection** (switches) — records on flow ports 101/102.
- **Baseline**: a *Run flow baseline* button (`POST baseline` then `spruceset=baseline,0,<id>`; runs every
  zone 60 s + 10 s pause), per-zone **baseline flow** sensors (`current_baseline`), and **Last baselined**.
- **Live flow**: a *flow rate* sensor fed by live logging (`live_log_flow` → `spruceset=flowlog,1,<port>,<id>`,
  stream `live-logging-flow.php`, 2 s points) while a zone runs; stop with `flowlog,0,<port>` / `flowlog,0,0`.
- Flow meter **model / k / offset** as diagnostics, with a Repairs issue when the offset is outside 0–2 (the app's
  own validation).

### Zones and zone groups ("Places")
- Zone characteristics as config selects: **landscape, nozzle, soil, sun, slope**; **gpm** (number); custom ET
  coefficients (service).
- **Zone groups** as Home Assistant areas or a device per group, with the group's sensor.
- **Place charts**: import `chart_data` (per-zone daily ET balance, watering on/off, per-sensor moisture and
  temperature) as statistics so the dashboard can draw moisture vs watering vs rain like the app's Place details
  (3 / 7 / 14 / 30 days, temperature band, punchcard).

### Moisture sensors
- **Moisture threshold** (number, 1–59 %, applies to all schedules using the sensor), **Polling interval**
  (select; `spruceset=poll,<sensor>,<min>` + setting), **Learn mode** (button; `start_learn` / `stop_learn`).
- **Sensor history** as statistics (`sensor_data` / `chart_data`).
- Pairing: keep the button; add a *Cancel pairing* button (`device,add,cancel`) and surface what the
  `listen-device-stream` finds.

### Schedules
- **Create / delete schedule** (services): `POST app/api/schedule schedule_type=connected|basic|manual` →
  id; `DELETE app/api/schedule/<id>` + `spruceset=delete,<id>`. Entities appear/disappear by themselves (the
  integration already reloads on schedule changes).
- **Name** (text), **Start time mode** (select: set / relative to sunrise / relative to sunset) + **offset**
  (number, ±180 min), **Watering days** (select: days / even-odd / every N; plus weekday switches or a text
  `1111111`), **Watering window** (switch + two times).
- Per zone in a connected schedule: **enabled**, **mode** (select: Weather Balance – Vary Days, Weather Balance –
  Vary Time, Sensor, Fixed), **watering per day %** (have), **frequency**, **cycles** (1–4 / auto), **weekly
  minimum / maximum** (days/week), **climate adjust** on/off.
- Basic schedules: run list per weekday (service).
- Manual schedules: per zone **time** and **cycles** (service), run (have).
- After any schedule change: `spruceset=schedule,<id>,<start>` (have, via `schedule/runlist`).

### Plan detail
- Per zone per day from `prediction` / `history`: minutes, cycles, **reason** (flags such as
  SKIP_ET_SUFFICIENT, RAIN_DELAY, SENSOR_ERROR_OLD, MAXIMUM_WEEKLY_TIME_*), and the **water budget**
  (previous / projected balance, water needed, allowed depletion) — sensors per zone and calendar descriptions.
- The app's **projection** per zone ("2 days per week, 40 minutes per day") as attributes.
- Past day detail: `schedule_history_zone_state` (what actually ran: start, duration, gpm per zone).

### Other Zigbee devices (outlets / extenders)
- A **switch** per outlet (`run,oon,<device>,<seconds>` / `run,ooff,<device>`), with **online**, **last seen**
  and **signal** (from `outlet_status`). These are often the sensor mesh's repeaters, so a Repairs issue when a
  repeater has gone silent explains silent sensors.

### Log and alerts
- Keep `spruce_irrigation_event`; add **Logbook** descriptions and an **event entity** per type (zone finished,
  flow alert, leak, schedule summary, network, reset, hibernate).
- **Repairs / binary sensors** for burst leak, sustained leak, pump protect, flow deviation (parsed from `flow`
  entries), and for a baseline older than a year.

### Notifications
- Spruce's own push switches (`push_delivery` global record) as config switches, or leave alerts to Home
  Assistant (blueprints, §3).

## 3. Beyond the app

- **Instant updates**: listen to `zone-status-stream.php` (server-sent events) so valves change state within a
  second; keep the 60 s poll as a fallback.
- **Water cost**: litres × tariff per zone and month (Energy dashboard water + a cost sensor).
- **Health checks** as Repairs: flow-meter calibration out of range, zones measuring no flow at baseline,
  sensors silent > 2 days, repeaters offline, stale baseline, controller offline.
- **Forecast vs actual rain** to tune the rain threshold (WeatherKit forecast vs Spruce's recorded rain vs an
  optional local rain gauge entity).
- **Blueprints**: leak alert, "skip tonight if my rain gauge measured more than X", notify when a schedule will
  water tonight, run a zone from a button.
- **Diagnostics** download with the plan, baselines and flow settings (redacted).

## 4. Local control (separate research)

The Gen2 controller is a Particle Photon module that talks only to Particle's cloud (encrypted; no local
ports). Local control means pointing the module at a self-hosted Particle-compatible server or new firmware —
out of scope for this integration until proven; tracked separately.

## 5. Order of work

| Release | Scope |
|---|---|
| 0.6 | §1 fixes; controller settings (max manual time, pump delay, concurrent zones, zone enabled); wind threshold; flow meter switches and limits; outlet switches + repeater health |
| 0.7 | Flow baseline button + baseline sensors; live flow sensor; Repairs for leaks / calibration / no-flow zones / stale baseline |
| 0.8 | Schedules: create / delete, start mode + offset, days, watering window, per-zone mode / cycles / weekly limits; manual and basic schedule editing services |
| 0.9 | Plan detail: per-zone reasons and water budget; projections; past day detail; sensor settings (threshold, polling, learn mode) |
| 0.10 | Charts: `chart_data` / `sensor_data` statistics and Place-style dashboard; climate adjust; weather entity |
| 1.0 | Instant updates (stream), Logbook / event entities, blueprints, cost, diagnostics; quality pass for HACS default / core |

Each release: tests against the fake account for every new write (request body as in §6), then a live check
against a real controller with the change reverted, as done for the write tests.

## 6. Request shapes (from the write tests)

All saves go to `POST app/api/settings` with `settings` = JSON of records
`{"<name>[,<zone>]": {"name", "value", "device_id", "zone", "schedule_id", "settings_key_type", "settings_key"}}`:

| Level | device_id | zone | schedule_id | settings_key_type | settings_key |
|---|---|---|---|---|---|
| Controller | controller | 99 | 0 | `controller` | `controller.<id>` |
| Zone (incl. flow ports 101/102) | controller | n | 0 | `zone` | `controller.<id>.zone.<n>` |
| Schedule | controller | 99 | id | `schedule` | `schedule.<id>` |
| Zone in a schedule | controller | n | id | `schedule_zone` | `schedule.<id>.zone.<n>` |
| Sensor | sensor | 99 | 0 | `sensor` (+ `"sensor": <id>`) | `sensor.<id>` |
| Account | `global` | 99 | 0 | `global` | `global` |

Controller commands: `POST app/device/spruce` with `spruceset=<command>`, `device_id`, `gateway=wifi` — see
the reference for the full list.
