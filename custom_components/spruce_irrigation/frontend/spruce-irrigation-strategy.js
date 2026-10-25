/*
 * Spruce Irrigation dashboard: a Home Assistant dashboard strategy, served and loaded by the integration.
 * Settings → Dashboards → Add dashboard → Community dashboards → "Spruce Irrigation".
 * The dashboard is built from the integration's entities and statistics each time it opens, so new zones,
 * schedules and moisture sensors appear by themselves. Use "Take control" in the dashboard to edit it by hand.
 */
const DOMAIN = "spruce_irrigation";

const CONTROLLER_KEYS = ["status", "active_zone", "watering_ends", "water_today", "water_month", "wifi_signal",
  "last_seen", "online", "watering", "hibernate", "stop", "pair_sensor", "cancel_pairing", "freeze_temperature",
  "rain_skip_threshold", "wind_skip_threshold", "manual_max_time", "pump_delay", "concurrent_zones",
  "flow_limit_high", "flow_limit_low", "flow_stop_on_high", "flow_pump_protect", "flow_leak_sustained",
  "flow_leak_burst", "pause_until_tomorrow", "water_cost_today", "water_cost_month", "next_watering",
  "watering_today", "plan", "run_baseline", "accept_baseline", "keep_previous_baseline", "last_baselined",
  "baseline_review", "forecast", "climate_adjust", "live_flow", "flow_rate", "activity"];
const SENSOR_KEYS = ["moisture", "temperature", "battery", "signal", "last_reading"];

/** The integration's controllers, zones, schedules, moisture sensors and water statistics. */
async function collect(hass) {
  const ids = Object.values(hass.entities).filter((e) => e.platform === DOMAIN).map((e) => e.entity_id);
  if (!ids.length) return [];
  const [entries, stats] = await Promise.all([
    hass.callWS({ type: "config/entity_registry/get_entries", entity_ids: ids }),
    hass.callWS({ type: "recorder/list_statistic_ids" }).catch(() => []),
  ]);
  const byUnique = {};
  for (const [entityId, entry] of Object.entries(entries)) {
    if (entry && !entry.disabled_by && !entry.hidden_by) byUnique[entry.unique_id] = entityId;
  }
  const controllers = Object.keys(byUnique).filter((u) => u.endsWith("_status")).map((u) => u.slice(0, -7));

  return controllers.map((ctrl) => {
    const c = { id: ctrl, entities: {}, zones: {}, schedules: {}, sensors: {}, outlets: {} };
    const zoneRe = new RegExp(`^${ctrl}_zone_(\\d+)_(\\w+)$`);
    const schedRe = new RegExp(`^${ctrl}_schedule_(\\d+)_(\\w+)$`);
    const sensorRe = new RegExp(`^${ctrl}_(\\w+?)_(${SENSOR_KEYS.join("|")})$`);
    const outletRe = new RegExp(`^${ctrl}_(\\w+?)_(outlet|silent|last_seen)$`);
    for (const [unique, entityId] of Object.entries(byUnique)) {
      if (!unique.startsWith(`${ctrl}_`)) continue;
      const key = unique.slice(ctrl.length + 1);
      let m;
      if ((m = unique.match(zoneRe))) (c.zones[m[1]] ??= {})[m[2]] = entityId;
      else if ((m = unique.match(schedRe))) (c.schedules[m[1]] ??= {})[m[2]] = entityId;
      else if (CONTROLLER_KEYS.includes(key)) c.entities[key] = entityId;
      else if ((m = unique.match(outletRe))) (c.outlets[m[1]] ??= {})[m[2]] = entityId;
      else if ((m = unique.match(sensorRe))) (c.sensors[m[1]] ??= {})[m[2]] = entityId;
    }
    // Outlets (repeater plugs) also have a signal sensor: keep them out of the moisture sensors
    for (const [id, o] of Object.entries(c.outlets)) {
      if (c.sensors[id]) { o.signal = c.sensors[id].signal; delete c.sensors[id]; }
      o.name = deviceName(hass, o.outlet || o.last_seen) || "Outlet";
    }
    c.name = deviceName(hass, c.entities.status) || "Spruce";
    for (const s of Object.values(c.sensors)) s.name = deviceName(hass, s.moisture || s.last_reading) || "Moisture sensor";

    const short = ctrl.toLowerCase().replace(/[^a-z0-9]/g, "").slice(-8);
    const prefix = `${DOMAIN}:${short}_`;
    c.stats = { total: null, rain: null, temperature: null, zones: {}, zoneMinutes: {}, schedules: {} };
    for (const s of stats) {
      const id = s.statistic_id;
      if (!id.startsWith(prefix)) continue;
      const rest = id.slice(prefix.length);
      let m;
      if (rest === "water") c.stats.total = id;
      else if (rest === "rain") c.stats.rain = id;
      else if (rest === "temperature") c.stats.temperature = id;
      else if ((m = rest.match(/^zone_(\d+)_schedule_(\d+)_water$/))) (c.stats.schedules[m[2]] ??= []).push({ zone: m[1], id });
      else if ((m = rest.match(/^zone_(\d+)_water$/))) c.stats.zones[m[1]] = id;
      else if ((m = rest.match(/^zone_(\d+)_minutes$/))) c.stats.zoneMinutes[m[1]] = id;
    }
    return c;
  });
}

