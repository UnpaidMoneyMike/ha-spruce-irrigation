# Dashboard plan

The built-in "Spruce Irrigation" dashboard (a dashboard strategy, generated from the integration's entities each
time it opens) should be the best way to run a Spruce system — better than the Spruce app. This is the design,
the data it needs, and the order to build it. Data and API details: [`spruce-app-reference.md`](spruce-app-reference.md),
roadmap of entities: [`ROADMAP.md`](ROADMAP.md).

## 1. What the dashboard is for

Every screen answers one of these, in this order of importance:

1. **Is anything wrong?** Leak, zone with no flow, controller offline, sensors or repeaters silent, flow meter
   miscalibrated, stale baseline.
2. **What happens next, and why?** Tonight's / the next watering, which zones, how long, and why other days
   skip (rain, wind, freeze, soil still wet).
3. **What did it do, and what did it cost?** Water per zone per day/week/month, against rain and temperature,
   in litres and dollars.
4. **Let me act quickly.** Stop, run a zone for N minutes, skip tonight, hibernate.
5. **Let me tune it.** Schedules, weather thresholds, flow alarms — rarely, and out of the way.

## 2. Design rules

- **Built-in cards only** (tile, heading + badges, markdown, calendar, statistics-graph, history-graph, gauge,
  logbook, area / picture cards). Works on every install with nothing from HACS. If ApexCharts card is
  installed, use it for the combined rain + water + temperature chart; otherwise the stacked built-in charts.
- **Phone first.** Sections view; every section reads well at one column; desktop gets 3–4 columns.
- **Show only what matters now.** Alerts appear only when there is one (`visibility` conditions); disabled zones,
  disabled schedules, retired zones and long-dead sensors are folded into one "Not in use" section.
- **Plain language.** "Skipping tonight: 18 mm rain forecast" — not "is_rain_delay: true".
- **Consistent meaning of colour.** Watering = blue, rain = teal, skip / waiting = amber, fault = red,
  off / inactive = grey. Icons the same everywhere (valve, rain, wind, snowflake, leak, sensor).
- **Units from Home Assistant** (mm / °C / L / L/min, or inches / °F / gal).
- **Dates and times, not "in 2 weeks".** Next-watering text uses absolute times.
- **Everything taps through** to the entity's more-info (history, controls) — no dead text.
- **Fast.** No chart over 90 days on first open; long ranges on the Water & cost view only.

## 3. Views

Five views plus two sub-views (baseline history, settings). Wireframes are for phone width.

### 3.1 Today

```
┌─────────────────────────────────────┐
│ ⚠ Burst leak 1.2 gpm at 3:15 pm     │  ← alerts strip, only when present (Repairs + binary sensors)
│ ⚠ 2 sensor repeaters offline         │
├─────────────────────────────────────┤
│ 💧 Idle · Next: Tue 4 Jun 9:00 pm    │  ← hero: status + next watering (markdown + tile)
│    120 min · 6 zones · ends 11:00 pm │
│ Tonight: skipping — 12 mm rain       │
│ [Stop] [Run zone…] [Skip 24 h] [❄]   │  ← quick actions (buttons; Run zone opens a picker)
├─────────────────────────────────────┤
│ Next 7 days                          │  ← plan strip: one chip per day
│ Su  Mo  Tu  We  Th  Fr  Sa           │     icon + minutes, coloured by outcome
│ 🌧  ✓   💧120 ✓  ✓   🌧  💧90        │     (✓ = soil has enough water)
├─────────────────────────────────────┤
│ Water this week  540 L  ▼ 30 %       │  ← KPIs: week vs last week, month, cost
│ This month 3.9 kL · $9.10            │
│ Rain this week 14 mm                 │
├─────────────────────────────────────┤
│ Zones now                            │  ← compact valve tiles: running zone first,
│ [Front High ▶ 4 min] [Front Low]…    │     then last-run / next-run as state content
└─────────────────────────────────────┘
```

