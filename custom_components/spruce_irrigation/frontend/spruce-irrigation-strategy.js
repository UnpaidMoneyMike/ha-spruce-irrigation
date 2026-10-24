/*
 * Spruce Irrigation dashboard: a Home Assistant dashboard strategy, served and loaded by the integration.
 * Settings → Dashboards → Add dashboard → Community dashboards → "Spruce Irrigation".
 * The dashboard is built from the integration's entities and statistics each time it opens, so new zones,
 * schedules and moisture sensors appear by themselves. Use "Take control" in the dashboard to edit it by hand.
 */
const DOMAIN = "spruce_irrigation";

const CONTROLLER_KEYS = ["status", "active_zone", "watering_ends", "water_today", "water_month", "wifi_signal",
  "last_seen", "online", "watering", "hibernate", "stop", "pair_sensor", "freeze_temperature"];
const SENSOR_KEYS = ["moisture", "temperature", "battery", "signal", "last_reading"];

/** The integration's controllers, zones, schedules, moisture sensors and water statistics. */
async function collect(hass) {
  const ids = Object.values(hass.entities).filter((e) => e.platform === DOMAIN).map((e) => e.entity_id);
  if (!ids.length) return [];
  const [entries, stats] = await Promise.all([
    hass.callWS({ type: "config/entity_registry/get_entries", entity_ids: ids }),
    hass.callWS({ type: "recorder/list_statistic_ids", statistic_type: "sum" }).catch(() => []),
  ]);
  const byUnique = {};
  for (const [entityId, entry] of Object.entries(entries)) {
    if (entry && !entry.disabled_by && !entry.hidden_by) byUnique[entry.unique_id] = entityId;
  }
  const controllers = Object.keys(byUnique).filter((u) => u.endsWith("_status")).map((u) => u.slice(0, -7));

  return controllers.map((ctrl) => {
    const c = { id: ctrl, entities: {}, zones: {}, schedules: {}, sensors: {} };
    const zoneRe = new RegExp(`^${ctrl}_zone_(\\d+)_(\\w+)$`);
    const schedRe = new RegExp(`^${ctrl}_schedule_(\\d+)_(\\w+)$`);
    const sensorRe = new RegExp(`^${ctrl}_(\\w+?)_(${SENSOR_KEYS.join("|")})$`);
    for (const [unique, entityId] of Object.entries(byUnique)) {
      if (!unique.startsWith(`${ctrl}_`)) continue;
      const key = unique.slice(ctrl.length + 1);
      let m;
      if ((m = unique.match(zoneRe))) (c.zones[m[1]] ??= {})[m[2]] = entityId;
      else if ((m = unique.match(schedRe))) (c.schedules[m[1]] ??= {})[m[2]] = entityId;
      else if (CONTROLLER_KEYS.includes(key)) c.entities[key] = entityId;
      else if ((m = unique.match(sensorRe))) (c.sensors[m[1]] ??= {})[m[2]] = entityId;
    }
    c.name = deviceName(hass, c.entities.status) || "Spruce";
    for (const s of Object.values(c.sensors)) s.name = deviceName(hass, s.moisture || s.last_reading) || "Moisture sensor";

    const short = ctrl.toLowerCase().replace(/[^a-z0-9]/g, "").slice(-8);
    const prefix = `${DOMAIN}:${short}_`;
    c.stats = { total: null, zones: {}, zoneMinutes: {}, schedules: {} };
    for (const s of stats) {
      const id = s.statistic_id;
      if (!id.startsWith(prefix)) continue;
      const rest = id.slice(prefix.length);
      let m;
      if (rest === "water") c.stats.total = id;
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
      tile(e.water_today), tile(e.water_month),
      tile(e.hibernate), press(e.stop),
    ]),
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
        name: schedules[sid], state_content: ["state", "start_time", "next_date"],
      })),
      ...runOnly.map((sid) => press(c.schedules[sid].run, `Run ${schedules[sid]} now?`)),
    ]));
  }
  if (c.stats.total) {
    sections.push(section([heading("Water used", "mdi:water"),
      waterChart("Last 30 days", [{ entity: c.stats.total, name: "Water" }], "day", 30)], 2));
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
    sections,
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
        tile(e.last_flow, { name: "Last flow" }),
        tile(e.usual_flow, { name: "Usual flow" }),
        tile(e.flow_fault, { name: "Flow fault" }),
        c.stats.zones[z] ? waterChart("Water, last 30 days", [{ entity: c.stats.zones[z], name: zones[z] }], "day", 30)
          : null,
      ]);
    }),
  };
}