function deviceName(hass, entityId) {
  const device = hass.devices[hass.entities[entityId]?.device_id];
  return device && (device.name_by_user || device.name);
}

/** An entity's name without the controller (device) name in front. */
function shortName(hass, entityId, deviceName, fallback) {
  const full = hass.states[entityId]?.attributes?.friendly_name;
  if (!full) return fallback;
  return full.startsWith(`${deviceName} `) ? full.slice(deviceName.length + 1) : full;
}

const byNumber = (obj) => Object.keys(obj).sort((a, b) => Number(a) - Number(b));
let naming = null;   // {hass, device}: tiles are named without the controller's name in front
const tile = (entity, extra = {}) => (entity ? {
  type: "tile", entity, ...(naming ? { name: shortName(naming.hass, entity, naming.device, undefined) } : {}), ...extra,
} : null);
const press = (entity, confirmation) => tile(entity, {
  hide_state: true,
  tap_action: { action: "perform-action", perform_action: "button.press", target: { entity_id: entity },
    ...(confirmation ? { confirmation: { text: confirmation } } : {}) },
  icon_tap_action: { action: "none" },
});
const heading = (text, icon) => ({ type: "heading", heading: text, ...(icon ? { icon } : {}) });
const section = (cards, span) => ({ type: "grid", cards: cards.filter(Boolean), ...(span ? { column_span: span } : {}) });
const waterChart = (title, entities, period, days, chart = "bar") => ({
  type: "statistics-graph", title, entities, period, days_to_show: days, stat_types: ["change"],
  chart_type: chart, hide_legend: entities.length < 2, grid_options: { columns: "full" },
});

/** The next watering as a date and time (a tile would say "in 2 weeks"), with today's plan. */
function planCard(c) {
  const next = c.entities.next_watering, today = c.entities.watering_today;
  if (!next) return null;
  const when = "%a %-d %b, %-I:%M %p";
  return {
    type: "markdown", grid_options: { columns: "full" },
    content: `{% set n = '${next}' %}{% set t = states(n) %}`
      + "{% if t in ['unknown', 'unavailable'] %}**Next watering:** none planned in Spruce's forecast"
      + "{% else %}{% set a = states[n].attributes %}"
      + `**Next watering:** {{ as_timestamp(t) | timestamp_custom('${when}') }}`
      + " to {{ as_timestamp(a.ends) | timestamp_custom('%-I:%M %p') }}, {{ a.minutes }} min ({{ a.schedule }})"
      + "{% endif %}"
      + (today ? `\n\n**Today:** {{ state_translated('${today}') }}` : ""),
  };
}