Data: status, next watering, watering today (have); 7-day plan (calendar entity events → a template or a new
"plan" sensor with per-day attributes); water week/month (statistics); cost (new sensor: litres × tariff option);
alerts (new binary sensors / Repairs, §4); quick actions (have: stop, run zone, hibernate; new: *skip next 24 h* =
rain delay).

### 3.2 Zones

One section per zone **group** ("Place", as in the app), zones inside it:

```
┌─────────────────────────────────────┐
│ Front Lawn (zones 1 + 2)  🌱 31 %    │  ← heading: place name + sensor badge (if live)
│ [Front High  ▶]  [Front Low  ▶]      │  ← valve tiles with run-time feature (+/− minutes)
│ Flow  30 L/min  ●ok   base 31       │  ← last vs baseline, fault chip
│ Soil water  ███████░░  needs water   │  ← water budget bar: balance vs allowed depletion
│   in ~2 days                         │
│ Last 15 min yesterday · Next Sun 30m │
│ ▁▂▁▁▅▁▁▁▃▁  water, 30 days           │  ← small bar chart (statistics-graph, day)
└─────────────────────────────────────┘
```

Single zones get the same card without the group heading. Zones measuring no flow at baseline or with a flow
fault show a red chip and sort first. Data: valves, run time, flow, flow fault, last run, water (have); baseline
flow, water budget, next run per zone, place grouping (new — 0.7 / 0.9).

### 3.3 Plan & schedules

```
┌─────────────────────────────────────┐
│ Watering plan          [month|list] │  ← calendar (have); list view on phones
├─────────────────────────────────────┤
│ Lawns schedule          ● on        │
│ 10:00 pm · every day · window off   │
│ Weather Watch   rain ✓ freeze ✓ wind 8/10
│ Zone          mode     %   cycles   │  ← per-zone table (markdown) with tap-to-edit tiles
│ Front Lawn    Sensor  81 %   2      │
│ Garden Beds   Weather 100 %  auto   │
│ [Run now] [Edit…]                    │
├─────────────────────────────────────┤
│ Manual: Quick soak [Run]            │
│ Not in use: Winter, …               │  ← folded
└─────────────────────────────────────┘
```

Data: have (enabled, start, skips, run); new (start mode + offset, days, window, per-zone mode / cycles / weekly
limits, projections — 0.8 / 0.9). Editing that doesn't fit a tile (per-weekday run lists, manual run times) opens
a script / service form.

### 3.4 Water & cost

```
┌─────────────────────────────────────┐
│ Last 30 days                         │
│ Rain (mm)          ▁ █ ▁ ▁ ▂ ▁       │  ← stacked, same days (or one ApexCharts chart)
│ Temperature (°C)   ~~~~~~~~          │
│ Water by zone (L)  ▁ ▁ ▃ █ ▁        │
├─────────────────────────────────────┤
│ Did it water to need?                │  ← ET demand vs water applied per week
│ Demand ▂▃▅▆  Applied ▂▂▆▅            │     (from chart_data ET balance — 0.10)
├─────────────────────────────────────┤
│ 12 months: water per month, rain     │
│ Cost per month ($)                   │
│ This year vs last year               │
└─────────────────────────────────────┘
```

Data: rain, temperature, water per zone (have); cost (new); ET demand (new, `chart_data`).

### 3.5 Health

```
┌─────────────────────────────────────┐
│ Controller  ● online · Wi-Fi −60 dBm │
│ Last message 1 min ago · fw 2.7xx    │
├─────────────────────────────────────┤
│ Flow meter                           │
│ Live 0.0 L/min   [Start live log]    │  ← live flow sensor + button (0.7)
│ Leak detection: burst ✓ sustained ✓  │
│ Baseline 3 May 2026   [Run baseline] │
│ Zone   base   last   status          │  ← table: no-flow zones in red
│ ⚠ Calibration offset outside 0–2   │
├─────────────────────────────────────┤
│ Sensor mesh                          │
│ Sensors   4 live · 1 silent          │  ← each: moisture, battery, signal, last seen
│ Repeaters 1 of 2 online               │  ← outlets / extenders with last seen
├─────────────────────────────────────┤
│ Activity log                         │  ← logbook card of spruce events
└─────────────────────────────────────┘
```

