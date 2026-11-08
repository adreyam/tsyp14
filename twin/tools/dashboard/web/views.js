"use strict";
/* Shell, shared widgets and the first pages: Overview (with the live city map) and Control (everything the Godot panel can do). */

const VIEWS = [];
const defineView = v => VIEWS.push(v);
let activeView = "overview";
const ICONS = {
  overview: '<path d="M3 12l9-8 9 8M5 10v10h14V10"/>', control: '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
  sensors: '<circle cx="12" cy="12" r="2"/><path d="M7.8 7.8a6 6 0 000 8.4M16.2 7.8a6 6 0 010 8.4M4.9 4.9a10 10 0 000 14.2M19.1 4.9a10 10 0 010 14.2"/>',
  trends: '<path d="M3 3v18h18M7 15l4-5 3 3 5-7"/>', traffic: '<rect x="3" y="11" width="18" height="6" rx="2"/><circle cx="7.5" cy="17" r="1.5"/><circle cx="16.5" cy="17" r="1.5"/><path d="M5 11l2-5h10l2 5"/>',
  people: '<circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.5 2.7-6 6-6s6 2.5 6 6M16 5.5a3 3 0 010 5.6M18 14.5c1.8.8 3 2.8 3 5.5"/>',
  parking: '<rect x="4" y="3" width="16" height="18" rx="3"/><path d="M9.5 16V8h3.2a2.4 2.4 0 010 4.8H9.5"/>', zones: '<path d="M3 3h18v18H3zM9 3v18M15 3v18M3 9h18M3 15h18"/>',
  incidents: '<path d="M12 3l10 18H2zM12 10v4.5M12 17.5h0"/>', utilities: '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>', payload: '<path d="M8 4c-3 0-3 2-3 4s0 4-2 4c2 0 2 2 2 4s0 4 3 4M16 4c3 0 3 2 3 4s0 4 2 4c-2 0-2 2-2 4s0 4-3 4"/>',
};

/* ---------- small widgets ---------- */
function kpiCard(id, label) { return html(`<div class="kpi" id="${id}"><div class="l">${label}</div><div class="v">–</div><div class="s">&nbsp;</div><canvas></canvas></div>`); }
function setKpi(id, { v, unit = "", sub = "", cls = "", spark = null, color = "#38bdf8" }) {
  const n = $("#" + id); if (!n) return;
  n.className = "kpi " + cls; $(".v", n).innerHTML = `${v}${unit ? `<small>${unit}</small>` : ""}`; $(".s", n).innerHTML = sub || "&nbsp;";
  if (spark) { const h = hist.get(spark); if (h) sparkline($("canvas", n), h.v.slice(-300), color); }
}
const seg = (name, items) => `<div class="seg" data-seg="${name}">${items.map(([v, label]) => `<button data-act="${name}" data-v="${v}" data-on="${name}:${v}">${label}</button>`).join("")}</div>`;
const barRow = (label, value, max, extra = "", cls = "") => `<div style="margin:7px 0"><div class="row" style="justify-content:space-between;margin-bottom:3px"><span>${label}</span><span class="muted">${extra}</span></div><div class="bar ${cls}"><i style="width:${clamp(max ? value / max * 100 : 0, 0, 100)}%"></i></div></div>`;
const pct = (v) => clamp(v, 0, 100);
function setHtmlIfChanged(node, markup) { if (node._h !== markup) { node.innerHTML = markup; node._h = markup; } }
const classColor = { car: "#8ab4ff", taxi: "#fde047", bus: "#86efac", police: "#60a5fa", ambulance: "#fb7185", fire: "#fb923c", tow: "#fbbf24", utility: "#d9f99d" };
const ZONE_IDS = "ABCDEFGHIJKLMNOP".split("");
const INCIDENT_KINDS = [["rear_end", "Rear-end crash"], ["red_light_tbone", "Red-light T-bone"], ["unsafe_overtake", "Unsafe overtake"], ["breakdown", "Breakdown"], ["vehicle_fire", "Vehicle fire"],
  ["lost_load", "Lost load"], ["building_fire", "Building fire"], ["water_main_break", "Water-main break"], ["signal_failure", "Signal failure"], ["garbage_collection", "Garbage collection"]];