function rainChart(c, title, period, days) {
  return c.stats.rain ? waterChart(title, [{ entity: c.stats.rain, name: "Rain" }], period, days) : null;
}

function zoneNames(hass, c) {
  const names = {};
  for (const z of byNumber(c.zones)) names[z] = shortName(hass, c.zones[z].valve, c.name, `Zone ${z}`);
  return names;
}

function scheduleNames(hass, c) {
  const names = {};
  for (const [sid, e] of Object.entries(c.schedules)) {
    const run = e.run && shortName(hass, e.run, c.name, null);
    names[sid] = (e.enabled && shortName(hass, e.enabled, c.name, null)) || (run && run.replace(/^Run /, ""))
      || `Schedule ${sid}`;
  }
  return names;
}

function overviewView(hass, c, zones, schedules) {
  const e = c.entities;
  const sections = [
    section([
      heading("Controller", "mdi:sprinkler-variant"),
      tile(e.status), tile(e.active_zone), tile(e.watering_ends), tile(e.online),
      planCard(c),
      tile(e.water_today), tile(e.water_month), tile(e.water_cost_month),
      tile(e.activity, { name: "Latest activity", state_content: ["event_type", "message"] }),
      tile(e.hibernate), press(e.pause_until_tomorrow, "Skip scheduled watering until tomorrow?"), press(e.stop),
    ]),
    e.forecast ? section([heading("Forecast Spruce plans with", "mdi:weather-partly-rainy"),
      { type: "weather-forecast", entity: e.forecast, forecast_type: "daily", grid_options: { columns: "full" } }]) : null,
    section([
      heading("Zones", "mdi:valve"),
      ...byNumber(c.zones).map((z) => tile(c.zones[z].valve, {
        name: zones[z], state_content: ["state", "last_changed"],
      })),
    ]),
  ];
  const watering = Object.keys(c.schedules).filter((sid) => c.schedules[sid].enabled);
  const runOnly = Object.keys(c.schedules).filter((sid) => !c.schedules[sid].enabled && c.schedules[sid].run);
  if (watering.length || runOnly.length) {
    sections.push(section([
      heading("Schedules", "mdi:calendar-clock"),
      ...watering.map((sid) => tile(c.schedules[sid].enabled, {
        name: schedules[sid], state_content: ["state", "start_time", "watering_days"],
      })),
      ...runOnly.map((sid) => press(c.schedules[sid].run, `Run ${schedules[sid]} now?`)),
    ]));
  }
  if (c.stats.total) {
    sections.push(section([heading("Water used", "mdi:water"),
      waterChart("Last 30 days", [{ entity: c.stats.total, name: "Water" }], "day", 30),
      rainChart(c, "Rain, last 30 days", "day", 30)], 2));
  }
  const unavailable = ["unavailable", "unknown", undefined];
  const sensors = Object.values(c.sensors).filter((s) => !unavailable.includes(hass.states[s.moisture]?.state));
  if (sensors.length) {
    sections.push(section([heading("Moisture", "mdi:water-percent"),
      ...sensors.map((s) => tile(s.moisture, { name: s.name, state_content: ["state", "last_changed"] }))]));
  }
  return {
    type: "sections", title: "Overview", path: "overview", icon: "mdi:sprinkler-variant", max_columns: 4,
    badges: [e.status, e.active_zone, e.water_today].filter(Boolean).map((entity) => ({ type: "entity", entity })),
    sections: sections.filter(Boolean),
  };
}

