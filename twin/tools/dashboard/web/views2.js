"use strict";
/* Pages: Sensors, Trends, Traffic, People, Parking & waste, Zones, Incidents & quakes, Utilities, Payload. */

const familyLabel = f => S.status?.family_labels?.[f] || f;
const headline = d => { const first = Object.entries(d.readings)[0]; return first ? `${fmt(first[1].value, 2)} <span class="faint">${esc(first[1].unit)}</span>` : ""; };
const stateText = d => (d.state ? Object.entries(d.state).filter(([k]) => ["state", "mode", "barrier_mode", "power_cut", "water_cut"].includes(k)).map(([k, v]) => `${v}`).join(" · ") : "");

/* ---------- Sensors ---------- */
const SENS = { family: "all", kind: "all", query: "", sel: null, metric: null, cmds: new Map(), json: null, tableAt: 0, chart: null, wave: null };
ACTIONS.pick = d => { SENS.sel = d.id; SENS.metric = null; SENS.json = null; SENS.ctlFor = null; $("#se-json").replaceChildren(); renderSensors(true); };
ACTIONS.metric = d => { SENS.metric = d.v; track(`dev:${SENS.sel}:${d.v}`); renderSensors(true); };
ACTIONS.family = d => { SENS.family = d.v; renderSensors(true); };
ACTIONS.kind = d => { SENS.kind = d.v; renderSensors(true); };
function renderSensors(force) {
  if (!S.city) return; const t = performance.now(); if (!force && t - SENS.tableAt < 250) { renderDetail(); return; } SENS.tableAt = t;
  const counts = [...S.byFamily.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  setHtmlIfChanged($("#se-chips"), [`<button class="chip ${SENS.family === "all" ? "on" : ""}" data-act="family" data-v="all">All<small>${S.devices.size}</small></button>`, ...counts.map(([f, rows]) => `<button class="chip ${SENS.family === f ? "on" : ""}" data-act="family" data-v="${f}">${esc(familyLabel(f))}<small>${rows.length}</small></button>`)].join(""));
  const q = SENS.query.toLowerCase(); let rows = [...S.devices.values()].filter(d => (SENS.family === "all" || d.family === SENS.family) && (SENS.kind === "all" || d.kind === SENS.kind) && (!q || `${d.id} ${d.name} ${d.zone} ${d.type}`.toLowerCase().includes(q)));
  rows.sort((a, b) => a.id.localeCompare(b.id)); const total = rows.length; rows = rows.slice(0, 250);
  $("#se-count").textContent = `${total} device${total === 1 ? "" : "s"}${total > 250 ? " (first 250 shown — narrow the filter)" : ""}`;
  setHtmlIfChanged($("#se-rows"), rows.map(d => `<tr class="row ${d.id === SENS.sel ? "sel" : ""}" data-act="pick" data-id="${d.id}"><td><b>${d.id}</b></td><td>${esc(d.name)}</td><td>${d.zone}</td><td>${headline(d)}</td><td class="muted">${esc(stateText(d))}</td>
    <td>${d.kind === "actuator" ? '<span class="tag info">actuator</span>' : ""}</td></tr>`).join(""));
  renderDetail();
}
async function loadCommands(d) {
  if (SENS.cmds.has(d.id) || d.kind !== "actuator") return; SENS.cmds.set(d.id, null);
  try { const r = await client.call("list_commands", { id: d.id }); SENS.cmds.set(d.id, r.commands || {}); SENS.ctlFor = null; } catch (e) { SENS.cmds.set(d.id, {}); }
}
function renderDetail() {
  const d = S.devices.get(SENS.sel), box = $("#se-detail"); if (!d) { $("#se-empty").style.display = ""; box.style.display = "none"; return; }
  $("#se-empty").style.display = "none"; box.style.display = "";
  $("#se-title").innerHTML = `${d.id} <span class="muted">· ${esc(d.type)}</span>`;
  $("#se-meta").innerHTML = `<b>${esc(d.name)}</b><br><span class="tag">${esc(familyLabel(d.family))}</span><span class="tag">zone ${d.zone}</span><span class="tag ${d.kind === "actuator" ? "info" : ""}">${d.kind}</span> <span class="faint">x ${fmt(d.location.x)} · z ${fmt(d.location.z)}</span>`;
  $("#se-focus").dataset.id = d.id;
  const metrics = Object.entries(d.readings); if (!SENS.metric || !(SENS.metric in d.readings)) { SENS.metric = metrics[0]?.[0]; if (SENS.metric) track(`dev:${d.id}:${SENS.metric}`); }
  setHtmlIfChanged($("#se-metrics"), metrics.map(([m, r]) => `<tr class="row ${m === SENS.metric ? "sel" : ""}" data-act="metric" data-v="${m}"><td>${m.replaceAll("_", " ")}</td><td style="text-align:right"><b>${fmt(r.value, 3)}</b> <span class="faint">${esc(r.unit)}</span></td></tr>`).join(""));
  setHtmlIfChanged($("#se-state"), d.state && Object.keys(d.state).length ? Object.entries(d.state).map(([k, v]) => `<dt>${k.replaceAll("_", " ")}</dt><dd>${esc(typeof v === "object" ? JSON.stringify(v) : v)}</dd>`).join("") : "");
  const key = `dev:${d.id}:${SENS.metric}`, unit = d.readings[SENS.metric]?.unit || "";
  $("#se-chart-title").textContent = `${SENS.metric?.replaceAll("_", " ")} · last 2 minutes`;
  SENS.chart.drawHistory(hist, [{ key, label: SENS.metric, color: "#38bdf8", unit }], 120);
  const ctl = $("#se-ctl"); loadCommands(d); const cmds = SENS.cmds.get(d.id);
  const sig = d.id + (cmds ? Object.keys(cmds).join() : "");
  if (ctl._sig !== sig && !ctl.contains(document.activeElement)) { ctl._sig = sig;
    ctl.innerHTML = cmds && Object.keys(cmds).length ? `<h3 style="margin:6px 0 8px">Commands</h3><div class="chips">${Object.entries(cmds).map(([c, desc]) => c === "dim"
      ? `<span class="row"><input class="input" id="se-dim" type="number" min="0" max="1" step="0.1" value="0.5" style="width:70px"><button class="btn sm" data-act="device" data-id="${d.id}" data-v="dim" data-valueinput="se-dim" title="${esc(desc)}">dim</button></span>`
      : `<button class="btn sm" data-act="device" data-id="${d.id}" data-v="${c}" title="${esc(desc)}">${c.replaceAll("_", " ")}</button>`).join("")}</div>` : ""; }
  const wave = $("#se-wave"); if (d.waveform) { wave.style.display = ""; drawWave(SENS.wave, d); } else wave.style.display = "none";
  if (!SENS.json) SENS.json = new LiveJson($("#se-json"), { openDepth: 2 });
  if ($("#se-jsonwrap").open) SENS.json.update(d);
}
function drawWave(chart, d) {
  const w = d.waveform, rate = w.samplingRateHz || 50, names = { vertical_z: ["Vertical", "#38bdf8"], east_west_e: ["East-west", "#4ade80"], north_south_n: ["North-south", "#fbbf24"] };
  const series = Object.entries(w.components).map(([k, ys]) => ({ label: names[k]?.[0] || k, color: names[k]?.[1], unit: "m/s²", ys, xs: ys.map((_, i) => i / rate - ys.length / rate) }));
  const n = series[0]?.ys.length || 1, ticks = []; for (let s = 0; s >= -n / rate; s -= 0.5) ticks.push({ x: -s * 0 + s, label: s === 0 ? "now" : `${s.toFixed(1)}s` });
  chart.plot(series, { xMin: -n / rate, xMax: 0, xTicks: ticks });
  $("#se-dist").textContent = d.distance_to_affected_area_m != null ? `${d.distance_to_affected_area_m} m from the shaken area` : "no earthquake in range";
}
defineView({
  id: "sensors", title: "Sensors", sub: "Every device and its live readings",
  build(root) {
    root.innerHTML = `<div class="viewhead"><div><h2>Sensors &amp; devices</h2><p>All devices of the city. Pick one to see every reading, its history, its commands and its raw JSON.</p></div></div>
      <div class="card" style="margin-bottom:14px"><div class="chips" id="se-chips"></div>
        <div class="row" style="margin-top:12px"><input class="input grow" id="se-q" placeholder="Search by id, name, type or zone…" style="max-width:380px">
          <div class="seg">${[["all", "All"], ["sensor", "Sensors"], ["actuator", "Actuators"]].map(([v, l]) => `<button data-act="kind" data-v="${v}" data-on="kind:${v}">${l}</button>`).join("")}</div><span class="muted" id="se-count"></span></div></div>
      <div class="grid g-side"><div class="card"><div class="tablewrap"><table><thead><tr><th>ID</th><th>Name</th><th>Zone</th><th>Reading</th><th>State</th><th></th></tr></thead><tbody id="se-rows"></tbody></table></div></div>
        <div class="card" style="position:sticky;top:0;align-self:start;max-height:calc(100vh - 130px);overflow:auto"><div id="se-empty" class="empty">Select a device in the table.</div>
          <div id="se-detail" style="display:none"><header><h3 id="se-title"></h3><span class="sp"></span><button class="btn primary sm" id="se-focus" data-act="focusdevice">Show in simulation</button></header><div id="se-meta" style="margin-bottom:10px"></div>
            <table><tbody id="se-metrics"></tbody></table><dl class="kv" id="se-state" style="margin:10px 0"></dl>
            <h3 style="margin:14px 0 6px" id="se-chart-title"></h3><div id="se-chart"></div>
            <div id="se-wave" style="display:none"><h3 style="margin:14px 0 6px">Ground motion (2 s window)</h3><div id="se-wavechart"></div><div class="muted" id="se-dist" style="margin-top:6px"></div></div>
            <div id="se-ctl"></div>
            <details id="se-jsonwrap" style="margin-top:14px"><summary class="muted" style="cursor:pointer">Raw JSON (live)</summary><div class="jsonbox" id="se-json" style="height:320px;min-height:0;margin-top:8px"></div></details></div></div></div>`;
    $("#se-q", root).addEventListener("input", e => { SENS.query = e.target.value; renderSensors(true); });
    SENS.chart = new Chart($("#se-chart", root), { height: 170 }); SENS.wave = new Chart($("#se-wavechart", root), { height: 170, area: false, xLabel: x => x.toFixed(2) + " s" });
    root.addEventListener("click", e => { const b = e.target.closest("[data-valueinput]"); if (b) { b.dataset.value = $("#" + b.dataset.valueinput).value; } }, true);
  },
  update() { renderSensors(false); $$("[data-on^='kind:']").forEach(b => b.classList.toggle("on", b.dataset.on === "kind:" + SENS.kind)); },
});

/* ---------- Trends ---------- */
const TR = { keys: ["vehicles", "stopped"], window: 120, normalize: false, chart: null };
const OWN_META = { vehicles: ["Vehicles on the road", ""], stopped: ["Stopped vehicles", ""], citizens: ["Citizens shown", ""], parked: ["Parked cars", ""], "grid:energy_level": ["Stored energy", "%"], "grid:grid_demand": ["Grid demand", "kW"],
  units_out: ["Emergency units out", ""], incidents: ["Open incidents", ""] };
function seriesMeta(key) {
  if (OWN_META[key]) return { label: OWN_META[key][0], unit: OWN_META[key][1] };
  const parts = key.split(":");
  if (["agg", "max", "min", "sum"].includes(parts[0])) { const unit = S.byFamily.get(parts[1])?.[0]?.readings[parts[2]]?.unit || ""; const how = { agg: "mean", max: "max", min: "min", sum: "total" }[parts[0]];
    return { label: `${familyLabel(parts[1])} · ${parts[2].replaceAll("_", " ")} (${how})`, unit }; }
  if (parts[0] === "dev") return { label: `${parts[1]} · ${parts[2].replaceAll("_", " ")}`, unit: S.devices.get(parts[1])?.readings[parts[2]]?.unit || "" };
  if (parts[0] === "lot") return { label: `${parts[1]} bays used`, unit: "" };
  return { label: key, unit: "" };
}
const PRESETS = { Traffic: ["vehicles", "stopped", "agg:traffic:vehicle_count", "agg:traffic:speed"], "Air & noise": ["agg:air:pm2_5", "agg:noise:db_a"], Weather: ["agg:weather:temperature", "agg:weather:wind_speed"],
  Power: ["grid:energy_level", "grid:grid_demand"], Water: ["agg:reservoir:water_level"], Waste: ["sum:waste:bin_weight", "agg:waste:fill_level"], Seismic: ["max:seismic:ground_acceleration"],
  Parking: ["parked", ...Array.from({ length: 8 }, (_, i) => `lot:LOT-0${i + 1}`)], "People & incidents": ["citizens", "incidents", "units_out"] };
ACTIONS.preset = d => { TR.keys = [...PRESETS[d.v]]; TR.keys.forEach(track); renderTrends(); };
ACTIONS.twin = d => { TR.window = +d.v; renderTrends(); };
ACTIONS.trnorm = () => { TR.normalize = !TR.normalize; renderTrends(); };
ACTIONS.trremove = d => { TR.keys = TR.keys.filter(k => k !== d.v); renderTrends(); };
ACTIONS.tradd = () => {
  const mode = $("#tr-mode").value, family = $("#tr-family").value, metric = $("#tr-metric").value, stat = $("#tr-stat").value, dev = $("#tr-device").value;
  const key = mode === "family" ? `${stat}:${family}:${metric}` : `dev:${dev}:${metric}`; if (!TR.keys.includes(key)) TR.keys.push(key); track(key); renderTrends();
};
function fillTrendPickers() {
  const fam = $("#tr-family"), mode = $("#tr-mode").value; const fams = [...S.byFamily.keys()].sort();
  if (fam.options.length !== fams.length) { const cur = fam.value; fam.innerHTML = fams.map(f => `<option value="${f}">${esc(familyLabel(f))}</option>`).join(""); if (fams.includes(cur)) fam.value = cur; }
  const rows = S.byFamily.get(fam.value) || [], metrics = Object.keys(rows[0]?.readings || {}); const met = $("#tr-metric");
  if (met._w !== fam.value) { met._w = fam.value; met.innerHTML = metrics.map(m => `<option>${m}</option>`).join(""); $("#tr-device").innerHTML = rows.slice(0, 300).map(d => `<option value="${d.id}">${d.id}</option>`).join(""); }
  $("#tr-stat").style.display = mode === "family" ? "" : "none"; $("#tr-device").style.display = mode === "device" ? "" : "none";
}
function renderTrends() {
  if (!S.city) return; fillTrendPickers();
  const series = TR.keys.map((k, i) => ({ key: k, ...seriesMeta(k), color: PALETTE[i % PALETTE.length], area: false }));
  TR.chart.opts.normalize = TR.normalize; TR.chart.opts.area = series.length === 1; TR.chart.drawHistory(hist, series, TR.window);
  legend($("#tr-legend"), series); legendValues($("#tr-legend"), series, hist);
  $$("[data-act=twin]").forEach(b => b.classList.toggle("on", +b.dataset.v === TR.window)); $("[data-act=trnorm]").classList.toggle("on", TR.normalize);
  const now = nowSec();
  setHtmlIfChanged($("#tr-stats"), series.map(s => { const h = hist.get(s.key); if (!h || !h.v.length) return `<tr><td>${esc(s.label)}</td><td colspan=5 class="faint">recording from now…</td><td></td></tr>`;
    const from = bisect(h.t, now - TR.window), v = h.v.slice(from), mn = Math.min(...v), mx = Math.max(...v), av = v.reduce((a, b) => a + b, 0) / v.length;
    return `<tr><td><i style="display:inline-block;width:10px;height:10px;border-radius:3px;background:${s.color};margin-right:8px"></i>${esc(s.label)}</td><td>${fmtValue(v.at(-1))}</td><td>${fmtValue(mn)}</td><td>${fmtValue(mx)}</td><td>${fmtValue(av)}</td><td class="muted">${s.unit}</td><td><button class="btn sm" data-act="trremove" data-v="${s.key}">remove</button></td></tr>`; }).join(""));
}
defineView({
  id: "trends", title: "Trends", sub: "Live charts of anything",
  build(root) {
    root.innerHTML = `<div class="viewhead"><div><h2>Trends</h2><p>Live history at the 100 ms tick. Hover a chart for exact values. Build your own chart from any sensor family or device.</p></div><span class="sp"></span>
      <div class="seg">${[[30, "30 s"], [120, "2 min"], [300, "5 min"], [600, "10 min"]].map(([v, l]) => `<button data-act="twin" data-v="${v}">${l}</button>`).join("")}</div><button class="btn" data-act="trnorm" title="Scale every line to its own range so different units can share a chart">Compare shapes</button></div>
      <div class="card" style="margin-bottom:14px"><div class="chips">${Object.keys(PRESETS).map(p => `<button class="chip" data-act="preset" data-v="${p}">${p}</button>`).join("")}</div></div>
      <div class="card" style="margin-bottom:14px"><div id="tr-chart"></div><div id="tr-legend"></div></div>
      <div class="grid g-side"><div class="card"><header><h3>Statistics for the window</h3></header><table><thead><tr><th>Series</th><th>Now</th><th>Min</th><th>Max</th><th>Mean</th><th>Unit</th><th></th></tr></thead><tbody id="tr-stats"></tbody></table></div>
        <div class="card"><header><h3>Add a series</h3></header><div class="stack"><select class="input" id="tr-mode"><option value="family">A whole family (mean, min, max, total)</option><option value="device">One device</option></select>
          <select class="input" id="tr-family"></select><select class="input" id="tr-metric"></select><select class="input" id="tr-stat"><option value="agg">mean</option><option value="max">max</option><option value="min">min</option><option value="sum">total</option></select>
          <select class="input" id="tr-device" style="display:none"></select><button class="btn primary" data-act="tradd">Add to chart</button><p class="muted" style="margin:0">A new series starts recording the moment you add it.</p></div></div></div>`;
    TR.chart = new Chart($("#tr-chart", root), { height: 340, yUnit: "" }); TR.keys.forEach(track);
    $("#tr-mode", root).addEventListener("change", () => { renderTrends(); }); $("#tr-family", root).addEventListener("change", () => { renderTrends(); });
  },
  update() { renderTrends(); },
});

/* ---------- Traffic ---------- */
const TF = { cls: "all", sort: "speed", sel: null, chart: null };
ACTIONS.tfcls = d => { TF.cls = d.v; renderTraffic(); };
function renderTraffic() {
  const c = S.city; if (!c) return; const t = c.status.traffic || {}, vs = c.vehicles?.vehicles || [];
  const moving = vs.filter(v => !v.stopped), mean = moving.length ? moving.reduce((a, v) => a + v.speed_mps, 0) / moving.length * 3.6 : 0;
  setKpi("tf-k1", { v: fmt(t.simulated_vehicles, 0), sub: "simulated by SUMO", spark: "vehicles" }); setKpi("tf-k2", { v: fmt(t.stopped_vehicles, 0), sub: `${t.simulated_vehicles ? fmt(t.stopped_vehicles / t.simulated_vehicles * 100, 0) : 0}% of traffic`, cls: t.stopped_vehicles > 20 ? "warn" : "", spark: "stopped", color: "#fb7185" });
  setKpi("tf-k3", { v: fmt(t.turning_vehicles, 0), sub: "turning at junctions" }); setKpi("tf-k4", { v: fmt(mean, 1), unit: "km/h", sub: "mean of moving vehicles", spark: "agg:traffic:speed", color: "#4ade80" });
  TF.chart.drawHistory(hist, [{ key: "vehicles", label: "On the road", color: "#38bdf8", area: true }, { key: "stopped", label: "Stopped", color: "#fb7185" }], 120);
  const byClass = {}; for (const v of vs) byClass[v.class] = (byClass[v.class] || 0) + 1; const max = Math.max(1, ...Object.values(byClass));
  setHtmlIfChanged($("#tf-class"), Object.entries(byClass).sort((a, b) => b[1] - a[1]).map(([k, n]) => `<div class="row" style="gap:10px;margin:6px 0"><span style="width:76px"><i style="display:inline-block;width:10px;height:10px;border-radius:3px;background:${classColor[k] || "#aaa"};margin-right:6px"></i>${k}</span><div class="bar grow"><i style="width:${n / max * 100}%;background:${classColor[k] || "#38bdf8"}"></i></div><b style="width:36px;text-align:right">${n}</b></div>`).join(""));
  const bins = Array.from({ length: 8 }, () => 0); for (const v of vs) bins[Math.min(7, Math.floor(v.speed_mps * 3.6 / 10))]++; const bm = Math.max(1, ...bins);
  setHtmlIfChanged($("#tf-hist"), `<div style="display:flex;align-items:flex-end;gap:6px;height:130px">${bins.map((n, i) => `<div style="flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;gap:4px;height:100%"><span class="muted" style="font-size:11px">${n}</span><div style="width:100%;height:${n / bm * 100}%;min-height:2px;background:linear-gradient(#38bdf8,#0e5a82);border-radius:4px 4px 0 0"></div></div>`).join("")}</div>
    <div style="display:flex;gap:6px;margin-top:6px">${bins.map((_, i) => `<span class="faint" style="flex:1;text-align:center;font-size:11px">${i * 10}${i === 7 ? "+" : ""}</span>`).join("")}</div><div class="faint" style="text-align:center;margin-top:2px">km/h</div>`);
  const sig = S.byFamily.get("signal") || [], st = { green: 0, amber: 0, red: 0 }; for (const d of sig) { const v = d.readings.ns_signal?.value; st[v === 2 ? "green" : v === 1 ? "amber" : "red"]++; }
  setHtmlIfChanged($("#tf-sig"), `<div class="row" style="gap:18px">${[["green", "#4ade80"], ["amber", "#fbbf24"], ["red", "#f87171"]].map(([k, col]) => `<div><div style="font-size:26px;font-weight:700;color:${col}">${st[k]}</div><div class="muted">N-S ${k}</div></div>`).join("")}<div class="muted" style="margin-left:auto">${sig.length} junction signals</div></div>`);
  const classes = ["all", ...Object.keys(byClass).sort()]; setHtmlIfChanged($("#tf-chips"), classes.map(k => `<button class="chip ${TF.cls === k ? "on" : ""}" data-act="tfcls" data-v="${k}">${k}</button>`).join(""));
  let rows = vs.filter(v => TF.cls === "all" || v.class === TF.cls).sort((a, b) => b.speed_mps - a.speed_mps).slice(0, 120);
  setHtmlIfChanged($("#tf-rows"), rows.map(v => `<tr class="row" data-act="followthis" data-v="${v.id}"><td><b>${esc(v.id)}</b></td><td><i style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${classColor[v.class] || "#aaa"};margin-right:6px"></i>${v.class}</td><td>${v.zone}</td><td>${fmt(v.speed_mps * 3.6, 0)} km/h</td><td>${v.stopped ? `<span class="tag bad">${esc(v.stop_reason)}</span>` : '<span class="tag good">moving</span>'}</td><td class="muted">${esc(v.road)}</td></tr>`).join(""));
}
defineView({
  id: "traffic", title: "Traffic", sub: "Vehicles, speeds and signals",
  build(root) {
    root.innerHTML = `<div class="viewhead"><div><h2>Traffic</h2><p>Every vehicle SUMO is simulating. Click a vehicle to follow it in the simulation window.</p></div></div>
      <div class="grid g4" style="margin-bottom:14px"><div id="tf-k1"></div><div id="tf-k2"></div><div id="tf-k3"></div><div id="tf-k4"></div></div>
      <div class="grid g-main" style="margin-bottom:14px"><div class="card"><header><h3>Vehicles over time</h3></header><div id="tf-chart"></div></div>
        <div class="stack"><div class="card"><header><h3>By class</h3></header><div id="tf-class"></div></div><div class="card"><header><h3>Traffic signals</h3></header><div id="tf-sig"></div></div></div></div>
      <div class="grid g-side"><div class="card"><header><h3>Vehicles</h3><span class="sp"></span><div class="chips" id="tf-chips"></div></header><div class="tablewrap"><table><thead><tr><th>ID</th><th>Class</th><th>Zone</th><th>Speed</th><th>State</th><th>Road</th></tr></thead><tbody id="tf-rows"></tbody></table></div></div>
        <div class="card"><header><h3>Speed distribution</h3></header><div id="tf-hist"></div></div></div>`;
    ["tf-k1", "tf-k2", "tf-k3", "tf-k4"].forEach((id, i) => $("#" + id, root).replaceWith(kpiCard(id, ["Vehicles on the road", "Stopped", "Turning", "Average speed"][i]))); TF.chart = new Chart($("#tf-chart", root), { height: 230 });
  },
  update() { renderTraffic(); },
});

/* ---------- People ---------- */
const PE = { sel: null, stream: null, json: null, q: "" };
ACTIONS.person = d => { PE.sel = d.v; if (PE.stream) PE.stream.cancel(); $("#pe-json").replaceChildren(); PE.json = new LiveJson($("#pe-json"), { openDepth: 3 });
  PE.stream = client.stream("read_citizen", { id: d.v, interval_s: 0.5 }, data => { PE.data = data; PE.json.update(data); renderPerson(); }); renderPeople(); };
function renderPerson() { const p = PE.data; if (!p) return; $("#pe-name").innerHTML = `${esc(p.name || p.id)} <span class="muted">· ${esc(p.id)}</span>`; $("#pe-follow").dataset.v = p.id; }
function renderPeople() {
  const c = S.city; if (!c) return; const ps = c.citizens?.citizens || [], q = PE.q.toLowerCase();
  setKpi("pe-k1", { v: ps.length, sub: `embodied right now`, spark: "citizens", color: "#a78bfa" }); setKpi("pe-k2", { v: c.citizens?.population || "?", sub: "citizens in the society record" });
  setKpi("pe-k3", { v: ps.filter(p => p.speed_mps > 0.05).length, sub: "moving" }); setKpi("pe-k4", { v: new Set(ps.map(p => p.zone)).size, sub: "zones with people" });
  const by = {}; for (const p of ps) { const k = (p.motion_state || "other").split("_")[0]; by[k] = (by[k] || 0) + 1; } const mx = Math.max(1, ...Object.values(by));
  setHtmlIfChanged($("#pe-by"), Object.entries(by).sort((a, b) => b[1] - a[1]).map(([k, n]) => barRow(k, n, mx, n)).join(""));
  const roles = {}; for (const p of ps) roles[p.role] = (roles[p.role] || 0) + 1;
  setHtmlIfChanged($("#pe-roles"), Object.entries(roles).sort((a, b) => b[1] - a[1]).map(([k, n]) => `<span class="tag info">${esc(k)} · ${n}</span>`).join(""));
  setHtmlIfChanged($("#pe-rows"), ps.filter(p => !q || `${p.name} ${p.role} ${p.action} ${p.zone}`.toLowerCase().includes(q)).map(p => `<tr class="row ${p.id === PE.sel ? "sel" : ""}" data-act="person" data-v="${p.id}"><td><b>${esc(p.name)}</b></td><td>${esc(p.role)}</td><td>${esc(p.action)}</td><td class="muted">${esc(p.decision_reason)}</td><td>${p.zone}</td><td>${fmt(p.speed_mps, 2)}</td></tr>`).join(""));
  $("#pe-detail").style.display = PE.sel ? "" : "none"; $("#pe-empty").style.display = PE.sel ? "none" : "";
}
defineView({
  id: "people", title: "People", sub: "Citizens and what they are doing",
  build(root) {
    root.innerHTML = `<div class="viewhead"><div><h2>People</h2><p>The embodied citizens of the city and the reason behind everything they do.</p></div><span class="sp"></span><input class="input" id="pe-q" placeholder="Filter people…"></div>
      <div class="grid g4" style="margin-bottom:14px"><div id="pe-k1"></div><div id="pe-k2"></div><div id="pe-k3"></div><div id="pe-k4"></div></div>
      <div class="grid g-side"><div class="stack"><div class="card"><header><h3>Activity</h3></header><div id="pe-by"></div><div id="pe-roles" style="margin-top:10px"></div></div>
        <div class="card"><header><h3>Citizens</h3></header><div class="tablewrap"><table><thead><tr><th>Name</th><th>Role</th><th>Doing</th><th>Why</th><th>Zone</th><th>m/s</th></tr></thead><tbody id="pe-rows"></tbody></table></div></div></div>
        <div class="card" style="position:sticky;top:0;align-self:start"><div id="pe-empty" class="empty">Select a citizen to stream their full record.</div><div id="pe-detail" style="display:none"><header><h3 id="pe-name"></h3><span class="sp"></span><button class="btn primary sm" id="pe-follow" data-act="followthis">Follow in simulation</button></header><div class="jsonbox" id="pe-json" style="height:calc(100vh - 260px);min-height:300px"></div></div></div></div>`;
    ["pe-k1", "pe-k2", "pe-k3", "pe-k4"].forEach((id, i) => $("#" + id, root).replaceWith(kpiCard(id, ["Shown in the city", "Population", "Moving", "Zones occupied"][i])));
    $("#pe-q", root).addEventListener("input", e => { PE.q = e.target.value; renderPeople(); });
  },
  update() { renderPeople(); },
});

/* ---------- Parking & waste ---------- */
const PK = { binMin: 0, chart: null };
ACTIONS.binfilter = d => { PK.binMin = +d.v; renderParking(); };
function renderParking() {
  const c = S.city; if (!c) return; const lots = c.parking?.lots || [], bins = (S.byFamily.get("waste") || []);
  const kg = bins.reduce((a, d) => a + (d.readings.bin_weight?.value || 0), 0), fill = bins.length ? bins.reduce((a, d) => a + (d.readings.fill_level?.value || 0), 0) / bins.length : 0;
  setKpi("pk-k1", { v: `${c.parking?.parked ?? 0}<small>/ ${c.parking?.capacity ?? 0}</small>`, sub: "bays used", spark: "parked", color: "#2dd4bf" });
  setKpi("pk-k2", { v: lots.reduce((a, l) => a + l.denied_entries, 0), sub: "cars turned away at barriers" }); setKpi("pk-k3", { v: fmt(kg, 1), unit: "kg", sub: `${bins.length} bins`, spark: "agg:waste:bin_weight" });
  setKpi("pk-k4", { v: fmt(fill, 1), unit: "%", sub: `${bins.filter(d => d.readings.fill_level?.value >= 80).length} bins almost full`, cls: bins.some(d => d.readings.fill_level?.value >= 95) ? "warn" : "", spark: "agg:waste:fill_level", color: "#fbbf24" });
  setHtmlIfChanged($("#pk-lots"), lots.map(l => { const full = l.occupied >= l.capacity, r = l.occupied / l.capacity;
    return `<div class="lot ${full ? "full" : ""} ${l.warning ? "warn" : ""}"><div class="row" style="justify-content:space-between"><b>${l.id} <span class="muted">zone ${l.zone}</span></b>${l.warning ? '<span class="tag bad">⚠ car refused</span>' : l.gate_open ? '<span class="tag good">barrier open</span>' : ""}</div>
      <div class="n">${l.occupied}<small> / ${l.capacity} bays</small></div><div class="bar ${full ? "bad" : r > .75 ? "warn" : ""}"><i style="width:${r * 100}%"></i></div>
      <div class="seg" style="width:fit-content">${["auto", "open", "closed"].map(m => `<button class="${l.barrier_mode === m ? "on" : ""}" data-act="barrier" data-id="${l.id}" data-v="${m}">${m}</button>`).join("")}</div>
      <div class="faint" style="font-size:12px">in ${l.entered_total} · out ${l.left_total} · refused ${l.denied_entries} · rerouted ${l.redirected_total} · ${l.incoming} cars on the way</div></div>`; }).join(""));
  const series = lots.map((l, i) => ({ key: "lot:" + l.id, label: l.id, color: PALETTE[i % PALETTE.length], unit: "bays" })); lots.forEach(l => track("lot:" + l.id));
  PK.chart.drawHistory(hist, series, 120); if (!$("#pk-legend")._built || $("#pk-legend")._n !== series.length) { legend($("#pk-legend"), series); $("#pk-legend")._built = 1; $("#pk-legend")._n = series.length; } legendValues($("#pk-legend"), series, hist);
  const rows = [...bins].filter(d => (d.readings.fill_level?.value || 0) >= PK.binMin).sort((a, b) => b.readings.fill_level.value - a.readings.fill_level.value).slice(0, 80);
  $$("[data-act=binfilter]").forEach(b => b.classList.toggle("on", +b.dataset.v === PK.binMin));
  setHtmlIfChanged($("#pk-bins"), rows.map(d => { const f = d.readings.fill_level.value; return `<tr><td><b>${d.id}</b></td><td>${d.id.startsWith("WGT-C") ? "container" : "street can"}</td><td>${d.zone}</td><td>${fmt(d.readings.bin_weight.value, 2)} kg</td>
    <td style="width:150px"><div class="bar ${f >= 95 ? "bad" : f >= 75 ? "warn" : ""}"><i style="width:${clamp(f, 0, 100)}%"></i></div></td><td>${fmt(f, 0)}%</td><td>${d.readings.deposits.value}</td><td><button class="btn sm" data-act="emptybin" data-id="${d.id}">Empty</button></td></tr>`; }).join(""));
}
defineView({
  id: "parking", title: "Parking & waste", sub: "Car parks and bins",
  build(root) {
    root.innerHTML = `<div class="viewhead"><div><h2>Parking &amp; waste</h2><p>Smart car parks with barrier control, and the weight sensors under every bin.</p></div></div>
      <div class="grid g4" style="margin-bottom:14px"><div id="pk-k1"></div><div id="pk-k2"></div><div id="pk-k3"></div><div id="pk-k4"></div></div>
      <div class="card" style="margin-bottom:14px"><header><h3>Car parks</h3></header><div class="grid g4" id="pk-lots"></div></div>
      <div class="card" style="margin-bottom:14px"><header><h3>Occupancy over time</h3></header><div id="pk-chart"></div><div id="pk-legend"></div></div>
      <div class="card"><header><h3>Waste bins</h3><span class="sp"></span><div class="seg">${[[0, "All"], [50, "≥ 50%"], [75, "≥ 75%"], [90, "≥ 90%"]].map(([v, l]) => `<button data-act="binfilter" data-v="${v}">${l}</button>`).join("")}</div></header>
        <div class="tablewrap"><table><thead><tr><th>Bin</th><th>Kind</th><th>Zone</th><th>Weight</th><th>Fill</th><th></th><th>Deposits</th><th></th></tr></thead><tbody id="pk-bins"></tbody></table></div></div>`;
    ["pk-k1", "pk-k2", "pk-k3", "pk-k4"].forEach((id, i) => $("#" + id, root).replaceWith(kpiCard(id, ["Parked cars", "Refused entries", "Waste weight", "Average fill"][i]))); PK.chart = new Chart($("#pk-chart", root), { height: 240 });
  },
  update() { renderParking(); },
});

/* ---------- Zones ---------- */
function renderZones() {
  const c = S.city; if (!c) return; const zones = S.zones?.zones || [], shown = new Set(S.zones?.shown_zones || []);
  const veh = zoneCounts(c.vehicles?.vehicles), peo = zoneCounts(c.citizens?.citizens), q = (c.earthquakes?.earthquakes || []).filter(e => e.status === "active"), allQ = q.some(e => e.zone === "ALL");
  const seis = {}; for (const d of S.byFamily.get("seismic") || []) seis[d.zone] = d;
  $("#zo-mag").textContent = UI.magnitude.toFixed(1);
  setHtmlIfChanged($("#zo-grid"), ZONE_IDS.map(z => { const info = zones.find(x => x.zone === z) || {}, d = seis[z], mmi = d?.readings.shaking_intensity?.value || 1, hit = allQ || q.some(e => e.zone === z);
    return `<div class="zonecell ${hit ? "shake" : ""}"><h4>${z} <small>${esc(info.name || "")}</small><span style="flex:1"></span>${shown.has(z) ? '<span class="tag info">overlay</span>' : ""}</h4>
      <div class="row2"><span>${veh[z] || 0} vehicles · ${peo[z] || 0} people</span><span>${info.devices ?? "–"} devices</span></div>
      <div class="row2"><span>${info.buildings ?? "–"} buildings</span><span style="color:${info.collapsed_buildings ? "var(--bad)" : "inherit"}">${info.collapsed_buildings || 0} collapsed</span></div>
      <div class="bar ${mmi > 5 ? "bad" : mmi > 3 ? "warn" : ""}"><i style="width:${clamp(mmi * 10, 0, 100)}%"></i></div><div class="row2"><span>shaking MMI ${fmt(mmi)}</span><span>${fmt(d?.readings.ground_acceleration?.value, 3)} m/s²</span></div>
      <div class="row"><button class="btn sm" data-act="zone" data-v="${z}">${shown.has(z) ? "Hide" : "Show"} overlay</button><button class="btn sm danger" data-act="shake" data-v="${z}">Shake</button></div></div>`; }).join(""));
}
defineView({
  id: "zones", title: "Zones", sub: "The 16 zones A–P",
  build(root) {
    root.innerHTML = `<div class="viewhead"><div><h2>Zones</h2><p>The city is a 4 × 4 grid of zones, A (north-west) to P (south-east). Show a zone on the ground or shake it.</p></div><span class="sp"></span>
      <div class="row"><span class="muted">Shake magnitude <b id="zo-mag" style="color:var(--text)">6.0</b></span><input type="range" min="4" max="8" step="0.1" value="6" id="zo-slider" style="width:140px"><button class="btn" data-act="zoneall" data-v="1">Show all</button><button class="btn" data-act="zoneall" data-v="0">Hide all</button><button class="btn danger" data-act="quakeclear">Clean up quakes</button></div></div>
      <div class="zonegrid" id="zo-grid"></div>`;
    $("#zo-slider", root).addEventListener("input", e => { UI.magnitude = +e.target.value; $("#zo-mag").textContent = UI.magnitude.toFixed(1); });
  },
  update() { renderZones(); },
});

/* ---------- Incidents & quakes ---------- */
const IQ = { seis: null, chart: null };
function renderIncidents() {
  const c = S.city; if (!c) return; const inc = c.incidents?.incidents || [], done = c.incidents?.resolved_recently || [], units = c.units?.units || [], quakes = c.earthquakes?.earthquakes || [];
  const card = (i, resolved) => `<div class="lot" style="gap:6px"><div class="row" style="justify-content:space-between"><b>${esc(i.id)} · ${esc(i.title || i.kind || "")}</b><span class="tag ${resolved ? "good" : "warn"}">${esc(i.phase || (resolved ? "resolved" : ""))}</span></div>
    <div>${i.severity ? `<span class="tag">${esc(i.severity)}</span>` : ""}${(i.units || []).map(u => `<span class="tag info">${esc(u.unit)} ${esc(u.status)}</span>`).join("")}</div>
    <div class="muted" style="font-size:12px">${esc((i.timeline || []).slice(-2).map(t => t.text || t.note || t.event || "").join(" · "))}</div><div class="row"><button class="btn sm" data-act="replayid" data-v="${esc(i.id)}">Replay</button><button class="btn sm" data-act="incidentwatch" data-id="${esc(i.id)}">Watch</button>${resolved ? "" : `<button class="btn sm good" data-act="incidentresolve" data-id="${esc(i.id)}">Resolve</button>${["police","ambulance","fire","tow"].map(k => `<button class="btn sm" data-act="incidentdispatch" data-id="${esc(i.id)}" data-v="${k}">+ ${k}</button>`).join("")}`}</div></div>`;
  setHtmlIfChanged($("#ic-list"), inc.length || done.length ? inc.map(i => card(i, false)).join("") + done.slice(0, 4).map(i => card(i, true)).join("") : '<div class="empty">No incidents. Trigger one from the buttons above.</div>');
  setHtmlIfChanged($("#ic-units"), units.map(u => `<tr><td><b>${esc(u.unit)}</b></td><td><i style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${classColor[u.kind] || "#aaa"};margin-right:6px"></i>${u.kind}</td><td><span class="tag ${["garaged", "idle", "patrol"].includes(u.status) ? "" : "warn"}">${u.status}</span></td><td>${esc(u.incident || "")}</td><td>${u.zone || ""}</td><td>${u.speed_mps != null ? fmt(u.speed_mps * 3.6, 0) : ""}</td></tr>`).join(""));
  setHtmlIfChanged($("#ic-quakes"), quakes.length ? quakes.slice().reverse().map(e => `<div class="lot" style="margin-bottom:8px"><div class="row" style="justify-content:space-between"><b>${esc(e.id)} · M${e.magnitude}</b><span class="tag ${e.status === "active" ? "bad" : ""}">${e.status}</span></div>
    <div>${e.zone === "ALL" ? "Whole city" : "Zone " + e.zone} · ${e.buildings_collapsed}/${e.buildings_planned} buildings collapsed · ${fmt(e.age_s, 0)} s ago</div></div>`).join("") : '<div class="empty">No earthquakes.</div>');
  const replay = c.ui?.replay || { playing:false };
  $("#ic-replay").style.display = replay.playing ? "" : "none";
  if (replay.playing) setHtmlIfChanged($("#ic-replay"), `<header><h3>Collision replay</h3><span class="sp"></span><span class="tag info">${fmt(replay.time,1)} / ${fmt(replay.duration,1)} s · ${fmt(replay.speed,2)}× · ${esc(replay.camera_mode)}</span></header><div class="row"><button class="btn" data-act="replayctl" data-v="${replay.paused ? "resume" : "pause"}">${replay.paused ? "▶ Resume" : "⏸ Pause"}</button><button class="btn" data-act="replayctl" data-v="restart">⏮ Restart</button>${[0.25,0.5,1,2].map(v => `<button class="btn sm" data-act="replayctl" data-v="speed" data-value="${v}">${v}×</button>`).join("")}${["orbit","chase","driver","street","overhead"].map(v => `<button class="btn sm" data-act="replayctl" data-v="camera" data-value="${v}">${v}</button>`).join("")}<button class="btn danger" data-act="replayctl" data-v="stop">Close</button></div>`);
  const sensors = S.byFamily.get("seismic") || []; const strongest = [...sensors].sort((a, b) => b.readings.ground_acceleration.value - a.readings.ground_acceleration.value)[0];
  const sel = IQ.seis ? S.devices.get(IQ.seis) : strongest; const pick = $("#ic-seis");
  if (pick.options.length !== sensors.length) { pick.innerHTML = `<option value="">strongest shaking</option>` + sensors.map(d => `<option>${d.id}</option>`).join(""); }
  if (sel?.waveform) { drawWave(IQ.chart, sel); $("#ic-wavetitle").textContent = `${sel.id} · zone ${sel.zone} · ${fmt(sel.readings.ground_acceleration.value, 3)} m/s² · MMI ${fmt(sel.readings.shaking_intensity.value)}`; $("#ic-dist").textContent = $("#se-dist").textContent; }
  setHtmlIfChanged($("#ic-seisrows"), sensors.map(d => `<tr><td><b>${d.id}</b></td><td>${d.zone}</td><td>${fmt(d.readings.ground_acceleration.value, 3)}</td><td>${fmt(d.readings.ground_velocity.value, 2)}</td><td><span class="tag ${d.readings.shaking_intensity.value > 4 ? "bad" : d.readings.shaking_intensity.value > 2 ? "warn" : ""}">MMI ${fmt(d.readings.shaking_intensity.value)}</span></td><td class="muted">${d.distance_to_affected_area_m != null ? d.distance_to_affected_area_m + " m" : "–"}</td></tr>`).join(""));
}
defineView({
  id: "incidents", title: "Incidents & quakes", sub: "Emergencies and ground motion",
  build(root) {
    root.innerHTML = `<div class="viewhead"><div><h2>Incidents &amp; earthquakes</h2><p>Start an emergency, watch the response, record the ground shake.</p></div></div>
      <div class="card" id="ic-replay" style="display:none;margin-bottom:14px"></div>
      <div class="grid g2" style="margin-bottom:14px"><div class="card"><header><h3>Trigger an incident</h3><span class="sp"></span><button class="btn good sm" data-act="resolveall">Resolve all now</button></header><div class="chips">${INCIDENT_KINDS.map(([k, l]) => `<button class="chip" data-act="incident" data-v="${k}">${l}</button>`).join("")}</div></div>
        <div class="card"><header><h3>Trigger an earthquake</h3><span class="sp"></span><button class="btn sm" data-act="quakeclear">Clean up</button></header><div class="row"><div class="chips">${["ALL", ...ZONE_IDS].map(z => `<button class="chip" data-act="qzone" data-v="${z}" data-on="qz:${z}">${z === "ALL" ? "Whole city" : z}</button>`).join("")}</div></div>
          <div class="row" style="margin-top:12px"><span class="muted">Magnitude</span><input type="range" min="4" max="8" step="0.1" value="6" class="grow" id="ic-mag"><b id="ic-magv">6.0</b><button class="btn danger" data-act="quake">⚠ Shake</button></div></div></div>
      <div class="grid g2" style="margin-bottom:14px"><div class="card"><header><h3>Incidents</h3></header><div class="stack" id="ic-list"></div></div><div class="card"><header><h3>Earthquakes</h3></header><div id="ic-quakes"></div></div></div>
      <div class="card" style="margin-bottom:14px"><header><h3>Seismic record</h3><span class="sp"></span><select class="input" id="ic-seis"></select></header><div class="muted" id="ic-wavetitle" style="margin-bottom:6px"></div><div id="ic-wave"></div><div class="muted" id="ic-dist"></div></div>
      <div class="grid g2"><div class="card"><header><h3>Seismic sensors</h3></header><div class="tablewrap"><table><thead><tr><th>Sensor</th><th>Zone</th><th>m/s²</th><th>mm/s</th><th>Intensity</th><th>Distance</th></tr></thead><tbody id="ic-seisrows"></tbody></table></div></div>
        <div class="card"><header><h3>Emergency fleet</h3></header><div class="tablewrap"><table><thead><tr><th>Unit</th><th>Kind</th><th>Status</th><th>Incident</th><th>Zone</th><th>km/h</th></tr></thead><tbody id="ic-units"></tbody></table></div></div></div>`;
    IQ.chart = new Chart($("#ic-wave", root), { height: 220, area: false, xLabel: x => x.toFixed(2) + " s" });
    $("#ic-mag", root).addEventListener("input", e => { UI.magnitude = +e.target.value; $("#ic-magv").textContent = UI.magnitude.toFixed(1); });
    $("#ic-seis", root).addEventListener("change", e => { IQ.seis = e.target.value || null; renderIncidents(); });
  },
  update() { renderIncidents(); },
});

/* ---------- Utilities ---------- */
const UT = { c1: null, c2: null };
function renderUtilities() {
  const c = S.city; if (!c) return; const grid = S.devices.get("GRID-01"), res = (S.byFamily.get("reservoir") || []).sort((a, b) => a.id.localeCompare(b.id));
  const en = grid?.readings.energy_level?.value; setKpi("ut-k1", { v: fmt(en, 1), unit: "%", sub: `${fmt(grid?.readings.energy_available?.value, 0)} kWh stored`, cls: en < 15 ? "bad" : en < 35 ? "warn" : "", spark: "grid:energy_level", color: "#4ade80" });
  setKpi("ut-k2", { v: fmt(grid?.readings.grid_demand?.value, 0), unit: "kW", sub: "city demand", spark: "grid:grid_demand", color: "#fb923c" });
  const lvl = res.length ? res.reduce((a, d) => a + d.readings.water_level.value, 0) / res.length : 0; setKpi("ut-k3", { v: fmt(lvl, 1), unit: "%", sub: `${res.length} reservoirs`, spark: "agg:reservoir:water_level", color: "#38bdf8" });
  const cut = (S.byFamily.get("service_zone") || []).filter(d => !d.readings.power_supplied.value || !d.readings.water_supplied.value).length; setKpi("ut-k4", { v: cut, sub: "service zones with a cut", cls: cut ? "warn" : "" });
  UT.c1.drawHistory(hist, [{ key: "grid:energy_level", label: "Stored energy", color: "#4ade80", unit: "%" }], 120); UT.c2.drawHistory(hist, [{ key: "agg:reservoir:water_level", label: "Mean reservoir level", color: "#38bdf8", unit: "%" }], 120);
  setHtmlIfChanged($("#ut-res"), res.map(d => barRow(d.name.replace("Reservoir_", ""), d.readings.water_level.value, 100, `${fmt(d.readings.water_volume.value, 0)} m³ · draw ${fmt(d.readings.water_demand.value, 1)} m³/h`, d.readings.water_level.value < 20 ? "bad" : d.readings.water_level.value < 45 ? "warn" : "")).join(""));
  const isOn = reading => reading == null || Number(reading.value) > 0;
  const tg = (id, kind, closed) => `<div class="seg"><button class="${closed ? "on" : ""}" data-act="${kind}" data-id="${id}" data-v="${kind === "switch" ? "close" : "open"}">${kind === "switch" ? "closed" : "open"}</button><button class="${!closed ? "on" : ""}" data-act="${kind}" data-id="${id}" data-v="${kind === "switch" ? "open" : "close"}">${kind === "switch" ? "open" : "closed"}</button></div>`;
  setHtmlIfChanged($("#ut-sw"), (S.byFamily.get("power_switch") || []).map(d => `<tr><td><b>${d.id}</b></td><td>${esc(d.name)}</td><td>${isOn(d.readings.powered) ? '<span class="tag good">powered</span>' : '<span class="tag bad">dark</span>'}</td><td>${tg(d.id, "switch", isOn(d.readings.switch_closed))}</td></tr>`).join(""));
  setHtmlIfChanged($("#ut-vl"), [...(S.byFamily.get("water_valve") || []), ...(S.byFamily.get("irrigation") || [])].map(d => `<tr><td><b>${d.id}</b></td><td>${esc(d.name)}</td><td>${d.family === "irrigation" ? `<span class="muted">${fmt(d.readings.irrigation_flow?.value, 1)} m³/h</span>` : ""}</td><td>${tg(d.id, "valve", isOn(d.readings.valve_open))}</td></tr>`).join(""));
  setHtmlIfChanged($("#ut-zones"), (S.byFamily.get("service_zone") || []).sort((a, b) => a.id.localeCompare(b.id)).map(d => { const p = d.readings.power_supplied.value, w = d.readings.water_supplied.value;
    return `<tr><td><b>${esc(d.name.replace("Zone_", ""))}</b></td><td>${p ? '<span class="tag good">power</span>' : '<span class="tag bad">CUT</span>'}</td><td>${w ? '<span class="tag good">water</span>' : '<span class="tag bad">CUT</span>'}</td><td>${fmt(d.readings.zone_power.value, 1)} kW</td><td>${fmt(d.readings.zone_water.value, 1)} m³/h</td>
      <td><div class="row"><button class="btn sm" data-act="service" data-id="${d.id}" data-service="power" data-v="${p ? "cut" : "restore"}">${p ? "Cut power" : "Restore power"}</button><button class="btn sm" data-act="service" data-id="${d.id}" data-service="water" data-v="${w ? "cut" : "restore"}">${w ? "Cut water" : "Restore water"}</button></div></td></tr>`; }).join(""));
  const lights = S.byFamily.get("lighting") || [], by = {}; for (const d of lights) { const k = d.state?.mode === "auto" ? (d.state.state || "auto") : `forced ${d.state?.mode}`; by[k] = (by[k] || 0) + 1; }
  setHtmlIfChanged($("#ut-lights"), `<div class="muted" style="margin-bottom:8px">${lights.length} lights</div>` + Object.entries(by).map(([k, n]) => `<span class="tag info">${esc(k.replaceAll("_", " "))} · ${n}</span>`).join(""));
}
defineView({
  id: "utilities", title: "Utilities", sub: "Power, water, lights",
  build(root) {
    root.innerHTML = `<div class="viewhead"><div><h2>Utilities</h2><p>The power reserve, water reservoirs, switch cabinets, valves and service zones.</p></div><span class="sp"></span>
      <div class="row"><button class="btn good" data-act="recharge">Recharge reserves</button><button class="btn" data-act="autorecharge" data-v="1">Auto-recharge on</button><button class="btn" data-act="autorecharge" data-v="0">Auto-recharge off</button></div></div>
      <div class="grid g4" style="margin-bottom:14px"><div id="ut-k1"></div><div id="ut-k2"></div><div id="ut-k3"></div><div id="ut-k4"></div></div>
      <div class="grid g2" style="margin-bottom:14px"><div class="card"><header><h3>Power reserve</h3></header><div id="ut-c1"></div></div><div class="card"><header><h3>Water level</h3></header><div id="ut-c2"></div></div></div>
      <div class="grid g3" style="margin-bottom:14px"><div class="card"><header><h3>Reservoirs</h3></header><div id="ut-res"></div></div>
        <div class="card"><header><h3>Power switches</h3></header><div class="tablewrap"><table><tbody id="ut-sw"></tbody></table></div><header style="margin-top:16px"><h3>Valves &amp; irrigation</h3></header><div class="tablewrap"><table><tbody id="ut-vl"></tbody></table></div></div>
        <div class="card"><header><h3>Street lights</h3></header><div id="ut-lights"></div><div class="row" style="margin-top:12px"><button class="btn" data-act="lights" data-v="on">All on</button><button class="btn" data-act="lights" data-v="off">All off</button><button class="btn" data-act="lights" data-v="auto">Auto</button></div></div></div>
      <div class="card"><header><h3>Service zones</h3></header><div class="tablewrap"><table><thead><tr><th>Zone</th><th>Power</th><th>Water</th><th>Load</th><th>Flow</th><th></th></tr></thead><tbody id="ut-zones"></tbody></table></div></div>`;
    ["ut-k1", "ut-k2", "ut-k3", "ut-k4"].forEach((id, i) => $("#" + id, root).replaceWith(kpiCard(id, ["Stored energy", "Grid demand", "Water level", "Cut service zones"][i])));
    UT.c1 = new Chart($("#ut-c1", root), { height: 200, fixedY: [0, 100], yUnit: "%" }); UT.c2 = new Chart($("#ut-c2", root), { height: 200, fixedY: [0, 100], yUnit: "%" });
  },
  update() { renderUtilities(); },
});

/* ---------- Payload: the raw JSON of any MCP stream, updated in place ---------- */
const PL = { tool: "@feed", args: {}, stream: null, json: null, mode: "tree", paused: false, n: 0, since: nowSec(), rate: 0, last: null, bytes: 0, lastText: 0 };
const STREAM_KEYS = new Set(["once", "duration_s", "interval_s"]);
function streamableTools() { return client.tools.filter(t => t.inputSchema.properties.interval_s && !t.name.startsWith("watch")).map(t => t.name).sort(); }
function plField(name, spec, required) {
  const label = `<label>${name}${required ? " *" : ""}</label>`, ph = esc(spec.description || "");
  if (spec.enum) return `<div class="field">${label}<select class="input" data-arg="${name}">${required ? "" : "<option value=''>(any)</option>"}${spec.enum.map(v => `<option>${v}</option>`).join("")}</select></div>`;
  if (spec.type === "boolean") return `<div class="field">${label}<select class="input" data-arg="${name}" data-type="boolean"><option value=''>(default)</option><option>true</option><option>false</option></select></div>`;
  if (spec.type === "array") return `<div class="field">${label}<input class="input" data-arg="${name}" data-type="array" placeholder="comma separated"></div>`;
  return `<div class="field">${label}<input class="input" data-arg="${name}" data-type="${spec.type === "integer" || spec.type === "number" ? "number" : "string"}" placeholder="${ph.slice(0, 60)}"></div>`;
}
function plBuildForm() {
  const sel = $("#pl-source"); if (sel.options.length < 3 && client.tools.length) {
    sel.innerHTML = `<option value="@feed">Dashboard feed (read_city) — no extra load</option><option value="@cmd">Last command response</option><optgroup label="Open a stream">${streamableTools().map(t => `<option>${t}</option>`).join("")}</optgroup>`; sel.value = PL.tool; }
}
function plSourceChanged() {
  PL.tool = $("#pl-source").value; const form = $("#pl-form"), run_ = $("#pl-run"); form.innerHTML = "";
  if (PL.tool === "@feed" || PL.tool === "@cmd") { run_.style.display = "none"; $("#pl-interval").parentElement.style.display = "none"; plStop(); PL.json.clear(); if (PL.tool === "@cmd") plShow(S.lastCommand, 0); return; }
  const tool = client.tools.find(t => t.name === PL.tool); run_.style.display = ""; $("#pl-interval").parentElement.style.display = "";
  const props = tool.inputSchema.properties, req = tool.inputSchema.required || [];
  form.innerHTML = Object.entries(props).filter(([k]) => !STREAM_KEYS.has(k)).map(([k, spec]) => plField(k, spec, req.includes(k))).join(""); plStop(); PL.json.clear();
  if (!req.length) plStart();
}
function plArgs() {
  const args = {}; for (const f of $$("#pl-form [data-arg]")) { let v = f.value.trim(); if (v === "") continue; const t = f.dataset.type;
    if (t === "number") v = +v; else if (t === "boolean") v = v === "true"; else if (t === "array") v = v.split(",").map(s => s.trim()).filter(Boolean); args[f.dataset.arg] = v; }
  args.interval_s = +$("#pl-interval").value || TICK; return args;
}
function plStart() { plStop(); PL.json.clear(); const args = plArgs(); PL.stream = client.stream(PL.tool, args, (data, parsed, bytes) => plShow(parsed, bytes)); $("#pl-run").textContent = "Restart stream"; $("#pl-stop").style.display = ""; }
function plStop() { if (PL.stream) { PL.stream.cancel(); PL.stream = null; } $("#pl-stop").style.display = "none"; $("#pl-run").textContent = "Start stream"; }
function plShow(payload, bytes) {
  if (payload == null) return; PL.last = payload; PL.n++; PL.bytes = bytes; const t = nowSec();
  if (t - PL.since >= 1) { PL.rate = PL.n / (t - PL.since); PL.n = 0; PL.since = t; }
  if (PL.paused) return;
  if (PL.mode === "tree") PL.json.update(payload);
  else if (t - PL.lastText > 0.2) { PL.lastText = t; $("#pl-text").textContent = jsonText(payload); }
  $("#pl-stats").innerHTML = `<span>source <b>${PL.tool === "@feed" ? "read_city" : PL.tool === "@cmd" ? "command response" : PL.tool}</b></span><span><b>${fmt(PL.rate, 1)}</b> updates/s</span><span>size <b>${fmt((bytes || JSON.stringify(payload).length) / 1024, 1)} KB</b></span>${payload.t !== undefined ? `<span>stream time <b>${fmt(payload.t, 1)} s</b></span>` : ""}`;
}
ACTIONS.plmode = d => { PL.mode = d.v; $("#pl-tree").style.display = d.v === "tree" ? "" : "none"; $("#pl-text").style.display = d.v === "text" ? "" : "none"; $$("[data-act=plmode]").forEach(b => b.classList.toggle("on", b.dataset.v === d.v)); if (PL.last) { const p = PL.paused; PL.paused = false; plShow(PL.last, PL.bytes); PL.paused = p; } };
ACTIONS.plpause = () => { PL.paused = !PL.paused; $("[data-act=plpause]").textContent = PL.paused ? "▶ Resume view" : "⏸ Freeze view"; $("[data-act=plpause]").classList.toggle("on", PL.paused); };
ACTIONS.plcopy = () => { navigator.clipboard?.writeText(JSON.stringify(PL.last, null, 2)).then(() => toast("JSON copied")); };
ACTIONS.plrun = () => plStart(); ACTIONS.plstop = () => plStop();
ACTIONS.plexpand = d => { PL.json.openDepth = +d.v; PL.json.open.clear(); PL.json.clear(); if (PL.last) PL.json.update(PL.last); };
Boot.onCommand = () => { if (PL.tool === "@cmd" && S.lastCommand) plShow(S.lastCommand, 0); };
defineView({
  id: "payload", title: "Payload", sub: "Raw JSON, live",
  build(root) {
    root.innerHTML = `<div class="viewhead"><div><h2>Raw payload</h2><p>The exact JSON the MCP server sends. One view that changes in place — values that change flash once.</p></div></div>
      <div class="payload"><div class="card stack"><div class="field"><label>Source</label><select class="input" id="pl-source"></select></div><div id="pl-form" class="stack"></div>
        <div class="field"><label>Tick (seconds between updates)</label><div><select class="input" id="pl-interval" style="width:100%">${[0.1, 0.2, 0.5, 1, 2].map(v => `<option value="${v}">${v === 0.1 ? "0.1 s — the 100 ms tick" : v + " s"}</option>`).join("")}</select></div></div>
        <div class="row"><button class="btn primary" id="pl-run" data-act="plrun" style="display:none">Start stream</button><button class="btn danger" id="pl-stop" data-act="plstop" style="display:none">Stop</button></div>
        <hr style="border:0;border-top:1px solid var(--line);width:100%"><div class="field"><label>View</label><div class="seg"><button class="on" data-act="plmode" data-v="tree">Tree</button><button data-act="plmode" data-v="text">Raw text</button></div></div>
        <div class="row"><button class="btn" data-act="plpause">⏸ Freeze view</button><button class="btn" data-act="plcopy">Copy JSON</button></div>
        <div class="field"><label>Expand</label><div class="row"><button class="btn sm" data-act="plexpand" data-v="1">1 level</button><button class="btn sm" data-act="plexpand" data-v="2">2 levels</button><button class="btn sm" data-act="plexpand" data-v="4">4 levels</button></div></div>
        <p class="muted" style="margin:0;font-size:12.5px">Tree mode keeps only what you have expanded in view, so even the 300 KB city snapshot stays smooth at 10 updates a second.</p></div>
        <div class="stack"><div class="statline" id="pl-stats">waiting for the first update…</div><div class="jsonbox" id="pl-tree"></div><pre class="jsonbox" id="pl-text" style="display:none"></pre></div></div>`;
    PL.json = new LiveJson($("#pl-tree", root), { openDepth: 2 });
    $("#pl-source", root).addEventListener("change", plSourceChanged); $("#pl-interval", root).addEventListener("change", () => { if (PL.stream) plStart(); });
    $("#pl-form", root).addEventListener("change", () => { if (PL.stream || (PL.tool[0] !== "@" && !PL.stream)) plStart(); });
    // The default feed is the dashboard's own read_city stream: tap it, no second subscription.
    PL.tap = (data, parsed, bytes) => { if (PL.tool === "@feed") plShow(parsed, bytes); };
  },
  show() { plBuildForm(); },
  update() { plBuildForm(); },
});

boot();