function schedulesView(c, zones, schedules) {
  const sections = Object.keys(c.schedules).filter((sid) => c.schedules[sid].enabled).map((sid) => {
    const e = c.schedules[sid];
    const series = (c.stats.schedules[sid] || []).sort((a, b) => a.zone - b.zone)
      .map((s) => ({ entity: s.id, name: zones[s.zone] || `Zone ${s.zone}` }));
    return section([
      heading(schedules[sid], "mdi:calendar-clock"),
      tile(e.enabled, { name: "Enabled", state_content: ["state", "next_date"] }),
      tile(e.start_time, { name: "Start time" }),
      tile(e.rain_skip, { name: "Rain skip" }),
      tile(e.rain_threshold, { name: "Rain skip threshold" }),
      tile(e.freeze_skip, { name: "Freeze skip" }),
      tile(e.wind_skip, { name: "Wind skip" }),
      press(e.run, `Run ${schedules[sid]} now?`),
      series.length ? waterChart("Water by zone, last 30 days", series, "day", 30) : null,
    ]);
  });
  const other = Object.keys(c.stats.schedules).filter((sid) => !c.schedules[sid]?.enabled);
  if (other.length) {
    sections.push(section([
      heading("Manual and unscheduled watering", "mdi:hand-water"),
      waterChart("Last 90 days", other.flatMap((sid) => c.stats.schedules[sid].map((s) => ({
        entity: s.id, name: `${zones[s.zone] || `Zone ${s.zone}`} · ${schedules[sid] || `Schedule ${sid}`}`,
      }))), "week", 90),
    ], 2));
  }
  sections.push(section([heading("Weather skips", "mdi:weather-snowy-rainy"),
    tile(c.entities.freeze_temperature, { name: "Freeze skip below" })]));
  return { type: "sections", title: "Schedules", path: "schedules", icon: "mdi:calendar-clock", max_columns: 4, sections };
}

function historyView(c, zones) {
  const zoneSeries = (stats) => byNumber(stats).map((z) => ({ entity: stats[z], name: zones[z] || `Zone ${z}` }));
  return {
    type: "sections", title: "History", path: "history", icon: "mdi:chart-bar", max_columns: 4,
    sections: [
      c.stats.total ? section([heading("Total water", "mdi:water"),
        waterChart("Per month, last 2 years", [{ entity: c.stats.total, name: "Water" }], "month", 730)], 4) : null,
      section([heading("Water by zone", "mdi:sprinkler"),
        waterChart("Per month, last 12 months", zoneSeries(c.stats.zones), "month", 365)], 4),
      section([heading("Watering time by zone", "mdi:timer-outline"),
        waterChart("Per week, last 12 weeks", zoneSeries(c.stats.zoneMinutes), "week", 84)], 4),
    ].filter(Boolean),
  };
}

function diagnosticsView(c) {
  const e = c.entities;
  return {
    type: "sections", title: "Diagnostics", path: "diagnostics", icon: "mdi:stethoscope", max_columns: 4,
    sections: [
      section([heading("Controller", "mdi:wifi"), tile(e.online), tile(e.wifi_signal), tile(e.last_seen),
        press(e.pair_sensor, "Let a moisture sensor join the controller?")]),
      ...Object.values(c.sensors).map((s) => section([
        heading(s.name, "mdi:leaf"), tile(s.moisture, { name: "Moisture" }), tile(s.temperature, { name: "Temperature" }),
        tile(s.battery, { name: "Battery" }), tile(s.signal, { name: "Signal" }),
        tile(s.last_reading, { name: "Last reading" }),
      ])),
    ],
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
      const own = [overviewView(hass, c, zones, schedules), zonesView(c, zones), schedulesView(c, zones, schedules),
        historyView(c, zones), diagnosticsView(c)];
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