function zonesView(c, zones) {
  return {
    type: "sections", title: "Zones", path: "zones", icon: "mdi:valve", max_columns: 4,
    sections: byNumber(c.zones).map((z) => {
      const e = c.zones[z];
      return section([
        heading(zones[z], "mdi:sprinkler"),
        tile(e.valve, { name: "Valve", state_content: ["state", "last_changed"] }),
        tile(e.run_time, { name: "Run time", features: [{ type: "numeric-input", style: "buttons" }] }),
        tile(e.water_today, { name: "Water today" }),
        tile(e.last_run, { name: "Last run" }),
        tile(e.next_watering, { name: "Next watering", state_content: ["state", "minutes", "days_next_7"] }),
        tile(e.last_flow, { name: "Last flow" }),
        tile(e.usual_flow, { name: "Usual flow" }),
        tile(e.flow_fault, { name: "Flow fault" }),
        tile(e.flow_check, { name: "Flow check", state_content: ["state", "deviation_percent"] }),
        tile(e.baseline_flow, { name: "Baseline flow" }),
        tile(e.design_flow, { name: "Design flow (heads)" }),
        c.stats.zones[z] ? waterChart("Water, last 30 days", [{ entity: c.stats.zones[z], name: zones[z] }], "day", 30)
          : null,
      ]);
    }),
  };
}

function schedulesView(hass, c, zones, schedules) {
  const all = Object.keys(c.schedules).filter((sid) => c.schedules[sid].enabled);
  const off = all.filter((sid) => hass.states[c.schedules[sid].enabled]?.state === "off");
  const sections = all.filter((sid) => !off.includes(sid)).map((sid) => {
    const e = c.schedules[sid];
    const series = (c.stats.schedules[sid] || []).sort((a, b) => a.zone - b.zone)
      .map((s) => ({ entity: s.id, name: zones[s.zone] || `Zone ${s.zone}` }));
    return section([
      heading(schedules[sid], "mdi:calendar-clock"),
      tile(e.enabled, { name: "Enabled", state_content: ["state", "watering_days"] }),
      tile(e.start_time, { name: "Start time" }),
      tile(e.start_mode, { name: "Start mode" }),
      e.start_offset && hass.states[e.start_mode]?.state !== "set" ? tile(e.start_offset, { name: "Offset from the sun" }) : null,
      tile(e.days, { name: "Watering days", state_content: ["state", "watering_days"] }),
      tile(e.window, { name: "Watering window", state_content: ["state", "start", "end"] }),
      hass.states[e.window]?.state === "on" ? tile(e.window_start, { name: "Window start" }) : null,
      hass.states[e.window]?.state === "on" ? tile(e.window_end, { name: "Window end" }) : null,
      tile(e.rain_skip, { name: "Rain skip" }),
      tile(e.freeze_skip, { name: "Freeze skip" }),
      tile(e.wind_skip, { name: "Wind skip" }),
      press(e.run, `Run ${schedules[sid]} now?`),
      series.length ? waterChart("Water by zone, last 30 days", series, "day", 30) : null,
    ]);
  });
  const zonesPlanned = byNumber(c.zones).filter((z) => c.zones[z].next_watering);
  if (zonesPlanned.length) {   // Spruce's projection per zone, as its schedule page shows it
    const when = "%a %-d %b %-I:%M %p";
    sections.unshift(section([heading("Next 7 days by zone", "mdi:calendar-week"), {
      type: "markdown", grid_options: { columns: "full" },
      content: "| Zone | Next | Minutes | Days in next 7 | Why |\n|---|---|---|---|---|\n" + zonesPlanned.map((z) => {
        const id = c.zones[z].next_watering;
        return `{% set a = states['${id}'].attributes %}| ${zones[z]} | {% if states('${id}') not in ['unknown', 'unavailable'] %}`
          + `{{ as_timestamp(states('${id}')) | timestamp_custom('${when}') }}{% else %}–{% endif %}`
          + " | {{ a.get('minutes') or '–' }} | {{ a.get('days_next_7', 0) }} (avg {{ a.get('average_minutes', 0) }} min)"
          + " | {{ a.get('message') or a.get('today') or '' }} |";
      }).join("\n"),
    }], 2));
  }
  if (c.entities.plan) {
    sections.unshift(section([
      heading("Watering plan", "mdi:calendar-month"),
      planCard(c),
      { type: "calendar", entities: [c.entities.plan], initial_view: "dayGridMonth", grid_options: { columns: "full" } },
    ], 2));
  }
  if (off.length) {   // turned off: just the switch to turn it back on and a run button (refresh after changing)
    sections.push(section([
      heading("Turned off", "mdi:calendar-remove"),
      ...off.flatMap((sid) => [
        tile(c.schedules[sid].enabled, { name: schedules[sid], state_content: ["state", "watering_days"] }),
        press(c.schedules[sid].run, `Run ${schedules[sid]} now?`),
      ]),
    ]));
  }
  const other = Object.keys(c.stats.schedules).filter((sid) => !c.schedules[sid]?.enabled);
  if (other.length) {
    sections.push(section([
      heading("Manual and unscheduled watering", "mdi:hand-water"),
      waterChart("Last 90 days", other.flatMap((sid) => c.stats.schedules[sid].map((s) => ({
        entity: s.id, name: `${zones[s.zone] || `Zone ${s.zone}`} · ${schedules[sid] || `Schedule ${sid}`}`,
      }))), "week", 90),
    ], 2));
  }
  sections.push(section([heading("Weather Watch (all schedules)", "mdi:weather-snowy-rainy"),
    tile(c.entities.rain_skip_threshold, { name: "Rain skip above" }),
    tile(c.entities.freeze_temperature, { name: "Freeze skip below" }),
    tile(c.entities.wind_skip_threshold, { name: "Wind skip above" }),
    tile(c.entities.climate_adjust, { name: "Climate adjust this month" })]));
  return { type: "sections", title: "Schedules", path: "schedules", icon: "mdi:calendar-clock", max_columns: 4, sections };
}

