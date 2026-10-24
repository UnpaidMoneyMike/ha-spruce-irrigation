# Spruce Irrigation for Home Assistant

A Home Assistant integration for **Spruce** smart irrigation controllers (Plaid Systems, Gen2 Wi-Fi, e.g.
PS-SPRWIFI16-01): control every zone, see what is watering, how much water each zone uses, what the flow
meter measured, and the wireless soil moisture sensors, with full water history in long-term statistics.

Spruce controllers have no local API: like the Spruce app, this integration talks to Spruce's cloud
(`api.spruceirrigation.com`) with your account API key.

## What you get

| | Entities and features |
|---|---|
| **Control** | A water **valve** per zone (open = run for the zone's run time, close = stop); a **run time** setting per zone; **Run schedule** buttons; **Stop watering**; **Hibernate** switch (winter mode); **Pair moisture sensor** (lets a reset sensor join) |
| **Live status** (every minute) | Status (idle, watering, paused, pump delay, cycle and soak, hibernating, offline), active zone, watering ends, watering, online, last seen |
| **Water** | Water today and this month (controller and per zone), usable in the Energy dashboard; per-zone last measured flow, usual flow and last run |
| **Flow faults** | Per-zone *flow fault* when a run measures far less than the zone's usual flow (failed meter, closed valve, broken pipe); a **Repairs** notice when several zones show it |
| **Moisture sensors** | A device per sensor: moisture, temperature, battery voltage, Zigbee signal, last reading; unavailable when a sensor has gone quiet for 2 days |
| **History** | Spruce's water history (litres and minutes per zone per day, back to when the controller was added) imported into long-term statistics `spruce_irrigation:<controller>_zone_<n>_water` / `_minutes` and a controller total |
| **Events** | `spruce_irrigation_event` fired for each new Spruce activity-log entry (zone finished with gallons and gpm, flow alerts, schedule skips…) for automations |

### Services

`spruce_irrigation.run_zone` (valve target, minutes), `stop_zone`, `run_all_zones` (minutes),
`run_schedule` / `stop_schedule` (schedule name as in the Spruce app), `stop`, `set_hibernate`,
`pair_sensor`, `import_history` (re-import water statistics from a date).

## Install

**HACS** (once the repository is public): HACS → ⋮ → Custom repositories → add this repository as an
*Integration* → install **Spruce Irrigation** → restart Home Assistant.

**Manually:** copy `custom_components/spruce_irrigation` into your `config/custom_components/` and restart.

Then Settings → Devices & services → Add integration → **Spruce Irrigation**, and enter your API key.

### Getting the API key

Spruce doesn't show it in the app yet. Either ask Spruce support for an account API token, or: sign in at
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
- Not affiliated with Plaid Systems. Uses the same cloud endpoints as the Spruce web app.

## Development

`pip install -r requirements_test.txt && pytest` (Python 3.14, Linux). GitHub Actions run hassfest, the
HACS validator and the tests on every push.