### 3.6 Baseline history (sub-view of Health)

Spruce keeps only the latest flow baseline (`current_baseline`). The integration saves every baseline it sees
(id, time, how it was started, per-zone flow) in its own storage, so the user can see how each zone changed:

- **Baselines list** — newest first, with date, trigger and zone count; the current one marked.
- **Before and after** — pick two baselines; per zone: before, after, change %. Changes over 20 % highlighted,
  a fall to zero flagged "No flow".
- **One zone over time** — the baseline as a step line with the flow measured on each run (`gpm_data`) as a
  dashed line, so drift and faults show between baselines.
- A *Run a new baseline* button. A new baseline also raises a short notification with the biggest changes.

The first baseline seen after install is recorded as it is; older ones can be added with a service
(`spruce_irrigation.record_baseline`, values per zone) when the user has them on paper.

### 3.7 Settings (sub-view, admin only)

- **Units** — Metric (default for new installs), US, or Custom per quantity: water volume (L / kL or gal), flow
  (L/min or gal/min), rain (mm or in), temperature (°C or °F), wind (km/h or mph), soil water / root depth (mm or
  in). Spruce stores US units; the integration converts every sensor, number and chart. Kept in the config
  entry's options (also editable from *Configure*), so it applies to entities, not only the dashboard.
- **Water price** — price per kL (or per gallon) or an entity holding the tariff; currency from Home Assistant.
- Controller (max manual time, pump delay, concurrent zones, zone enabled), Weather Watch (rain / freeze / wind
  thresholds, climate adjust by month), Flow meter (limits, stop on deviation, pump protect, leak detection),
  Moisture sensors (thresholds, polling, learn mode), Notifications. Reached from the gear on Today.

## 4. New data the dashboard needs

| Need | Entity / source | Roadmap |
|---|---|---|
| Alerts | binary sensors: leak, no-flow zone, calibration out of range, stale baseline, repeater offline, sensor silent; Repairs | 0.7 |
| 7-day plan strip | sensor "Plan" with per-day attributes (date, minutes, reason) | 0.9 |
| Skip next 24 h | button / service (rain delay) | 0.6 |
| Cost | sensor: litres × tariff (option in config) | 0.9 |
| Baseline flow per zone, last baselined, run baseline | sensors + button | 0.7 |
| Live flow | sensor (live log while zones run) | 0.7 |
| Water budget per zone | sensors (balance, allowed depletion, water needed) | 0.9 |
| Next run per zone | from plan zones | 0.9 |
| Places (zone groups) | device per group / area suggestion | 0.8 |
| Schedule details | selects / numbers / times (mode, days, window, per-zone settings) | 0.8 |
| ET demand vs applied | statistics from `chart_data` | 0.10 |
| Repeaters / outlets | switch + online / last seen / signal | 0.6 |
| Baseline history | stored snapshots per baseline + per-zone baseline statistics; `record_baseline` service | 0.7 |
| Unit system | option: metric (default) / US / custom per quantity; entities convert | 0.6 |
| Water price | option: price per kL or tariff entity | 0.6 |

## 5. Build order

| Step | Ships with | Dashboard work |
|---|---|---|
| D1 | now (no new entities) | Restructure into Today / Zones / Plan / Water / Health; hero card; KPIs from statistics; fold unused zones, schedules and dead sensors; plan strip from the calendar; consistent icons and colours; phone layout pass |
| D2 | 0.6–0.7 | Alerts strip; quick "skip 24 h"; Health: flow meter, baseline table, live flow, mesh and repeaters; baseline history; settings sub-view with units and water price |
| D3 | 0.8–0.9 | Zones as places with water budget and next run; schedule editor sections; cost KPIs |
| D4 | 0.10 | ET demand vs applied; moisture vs watering charts per place; optional ApexCharts combined chart |
| D5 | 1.0 | Polish: translations, empty states ("No sensors — add one in Spruce"), screenshots for the README, a YAML copy for people who want to edit it |

Each step: build from the fake account in tests (the strategy's `generate` gets a snapshot test of its config),
check on a phone and a desktop browser, then on the live system.