function historyView(c, zones) {
  const zoneSeries = (stats) => byNumber(stats).map((z) => ({ entity: stats[z], name: zones[z] || `Zone ${z}` }));
  return {
    type: "sections", title: "History", path: "history", icon: "mdi:chart-bar", max_columns: 4,
    sections: [
      // Rain (mm) and water (L) can't share a chart's axis: one chart each, same days, one above the other
      c.stats.rain ? section([heading("Weather and watering, last 30 days", "mdi:weather-pouring"),
        rainChart(c, "Rain", "day", 30),
        c.stats.temperature ? {
          type: "statistics-graph", title: "Temperature (low and high)", period: "day", days_to_show: 30,
          entities: [{ entity: c.stats.temperature, name: "Temperature" }], stat_types: ["min", "max"],
          chart_type: "line", grid_options: { columns: "full" },
        } : null,
        waterChart("Water by zone", zoneSeries(c.stats.zones), "day", 30),
      ], 4) : null,
      c.stats.rain ? section([heading("Rain and watering, last 12 months", "mdi:weather-pouring"),
        rainChart(c, "Rain per month", "month", 365),
        c.stats.total ? waterChart("Water per month", [{ entity: c.stats.total, name: "Water" }], "month", 365) : null,
      ], 4) : null,
      c.stats.total ? section([heading("Total water", "mdi:water"),
        waterChart("Per month, last 2 years", [{ entity: c.stats.total, name: "Water" }], "month", 730)], 4) : null,
      section([heading("Water by zone", "mdi:sprinkler"),
        waterChart("Per month, last 12 months", zoneSeries(c.stats.zones), "month", 365)], 4),
      section([heading("Watering time by zone", "mdi:timer-outline"),
        waterChart("Per week, last 12 weeks", zoneSeries(c.stats.zoneMinutes), "week", 84)], 4),
    ].filter(Boolean),
  };
}