const timeText = minute => `${String(Math.floor(minute / 60)).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;
const clockMinute = text => { const p = String(text || "0:0").split(":"); return (+p[0] || 0) * 60 + (+p[1] || 0); };
const zoneCounts = (rows, pick = r => r.zone) => { const m = {}; for (const r of rows || []) { const z = pick(r); if (z) m[z] = (m[z] || 0) + 1; } return m; };

/* ---------- the live city map ---------- */
class CityMap {
  constructor(host) {
    this.host = host; this.canvas = document.createElement("canvas"); this.tip = html('<div class="maptip" style="display:none"></div>'); host.append(this.canvas, this.tip);
    this.layers = { vehicles: true, citizens: true, parking: true, units: true, signals: true, sensors: false, incidents: true, collapsed: true };
    this.hits = []; this.mouse = null; this.collapsed = []; this.collapsedAt = 0;
    this.canvas.addEventListener("mousemove", e => { const r = this.canvas.getBoundingClientRect(); this.mouse = [e.clientX - r.left, e.clientY - r.top]; this.hover(); });
    this.canvas.addEventListener("mouseleave", () => { this.mouse = null; this.tip.style.display = "none"; });
    this.canvas.addEventListener("click", () => { const h = this.nearest(); if (h && h.follow) run("follow", { target: h.follow }); });
    new ResizeObserver(() => this.draw()).observe(host);
  }
  nearest() { if (!this.mouse) return null; let best = null, bd = 144; for (const h of this.hits) { const d = (h.x - this.mouse[0]) ** 2 + (h.y - this.mouse[1]) ** 2; if (d < bd) { bd = d; best = h; } } return best; }
  hover() {
    const h = this.nearest();
    if (!h) { this.tip.style.display = "none"; this.canvas.style.cursor = ""; return; }
    this.tip.innerHTML = h.text + (h.follow ? '<div class="faint">click to follow in the simulation</div>' : ""); this.tip.style.display = "block"; this.canvas.style.cursor = h.follow ? "pointer" : "";
    this.tip.style.left = Math.min(h.x + 12, this.host.clientWidth - this.tip.offsetWidth - 4) + "px"; this.tip.style.top = Math.max(4, h.y - 8) + "px";
  }
  draw() {
    const c = S.city; const dpr = window.devicePixelRatio || 1, W = this.host.clientWidth, H = this.host.clientHeight; if (!W || !H) return;
    if (this.canvas.width !== Math.round(W * dpr)) { this.canvas.width = Math.round(W * dpr); this.canvas.height = Math.round(H * dpr); }
    const g = this.canvas.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);
    const k = Math.min(W, H) / 780, ox = W / 2, oy = H / 2, X = x => ox + x * k, Y = z => oy + z * k; this.hits = [];
    const quakeZones = new Set((c?.earthquakes?.earthquakes || []).filter(q => q.status === "active").map(q => q.zone)), allQuake = quakeZones.has("ALL");
    const shown = new Set(S.zones?.shown_zones || []);
    ZONE_IDS.forEach((z, i) => {
      const col = i % 4, row = Math.floor(i / 4), x = X(-380 + col * 190), y = Y(-380 + row * 190), s = 190 * k, hit = allQuake || quakeZones.has(z);
      g.fillStyle = hit ? "rgba(248,113,113,.16)" : (col + row) % 2 ? "#0e1621" : "#0c131c"; g.fillRect(x, y, s, s);
      g.strokeStyle = hit ? "#f87171" : "#1c2a3d"; g.lineWidth = hit ? 2 : 1; g.strokeRect(x + .5, y + .5, s - 1, s - 1);
      g.fillStyle = shown.has(z) ? "#38bdf8" : hit ? "#fca5a5" : "#2b3d55"; g.font = `700 ${Math.max(14, 46 * k)}px Inter, sans-serif`; g.textAlign = "left"; g.textBaseline = "top"; g.fillText(z, x + 8 * k, y + 6 * k);
    });
    g.strokeStyle = "#17222f"; g.lineWidth = Math.max(3, 9 * k);
    for (let i = 0; i <= 8; i++) { const v = -380 + i * 95; g.beginPath(); g.moveTo(X(v), Y(-380)); g.lineTo(X(v), Y(380)); g.moveTo(X(-380), Y(v)); g.lineTo(X(380), Y(v)); g.stroke(); }
    if (!c) return;
    const dot = (x, y, r, color, text, follow) => { g.fillStyle = color; g.beginPath(); g.arc(x, y, r, 0, 7); g.fill(); if (text) this.hits.push({ x, y, text, follow }); };
    if (this.layers.collapsed && c.earthquakes?.collapsed_buildings) {
      if (nowSec() - this.collapsedAt > 2.5) { this.collapsedAt = nowSec(); client.call("list_buildings", { collapsed_only: true, limit: 500 }).then(d => { this.collapsed = d.buildings || []; }).catch(() => {}); }
      g.strokeStyle = "#f87171"; g.lineWidth = 2.5; for (const b of this.collapsed) { const x = X(b.location.x), y = Y(b.location.z); g.beginPath(); g.moveTo(x - 5, y - 5); g.lineTo(x + 5, y + 5); g.moveTo(x + 5, y - 5); g.lineTo(x - 5, y + 5); g.stroke(); }
    } else if (!c.earthquakes?.collapsed_buildings) this.collapsed = [];
    if (this.layers.signals) for (const d of S.byFamily.get("signal") || []) {
      const ns = d.readings.ns_signal?.value, col = ns === 2 ? "#4ade80" : ns === 1 ? "#fbbf24" : "#f87171", x = X(d.location.x), y = Y(d.location.z);
      g.strokeStyle = col; g.lineWidth = 2; g.strokeRect(x - 3, y - 3, 6, 6); this.hits.push({ x, y, text: `<b>${d.id}</b><br>N-S ${["red", "amber", "green"][ns] ?? "?"} · E-W ${["red", "amber", "green"][d.readings.ew_signal?.value] ?? "?"}` });
    }
    if (this.layers.sensors) for (const d of S.byFamily.get("seismic") || []) { const x = X(d.location.x), y = Y(d.location.z), mmi = d.readings.shaking_intensity?.value || 1;
      g.fillStyle = mmi > 4 ? "#f87171" : mmi > 2 ? "#fbbf24" : "#a78bfa"; g.save(); g.translate(x, y); g.rotate(Math.PI / 4); g.fillRect(-3.5, -3.5, 7, 7); g.restore(); this.hits.push({ x, y, text: `<b>${d.id}</b> MMI ${fmt(mmi)}` }); }
    if (this.layers.parking) for (const l of c.parking?.lots || []) {
      const x = X(l.location.x), y = Y(l.location.z), full = l.occupied >= l.capacity || l.barrier_mode === "closed", w = 30, h = 18;
      g.fillStyle = full ? "#4b1d22" : "#12344d"; g.strokeStyle = l.warning ? "#f87171" : "#38bdf8"; g.lineWidth = l.warning ? 3 : 1.5; g.beginPath(); g.roundRect(x - w / 2, y - h / 2, w, h, 4); g.fill(); g.stroke();
      g.fillStyle = "#fff"; g.font = "600 10px Inter, sans-serif"; g.textAlign = "center"; g.textBaseline = "middle"; g.fillText(`${l.occupied}/${l.capacity}`, x, y + .5);
      this.hits.push({ x, y, text: `<b>${l.id}</b> · zone ${l.zone}<br>${l.occupied}/${l.capacity} bays · barrier ${l.barrier_mode}` });
    }
    if (this.layers.vehicles) for (const v of c.vehicles?.vehicles || []) {
      const x = X(v.location.x), y = Y(v.location.z); g.fillStyle = classColor[v.class] || "#aaa"; g.beginPath(); g.arc(x, y, v.class === "bus" ? 3.6 : 2.7, 0, 7); g.fill();
      if (v.stopped) { g.strokeStyle = "#f8717199"; g.lineWidth = 1; g.beginPath(); g.arc(x, y, 4.6, 0, 7); g.stroke(); }
      this.hits.push({ x, y, follow: v.id, text: `<b>${v.id}</b> · ${v.class}<br>${fmt(v.speed_mps * 3.6, 0)} km/h${v.stopped ? " · stopped (" + v.stop_reason + ")" : ""}<br><span class="faint">${v.road}</span>` });
    }
    if (this.layers.units) for (const u of c.units?.units || []) { if (!u.location) continue; const x = X(u.location.x), y = Y(u.location.z);
      g.strokeStyle = classColor[u.kind] || "#fff"; g.lineWidth = 2.4; g.beginPath(); g.arc(x, y, 7, 0, 7); g.stroke(); this.hits.push({ x, y, follow: u.vehicle || u.unit, text: `<b>${u.unit}</b> · ${u.kind}<br>${u.status}${u.incident ? " · " + u.incident : ""}` }); }
    if (this.layers.citizens) for (const p of c.citizens?.citizens || []) { const x = X(p.location.x), y = Y(p.location.z); dot(x, y, 2.3, "#f1f5f9", `<b>${esc(p.name)}</b> · ${esc(p.role)}<br>${esc(p.action)}`, p.id); }
    if (this.layers.incidents) for (const i of c.incidents?.incidents || []) {
      const l = i.location || {}, lx = l.x ?? l.sx, lz = l.z ?? l.y ?? l.sy; if (lx === undefined || lz === undefined) continue; const x = X(lx), y = Y(lz), pulse = 9 + 4 * Math.sin(nowSec() * 5);
      g.fillStyle = "rgba(248,113,113,.22)"; g.strokeStyle = "#f87171"; g.lineWidth = 2; g.beginPath(); g.arc(x, y, pulse, 0, 7); g.fill(); g.stroke(); this.hits.push({ x, y, text: `<b>${esc(i.id)}</b> ${esc(i.title || i.kind || "")}<br>${esc(i.phase)}` });
    }
    if (this.mouse) this.hover();
  }
}

/* ---------- Overview ---------- */
let cityMap = null, ovCharts = {};
defineView({
  id: "overview", title: "Overview", sub: "The whole city, live",
  build(root) {
    root.innerHTML = `<div class="viewhead"><div><h2>Overview</h2><p>The whole city at a glance — every number on this page arrives through MCP, ten times a second.</p></div></div>
      <div class="grid g4" id="ov-kpis" style="margin-bottom:14px"></div>
      <div class="grid g-side" style="margin-bottom:14px">
        <div class="card"><header><h3>Live city map</h3><span class="sp"></span><div class="maptools" id="ov-layers"></div></header><div class="mapbox" id="ov-map"></div>
          <div class="legend" id="ov-legend" style="margin-top:10px"></div></div>
        <div class="stack"><div class="card"><header><h3>City status</h3></header><dl class="kv" id="ov-status"></dl></div>
          <div class="card"><header><h3>Event log</h3><span class="sp"></span><button class="btn sm" data-goto="control">Open controls</button></header><div class="eventlog" id="ov-events" style="max-height:300px"></div></div></div>
      </div>
      <div class="grid g3"><div class="card"><header><h3>Traffic</h3></header><div id="ov-c1"></div><div id="ov-l1"></div></div>
        <div class="card"><header><h3>Air &amp; weather</h3></header><div id="ov-c2"></div><div id="ov-l2"></div></div>
        <div class="card"><header><h3>Power reserve</h3></header><div id="ov-c3"></div><div id="ov-l3"></div></div></div>`;
    const kp = $("#ov-kpis", root);
    [["k-veh", "Vehicles on the road"], ["k-cit", "Citizens shown"], ["k-park", "Parking bays used"], ["k-pow", "Power reserve"], ["k-air", "Air quality PM2.5"], ["k-noise", "Noise level"], ["k-inc", "Open incidents"], ["k-quake", "Earthquakes"]].forEach(([id, label]) => kp.append(kpiCard(id, label)));
    cityMap = new CityMap($("#ov-map", root));
    const names = { vehicles: "Vehicles", citizens: "People", parking: "Car parks", units: "Emergency", signals: "Signals", sensors: "Seismic", incidents: "Incidents", collapsed: "Collapsed" };
    $("#ov-layers", root).append(...Object.keys(names).map(k => { const b = html(`<button class="chip ${cityMap.layers[k] ? "on" : ""}">${names[k]}</button>`); b.onclick = () => { cityMap.layers[k] = !cityMap.layers[k]; b.classList.toggle("on"); cityMap.draw(); }; return b; }));
    $("#ov-legend", root).innerHTML = Object.entries(classColor).map(([k, c]) => `<span><i style="background:${c}"></i>${k}</span>`).join("") + '<span><i style="background:#f1f5f9"></i>citizen</span><span><i style="background:#f87171"></i>stopped / incident</span>';
    ovCharts.c1 = new Chart($("#ov-c1", root), { height: 190, yUnit: "" }); ovCharts.c2 = new Chart($("#ov-c2", root), { height: 190, normalize: true });
    ovCharts.c3 = new Chart($("#ov-c3", root), { height: 190, fixedY: [0, 100], yUnit: "%" });
    ovCharts.s1 = [{ key: "vehicles", label: "On the road", color: "#38bdf8", area: true }, { key: "stopped", label: "Stopped", color: "#fb7185" }];
    ovCharts.s2 = [{ key: "agg:air:pm2_5", label: "PM2.5 µg/m³", color: "#fbbf24" }, { key: "agg:noise:db_a", label: "Noise dB(A)", color: "#a78bfa" }, { key: "agg:weather:temperature", label: "Temperature °C", color: "#fb923c" }, { key: "agg:weather:wind_speed", label: "Wind m/s", color: "#2dd4bf" }];
    ovCharts.s3 = [{ key: "grid:energy_level", label: "Stored energy %", color: "#4ade80" }];
    ["1", "2", "3"].forEach(n => legend($("#ov-l" + n, root), ovCharts["s" + n]));
  },
  update() {
    const c = S.city, st = S.status; if (!c || !st) return; const t = st.traffic || {}, grid = S.devices.get("GRID-01"), lots = c.parking || {};
    const open = (c.incidents?.incidents || []).length, q = c.earthquakes || {}, air = hist.get("agg:air:pm2_5"), noise = hist.get("agg:noise:db_a");
    setKpi("k-veh", { v: fmt(t.simulated_vehicles, 0), sub: `${t.stopped_vehicles ?? 0} stopped · ${t.turning_vehicles ?? 0} turning`, spark: "vehicles" });
    setKpi("k-cit", { v: fmt(st.citizens_shown, 0), sub: `of ${c.citizens?.population || "?"} citizens`, spark: "citizens", color: "#a78bfa" });
    setKpi("k-park", { v: `${lots.parked ?? 0}<small>/ ${lots.capacity ?? 0}</small>`, sub: `${(lots.lots || []).filter(l => l.occupied >= l.capacity).length} full · ${(lots.lots || []).reduce((a, l) => a + l.denied_entries, 0)} cars refused`, spark: "parked", color: "#2dd4bf" });
    const en = grid?.readings.energy_level?.value; setKpi("k-pow", { v: fmt(en, 1), unit: "%", sub: `demand ${fmt(grid?.readings.grid_demand?.value, 0)} kW`, cls: en < 15 ? "bad" : en < 35 ? "warn" : "", spark: "grid:energy_level", color: "#4ade80" });
    setKpi("k-air", { v: fmt(air?.v.at(-1), 1), unit: "µg/m³", sub: "mean of 8 monitors", spark: "agg:air:pm2_5", color: "#fbbf24" });
    setKpi("k-noise", { v: fmt(noise?.v.at(-1), 1), unit: "dB(A)", sub: "mean of 4 monitors", spark: "agg:noise:db_a", color: "#a78bfa" });
    setKpi("k-inc", { v: open, sub: `${(c.units?.units || []).filter(u => !["garaged", "patrol", "idle"].includes(u.status)).length} emergency units out`, cls: open ? "warn" : "", spark: "incidents", color: "#fb923c" });
    setKpi("k-quake", { v: q.active || 0, sub: `${q.collapsed_buildings || 0} buildings collapsed`, cls: q.active ? "bad" : "", spark: "max:seismic:ground_acceleration", color: "#f87171" });
    cityMap.draw();
    setHtmlIfChanged($("#ov-status"), [["Clock", `Day ${st.day} · ${st.clock}`], ["Period", st.period], ["Weather", st.weather], ["Simulation", st.paused ? "paused" : `${st.speed}× speed`], ["Clock pace", `${st.clock_rate} clock s / s`],
      ["Traffic", t.connected ? "SUMO live" : "waiting for SUMO"], ["Devices", st.device_count], ["Requests served", st.requests_served]].map(([a, b]) => `<dt>${a}</dt><dd>${b}</dd>`).join(""));
    setHtmlIfChanged($("#ov-events"), (c.events?.events || []).slice(0, 14).map(e => `<div><time>${esc(e.slice(0, 8))}</time>${esc(e.slice(10))}</div>`).join("") || '<div class="faint">No events yet.</div>');
    const w = ovCharts.window || 120;
    ovCharts.c1.drawHistory(hist, ovCharts.s1, w); ovCharts.c2.drawHistory(hist, ovCharts.s2, w); ovCharts.c3.drawHistory(hist, ovCharts.s3, w);
    ["1", "2", "3"].forEach(n => legendValues($("#ov-l" + n), ovCharts["s" + n], hist));
  },
});

/* ---------- Control: everything the Godot panel can do ---------- */
const UI = { quakeZone: "ALL", magnitude: 6, cam: "", followId: "", signalId: "", signalPhase: "ns_green", dim: 0.6, replayId: "" };
defineView({
  id: "control", title: "Control", sub: "Everything the panel can do",
  build(root) {
    const pills = items => `<div class="row">${items}</div>`;
    root.innerHTML = `<div class="viewhead"><div><h2>Control</h2><p>Every button here is an MCP tool call — the same actions as the control panel in the simulation window.</p></div><span class="sp"></span>
      <div class="statline" id="ct-cam"></div></div>
      <div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(420px,1fr))">
      <div class="card"><header><h3>Time &amp; weather</h3></header><div class="stack">
        <div class="field"><label>Weather</label>${seg("weather", ["clear", "cloudy", "rain", "fog", "storm", "auto"].map(w => [w, w[0].toUpperCase() + w.slice(1)]))}</div>
        <div class="field"><label>Time of day <b id="ct-time" class="mono" style="color:var(--text)">--:--</b></label><input type="range" id="ct-slider" min="0" max="1439" step="5" value="540">
          <div class="row">${[["00:00", 0], ["06:00", 360], ["09:00", 540], ["12:00", 720], ["18:30", 1110], ["22:00", 1320]].map(([l, m]) => `<button class="btn sm" data-act="time" data-v="${m}">${l}</button>`).join("")}</div></div>
        <div class="field"><label>Simulation speed</label>${seg("speed", [1, 2, 3, 4, 6, 8].map(s => [s, s + "×"]))}</div>
        <div class="field"><label>Clock pace (clock seconds per second)</label>${seg("rate", [1, 10, 30, 60, 120, 300].map(s => [s, s]))}</div>
        <div class="row"><button class="btn" data-act="pause" data-on="paused:true">⏸ Pause</button><button class="btn good" data-act="resume">▶ Resume</button></div></div></div>
      <div class="card"><header><h3>Traffic signals</h3></header><div class="stack">
        <div class="field"><label>All signals</label><div class="chips">${[["ns_green", "N-S green"], ["ew_green", "E-W green"], ["all_red", "All red"], ["ns_yellow", "N-S amber"], ["ew_yellow", "E-W amber"], ["reset_cycle", "Normal cycle"]].map(([v, l]) => `<button class="chip" data-act="allsignals" data-v="${v}">${l}</button>`).join("")}</div></div>
        <div class="field"><label>One signal</label><div class="row"><select class="input grow" id="ct-sig"></select><select class="input" id="ct-sigphase">${["ns_green", "ew_green", "all_red", "ns_yellow", "ew_yellow", "reset_cycle"].map(p => `<option>${p}</option>`).join("")}</select><button class="btn primary" data-act="onesignal">Apply</button></div></div></div></div>
      <div class="card"><header><h3>Earthquake</h3></header><div class="stack">
        <div class="field"><label>Where</label><div class="chips">${["ALL", ...ZONE_IDS].map(z => `<button class="chip" data-act="qzone" data-v="${z}" data-on="qz:${z}">${z === "ALL" ? "Whole city" : z}</button>`).join("")}</div></div>
        <div class="field"><label>Magnitude <b id="ct-mag" style="color:var(--text)">6.0</b></label><input type="range" id="ct-magslider" min="4" max="8" step="0.1" value="6"></div>
        <div class="row"><button class="btn danger" data-act="quake">⚠ Trigger earthquake</button><button class="btn" data-act="quakeclear">Clean up everything</button></div></div></div>
      <div class="card"><header><h3>Incidents</h3></header><div class="stack">
        <div class="chips">${INCIDENT_KINDS.map(([k, l]) => `<button class="chip" data-act="incident" data-v="${k}">${l}</button>`).join("")}</div>
        <div class="row"><button class="btn good" data-act="resolveall">Resolve all now</button><select class="input grow" id="ct-replay"></select><button class="btn" data-act="replay">Replay</button><button class="btn" data-act="replaystop">Stop</button></div></div></div>
      <div class="card"><header><h3>Camera</h3></header><div class="stack">
        <div class="row"><button class="btn" data-act="cam" data-v="overview">City overview</button><button class="btn" data-act="cam" data-v="street">Street level</button><button class="btn" data-act="cam" data-v="reset">Reset</button><button class="btn" data-act="viewport" data-v="full">Canvas fills window</button><button class="btn" data-act="viewport" data-v="panel">Restore panels</button></div>
        <div class="camgrid">${Array.from({ length: 16 }, (_, i) => `<button class="btn sm" data-act="cam" data-v="CAM-${String(i + 1).padStart(2, "0")}">CAM-${String(i + 1).padStart(2, "0")}</button>`).join("")}</div>
        <div class="field"><label>Follow</label><div class="row"><button class="btn" data-act="follow" data-v="person">Random person</button><button class="btn" data-act="follow" data-v="car">Random car</button><input class="input grow" id="ct-follow" placeholder="citizen_0007 or vehicle id"><button class="btn primary" data-act="followid">Follow</button></div></div></div></div>
      <div class="card"><header><h3>Asset map</h3></header><div class="chips" id="ct-hl"></div><div class="row" style="margin-top:10px"><button class="btn" data-act="hl" data-v="clear">Clear highlight</button></div></div>
      <div class="card"><header><h3>Zone overlay</h3></header><div class="chips">${ZONE_IDS.map(z => `<button class="chip" data-act="zone" data-v="${z}" data-on="zone:${z}">${z}</button>`).join("")}</div>
        <div class="row" style="margin-top:10px"><button class="btn" data-act="zoneall" data-v="1">Show all</button><button class="btn" data-act="zoneall" data-v="0">Hide all</button></div></div>
      <div class="card"><header><h3>Utilities &amp; street lights</h3></header><div class="stack">
        <div class="row"><button class="btn good" data-act="recharge">Recharge reserves</button><button class="btn" data-act="autorecharge" data-v="1">Auto-recharge on</button><button class="btn" data-act="autorecharge" data-v="0">Auto-recharge off</button></div>
        <div class="field"><label>All street lights</label><div class="row"><button class="btn" data-act="lights" data-v="on">On</button><button class="btn" data-act="lights" data-v="off">Off</button><button class="btn" data-act="lights" data-v="auto">Auto</button>
          <input type="range" id="ct-dim" min="0" max="1" step="0.05" value="0.6" class="grow"><b id="ct-dimv" class="mono">60%</b><button class="btn" data-act="dim">Dim</button></div></div></div></div>
      <div class="card" style="grid-column:1/-1"><header><h3>Command console</h3><span class="sp"></span><span class="muted">latest first · click one to see its response under Payload</span></header><div class="cmdlog" id="ct-log"></div></div>
      </div>`;
    $("#ct-slider", root).addEventListener("input", e => { $("#ct-time").textContent = timeText(+e.target.value); UI.draggingTime = true; });
    $("#ct-slider", root).addEventListener("change", e => { UI.draggingTime = false; run("set_time_of_day", { time: timeText(+e.target.value) }); });
    $("#ct-magslider", root).addEventListener("input", e => { UI.magnitude = +e.target.value; $("#ct-mag").textContent = UI.magnitude.toFixed(1); });
    $("#ct-dim", root).addEventListener("input", e => { UI.dim = +e.target.value; $("#ct-dimv").textContent = Math.round(UI.dim * 100) + "%"; });
    $("#ct-follow", root).addEventListener("input", e => { UI.followId = e.target.value; });
  },
  update() {
    const c = S.city, st = S.status; if (!c) return;
    if (!UI.draggingTime) { const m = clockMinute(st.time || st.clock); $("#ct-slider").value = m; $("#ct-time").textContent = timeText(m); }
    $("#ct-cam").innerHTML = `<span>Viewport: <b>${esc(c.ui?.camera_status || "–")}</b></span>${c.ui?.highlight ? `<span>Highlight: <b>${esc(c.ui.highlight)}</b></span>` : ""}`;
    const sig = $("#ct-sig"); const ids = (S.byFamily.get("signal") || []).map(d => d.id);
    if (sig.options.length !== ids.length) sig.innerHTML = ids.map(i => `<option>${i}</option>`).join("");
    const rep = $("#ct-replay"), incs = [...(c.incidents?.incidents || []), ...(c.incidents?.resolved_recently || [])].map(i => i.id); const want = incs.join(",");
    if (rep._w !== want) { const cur = rep.value; rep.innerHTML = incs.length ? incs.map(i => `<option>${i}</option>`).join("") : "<option value=''>no incident yet</option>"; rep._w = want; if (incs.includes(cur)) rep.value = cur; }
    const kinds = c.ui?.highlight_kinds || []; const hl = $("#ct-hl"); if (hl._k !== kinds.join()) { hl._k = kinds.join(); hl.innerHTML = kinds.map(k => `<button class="chip" data-act="hl" data-v="${k}" data-on="hl:${k}">${k.replaceAll("_", " ")}</button>`).join(""); }
    setHtmlIfChanged($("#ct-log"), S.commands.slice(0, 14).map((e, i) => `<div data-act="showcmd" data-v="${i}" style="cursor:pointer"><span class="${e.ok ? "ok" : "no"}">${e.ok ? "✓" : "✗"}</span><span class="faint">${e.at.toLocaleTimeString()}</span><b>${e.tool}</b><span class="muted">${esc(JSON.stringify(e.args))}</span>${e.error ? `<span class="no">${esc(e.error)}</span>` : ""}</div>`).join("") || '<div class="faint">Nothing sent yet — press any button.</div>');
    syncStates();
  },
});

/* ---------- control state highlighting + actions ---------- */
function syncStates() {
  const st = S.status, ui = S.city?.ui || {}; if (!st) return;
  const on = new Set([`weather:${st.weather}`, `speed:${st.speed}`, `rate:${st.clock_rate}`, `paused:${st.paused}`, `hl:${ui.highlight || ""}`, `qz:${UI.quakeZone}`]);
  for (const z of S.zones?.shown_zones || []) on.add(`zone:${z}`);
  $$("[data-on]").forEach(b => b.classList.toggle("on", on.has(b.dataset.on)));
}
const ACTIONS = {
  weather: d => run("set_weather", { weather: d.v }), speed: d => run("set_simulation_speed", { speed: +d.v }), rate: d => run("set_clock_rate", { rate: +d.v }),
  time: d => run("set_time_of_day", { time: timeText(+d.v) }), pause: () => run("pause_simulation"), resume: () => run("resume_simulation"),
  allsignals: d => run("set_signal_phase", { phase: d.v }), onesignal: () => run("set_signal", { id: $("#ct-sig").value, phase: $("#ct-sigphase").value }),
  qzone: d => { UI.quakeZone = d.v; syncStates(); }, quake: () => run("trigger_earthquake", { zone: UI.quakeZone, magnitude: UI.magnitude }), quakeclear: () => run("clear_earthquakes"),
  shake: d => run("trigger_earthquake", { zone: d.v, magnitude: UI.magnitude }),
  incident: d => run("trigger_incident", { kind: d.v }), resolveall: () => run("resolve_all_incidents"),
  replay: () => { const id = $("#ct-replay").value; if (id) run("replay_incident", { id }); }, replaystop: () => run("replay_incident", { stop: true }),
  replayid: d => run("replay_incident", { id: d.v }),
  incidentwatch: d => run("control_incident", { id: d.id, action: "watch" }), incidentresolve: d => run("control_incident", { id: d.id, action: "resolve" }),
  incidentdispatch: d => run("control_incident", { id: d.id, action: "dispatch_extra", unit_kind: d.v }),
  replayctl: d => run("control_replay", { action: d.v, ...(d.value !== undefined ? { value: isNaN(+d.value) ? d.value : +d.value } : {}) }),
  cam: d => run("set_camera", { view: d.v }), follow: d => run("follow", { target: d.v }), followid: () => { if (UI.followId.trim()) run("follow", { target: UI.followId.trim() }); },
  viewport: d => run("set_viewport_layout", { layout: d.v }), focusdevice: d => run("focus_device", { id: d.id }),
  followthis: d => run("follow", { target: d.v }), hl: d => run("highlight_assets", { kind: d.v }),
  zone: d => run("show_zones", { zones: [d.v], visible: !(S.zones?.shown_zones || []).includes(d.v) }).then(refreshZones),
  zoneall: d => run("show_zones", { zones: ["ALL"], visible: d.v === "1" }).then(refreshZones),
  recharge: () => run("recharge_utilities"), autorecharge: d => run("set_auto_recharge", { enabled: d.v === "1" }),
  lights: d => run("set_all_street_lights", { mode: d.v }), dim: () => run("set_all_street_lights", { mode: "dim", level: UI.dim }),
  switch: d => run("set_power_switch", { id: d.id, state: d.v }), valve: d => run("set_valve", { id: d.id, state: d.v }), barrier: d => run("set_parking_barrier", { id: d.id, mode: d.v }),
  emptybin: d => run("empty_waste_bin", { id: d.id }), service: d => run("set_service_zone", { id: d.id, service: d.service, state: d.v }),
  device: d => run("command_actuator", { id: d.id, command: d.v, ...(d.value !== undefined ? { value: +d.value } : {}) }),
  showcmd: d => { const e = S.commands[+d.v]; if (e) { S.lastCommand = { tool: e.tool, args: e.args, result: e.result, error: e.error }; Boot.onCommand(); setView("payload"); } },
};
async function refreshZones() { try { S.zones = await client.call("list_zones"); } catch (e) { /* link down */ } }
document.addEventListener("click", e => {
  const g = e.target.closest("[data-goto]"); if (g) return setView(g.dataset.goto);
  const b = e.target.closest("[data-act]"); if (!b || !ACTIONS[b.dataset.act]) return;
  ACTIONS[b.dataset.act](b.dataset, b);
});

/* ---------- shell ---------- */
function setView(id) {
  activeView = id; $$(".view").forEach(v => v.classList.toggle("on", v.id === "view-" + id)); $$("#nav button").forEach(b => b.classList.toggle("on", b.dataset.view === id));
  try { localStorage.setItem("cgz-view", id); } catch (e) { /* private mode */ }
  const v = VIEWS.find(x => x.id === id); if (v?.show) v.show(); renderNow();
}
let lastVersion = -1, lastRender = 0;
function renderNow() { lastVersion = S.version; renderTop(); const v = VIEWS.find(x => x.id === activeView); try { v?.update(); } catch (e) { console.error(activeView, e); } syncStates(); }
function renderTop() {
  const st = S.status, c = S.city; if (!st) return;
  $("#clock").textContent = st.clock; $("#clocksub").textContent = `Day ${st.day} · ${st.period}`;
  const q = c.earthquakes?.active || 0, open = (c.incidents?.incidents || []).length;
  const pills = [[`<b>${esc(st.weather)}</b> weather`, ""], [`<b>${fmt(st.traffic?.simulated_vehicles, 0)}</b> vehicles`, ""], [`<b>${fmt(st.citizens_shown, 0)}</b> people`, ""], [`<b>${c.parking?.parked ?? 0}/${c.parking?.capacity ?? 0}</b> parked`, ""],
    [`<b>${st.device_count}</b> devices`, ""]];
  if (st.paused) pills.push(["<b>PAUSED</b>", "warn"]); if (q) pills.push([`<b>${q}</b> earthquake${q > 1 ? "s" : ""} active`, "bad"]); if (open) pills.push([`<b>${open}</b> open incident${open > 1 ? "s" : ""}`, "warn"]);
  setHtmlIfChanged($("#pills"), pills.map(([t, cls]) => `<span class="pill ${cls}">${t}</span>`).join(""));
  const r = S.rate; $("#rate").innerHTML = `${fmt(r.perSec, 1)} updates/s · ${fmt(r.kbps, 0)} KB/s<br><span class="faint">tick ${(TICK * 1000).toFixed(0)} ms · payload ${fmt(r.lastBytes / 1024, 0)} KB</span>`;
  const badge = (id, n, alert) => { const e = $(`#nav [data-view=${id}] .count`); if (e) { e.textContent = n || ""; e.style.display = n ? "" : "none"; e.classList.toggle("alert", !!alert); } };
  badge("incidents", open + q, open + q); badge("sensors", st.device_count, false);
  const quick = $("#quick"); const sp = `<div class="seg">${[1, 2, 4, 8].map(s => `<button data-act="speed" data-v="${s}" data-on="speed:${s}">${s}×</button>`).join("")}</div>`;
  const k = (st.paused ? "r" : "p") + st.speed; if (quick._k !== k) { quick._k = k; quick.innerHTML = `${st.paused ? '<button class="btn good" data-act="resume">▶ Resume</button>' : '<button class="btn" data-act="pause">⏸ Pause</button>'}${sp}`; }
}
function startStreams() {
  if (startStreams.city) startStreams.city.cancel();
  startStreams.city = client.stream("read_city", { interval_s: TICK }, onCity);      // the whole city, one progress notification per 100 ms tick
  refreshZones(); clearInterval(startStreams.zoneTimer); startStreams.zoneTimer = setInterval(() => { if (client.endpoint) refreshZones(); }, 1000);
}
function boot() {
  const nav = $("#nav"), views = $("#views");
  for (const v of VIEWS) {
    nav.append(html(`<button data-view="${v.id}" title="${v.sub}"><svg viewBox="0 0 24 24">${ICONS[v.id] || ""}</svg><span>${v.title}</span><em class="count" style="display:none"></em></button>`));
    const section = html(`<section class="view" id="view-${v.id}"></section>`); views.append(section); v.build(section);
  }
  nav.addEventListener("click", e => { const b = e.target.closest("button[data-view]"); if (b) setView(b.dataset.view); });
  client.onLink = up => { $("#linkdot").className = "dot " + (up ? "up" : "down"); $("#linktext").textContent = up ? "live via MCP" : "disconnected — retrying…"; };
  client.onSession = startStreams; Boot.onData = () => {}; client.connect();
  let first = "overview"; try { first = localStorage.getItem("cgz-view") || first; } catch (e) { /* ignore */ } if (!VIEWS.find(v => v.id === first)) first = "overview";
  setView(first);
  const loop = () => { const t = performance.now(); if (S.version !== lastVersion && t - lastRender > 80) { lastRender = t; renderNow(); } requestAnimationFrame(loop); };
  requestAnimationFrame(loop);
}
