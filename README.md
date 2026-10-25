# Spruce Irrigation for Home Assistant

> [!WARNING]
> **Unofficial — use at your own risk.** This is a personal, community project. It is **not** made, endorsed,
> supported or affiliated with Plaid Systems or Spruce Irrigation; "Spruce" and related names belong to their
> owners. It uses the same undocumented cloud endpoints as the Spruce web app, which may change or stop working
> at any time. This integration can turn water on and off: you are responsible for how you use it and for any
> water use, damage, bills or other consequences. Provided "as is", without warranty of any kind and with no
> liability, as set out in the [MIT licence](LICENSE).

A Home Assistant integration for **Spruce** smart irrigation controllers (Plaid Systems, Gen2 Wi-Fi, e.g.
PS-SPRWIFI16-01): control every zone, see what is watering, how much water each zone uses, what the flow
meter measured, and the wireless soil moisture sensors, with full water history in long-term statistics.

Spruce controllers have no local API: like the Spruce app, this integration talks to Spruce's cloud
(`api.spruceirrigation.com`) with your account API key.

## What you get

| | Entities and features |
|---|---|
| **Control** | A water **valve** per zone (open = run for the zone's run time, close = stop); a **run time** setting per zone; **Run schedule** buttons; **Stop watering**; **Hibernate** switch (winter mode, with an optional resume date) and **Pause schedules until tomorrow**; **Pair moisture sensor** / **Stop pairing**; a **zone enabled** switch per zone |
| **Schedules** | Per schedule (except the manual one): **enabled** switch, **start time**, **rain / freeze / wind skip** switches (in connected schedules the skips are per zone: the switch is on when every zone has it and lists the zones without it). Changes are saved in Spruce and sent to the controller, as the Spruce app does |
| **Weather Watch** | The controller's thresholds, shared by all schedules: **rain skip threshold**, **freeze skip temperature** and **wind skip threshold**, in your chosen units |
| **Controller settings** | **Maximum manual run time**, **pump delay**, **allow concurrent zones**; flow meter: **upper / lower flow deviation limits**, **stop on high flow**, **pump protect**, **sustained** and **burst leak detection** |
| **Schedule editing** | Per connected schedule: **start mode** (set time, or relative to sunrise / sunset) and **offset**, **watering days** (every day … once a week, even / odd days; days of the week with `set_schedule_days`), **watering window** with start and end. Services to **create**, **rename** and **delete** schedules, and per zone in a schedule: mode (weather balance – vary days / vary time, sensor, fixed), cycles, minimum / maximum days per week, climate adjust (`set_schedule_zone`) |
| **Watering plan** | A **calendar** with Spruce's plan for each enabled schedule, about 18 days ahead plus the last few days: each watering from start to finish (with minutes per zone), and days skipped for rain, freeze, wind or hibernation as all-day notes. **Next watering** (start time, schedule, minutes, finish, minutes per zone) and **Watering today** (watering, skipping for rain / freeze / wind, not needed, nothing planned) sensors; each schedule's **watering days** (every day, every N days, even/odd days, weekdays) |
| **Zone plan** | Each zone's **next watering** with minutes, cycles, Spruce's reason, the projection for the coming week (days and average minutes) and its **water budget** in mm (soil water, needed, scheduled, allowed depletion). `get_schedule_day` returns a day's full plan and, for past days, what actually ran; `set_schedule_zone` also sets a sensor zone's **moisture threshold** |
| **Live status** (every minute) | Status (idle, watering, paused, pump delay, cycle and soak, hibernating, offline), active zone, watering ends, watering, online, last seen |
| **Water** | Water today and this month (controller and per zone), usable in the Energy dashboard; per-zone last measured flow, usual flow and last run; **water cost** today and this month once a price is set in the options |
| **Flow baseline** | **Run flow baseline** button (every enabled zone runs ~1 minute while its flow is measured), each zone's **baseline flow**, **last flow baseline** with the history of every baseline seen (Spruce keeps only the latest; older ones can be added with `record_baseline`), and a notification with the biggest changes when a new one lands |
| **Zone heads and design flow** | Enter the heads in each zone (Settings → Devices & services → Spruce Irrigation → **Add zone heads**: model × arc × how many) to get its **design flow** and precipitation rate. A built-in library of common nozzles, drippers and drip line, plus your own models (**Add head model**: brand, model, type, flow, pressure, radius) that you can edit or delete like any entry |
| **Baseline review** | A new baseline that moves a zone more than 25% (option) from its design flow, or from its last accepted baseline, is held for review — a leak or a missing nozzle shouldn't quietly become "normal". **Accept** or **Keep previous** from Repairs, the buttons or the `review_baseline` service. Each zone's **flow check** (OK, higher / lower than expected, no flow) compares its last run with the design flow, else the accepted baseline |
| **Health notices** | Repairs for zones measuring no flow at baseline, a flow-meter calibration outside Spruce's own range, a baseline over a year old, and Zigbee repeaters not reporting |
| **Flow faults** | Per-zone *flow fault* when a run measures far less than the zone's usual flow (failed meter, closed valve, broken pipe); a **Repairs** notice when several zones show it |
| **Moisture sensors** | A device per sensor: moisture, temperature, battery voltage, Zigbee signal, last reading; unavailable when a sensor has gone quiet for 2 days |
| **Outlets** | Spruce smart outlets (which also repeat the sensors' Zigbee signal) as their own devices: a **socket** switch, last report, signal and **not reporting** (no report for 7 days) |
| **History** | Spruce's water history (litres and minutes per zone per day, back to when the controller was added) imported into long-term statistics `spruce_irrigation:<controller>_zone_<n>_water` / `_minutes`, the same split per schedule (`..._zone_<n>_schedule_<id>_water` / `_minutes`), and a controller total |
| **Weather** | Spruce's recorded weather at the controller's location (Apple WeatherKit) imported alongside: daily rain `spruce_irrigation:<controller>_rain` (mm) and the day's low/high temperature `..._temperature` (°C, min/max). The dashboard's History page shows rain, temperature and water per zone for the same 30 days one above the other (a chart can't mix mm and litres), and rain and water per month |
| **Events** | `spruce_irrigation_event` fired for each new Spruce activity-log entry (zone finished with gallons and gpm, flow alerts, schedule skips…) for automations |
| **Names** | Names come from the Spruce app. Rename a zone, schedule, sensor or the controller there (or add one) and the integration reloads itself within 15 minutes to pick it up; entity ids stay the same. Names you set in Home Assistant are kept |

### Dashboard

A ready-made dashboard comes with the integration: Settings → Dashboards → **Add dashboard** → under
*Community dashboards* pick **Spruce Irrigation** (Home Assistant 2026.5 or later). It has Overview, Zones,
Schedules, History, Flow health and Diagnostics views, built from your controller's entities and water statistics each time
it opens, so new zones, schedules and sensors appear by themselves. To change it by hand, open the dashboard →
✏ → **Take control**. After installing or updating the integration and restarting, reload Home Assistant in each browser
and in the companion app (app: Settings → Companion app → Troubleshooting → Reset frontend cache, or close
and reopen it); a page opened before the restart shows *Timeout waiting for strategy element
ll-strategy-dashboard-spruce-irrigation*. On older Home Assistant versions, create an empty dashboard, open its raw configuration
editor and enter:

```yaml
strategy:
  type: custom:spruce-irrigation
```

### Services

`spruce_irrigation.run_zone` (valve target, minutes), `stop_zone`, `run_all_zones` (minutes),
`run_schedule` / `stop_schedule` (schedule name as in the Spruce app), `stop`, `set_hibernate`,
`pause_schedules` (days or until a date), `run_baseline`, `record_baseline` (an older baseline, per zone),
`review_baseline` (accept or keep previous), `set_zone_heads` (a zone's heads, for scripts), `pair_sensor`, `import_history` (re-import water statistics from a date),
`create_schedule`, `rename_schedule`, `delete_schedule`, `set_schedule_days`,
`set_schedule_zone` (a zone's watering %, whether it is included, its rain / freeze / wind skips, mode, cycles,
weekly limits and climate adjust, in a schedule;
a zone in a zone group changes with its group, or give the group's name).

## Install

**HACS** (once the repository is public): HACS → ⋮ → Custom repositories → add this repository as an
*Integration* → install **Spruce Irrigation** → restart Home Assistant.

**Manually:** copy `custom_components/spruce_irrigation` into your `config/custom_components/` and restart.

Then Settings → Devices & services → Add integration → **Spruce Irrigation**, and either **sign in with your
Spruce account** (the integration fetches the API key; your password is used once and not stored) or paste
an API key. **Reconfigure** on the integration does the same later.

### Getting the API key

Only needed if you don't use *sign in*. Spruce doesn't show it in the app yet. Either ask Spruce support for an account API token, or: sign in at
[app.spruceirrigation.com](https://app.spruceirrigation.com), open the browser's developer tools (F12) →
**Network**, click any request to `api.spruceirrigation.com` and copy the value of its **Authorization**
request header. The key gives full access to your Spruce account; Home Assistant stores it in its config
entry. If you change your Spruce password the key changes and Home Assistant will ask for the new one.

## Options

- **General:** default zone run time, live status refresh (default 60 s), water/flow history refresh (default 15 min)
  the flow-fault threshold (default: a run under 20% of the zone's usual flow) and the baseline review tolerance
  (default 25%).
- **Units:** metric (default: L, L/min, mm, °C, km/h), US (gal, gal/min, in, °F, mph), or custom per quantity.
  Spruce stores US units; the integration converts both ways.
- **Water price:** per kL (per 1000 gal with US volume) in your Home Assistant currency, or an entity holding
  the tariff, for the water cost sensors.

## Notes

- Water figures are what the Spruce flow meter counted. If the meter model is set wrong in the Spruce app
  (Device Settings → flow meter), every figure is off by the same factor.
- Spruce answers slowly at times (HTTP 504); requests are retried and the activity log is optional.
- Not affiliated with Plaid Systems; see the disclaimer at the top.

## Development

`pip install -r requirements_test.txt && pytest` (Python 3.14, Linux). GitHub Actions run hassfest, the
HACS validator and the tests on every push.