/** Flow health: baselines (run, review, history before and after) and each zone's flow against its design. */
function flowView(c, zones) {
  const e = c.entities;
  const checks = byNumber(c.zones).filter((z) => c.zones[z].flow_check);
  if (!checks.length && !e.last_baselined) return null;
  const row = (z) => {
    const id = c.zones[z].flow_check;
    return `{% set a = states['${id}'].attributes %}| ${zones[z]} | {{ a.design_flow or '–' }} | {{ a.baseline_flow or '–' }}`
      + ` | {{ a.last_flow or '–' }} | {{ state_translated('${id}') }}{% if a.deviation_percent is not none %}`
      + ` ({{ '%+d' % a.deviation_percent }}%){% endif %} |`;
  };
  const unit = checks.length ? `{{ state_attr('${c.zones[checks[0]].flow_check}', 'unit') }}` : "";
  const flowTable = checks.length ? {
    type: "markdown", grid_options: { columns: "full" },
    content: `Checked against the design flow from the zone's heads, else the accepted baseline (${unit}).\n\n`
      + "| Zone | Design | Baseline | Last run | Check |\n|---|---|---|---|---|\n" + checks.map(row).join("\n"),
  } : null;
  // The last four baselines side by side, and the change from the one before to the latest
  const history = e.last_baselined ? {
    type: "markdown", grid_options: { columns: "full" },
    content: `{% set b = state_attr('${e.last_baselined}', 'baselines') or [] %}{% set cols = b[:4] %}`
      + "{% if not cols %}No baselines recorded yet.{% else %}"
      + `Flow in {{ state_attr('${e.last_baselined}', 'unit') }}; ⚠ = waiting for review or kept at the previous value.\n\n`
      + "| Zone |{% for s in cols %} {{ s.date }}{% if s.source == 'manual' %} ✎{% endif %} |{% endfor %}"
      + "{% if cols | length > 1 %} Change |{% endif %}\n"
      + "|---|{% for s in cols %}---|{% endfor %}{% if cols | length > 1 %}---|{% endif %}\n"
      + "{% for z in cols[0].zones %}| {{ z }} |{% for s in cols %} {{ s.zones.get(z, '–') }}"
      + "{% if s.review and z in s.review %} ⚠{% endif %} |{% endfor %}"
      + "{% if cols | length > 1 %}{% set new = cols[0].zones.get(z) %}{% set old = cols[1].zones.get(z) %}"
      + " {% if new is number and old %}{{ '%+d' % ((new - old) / old * 100) }}%{% else %}–{% endif %} |{% endif %}\n"
      + "{% endfor %}{% endif %}",
  } : null;
  return {
    type: "sections", title: "Flow health", path: "flow", icon: "mdi:water-check", max_columns: 4,
    sections: [
      section([heading("Flow baseline", "mdi:chart-bell-curve"),
        tile(e.live_flow, { name: "Live flow (10 min)" }), tile(e.flow_rate, { name: "Flow rate", state_content: ["state", "average_10_s"] }),
        tile(e.last_baselined, { name: "Last baseline" }), tile(e.baseline_review, { name: "Zones to review" }),
        press(e.run_baseline, "Run a flow baseline? Every enabled zone waters for about a minute (roughly 1 min 10 s per zone)."),
        press(e.accept_baseline, "Accept the new baseline for the zones waiting for review?"),
        press(e.keep_previous_baseline, "Keep the previous baseline for the zones waiting for review?")]),
      section([heading("Zone heads and design flow", "mdi:sprinkler-variant"), {
        type: "markdown",
        content: "Enter the heads in each zone (model, arc, how many) to get its **design flow**: a reference a leak "
          + "can't move, so a baseline taken with a leak or a missing nozzle is caught.\n\n"
          + "[Open the integration](/config/integrations/integration/spruce_irrigation) → **Add zone heads**; "
          + "your own nozzles and drippers: **Add head model**.",
      }]),
      flowTable && section([heading("Flow check", "mdi:water-check"), flowTable], 4),
      history && section([heading("Baseline history (before and after)", "mdi:history"), history], 4),
    ].filter(Boolean),
  };
}

