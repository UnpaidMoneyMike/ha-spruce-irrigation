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
  "baseline_review", "forecast", "climate_adjust", "live_flow", "flow_rate", "activity", "water_week", "rain_week",
  "week_plan"];
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

const ICON = { water: "💧", rain: "🌧️", rain_sensor: "🌧️", freeze: "❄️", wind: "💨", hibernating: "💤", not_needed: "✓", none: "–" };
const unavailable = ["unavailable", "unknown", undefined];
const md = (content, extra = {}) => ({ type: "markdown", content, grid_options: { columns: "full" }, ...extra });
const action = (entity, icon, name, tap, color) => (entity ? {
  type: "tile", entity, name, icon, hide_state: true, ...(color ? { color } : {}),
  grid_options: { columns: 6 }, tap_action: tap, icon_tap_action: tap,
} : null);
const pressAction = (entity, confirmation) => ({
  action: "perform-action", perform_action: "button.press", target: { entity_id: entity },
  ...(confirmation ? { confirmation: { text: confirmation } } : {}),
});

function zoneNames(hass, c) {
  const names = {};
  for (const z of byNumber(c.zones)) {
    const e = c.zones[z];
    names[z] = e.valve ? shortName(hass, e.valve, c.name, `Zone ${z}`)
      : (shortName(hass, e.enabled, c.name, `Zone ${z}`) || "").replace(/ enabled$/, "");
  }
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

const activeZones = (c) => byNumber(c.zones).filter((z) => c.zones[z].valve);

/** Problems worth knowing about now, as coloured alerts (or one green line when all is well). */
function alertsCard(c, zones) {
  const e = c.entities;
  const words = "{'no_flow': 'no flow measured', 'high': 'flow higher than expected — check for a leak or a missing nozzle', "
    + "'low': 'flow lower than expected — check for a blocked dripper or a closed valve'}";
  let t = "{% set ns = namespace(items=[]) %}";
  if (e.online) t += `{% if is_state('${e.online}', 'off') %}{% set ns.items = ns.items + ['error|Controller offline — schedules may not run'] %}{% endif %}`;
  for (const z of activeZones(c)) {
    const id = c.zones[z].flow_check;
    if (!id) continue;
    t += `{% set s = states('${id}') %}{% if s in ['no_flow', 'high', 'low'] %}{% set d = state_attr('${id}', 'deviation_percent') %}`
      + `{% set ns.items = ns.items + ['warning|${zones[z].replace(/'/g, "")}: ' ~ ${words}[s] ~ (' (' ~ ('%+d' % d) ~ '%)' if d is not none and s != 'no_flow' else '')] %}{% endif %}`;
  }
  if (e.baseline_review) t += `{% if states('${e.baseline_review}') | int(0) > 0 %}{% set ns.items = ns.items + ['warning|New flow baseline: ' ~ states('${e.baseline_review}') ~ ' zone(s) need your review (Health)'] %}{% endif %}`;
  const silent = Object.values(c.outlets).map((o) => o.silent).filter(Boolean);
  if (silent.length) {
    t += `{% set n = [${silent.map((s) => `'${s}'`).join(", ")}] | select('is_state', 'on') | list | count %}`
      + "{% if n %}{% set ns.items = ns.items + ['warning|' ~ n ~ ' sensor repeater(s) not reporting — moisture sensors may be silent'] %}{% endif %}";
  }
  if (e.hibernate) t += `{% if is_state('${e.hibernate}', 'on') %}{% set ns.items = ns.items + ['info|Hibernating: scheduled watering is paused'] %}{% endif %}`;
  t += "{% for i in ns.items %}<ha-alert alert-type=\"{{ i.split('|')[0] }}\">{{ i.split('|')[1] }}</ha-alert>{% endfor %}"
    + "{% if not ns.items %}<ha-alert alert-type=\"success\">All good: no flow problems, offline devices or reviews waiting.</ha-alert>{% endif %}";
  return md(t, { text_only: true });
}

/** Status and the next watering in big type, with today's plan in words. */
function heroCard(c) {
  const e = c.entities;
  if (!e.status) return null;
  const when = "%a %-d %b · %-I:%M %p";
  let t = `## {{ state_translated('${e.status}') }}\n`;
  if (e.online) t += `{% if is_state('${e.online}', 'on') %}Controller online{% else %}**Controller offline**{% endif %}`;
  if (e.active_zone) t += `{% if states('${e.active_zone}') != 'none' %} · watering **{{ states('${e.active_zone}') }}**{% endif %}`;
  if (e.next_watering) {
    t += `\n\n**Next watering**\n{% set n = states('${e.next_watering}') %}{% if n in ['unknown', 'unavailable'] %}`
      + "\nNothing planned in Spruce's forecast{% else %}{% set a = states['" + e.next_watering + "'].attributes %}"
      + `\n### {{ as_timestamp(n) | timestamp_custom('${when}') }}`
      + "\n{{ a.get('schedule', '') }} · {{ a.get('minutes', '?') }} min · {{ a.get('zones', {}) | length }} zones"
      + "{% if a.get('ends') %} · ends {{ as_timestamp(a.ends) | timestamp_custom('%-I:%M %p') }}{% endif %}{% endif %}";
  }
  if (e.watering_today) {
    t += `\n\n**Today:** {{ state_translated('${e.watering_today}') }}`
      + `{% set s = (state_attr('${e.watering_today}', 'schedules') or {}).values() | map(attribute='message') | select | first %}`
      + "{% if s %} — {{ s }}{% endif %}";
  }
  return md(t);
}

/** The next 7 days, one column each: water (minutes) or why not. */
function weekCard(c) {
  const id = c.entities.week_plan;
  if (!id) return null;
  return md(`{% set days = state_attr('${id}', 'days') or [] %}{% set icon = ${JSON.stringify(ICON)} %}`
    + "|{% for d in days %} {{ d.weekday[:2] }} |{% endfor %}\n|{% for d in days %}:-:|{% endfor %}\n"
    + "|{% for d in days %} {{ icon.get(d.outcome, '–') }}{% if d.outcome == 'water' %}<br>{{ d.minutes }}′{% endif %} |{% endfor %}\n\n"
    + "<sub>💧 water (minutes) · ✓ soil has enough · 🌧️ rain · 💨 wind · ❄️ freeze skip</sub>");
}

function todayView(hass, c, zones) {
  const e = c.entities;
  const zonesNow = activeZones(c).sort((a, b) => (hass.states[c.zones[b].valve]?.state === "open")
    - (hass.states[c.zones[a].valve]?.state === "open"));
  return {
    type: "sections", title: "Today", path: "today", icon: "mdi:sprinkler-variant", max_columns: 3,
    sections: [
      section([alertsCard(c, zones)], 3),
      section([
        heroCard(c),
        action(e.stop, "mdi:stop-circle", "Stop", pressAction(e.stop), "red"),
        action(zonesNow.length ? c.zones[zonesNow[0]].valve : null, "mdi:sprinkler", "Run zone",
          { action: "navigate", navigation_path: "zones" }, "blue"),
        action(e.pause_until_tomorrow, "mdi:weather-cloudy-clock", "Skip 24 h",
          pressAction(e.pause_until_tomorrow, "Skip scheduled watering until tomorrow?"), "amber"),
        action(e.hibernate, "mdi:snowflake", "Hibernate", { action: "toggle" }, "cyan"),
      ]),
      section([heading("Next 7 days", "mdi:calendar-week"), weekCard(c),
        tile(e.water_week, { name: "Water, last 7 days", state_content: ["state", "change_percent"], color: "blue" }),
        tile(e.water_month, { name: "This month", color: "blue" }),
        tile(e.water_cost_month, { name: "Cost this month", color: "green" }),
        tile(e.rain_week, { name: "Rain, last 7 days", color: "teal" }),
        tile(e.forecast, { name: "Forecast", state_content: ["state", "temperature"] }),
        tile(e.activity, { name: "Latest activity", state_content: ["event_type", "message"] })]),
      section([heading("Zones", "mdi:valve"),
        ...zonesNow.map((z) => tile(c.zones[z].valve, { name: zones[z], color: "blue",
          state_content: ["state", "last_changed"], grid_options: { columns: 6 } }))]),
    ],
  };
}

/** One section per place (zone group), as in the Spruce app: each zone's valve and run time, flow against its
 * reference, next watering and water budget, and its water over 30 days. */
function zonesView(hass, c, zones) {
  const places = {};
  for (const z of activeZones(c)) {
    const place = hass.states[c.zones[z].valve]?.attributes?.place || zones[z];
    (places[place] ??= []).push(z);
  }
  const budget = (id) => `{% set b = state_attr('${id}', 'water_budget') %}{% if b and b.allowed_depletion_mm %}`
    + "{% set pct = ((b.soil_water_mm or 0) / b.allowed_depletion_mm * 100) | round(0) | int %}"
    + "{% set pct = [0, [pct, 100] | min] | max %}"
    + "Soil water {{ '█' * (pct // 10) }}{{ '░' * (10 - pct // 10) }} {{ pct }} %"
    + " · needs {{ b.needed_mm }} mm{% endif %}";
  const sections = Object.entries(places).map(([place, members]) => section([
    heading(place, "mdi:sprinkler"),
    ...members.flatMap((z) => {
      const e = c.zones[z];
      return [
        members.length > 1 ? { type: "heading", heading: zones[z], heading_style: "subtitle" } : null,
        tile(e.valve, { name: "Valve", color: "blue", state_content: ["state", "last_changed"] }),
        tile(e.run_time, { name: "Run time", features: [{ type: "numeric-input", style: "buttons" }] }),
        tile(e.flow_check, { name: "Flow", state_content: ["state", "last_flow", "reference_flow"] }),
        tile(e.next_watering, { name: "Next watering", state_content: ["state", "minutes"] }),
        e.next_watering ? md(budget(e.next_watering), { text_only: true }) : null,
      ];
    }),
    members.some((z) => c.stats.zones[z]) ? waterChart("Water, last 30 days",
      members.filter((z) => c.stats.zones[z]).map((z) => ({ entity: c.stats.zones[z], name: zones[z] })), "day", 30) : null,
  ]));
  const off = byNumber(c.zones).filter((z) => !c.zones[z].valve && c.zones[z].enabled);
  if (off.length) {
    sections.push(section([heading("Not in use", "mdi:valve-closed"),
      ...off.map((z) => tile(c.zones[z].enabled, { name: zones[z], grid_options: { columns: 6 } }))]));
  }
  return { type: "sections", title: "Zones", path: "zones", icon: "mdi:valve", max_columns: 3, sections };
}

function planView(hass, c, zones, schedules) {
  const e = c.entities;
  const all = Object.keys(c.schedules).filter((sid) => c.schedules[sid].enabled);
  const on = all.filter((sid) => hass.states[c.schedules[sid].enabled]?.state !== "off");
  const off = all.filter((sid) => !on.includes(sid));
  const manual = Object.keys(c.schedules).filter((sid) => !c.schedules[sid].enabled && c.schedules[sid].run);
  const when = "%a %-d %b %-I:%M %p";
  const zonePlan = activeZones(c).filter((z) => c.zones[z].next_watering);
  const sections = [
    e.plan ? section([heading("Watering plan", "mdi:calendar-month"),
      { type: "calendar", entities: [e.plan], initial_view: "listWeek", grid_options: { columns: "full" } }], 2) : null,
    zonePlan.length ? section([heading("Next 7 days by zone", "mdi:calendar-week"), md(
      "| Zone | Next | Min | 7 days | Why |\n|---|---|---|---|---|\n" + zonePlan.map((z) => {
        const id = c.zones[z].next_watering;
        return `{% set a = states['${id}'].attributes %}| ${zones[z]} | {% if states('${id}') not in ['unknown', 'unavailable'] %}`
          + `{{ as_timestamp(states('${id}')) | timestamp_custom('${when}') }}{% else %}–{% endif %}`
          + " | {{ a.get('minutes') or '–' }} | {{ a.get('days_next_7', 0) }} × {{ a.get('average_minutes', 0) }}′"
          + " | {{ a.get('message') or a.get('today') or '' }} |";
      }).join("\n"))], 2) : null,
    ...on.map((sid) => {
      const s = c.schedules[sid];
      const summary = `{{ states('${s.start_time}')[:5] if '${s.start_time || ""}' else '' }}`
        + (s.days ? ` · {{ state_translated('${s.days}') | lower }}` : "")
        + (s.window ? ` · window {{ 'on' if is_state('${s.window}', 'on') else 'off' }}` : "")
        + (s.rain_skip ? ` · skips: {{ ['rain' if is_state('${s.rain_skip}', 'on'), 'freeze' if is_state('${s.freeze_skip}', 'on'), 'wind' if is_state('${s.wind_skip}', 'on')] | select | join(', ') or 'none' }}` : "");
      return section([
        heading(schedules[sid], "mdi:calendar-clock"),
        md(summary, { text_only: true }),
        tile(s.enabled, { name: "Enabled", color: "blue" }),
        press(s.run, `Run ${schedules[sid]} now?`),
        tile(s.start_time, { name: "Start time" }),
        tile(s.start_mode, { name: "Start mode" }),
        s.start_offset && hass.states[s.start_mode]?.state !== "set" ? tile(s.start_offset, { name: "Offset" }) : null,
        tile(s.days, { name: "Days", state_content: ["state", "watering_days"] }),
        tile(s.window, { name: "Watering window", state_content: ["state", "start", "end"] }),
        hass.states[s.window]?.state === "on" ? tile(s.window_start, { name: "From" }) : null,
        hass.states[s.window]?.state === "on" ? tile(s.window_end, { name: "To" }) : null,
        tile(s.rain_skip, { name: "Rain skip", color: "teal" }),
        tile(s.freeze_skip, { name: "Freeze skip", color: "cyan" }),
        tile(s.wind_skip, { name: "Wind skip", color: "grey" }),
      ]);
    }),
    section([heading("Weather Watch (all schedules)", "mdi:weather-snowy-rainy"),
      tile(e.rain_skip_threshold, { name: "Rain skip above" }), tile(e.freeze_temperature, { name: "Freeze skip below" }),
      tile(e.wind_skip_threshold, { name: "Wind skip above" }), tile(e.climate_adjust, { name: "Climate adjust" })]),
    manual.length ? section([heading("Manual", "mdi:hand-water"),
      ...manual.map((sid) => press(c.schedules[sid].run, `Run ${schedules[sid]} now?`))]) : null,
    off.length ? section([heading("Not in use", "mdi:calendar-remove"),
      ...off.map((sid) => tile(c.schedules[sid].enabled, { name: schedules[sid], state_content: ["state", "watering_days"] }))]) : null,
  ];
  return { type: "sections", title: "Plan", path: "plan", icon: "mdi:calendar-clock", max_columns: 3, sections: sections.filter(Boolean) };
}

function rainChart(c, title, period, days) {
  return c.stats.rain ? waterChart(title, [{ entity: c.stats.rain, name: "Rain" }], period, days) : null;
}

function waterView(c, zones, schedules) {
  const e = c.entities;
  const zoneSeries = (stats) => byNumber(stats).filter((z) => zones[z]).map((z) => ({ entity: stats[z], name: zones[z] }));
  const other = Object.keys(c.stats.schedules).filter((sid) => !c.schedules[sid]?.enabled);
  return {
    type: "sections", title: "Water", path: "water", icon: "mdi:water", max_columns: 3,
    sections: [
      section([heading("Water and cost", "mdi:cash"),
        tile(e.water_today, { name: "Today", color: "blue" }), tile(e.water_week, { name: "Last 7 days", color: "blue",
          state_content: ["state", "change_percent"] }),
        tile(e.water_month, { name: "This month", color: "blue" }), tile(e.water_cost_month, { name: "Cost this month", color: "green" }),
        tile(e.rain_week, { name: "Rain, last 7 days", color: "teal" }), tile(e.climate_adjust, { name: "Climate adjust" })]),
      // Rain (mm) and water (L) can't share a chart's axis: one chart each, same days, one above the other
      section([heading("Last 30 days", "mdi:weather-pouring"),
        rainChart(c, "Rain", "day", 30),
        c.stats.temperature ? {
          type: "statistics-graph", title: "Temperature (low and high)", period: "day", days_to_show: 30,
          entities: [{ entity: c.stats.temperature, name: "Temperature" }], stat_types: ["min", "max"],
          chart_type: "line", grid_options: { columns: "full" },
        } : null,
        waterChart("Water by zone", zoneSeries(c.stats.zones), "day", 30)], 2),
      section([heading("Last 12 months", "mdi:calendar-range"),
        rainChart(c, "Rain per month", "month", 365),
        c.stats.total ? waterChart("Water per month", [{ entity: c.stats.total, name: "Water" }], "month", 365) : null,
        waterChart("Water by zone per month", zoneSeries(c.stats.zones), "month", 365)], 3),
      section([heading("Watering time by zone", "mdi:timer-outline"),
        waterChart("Per week, last 12 weeks", zoneSeries(c.stats.zoneMinutes), "week", 84)], 2),
      other.length ? section([heading("Manual and unscheduled", "mdi:hand-water"),
        waterChart("Last 90 days", other.flatMap((sid) => c.stats.schedules[sid].filter((s) => zones[s.zone]).map((s) => ({
          entity: s.id, name: `${zones[s.zone]} · ${schedules[sid] || `Schedule ${sid}`}` }))), "week", 90)]) : null,
      c.stats.total ? section([heading("Total water", "mdi:water"),
        waterChart("Per month, last 2 years", [{ entity: c.stats.total, name: "Water" }], "month", 730)], 3) : null,
    ].filter(Boolean),
  };
}

function healthView(c, zones) {
  const e = c.entities;
  const checks = activeZones(c).filter((z) => c.zones[z].flow_check);
  const row = (z) => {
    const id = c.zones[z].flow_check;
    return `{% set a = states['${id}'].attributes %}| ${zones[z]} | {{ a.design_flow or '–' }} | {{ a.baseline_flow or '–' }}`
      + ` | {{ a.last_flow or '–' }} | {{ state_translated('${id}') }}{% if a.deviation_percent is not none %}`
      + ` ({{ '%+d' % a.deviation_percent }}%){% endif %} |`;
  };
  const unit = checks.length ? `{{ state_attr('${c.zones[checks[0]].flow_check}', 'unit') }}` : "";
  const history = e.last_baselined ? md(
    `{% set b = state_attr('${e.last_baselined}', 'baselines') or [] %}{% set cols = b[:4] %}`
    + "{% if not cols %}No baselines recorded yet.{% else %}"
    + `Flow in {{ state_attr('${e.last_baselined}', 'unit') }} · ✎ entered by hand · ⚠ waiting for review or kept at the previous value\n\n`
    + "| Zone |{% for s in cols %} {{ s.date }}{% if s.source == 'manual' %} ✎{% endif %} |{% endfor %}"
    + "{% if cols | length > 1 %} Change |{% endif %}\n"
    + "|---|{% for s in cols %}---|{% endfor %}{% if cols | length > 1 %}---|{% endif %}\n"
    + "{% for z in cols[0].zones %}| {{ z }} |{% for s in cols %} {{ s.zones.get(z, '–') }}"
    + "{% if s.review and z in s.review %} ⚠{% endif %} |{% endfor %}"
    + "{% if cols | length > 1 %}{% set new = cols[0].zones.get(z) %}{% set old = cols[1].zones.get(z) %}"
    + " {% if new is number and old %}{{ '%+d' % ((new - old) / old * 100) }}%{% else %}–{% endif %} |{% endif %}\n"
    + "{% endfor %}{% endif %}") : null;
  const live = Object.values(c.sensors).filter((s) => s.moisture);
  return {
    type: "sections", title: "Health", path: "health", icon: "mdi:stethoscope", max_columns: 3,
    sections: [
      section([alertsCard(c, zones)], 3),
      section([heading("Controller", "mdi:wifi"), tile(e.online, { color: "green" }), tile(e.wifi_signal), tile(e.last_seen),
        tile(e.status), press(e.pair_sensor, "Let a moisture sensor join the controller?"), press(e.cancel_pairing)]),
      section([heading("Flow", "mdi:water-check"),
        tile(e.live_flow, { name: "Live flow (10 min)", color: "blue" }),
        tile(e.flow_rate, { name: "Flow rate", state_content: ["state", "average_10_s"] }),
        tile(e.last_baselined, { name: "Last baseline" }), tile(e.baseline_review, { name: "Zones to review", color: "amber" }),
        press(e.run_baseline, "Run a flow baseline? Every enabled zone waters for about a minute."),
        press(e.accept_baseline, "Accept the new baseline for the zones waiting for review?"),
        press(e.keep_previous_baseline, "Keep the previous baseline for the zones waiting for review?")]),
      checks.length ? section([heading("Flow check", "mdi:water-alert"), md(
        `Last run against the design flow from the zone's heads, else the accepted baseline (${unit}).\n\n`
        + "| Zone | Design | Baseline | Last run | Check |\n|---|---|---|---|---|\n" + checks.map(row).join("\n"))], 2) : null,
      history ? section([heading("Baseline history, before and after", "mdi:history"), history], 3) : null,
      section([heading("Zone heads and settings", "mdi:cog"), md(
        "**Design flow:** enter the heads in each zone (model, arc, how many) so a baseline taken with a leak or a "
        + "missing nozzle is caught. [Open Spruce Irrigation](/config/integrations/integration/spruce_irrigation) → "
        + "**Add zone heads**; your own nozzles and drippers: **Add head model**.\n\n"
        + "**Units, water price, review tolerance, instant updates:** the integration's **Configure**.")]),
      e.flow_limit_high ? section([heading("Flow meter alarms", "mdi:pipe-leak"), tile(e.flow_limit_high), tile(e.flow_limit_low),
        tile(e.flow_stop_on_high), tile(e.flow_pump_protect), tile(e.flow_leak_sustained), tile(e.flow_leak_burst)]) : null,
      section([heading("Controller settings", "mdi:tune"), tile(e.manual_max_time), tile(e.pump_delay),
        tile(e.concurrent_zones)]),
      Object.keys(c.outlets).length ? section([heading("Sensor repeaters", "mdi:access-point-network"),
        ...Object.values(c.outlets).map((o) => tile(o.silent || o.last_seen, { name: o.name,
          state_content: o.last_seen ? ["state"] : undefined, color: "red", grid_options: { columns: 6 } })),
        ...Object.values(c.outlets).map((o) => tile(o.last_seen, { name: `${o.name} last report`, grid_options: { columns: 6 } }))]) : null,
      live.length ? section([heading("Moisture sensors", "mdi:leaf"),
        ...live.map((s) => tile(s.moisture, { name: s.name, state_content: ["state", "last_changed"], grid_options: { columns: 6 } }))]) : null,
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
      const own = [todayView(hass, c, zones), zonesView(hass, c, zones), planView(hass, c, zones, schedules),
        waterView(c, zones, schedules), healthView(c, zones)];
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
