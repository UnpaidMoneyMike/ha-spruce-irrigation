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
| **Control** | A water **valve** per zone (open = run for the zone's run time, close = stop); a **run time** setting per zone; **Run schedule** buttons; **Stop watering**; **Hibernate** switch (winter mode); **Pair moisture sensor** (lets a reset sensor join) |
| **Schedules** | Per schedule (except the manual one): **enabled** switch, **start time**, **rain / freeze / wind skip** switches and the **rain skip threshold**; the controller's **freeze skip temperature**. Changes are saved in Spruce and sent to the controller, as the Spruce app does |
| **Watering plan** | A **calendar** with Spruce's plan for each enabled schedule, about 18 days ahead plus the last few days: each watering from start to finish (with minutes per zone), and days skipped for rain, freeze, wind or hibernation as all-day notes. **Next watering** (start time, schedule, minutes, finish, minutes per zone) and **Watering today** (watering, skipping for rain / freeze / wind, not needed, nothing planned) sensors; each schedule's **watering days** (every day, every N days, even/odd days, weekdays) |
| **Live status** (every minute) | Status (idle, watering, paused, pump delay, cycle and soak, hibernating, offline), active zone, watering ends, watering, online, last seen |
| **Water** | Water today and this month (controller and per zone), usable in the Energy dashboard; per-zone last measured flow, usual flow and last run |
| **Flow faults** | Per-zone *flow fault* when a run measures far less than the zone's usual flow (failed meter, closed valve, broken pipe); a **Repairs** notice when several zones show it |
| **Moisture sensors** | A device per sensor: moisture, temperature, battery voltage, Zigbee signal, last reading; unavailable when a sensor has gone quiet for 2 days |
| **History** | Spruce's water history (litres and minutes per zone per day, back to when the controller was added) imported into long-term statistics `spruce_irrigation:<controller>_zone_<n>_water` / `_minutes`, the same split per schedule (`..._zone_<n>_schedule_<id>_water` / `_minutes`), and a controller total |
| **Events** | `spruce_irrigation_event` fired for each new Spruce activity-log entry (zone finished with gallons and gpm, flow alerts, schedule skips…) for automations |
| **Names** | Names come from the Spruce app. Rename a zone, schedule, sensor or the controller there (or add one) and the integration reloads itself within 15 minutes to pick it up; entity ids stay the same. Names you set in Home Assistant are kept |

### Dashboard

A ready-made dashboard comes with the integration: Settings → Dashboards → **Add dashboard** → under
*Community dashboards* pick **Spruce Irrigation** (Home Assistant 2026.5 or later). It has Overview, Zones,
Schedules, History and Diagnostics views, built from your controller's entities and water statistics each time
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
`pair_sensor`, `import_history` (re-import water statistics from a date), `set_schedule_zone` (a zone's
watering % and whether it is included, in a schedule).

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

Default zone run time, live status refresh (default 60 s), water/flow history refresh (default 15 min) and
the flow-fault threshold (default: a run under 20% of the zone's usual flow).

## Notes

- Water figures are what the Spruce flow meter counted. If the meter model is set wrong in the Spruce app
  (Device Settings → flow meter), every figure is off by the same factor.
- Spruce answers slowly at times (HTTP 504); requests are retried and the activity log is optional.
- Not affiliated with Plaid Systems; see the disclaimer at the top.

## Development

`pip install -r requirements_test.txt && pytest` (Python 3.14, Linux). GitHub Actions run hassfest, the
HACS validator and the tests on every push.