function diagnosticsView(c) {
  const e = c.entities;
  return {
    type: "sections", title: "Diagnostics", path: "diagnostics", icon: "mdi:stethoscope", max_columns: 4,
    sections: [
      section([heading("Controller", "mdi:wifi"), tile(e.online), tile(e.wifi_signal), tile(e.last_seen),
        press(e.pair_sensor, "Let a moisture sensor join the controller?"), press(e.cancel_pairing)]),
      e.flow_limit_high && section([heading("Flow meter", "mdi:water-check"), tile(e.flow_limit_high), tile(e.flow_limit_low),
        tile(e.flow_stop_on_high), tile(e.flow_pump_protect), tile(e.flow_leak_sustained), tile(e.flow_leak_burst)]),
      section([heading("Controller settings", "mdi:cog"), tile(e.manual_max_time), tile(e.pump_delay),
        tile(e.concurrent_zones)]),
      ...Object.values(c.outlets).map((o) => section([
        heading(o.name, "mdi:power-socket-au"), tile(o.outlet, { name: "Socket" }), tile(o.silent, { name: "Not reporting" }),
        tile(o.last_seen, { name: "Last report" }), tile(o.signal, { name: "Signal" }),
      ])),
      ...Object.values(c.sensors).map((s) => section([
        heading(s.name, "mdi:leaf"), tile(s.moisture, { name: "Moisture" }), tile(s.temperature, { name: "Temperature" }),
        tile(s.battery, { name: "Battery" }), tile(s.signal, { name: "Signal" }),
        tile(s.last_reading, { name: "Last reading" }),
      ])),
    ].filter(Boolean),
  };
}

class SpruceIrrigationDashboardStrategy extends HTMLElement {
  static noEditor = true;

  static async generate(config, hass) {
    const controllers = await collect(hass);
    if (!controllers.length) {
      return { title: config.title || "Irrigation", views: [{ title: "Irrigation", cards: [{ type: "markdown",
        content: "No Spruce controller found. Add the **Spruce Irrigation** integration in Settings → Devices & services." }] }] };
    }
    const views = [];
    for (const [i, c] of controllers.entries()) {
      naming = { hass, device: c.name };
      const zones = zoneNames(hass, c);
      const schedules = scheduleNames(hass, c);
      const own = [overviewView(hass, c, zones, schedules), zonesView(c, zones), schedulesView(hass, c, zones, schedules),
        historyView(c, zones), flowView(c, zones), diagnosticsView(c)].filter(Boolean);
      if (controllers.length > 1) {
        for (const v of own) { v.title = `${c.name} ${v.title}`; v.path = `${v.path}-${i + 1}`; }
      }
      views.push(...own);
    }
    naming = null;
    return { title: config.title || "Irrigation", views };
  }
}

// Define the strategy only once Home Assistant's app has started. On browsers without scoped custom element
// registries (e.g. the Android app's WebView) the app installs a polyfill that replaces window.customElements;
// anything defined before that is lost, and the polyfill then refuses the same name a second time.
const TAG = "ll-strategy-dashboard-spruce-irrigation";
const register = () => {
  if (!customElements.get("home-assistant")) return false;   // the app (and its registry) isn't up yet
  if (!customElements.get(TAG)) {
    customElements.define(TAG, class extends SpruceIrrigationDashboardStrategy {});
  }
  return true;
};
if (!register()) {
  const started = Date.now();
  const timer = setInterval(() => {
    let done = false;
    try {
      done = register();
    } catch (err) {
      done = true;
      console.error("Spruce Irrigation dashboard:", err);
    }
    if (done || Date.now() - started > 60000) clearInterval(timer);
  }, 20);
}

window.customStrategies = window.customStrategies || [];
if (!window.customStrategies.some((s) => s.type === "spruce-irrigation")) {
  window.customStrategies.push({
    type: "spruce-irrigation",
    strategyType: "dashboard",
    name: "Spruce Irrigation",
    description: "Zones, schedules, water use and moisture for your Spruce controller, built from the integration.",
    documentationURL: "https://github.com/UnpaidMoneyMike/ha-spruce-irrigation",
  });
}
