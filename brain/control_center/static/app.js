/* City Brain control center: one page for waste, air quality, energy and transport.
   Data comes from the control center gateway (server.py), which watches the three
   axis backends. No library, no network beyond the gateway: works offline on stage. */
(() => {
  "use strict";

  // ------------------------------------------------------------------ domains
  const DOMAINS = {
    waste:     { name: "Waste",       hue: "var(--waste)",     agent: "waste_air", link: "waste_air" },
    air:       { name: "Air quality", hue: "var(--air)",       agent: "waste_air", link: "waste_air" },
    energy:    { name: "Energy",      hue: "var(--energy)",    agent: "energy",    link: "energy" },
    transport: { name: "Transport",   hue: "var(--transport)", agent: "transport", link: "transport" },
  };
  const AGENT_NAMES = { waste_air: "Waste and air quality", energy: "Energy", transport: "Transport" };
  const LINK_NAMES = { waste_air: "Waste and air Brain", energy: "Energy Brain", transport: "Transport Brain", brain: "City Brain (orchestrator)" };
  const STATUS_WORD = { ok: "Normal", watch: "Watch", alert: "Attention", critical: "Critical", offline: "Offline" };
  // Validated categorical order (dark surface): used for zones inside a chart.
  const SERIES = ["#3987e5", "#d55181", "#9085e9", "#199e70", "#c98b2a", "#7a8ea6"];

  const S = {
    route: { view: "overview", sub: "live" },
    overview: null, wa: null, en: null, tr: null, world: null, agents: null, config: null,
    feed: [], feedLast: 0, feedFilter: new Set(), logSeverity: "info", logSearch: "",
    energyZone: null, toolSel: {}, lastOk: 0, scenarios: null,
  };

  // ------------------------------------------------------------------ helpers
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v, d = 0) => v == null || Number.isNaN(v) ? "–" : Number(v).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d });
  const hms = iso => { if (!iso) return ""; const d = new Date(iso); return isNaN(d) ? "" : d.toLocaleTimeString([], { hour12: false }); };
  const hm = iso => hms(iso).slice(0, 5);
  const statusOf = v => (v && v.status) || "offline";
  const sevRank = { info: 0, watch: 1, alert: 2, critical: 3 };

  async function getJSON(url) {
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    return r.json();
  }
  async function postJSON(url, body) {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
    return j;
  }
  let toastTimer = null;
  function toast(text, err) {
    const t = $("toast");
    t.textContent = text; t.hidden = false; t.className = "toast" + (err ? " err" : "");
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 3800);
  }

  // ------------------------------------------------------------------ routing
  function parseRoute() {
    const parts = (location.hash.replace(/^#\/?/, "") || "overview").split("/");
    const view = ["overview", "city", "waste", "air", "energy", "transport", "agents", "log"].includes(parts[0]) ? parts[0] : "overview";
    const sub = parts[1] || (DOMAINS[view] ? "twin" : "");
    return { view, sub };
  }
  window.addEventListener("hashchange", () => { S.route = parseRoute(); mount(); });

  // ------------------------------------------------------------------ rail
  function renderNav() {
    const ov = S.overview;
    const item = (key, label, extra) => {
      const d = DOMAINS[key];
      const a = ov?.axes?.[key];
      const st = a ? statusOf(a) : "offline";
      const current = S.route.view === key ? ' aria-current="page"' : "";
      return `<a href="#/${key}"${current}>
        <span class="hue" style="background:${d ? d.hue : "transparent"}"></span>
        <span class="label">${esc(label)}</span>
        ${d ? `<span class="dot ${st}" title="${STATUS_WORD[st]}"></span><span class="sr">${STATUS_WORD[st]}</span>` : ""}
        ${d ? `<span class="headline">${esc(a ? a.headline : "Waiting for data")}</span>` : extra || ""}
      </a>`;
    };
    $("nav").innerHTML =
      `<a href="#/overview" class="plain"${S.route.view === "overview" ? ' aria-current="page"' : ""}><span class="hue"></span><span class="label">Overview</span></a>
       <a href="#/city" class="plain"${S.route.view === "city" ? ' aria-current="page"' : ""}><span class="hue" style="background:var(--emergency)"></span><span class="label">City map</span></a>
       <div class="nav-group">Domains</div>
       ${item("waste", "Waste")}${item("air", "Air quality")}${item("energy", "Energy")}${item("transport", "Transport")}
       <div class="nav-group">Supervision</div>
       <a href="#/agents" class="plain"${S.route.view === "agents" ? ' aria-current="page"' : ""}><span class="hue"></span><span class="label">Agents</span></a>
       <a href="#/log" class="plain"${S.route.view === "log" ? ' aria-current="page"' : ""}><span class="hue"></span><span class="label">Decision log</span></a>`;
    const links = ov?.links || {};
    $("links").innerHTML = Object.entries(LINK_NAMES).map(([k, n]) => {
      const l = links[k];
      const on = l?.online;
      return `<div><span>${esc(n)}</span><span><span class="dot ${on ? "ok" : "offline"}"></span> ${on ? `${fmt(l.latency_ms)} ms` : "offline"}</span></div>`;
    }).join("");
  }

  function setLive() {
    const el = $("live");
    const age = (Date.now() - S.lastOk) / 1000;
    const cls = !S.lastOk || age > 10 ? "down" : age > 4 ? "stale" : "";
    el.className = "live " + cls;
    el.querySelector("span").textContent = !S.lastOk ? "Connecting to the control center"
      : age > 10 ? "Control center unreachable" : `Live, updated ${Math.max(0, Math.round(age))} s ago`;
  }

  // ------------------------------------------------------------------ feed
  function feedItem(i) {
    const d = DOMAINS[i.axis] || FEED_EXTRA[i.axis] || (i.axis === "city" ? { name: "Whole city", hue: "var(--text-2)" } : { name: i.axis, hue: "var(--muted)" });
    return `<li><time datetime="${esc(i.wall)}">${hms(i.wall)}</time>
      <span class="sev ${esc(i.severity)}" title="${esc(i.severity)}"></span>
      <span class="txt"><span class="src"><i style="background:${d.hue}"></i>${esc(d.name)}</span>${esc(i.text)}${i.count > 1 ? `<span class="x">×${i.count}</span>` : ""}</span></li>`;
  }
  function feedList(items, empty) {
    if (!items.length) return `<div class="feed-empty">${esc(empty || "No decision yet.")}</div>`;
    return items.slice().reverse().map(feedItem).join("");
  }
  function filteredFeed(axes, minSev = "info", search = "") {
    const q = search.trim().toLowerCase();
    return S.feed.filter(i => (!axes.size || axes.has(i.axis)) && sevRank[i.severity] >= sevRank[minSev]
      && (!q || i.text.toLowerCase().includes(q)));
  }

  // ------------------------------------------------------------------ charts (hand-drawn SVG, hover)
  function lineChart(host, series, o) {
    if (!host) return;
    const W = Math.max(320, Math.round(host.clientWidth || o.width || 760)), H = o.height || 240, L = 44, R = o.right ?? 70, T = 12, B = 26;
    const all = series.flatMap(s => s.points.filter(p => p[1] != null));
    if (!all.length) { host.innerHTML = `<div class="feed-empty">${esc(o.empty || "No data yet.")}</div>`; return; }
    const x0 = Math.min(...all.map(p => p[0])), x1 = Math.max(...all.map(p => p[0]), x0 + 1);
    const top = Math.max(...all.map(p => p[1]), ...(o.thresholds || []).map(t => t[0] * 1.08), o.minMax || 1);
    const step = niceStep(top / 4), nY = Math.ceil(top / step), ymax = step * nY;
    const X = t => L + (t - x0) / (x1 - x0) * (W - L - R), Y = v => T + (1 - v / ymax) * (H - T - B);
    let g = "";
    for (let i = 0; i <= nY; i++) {
      const y = Y(step * i);
      g += `<line class="grid" x1="${L}" x2="${W - R}" y1="${y}" y2="${y}"/><text x="${L - 6}" y="${y + 4}" text-anchor="end">${fmt(step * i)}</text>`;
    }
    const nT = 5;
    for (let i = 0; i <= nT; i++) {
      const t = x0 + (x1 - x0) * i / nT;
      g += `<text x="${X(t)}" y="${H - 6}" text-anchor="middle">${esc(o.xfmt ? o.xfmt(t) : fmt(t))}</text>`;
    }
    for (const [v, name] of (o.thresholds || [])) {
      if (v > ymax) continue;
      g += `<line class="thr" x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${L + 6}" y="${Y(v) - 4}">${esc(name)}</text>`;
    }
    for (const s of series) {
      if (s.band) {
        const pts = s.band.filter(p => p[1] != null && p[2] != null);
        if (pts.length > 1) {
          const up = pts.map(p => `${X(p[0]).toFixed(1)},${Y(p[2]).toFixed(1)}`).join(" ");
          const lo = pts.slice().reverse().map(p => `${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(" ");
          g += `<polygon class="band" fill="${s.color}" points="${up} ${lo}"/>`;
        }
      }
    }
    const ends = [];
    for (const s of series) {
      const pts = s.points.filter(p => p[1] != null);
      if (!pts.length) continue;
      let d = "", pen = false, lastT = null;
      for (const p of pts) {
        const gap = o.gap && lastT != null && p[0] - lastT > o.gap;
        d += `${!pen || gap ? "M" : "L"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`; pen = true; lastT = p[0];
      }
      g += `<path class="series" stroke="${s.color}" ${s.dash ? 'stroke-dasharray="5 4"' : ""} ${s.thin ? 'stroke-width="1.5" opacity=".8"' : ""} d="${d}"/>`;
      const last = pts[pts.length - 1];
      if (!s.thin && !s.dash) g += `<circle cx="${X(last[0])}" cy="${Y(last[1])}" r="4" fill="${s.color}" stroke="var(--panel)" stroke-width="2"/>`;
      if (o.endLabels && !s.thin) ends.push({ s, y: Y(last[1]), v: last[1], x: X(last[0]) });
    }
    ends.sort((a, b) => a.y - b.y);
    for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 13) ends[i].y = ends[i - 1].y + 13;
    for (const e of ends) g += `<text x="${e.x + 8}" y="${e.y + 4}" style="fill:var(--text)">${esc(e.s.name)} ${fmt(e.v)}</text>`;
    g += `<line class="cross" x1="0" x2="0" y1="${T}" y2="${H - B}" visibility="hidden"/><rect x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="transparent"/>`;
    host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(o.label)}">${g}</svg><div class="tip"></div>`;
    const svg = host.querySelector("svg"), cross = svg.querySelector(".cross"), tip = host.querySelector(".tip");
    const times = [...new Set(all.map(p => p[0]))].sort((a, b) => a - b);
    const show = frac => {
      const box = svg.getBoundingClientRect(), sx = frac * W;
      let t = times[0];
      for (const c of times) if (Math.abs(X(c) - sx) < Math.abs(X(t) - sx)) t = c;
      cross.setAttribute("x1", X(t)); cross.setAttribute("x2", X(t)); cross.setAttribute("visibility", "visible");
      const rows = series.map(s => [s, s.points.find(p => p[0] === t)]).filter(r => r[1] && r[1][1] != null);
      tip.innerHTML = `<div class="muted">${esc(o.xfmt ? o.xfmt(t) : fmt(t))}</div>` + rows.map(([s, p]) =>
        `<div><span class="k" style="background:${s.color}"></span>${esc(s.name)}: <b>${fmt(p[1], o.digits ?? 1)}</b> ${esc(o.unit || "")}</div>`).join("");
      tip.style.display = "block";
      const px = X(t) / W * box.width;
      tip.style.left = Math.max(4, Math.min(px + 14, box.width - tip.offsetWidth - 4)) + "px";
      tip.style.top = "8px";
    };
    const area = svg.querySelector("rect");
    area.addEventListener("mousemove", ev => { const b = svg.getBoundingClientRect(); host._h = (ev.clientX - b.left) / b.width; show(host._h); });
    area.addEventListener("mouseleave", () => { host._h = null; cross.setAttribute("visibility", "hidden"); tip.style.display = "none"; });
    if (host._h != null) show(host._h);
  }
  function niceStep(raw) {
    const p = Math.pow(10, Math.floor(Math.log10(Math.max(raw, 1e-9))));
    for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= raw) return m * p;
    return 10 * p;
  }
  function spark(points, color, max) {
    const pts = points.filter(p => p != null);
    if (pts.length < 2) return "";
    const W = 140, H = 28, hi = Math.max(max || 0, ...pts);
    const d = pts.map((v, i) => `${i ? "L" : "M"}${(i / (pts.length - 1) * W).toFixed(1)},${(H - 2 - v / hi * (H - 4)).toFixed(1)}`).join("");
    return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"><path d="${d}" fill="none" stroke="${color}" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>`;
  }

  // ------------------------------------------------------------------ domain pieces
  const BIN_COLORS = { red: "var(--critical)", orange: "var(--alert)", yellow: "var(--watch)", green: "var(--ok)" };
  function binMatrix() {
    const st = S.wa?.state || {}; const bins = st.bins || {}; const blocked = st.blocked_bins || {};
    const ids = Object.keys(bins).sort();
    if (!ids.length) return `<div class="feed-empty">Waiting for bin readings.</div>`;
    return `<div class="bins">${ids.map(id => {
      const b = bins[id];
      return `<div class="bin ${blocked[id] ? "blocked" : ""}" title="${esc(`${id}: ${b.reason}${blocked[id] ? ` — distrusted: ${blocked[id]}` : ""}`)}">
        <div class="fill ${b.color}" style="height:${Math.min(100, b.fill_pct)}%"></div>
        <span class="id">${esc(id)}</span><span class="pct num">${fmt(b.fill_pct)}%</span></div>`;
    }).join("")}</div>
    <div class="legend"><span><i style="background:var(--critical)"></i>Full, ≥ 90%</span><span><i style="background:var(--alert)"></i>Full before next pass</span><span><i style="background:var(--watch)"></i>Can wait</span><span><i style="background:var(--ok)"></i>Under 10%</span></div>`;
  }

  function airZones(big) {
    const zones = S.wa?.state?.air || {};
    const ids = Object.keys(zones).sort();
    if (!ids.length) return `<div class="feed-empty">Waiting for air readings.</div>`;
    const ACT = { sensitive_alert: "schools alerted", public_alert: "public alert", close_parking: "car parks closed" };
    return `<div class="zones${big ? " big" : ""}">${ids.map((z, i) => {
      const a = zones[z]; const lvl = a.kept_level || a.level;
      const risk = (S.wa?.risk?.[z] || []).slice(-60).map(p => p[1]);
      return `<div class="zone ${esc(lvl)} ${a.unknown ? "unknown" : ""}" title="${esc(a.reason)}">
        <div class="top"><span class="name">Zone ${esc(z)}</span><span class="risk num">${a.risk == null ? "–" : fmt(a.risk)}</span></div>
        <div class="lvl">${esc(a.unknown && a.kept_level ? `no trusted sensor, keeps ${a.kept_level}` : lvl)}</div>
        ${big ? `<div class="acts">${esc(a.actions.map(x => ACT[x] || x).join(", ") || "no action")}</div>` : ""}
        ${spark(risk, SERIES[i % SERIES.length], 140)}</div>`;
    }).join("")}</div>`;
  }

  function energyLoads() {
    const city = S.en?.city || {}, f = S.en?.frame || {};
    const zones = city.zones || [];
    if (!zones.length || !f.zones) return `<div class="feed-empty">Waiting for the first grid cycle.</div>`;
    return `<div class="loads">${zones.map(z => {
      const v = f.zones[z.id] || {};
      const load = v.acc ?? v.read;
      const max = z.cap * 1.25;
      const pct = load == null ? null : Math.round(100 * load / z.cap);
      const w = load == null ? 0 : Math.min(100, 100 * load / max);
      const up = v.up == null ? null : Math.min(100, 100 * v.up / max);
      return `<div class="load-row ${v.risk ? "risk" : ""}" title="${esc(`${z.id}: ${fmt(load)} kW of ${fmt(z.cap)} kW; 45 s upper band ${fmt(v.up)} kW; sensor ${v.trust}`)}">
        <span class="zn">${esc(z.id)}<small>${esc(z.tags.join(", "))}${v.prio ? ", priority" : ""}</small></span>
        <span class="load-track" role="img" aria-label="${esc(`${z.id} load ${pct}% of capacity`)}">
          ${up != null && up > w ? `<span class="band" style="left:${w}%;width:${up - w}%"></span>` : ""}
          <span class="now" style="width:${w}%"></span>
          <span class="cap85" style="left:${100 / 1.25}%"></span>
        </span>
        <span class="pct">${pct == null ? "–" : pct + "%"}<small>${fmt(load)} kW</small></span></div>`;
    }).join("")}</div>
    <div class="legend"><span><i style="background:var(--energy)"></i>Load now</span><span><i style="background:color-mix(in srgb, var(--energy) 35%, transparent)"></i>45 s forecast, upper band</span><span><i style="background:var(--muted);width:2px"></i>Capacity</span></div>`;
  }

  // transport map on a canvas
  function drawMap(canvas, opts = {}) {
    if (!canvas) return;
    const w = S.world, st = S.tr?.state;
    const box = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, box.width * dpr); canvas.height = Math.max(1, box.height * dpr);
    const c = canvas.getContext("2d"); c.scale(dpr, dpr);
    const css = getComputedStyle(document.documentElement);
    const col = n => css.getPropertyValue(n).trim();
    c.clearRect(0, 0, box.width, box.height);
    if (!w || !w.bbox) { c.fillStyle = col("--muted"); c.font = "13px system-ui"; c.fillText("Waiting for the city map", 16, 26); return; }
    const [bx0, by0, bx1, by1] = w.bbox, pad = 14;
    const sc = Math.min((box.width - 2 * pad) / (bx1 - bx0), (box.height - 2 * pad) / (by1 - by0));
    const ox = (box.width - sc * (bx1 - bx0)) / 2, oy = (box.height - sc * (by1 - by0)) / 2;
    const P = (x, y) => [ox + (x - bx0) * sc, box.height - oy - (y - by0) * sc];
    c.lineCap = "round";
    c.strokeStyle = col("--line"); c.lineWidth = Math.max(2, 7 * sc);
    c.beginPath();
    for (const e of w.edges || []) { const [a, b] = e.shape; const p = P(a[0], a[1]), q = P(b[0], b[1]); c.moveTo(p[0], p[1]); c.lineTo(q[0], q[1]); }
    c.stroke();
    // car parks
    for (const lot of w.lots || []) {
      const s = st?.lots?.[lot.id]; const used = s ? 1 - s.free / Math.max(1, s.total) : 0;
      c.beginPath(); lot.corners.forEach((p, i) => { const q = P(p[0], p[1]); i ? c.lineTo(q[0], q[1]) : c.moveTo(q[0], q[1]); }); c.closePath();
      c.fillStyle = s && s.free === 0 ? col("--alert") : col("--transport"); c.globalAlpha = .18 + .5 * used; c.fill(); c.globalAlpha = 1;
      c.strokeStyle = col("--transport"); c.lineWidth = 1; c.stroke();
      if (opts.labels) { const q = P(lot.corners[0][0], lot.corners[0][1]); c.fillStyle = col("--text-2"); c.font = "11px system-ui"; c.fillText(lot.id, q[0] + 2, q[1] - 4); }
    }
    // signals
    const sig = st?.signals || {};
    for (const s of w.signals || []) {
      const v = sig[s.id]; const p = P(s.x, s.y); const str = v ? v[0] : "";
      const color = /[Gg]/.test(str) ? col("--ok") : /[yY]/.test(str) ? col("--watch") : col("--critical");
      if (v && v[1]) { c.beginPath(); c.arc(p[0], p[1], 9, 0, 7); c.strokeStyle = col("--critical"); c.lineWidth = 2; c.stroke(); }
      c.beginPath(); c.arc(p[0], p[1], Math.max(2.5, 3.2 * sc * 3), 0, 7); c.fillStyle = color; c.fill();
    }
    // vehicles
    const VC = { C: col("--text-2"), T: col("--muted"), D: col("--energy"), A: col("--critical"), P: col("--transport"), F: col("--alert") };
    for (const v of st?.vehicles || []) {
      const p = P(v[0], v[1]); const em = "APF".includes(v[3]);
      c.beginPath(); c.arc(p[0], p[1], em ? 3.6 : v[3] === "D" ? 3.2 : 2.1, 0, 7);
      c.fillStyle = VC[v[3]] || VC.C; c.globalAlpha = v[4] < 0.5 && !em ? .55 : 1; c.fill(); c.globalAlpha = 1;
    }
  }
  const MAP_LEGEND = `<div class="map-legend"><span><i style="background:var(--text-2)"></i>Car</span><span><i style="background:var(--energy)"></i>Driver with an app</span><span><i style="background:var(--critical)"></i>Ambulance</span><span><i style="background:var(--transport)"></i>Police</span><span><i style="background:var(--alert)"></i>Fire truck</span><span><i style="background:var(--ok)"></i>Signal green</span><span><i style="border:2px solid var(--critical);background:transparent"></i>Emergency priority</span></div>`;

  function offline(axis) {
    const l = S.overview?.links?.[DOMAINS[axis].link];
    return `<div class="offline-note"><b>${esc(LINK_NAMES[DOMAINS[axis].link])} is offline</b>${esc(l?.error ? l.error.slice(0, 140) : "Start it with start_all, then this panel fills in by itself.")}</div>`;
  }
  const isOnline = axis => !!S.overview?.links?.[DOMAINS[axis].link]?.online;

  // ------------------------------------------------------------------ views: overview
  function mountOverview() {
    $("title").textContent = "Overview";
    $("content").innerHTML = `
      <section class="panel tw-strip" aria-label="Live Twin"><div class="panel-body" id="ov-live"></div></section>
      <section class="situation" id="situation" aria-live="polite"><span class="bar"></span><div><p id="sentence">Reading the city…</p><ul class="attention" id="attention"></ul></div></section>
      <section class="panel scen" aria-labelledby="h-scen">
        <div class="panel-head"><h2 id="h-scen">Scenarios</h2><span class="sub">Play an incident live and watch each Brain answer it. Every scenario undoes its changes when it ends.</span><span class="spacer"></span>
          <button class="btn" type="button" id="scen-reset">Reset everything</button></div>
        <div class="panel-body">
          <div class="scen-axes" id="scen-axes"></div>
          <div class="scen-city"><div class="scen-city-head"><span class="scen-dots">${Object.values(DOMAINS).map(d => `<i style="background:${d.hue}"></i>`).join("")}</span>Whole city, every axis at once</div><div class="scen-city-grid" id="scen-city"></div></div>
        </div>
      </section>
      <div class="overview">
        ${["waste", "air", "energy", "transport"].map(k => `
          <section class="panel domain-panel" aria-labelledby="h-${k}">
            <div class="panel-head"><h2 id="h-${k}"><span class="hue" style="background:${DOMAINS[k].hue}"></span>${DOMAINS[k].name}</h2>
              <span id="pill-${k}"></span><span class="sub">axis Brain · own simulation</span><span class="spacer"></span><a class="btn ghost" href="#/${k}">Open</a></div>
            <div class="panel-body"><p class="headline" id="hl-${k}"></p><div id="ov-${k}"></div></div>
            <div class="panel-foot" id="ft-${k}"></div>
          </section>`).join("")}
        <section class="panel feed-col" aria-labelledby="h-feed">
          <div class="panel-head"><h2 id="h-feed">Decisions across the city</h2><span class="spacer"></span><a class="btn ghost" href="#/log">Full log</a></div>
          <div class="panel-body" style="padding-bottom:10px"><div class="chips" id="feed-chips"></div></div>
          <ul class="feed" id="ov-feed"></ul>
        </section>
      </div>`;
    $("feed-chips").innerHTML = Object.entries(DOMAINS).map(([k, d]) =>
      `<button class="chip" type="button" data-axis="${k}" aria-pressed="${S.feedFilter.has(k)}"><i style="background:${d.hue}"></i>${d.name}</button>`).join("");
    $("feed-chips").addEventListener("click", e => {
      const b = e.target.closest("button[data-axis]"); if (!b) return;
      const k = b.dataset.axis; S.feedFilter.has(k) ? S.feedFilter.delete(k) : S.feedFilter.add(k);
      b.setAttribute("aria-pressed", S.feedFilter.has(k)); updateOverview();
    });
    $("content").querySelector(".scen").addEventListener("click", async e => {
      const b = e.target.closest("button[data-scen]");
      if (b) {
        if (b.getAttribute("aria-busy") === "true") return;
        b.setAttribute("aria-busy", "true");
        try { const r = await postJSON(`/api/scenarios/${b.dataset.scen}/run`, {}); toast(`Scenario ${r.title} started`); upsertScen(r); }
        catch (err) { toast(err.message.slice(0, 160), true); b.removeAttribute("aria-busy"); }
        return;
      }
      if (e.target.closest("#scen-reset")) {
        const r = e.target.closest("#scen-reset");
        if (!r.dataset.armed) { r.dataset.armed = "1"; r.textContent = "Confirm: reset every axis"; setTimeout(() => { delete r.dataset.armed; r.textContent = "Reset everything"; }, 4000); return; }
        delete r.dataset.armed; r.textContent = "Resetting…"; r.disabled = true;
        try { const res = await postJSON("/api/scenarios/reset", {}); toast(res.ok ? "Every axis is back to normal" : `Reset done with errors: ${res.errors.join("; ").slice(0, 140)}`, !res.ok); }
        catch (err) { toast(err.message.slice(0, 160), true); }
        r.disabled = false; r.textContent = "Reset everything"; loadScen();
      }
    });
    loadScen();
    $("attention").addEventListener("click", e => { const b = e.target.closest("button[data-go]"); if (b) location.hash = "#/" + b.dataset.go; });
    window.addEventListener("resize", onResize);
  }
  function onResize() { drawMap($("ov-map")); drawMap($("tr-map"), { labels: true }); }

  // scenarios ---------------------------------------------------------
  async function loadScen() { try { S.scen = await getJSON("/api/scenarios"); renderScen(); } catch (e) { /* gateway restarting */ } }
  function upsertScen(v) { if (!S.scen) return; const i = S.scen.findIndex(x => x.id === v.id); if (i >= 0) S.scen[i] = v; renderScen(); }
  function scenButton(sc, wide) {
    const running = sc.state === "running";
    const pct = running ? Math.min(100, 100 * (sc.elapsed || 0) / sc.duration) : 0;
    const failed = (sc.done || []).filter(d => !d.ok);
    const status = running ? `Running, ${Math.round(sc.elapsed || 0)} of ${sc.duration} s`
      : sc.state === "done" ? (failed.length ? `Last run: ${failed.length} step${failed.length > 1 ? "s" : ""} failed` : "Last run: completed")
      : sc.state === "stopped" ? "Last run: stopped by reset" : `${sc.duration} s`;
    const off = sc.axes.filter(a => !isOnline(a));
    const tip = failed.length ? failed.map(f => `${f.label}: ${f.error}`).join("\n") : sc.steps.join("\n");
    return `<button type="button" class="scen-btn ${wide ? "wide" : ""} ${running ? "running" : ""} ${failed.length && !running ? "failed" : ""}" data-scen="${esc(sc.id)}"
        ${running ? 'aria-busy="true"' : ""} title="${esc(tip)}">
      <span class="scen-top"><span class="scen-id">${esc(sc.id)}</span><span class="scen-title">${esc(sc.title)}</span>
        ${wide ? `<span class="scen-dots">${sc.axes.map(a => `<i style="background:${DOMAINS[a].hue}" title="${esc(DOMAINS[a].name)}"></i>`).join("")}</span>` : ""}</span>
      <span class="scen-watch">${esc(sc.watch)}</span>
      <span class="scen-foot"><span>${esc(status)}</span>${off.length ? `<span class="scen-warn">${esc(off.map(a => DOMAINS[a].name).join(", "))} offline</span>` : running ? "" : `<span class="scen-go">Run</span>`}</span>
      <span class="scen-bar"><b style="width:${pct}%"></b></span>
    </button>`;
  }
  function renderScen() {
    const host = $("scen-axes"); if (!host || !S.scen) return;
    const html = ["waste", "air", "energy", "transport"].map(k =>
      `<div class="scen-col"><div class="scen-col-head"><span class="hue" style="background:${DOMAINS[k].hue}"></span>${DOMAINS[k].name}</div>
        ${S.scen.filter(x => x.axis === k).map(x => scenButton(x)).join("")}</div>`).join("");
    const city = S.scen.filter(x => x.axis === "city").map(x => scenButton(x, true)).join("");
    if (host._html !== html) { host.innerHTML = html; host._html = html; }   // no rebuild when nothing changed: keeps hover and focus
    if ($("scen-city")._html !== city) { $("scen-city").innerHTML = city; $("scen-city")._html = city; }
  }

  function pill(st) { return `<span class="pill ${st}"><span class="dot ${st}"></span>${STATUS_WORD[st] || st}</span>`; }

  function updateOverview() {
    const ov = S.overview; if (!ov) return;
    const sit = $("situation");
    sit.className = "situation " + (ov.status || "ok");
    $("sentence").textContent = ov.sentence;
    const AX = { Waste: "waste", Air: "air", Energy: "energy", Transport: "transport" };
    $("attention").innerHTML = (ov.attention || []).slice(0, 8).map(a =>
      `<li><button type="button" data-go="${AX[a.axis]}"><span class="dot ${a.severity}"></span><span class="tag">${esc(a.axis)}</span>${esc(a.text)}</button></li>`).join("");
    for (const k of Object.keys(DOMAINS)) {
      const a = ov.axes[k]; const st = statusOf(a);
      $("pill-" + k).innerHTML = pill(st);
      $("hl-" + k).textContent = a.headline;
      const body = $("ov-" + k), foot = $("ft-" + k);
      if (st === "offline") { body.innerHTML = offline(k); foot.innerHTML = ""; continue; }
      const f = a.figures || {};
      if (k === "waste") { body.innerHTML = binMatrix(); foot.innerHTML = `<span>${fmt(f.trucks_busy)} of ${fmt(f.trucks)} trucks on a run</span><span>${fmt(f.blocked)} sensors distrusted</span>`; }
      if (k === "air") { body.innerHTML = airZones(false); foot.innerHTML = `<span>${fmt(f.restricted)} zones with car parks closed</span><span>Worst zone ${esc(f.worst_zone || "–")}</span>`; }
      if (k === "energy") { body.innerHTML = energyLoads(); foot.innerHTML = `<span>Mode ${esc(f.mode || "–")}</span><span>Knowledge ${esc(f.level || "–")}</span><span>Guardian ${f.guardian_ok === false ? "unreachable" : "online"}</span><span>${esc(f.scenario || "")}</span>`; }
      if (k === "transport") {
        if (!$("ov-map")) body.innerHTML = `<div class="map-wrap" style="height:260px"><canvas id="ov-map"></canvas></div>`;
        drawMap($("ov-map"));
        foot.innerHTML = `<span>${fmt(f.vehicles)} vehicles, ${fmt(f.stopped)} stopped</span><span>Mean wait ${fmt(f.mean_wait, 1)} s</span><span>${fmt(f.parking_free)} of ${fmt(f.parking_total)} spaces free</span><span>${esc(f.clock || "")}</span>`;
      }
    }
    $("ov-feed").innerHTML = feedList(filteredFeed(S.feedFilter).slice(-120), "Decisions from every domain appear here.");
  }

  // ------------------------------------------------------------------ views: domain pages
  function mountDomain(axis, sub) {
    const d = DOMAINS[axis];
    $("title").textContent = d.name;
    const tab = (k, label) => `<a role="tab" href="#/${axis}/${k}" aria-selected="${sub === k}">${label}</a>`;
    $("content").innerHTML = `
      <div class="domain-head"><span class="hue" style="background:${d.hue}"></span><h2 class="title">${d.name}</h2><span id="dpill"></span><span class="headline" id="dhl"></span></div>
      <nav class="tabs" role="tablist">${tab("twin", "Live Twin")}${tab("live", "Axis Brain (own simulation)")}${tab("console", "Full console")}${tab("agent", "Agent tools")}</nav>
      <div id="dbody"></div>`;
    const body = $("dbody");
    if (sub === "console") return mountConsole(axis, body);
    if (sub === "agent") return mountRunner(body, d.agent, axis);
    if (sub === "twin") return mountTwinDomain(axis, body);
    ({ waste: mountWaste, air: mountAir, energy: mountEnergy, transport: mountTransport })[axis](body);
  }
  function updateDomainHead(axis) {
    const a = S.overview?.axes?.[axis]; if (!a || !$("dpill")) return;
    $("dpill").innerHTML = pill(statusOf(a)); $("dhl").textContent = a.headline;
  }

  function mountConsole(axis, body) {
    const c = S.config?.consoles || {};
    const host = location.hostname;
    const fix = u => (u || "").replace("127.0.0.1", host).replace("localhost", host);
    const url = fix(c[DOMAINS[axis].link]);
    const extra = axis === "waste" || axis === "air" ? `<a class="btn ghost" href="${esc(fix(c.waste_air_controls))}" target="_blank" rel="noopener">Open the mock Twin controls</a>` : "";
    body.innerHTML = `<div class="stack">
      <div class="console-bar"><span>The ${esc(DOMAINS[axis].name.toLowerCase())} team's own dashboard, unchanged.</span><span class="spacer" style="flex:1"></span>${extra}<a class="btn" href="${esc(url)}" target="_blank" rel="noopener">Open in a new tab</a></div>
      ${isOnline(axis) ? `<div class="console"><iframe src="${esc(url)}" title="${esc(DOMAINS[axis].name)} console"></iframe></div>` : `<div class="panel">${offline(axis)}</div>`}
    </div>`;
  }

  // waste ------------------------------------------------------------
  function mountWaste(body) {
    body.innerHTML = `
      <div class="figures" id="w-fig"></div>
      <div class="grid-2">
        <div class="stack">
          <section class="panel"><div class="panel-head"><h2>Bins</h2><span class="spacer"></span><span class="sub" id="w-time"></span></div><div class="panel-body" id="w-bins"></div></section>
          <section class="panel"><div class="panel-head"><h2>Every bin, most urgent first</h2></div><div class="table-scroll"><table class="data" id="w-table"></table></div></section>
        </div>
        <div class="stack">
          <section class="panel"><div class="panel-head"><h2>Trucks</h2></div><div class="table-scroll"><table class="data" id="w-trucks"></table></div></section>
          <section class="panel"><div class="panel-head"><h2>Waste decisions</h2></div><ul class="feed" id="w-feed"></ul>
            ${controlsBlock("waste")}</section>
        </div>
      </div>`;
    wireControls(body);
  }
  function updateWaste() {
    if (!$("w-fig")) return;
    if (!isOnline("waste")) { $("w-bins").innerHTML = offline("waste"); return; }
    const st = S.wa?.state || {}, bins = st.bins || {}, blocked = st.blocked_bins || {}, f = S.overview?.axes?.waste?.figures || {};
    $("w-fig").innerHTML = figure(f.bins, "bins watched") + figure(f.red, "full now", f.red ? "critical" : "") +
      figure(f.orange, "full before the next pass", f.orange ? "alert" : "") + figure(f.blocked, "sensors distrusted", f.blocked ? "alert" : "") +
      figure(`${fmt(f.trucks_busy)}<small>/${fmt(f.trucks)}</small>`, "trucks on a run");
    $("w-time").textContent = st.city_time ? `City time ${hm(st.city_time)}` : "";
    $("w-bins").innerHTML = binMatrix();
    const rank = { red: 0, orange: 1, yellow: 2, green: 3 };
    const rows = Object.entries(bins).sort((a, b) => rank[a[1].color] - rank[b[1].color] || b[1].fill_pct - a[1].fill_pct);
    $("w-table").innerHTML = `<thead><tr><th>Bin</th><th>Fill</th><th class="num">Projected</th><th>Why this colour</th></tr></thead><tbody>${rows.map(([id, b]) =>
      `<tr><td><span class="swatch" style="background:${BIN_COLORS[b.color]}"></span><b>${esc(id)}</b>${blocked[id] ? ` <span class="pill critical">distrusted</span>` : ""}</td>
       <td class="num"><span class="fillbar"><b style="width:${Math.min(100, b.fill_pct)}%;background:${BIN_COLORS[b.color]}"></b></span>${fmt(b.fill_pct)}%</td>
       <td class="num">${b.projected_pct == null ? "–" : fmt(b.projected_pct) + "%"}</td><td class="reason">${esc(blocked[id] ? `Ignored: ${blocked[id]}` : b.reason)}</td></tr>`).join("")}</tbody>`;
    const tr = st.trucks || {};
    $("w-trucks").innerHTML = `<thead><tr><th>Truck</th><th>Status</th><th>Next stop</th><th class="num">Load</th></tr></thead><tbody>${Object.entries(tr).map(([id, t]) =>
      `<tr><td><b>${esc(id)}</b></td><td>${esc(t.status)}</td><td>${esc(t.next_stop || "–")}</td><td class="num">${fmt(t.load_kg)} kg</td></tr>`).join("") ||
      `<tr><td colspan="4" class="muted">No truck reported. With City Ground Zero the Brain orders crews instead.</td></tr>`}</tbody>`;
    $("w-feed").innerHTML = feedList(filteredFeed(new Set(["waste"])).slice(-80));
  }
  function figure(v, k, cls) { return `<div class="figure ${cls || ""}"><div class="v">${typeof v === "string" ? v : fmt(v)}</div><div class="k">${esc(k)}</div></div>`; }

  // air --------------------------------------------------------------
  function mountAir(body) {
    body.innerHTML = `
      <div id="a-zones"></div>
      <section class="panel"><div class="panel-head"><h2>Risk score by zone</h2><span class="spacer"></span><span class="sub">Risk = max(R<sub>pm</sub>, R<sub>ox</sub>) + 0.10 × min(R<sub>pm</sub>, R<sub>ox</sub>)</span></div>
        <div class="panel-body"><div class="series-legend" id="a-legend"></div><div class="chart" id="a-chart"></div></div></section>
      <div class="grid-2e">
        <section class="panel"><div class="panel-head"><h2>Latest reading of each sensor</h2></div><div class="table-scroll"><table class="data" id="a-sensors"></table></div></section>
        <section class="panel"><div class="panel-head"><h2>Air decisions</h2></div><ul class="feed" id="a-feed"></ul>${controlsBlock("air")}</section>
      </div>`;
    wireControls(body);
  }
  function updateAir() {
    if (!$("a-zones")) return;
    if (!isOnline("air")) { $("a-zones").innerHTML = `<div class="panel">${offline("air")}</div>`; return; }
    $("a-zones").innerHTML = airZones(true);
    const risk = S.wa?.risk || {};
    const zones = Object.keys(risk).sort();
    const series = zones.map((z, i) => ({ name: "Zone " + z, color: SERIES[i % SERIES.length], points: risk[z].slice(-360).map(p => [Date.parse(p[0]), p[1]]) }));
    $("a-legend").innerHTML = series.map(s => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("");
    lineChart($("a-chart"), series, { label: "Risk score by zone over time", height: 260, minMax: 140, endLabels: true, digits: 0, gap: 60000,
      thresholds: [[70, "warning 70"], [100, "high 100"], [130, "critical 130"]], xfmt: t => new Date(t).toLocaleTimeString([], { hour12: false }) });
    const sensors = Object.entries(S.wa?.sensors || {}).sort((a, b) => (a[1].zone || "").localeCompare(b[1].zone || "") || a[0].localeCompare(b[0]));
    $("a-sensors").innerHTML = `<thead><tr><th>Sensor</th><th>Zone</th><th class="num">PM2.5 µg/m³</th><th class="num">O3 ppb</th><th class="num">NO2 ppb</th><th>Guardian</th></tr></thead><tbody>${sensors.map(([id, s]) =>
      `<tr><td><b>${esc(id)}</b></td><td>${esc(s.zone)}</td><td class="num">${fmt(s.pm25, 1)}</td><td class="num">${fmt(s.o3, 1)}</td><td class="num">${fmt(s.no2, 1)}</td>
       <td>${s.trusted ? `<span class="pill ok">trusted</span>` : `<span class="pill critical" title="${esc(s.reason)}">ignored</span>`}</td></tr>`).join("") || `<tr><td colspan="6" class="muted">No air reading yet.</td></tr>`}</tbody>`;
    $("a-feed").innerHTML = feedList(filteredFeed(new Set(["air"])).slice(-80));
  }

  // energy -----------------------------------------------------------
  function mountEnergy(body) {
    body.innerHTML = `
      <div class="figures" id="e-fig"></div>
      <div class="grid-2">
        <div class="stack">
          <section class="panel"><div class="panel-head"><h2>Load against capacity</h2><span class="spacer"></span><span class="sub" id="e-scn"></span></div><div class="panel-body" id="e-loads"></div></section>
          <section class="panel"><div class="panel-head"><h2>Zone load and 45 s forecast</h2><span class="spacer"></span><div class="seg" id="e-zones" role="group" aria-label="Zone"></div></div>
            <div class="panel-body"><div class="series-legend"><span><i style="background:var(--energy)"></i>Measured load</span><span><i style="background:color-mix(in srgb, var(--energy) 40%, transparent)"></i>Forecast band, 45 s ahead</span><span style="color:var(--muted)"><i class="dash"></i>True load (simulation only)</span></div><div class="chart" id="e-chart"></div></div></section>
          <section class="panel"><div class="panel-head"><h2>Grid switches</h2></div><div class="table-scroll"><table class="data" id="e-switches"></table></div></section>
        </div>
        <div class="stack">
          <section class="panel"><div class="panel-head"><h2>Energy decisions</h2></div><ul class="feed" id="e-feed"></ul>${controlsBlock("energy")}</section>
        </div>
      </div>`;
    $("e-zones").addEventListener("click", e => { const b = e.target.closest("button[data-z]"); if (b) { S.energyZone = b.dataset.z; updateEnergy(); } });
    wireControls(body);
    loadScenarios();
  }
  async function loadScenarios() {
    try { S.scenarios = S.scenarios || await getJSON("/api/energy/scenarios"); } catch (e) { return; }
    const sel = $("e-scenario");
    if (sel) sel.innerHTML = `<option value="FREE">Free play</option>` + S.scenarios.map(s => `<option value="${esc(s.id)}">${esc(s.id)}: ${esc(s.name)}</option>`).join("");
  }
  function updateEnergy() {
    if (!$("e-fig")) return;
    if (!isOnline("energy")) { $("e-loads").innerHTML = offline("energy"); return; }
    const f = S.en?.frame || {}, city = S.en?.city || {}, fig = S.overview?.axes?.energy?.figures || {};
    const atRisk = Object.values(f.zones || {}).filter(z => z.risk).length;
    $("e-fig").innerHTML = figure(`${fmt(fig.max_load_pct)}<small>%</small>`, `highest load (${fig.max_load_zone || "–"})`, fig.max_load_pct >= 100 ? "critical" : fig.max_load_pct >= 85 ? "alert" : "") +
      figure(atRisk, "zones at overload risk", atRisk ? "alert" : "") + figure(f.commands, "switch commands sent") +
      figure(f.mode || "–", "mode") + figure(f.level || "–", "knowledge level") + figure(f.guardian_ok === false ? "down" : "online", "Guardian", f.guardian_ok === false ? "alert" : "");
    $("e-scn").textContent = city.scenario ? `${city.scenario.id}: ${city.scenario.name}, t = ${fmt(f.t)} s` : "";
    $("e-loads").innerHTML = energyLoads();
    const zones = (city.zones || []).map(z => z.id);
    if (!S.energyZone || !zones.includes(S.energyZone)) {
      const risky = zones.find(z => f.zones?.[z]?.risk); S.energyZone = risky || zones[0];
    }
    $("e-zones").innerHTML = zones.map(z => `<button type="button" data-z="${esc(z)}" aria-pressed="${z === S.energyZone}">${esc(z)}</button>`).join("");
    const zc = (city.zones || []).find(z => z.id === S.energyZone);
    const hist = (S.en?.history || []).filter(h => h.zones[S.energyZone]);
    const pts = k => hist.map(h => [h.t, h.zones[S.energyZone][k]]);
    lineChart($("e-chart"), [
      { name: "Measured", color: "var(--energy)", points: pts("read"), band: hist.map(h => [h.t, h.zones[S.energyZone].fc, h.zones[S.energyZone].up]) },
      { name: "True", color: "var(--muted)", dash: true, points: pts("true") },
    ], { label: `Load of ${S.energyZone}`, unit: "kW", height: 250, minMax: zc ? zc.cap * 1.1 : 1, endLabels: false,
      thresholds: zc ? [[zc.cap, `capacity ${fmt(zc.cap)} kW`]] : [], xfmt: t => `t = ${fmt(t)} s`, digits: 0 });
    const sw = f.switches || {}, geo = Object.fromEntries((city.switches || []).map(s => [s.id, s]));
    $("e-switches").innerHTML = `<thead><tr><th>Switch</th><th>Connects</th><th>State</th><th class="num">Flow</th><th>Trust</th><th>Note</th></tr></thead><tbody>${Object.entries(sw).map(([id, s]) => {
      const g = geo[id] || {};
      const note = [s.quar && "quarantined", s.failed && "failed", s.stuck && "jammed", s.pending && "awaiting check", s.cool > 0 && `cooldown ${fmt(s.cool)} s`, s.brain_changed && "moved by the Brain"].filter(Boolean).join(", ");
      return `<tr><td><b>${esc(id)}</b></td><td>${esc(g.kind === "feeder" ? `${g.from} feeder` : `${g.from} → ${g.to}`)}</td><td>${esc(s.state)}</td><td class="num">${s.flow == null ? "–" : fmt(s.flow) + " kW"}</td>
        <td>${s.trust === "trusted" ? `<span class="pill ok">trusted</span>` : `<span class="pill alert">${esc(s.trust)}</span>`}</td><td class="muted">${esc(note || "–")}</td></tr>`;
    }).join("")}</tbody>`;
    $("e-feed").innerHTML = feedList(filteredFeed(new Set(["energy"])).slice(-100));
  }

  // transport --------------------------------------------------------
  function mountTransport(body) {
    body.innerHTML = `
      <div class="figures" id="t-fig"></div>
      <div class="grid-2">
        <div class="stack">
          <section class="panel"><div class="panel-head"><h2>City traffic, live</h2><span class="spacer"></span><span class="sub" id="t-clock"></span></div>
            <div class="panel-body"><div class="map-wrap" style="height:520px"><canvas id="tr-map"></canvas></div>${MAP_LEGEND}</div></section>
          <section class="panel"><div class="panel-head"><h2>Signal learning: mean waiting time per training window</h2><span class="spacer"></span><span class="sub">Rolling mean over 10 windows</span></div>
            <div class="panel-body"><div class="series-legend"><span><i style="background:var(--transport)"></i>Mean wait, all vehicles</span><span><i style="background:var(--critical)"></i>Mean wait, emergency vehicles</span></div><div class="chart" id="t-learn"></div></div></section>
        </div>
        <div class="stack">
          <section class="panel" aria-labelledby="h-tb"><div class="panel-head"><h2 id="h-tb">Test Book scenarios</h2><span class="spacer"></span><span class="sub" id="tb-sum"></span></div>
            <p class="tb-intro">Each scenario drives the mock Twin and Guardian, checks what the Brain did, and writes a log in the logs folder. Follow it live in the decisions below.</p>
            <div class="tb-list" id="tb-list"><div class="feed-empty">Loading the Test Book…</div></div></section>
          <section class="panel"><div class="panel-head"><h2>Car parks</h2></div><div class="panel-body"><div class="lots" id="t-lots"></div></div></section>
          <section class="panel"><div class="panel-head"><h2>Transport decisions</h2></div><ul class="feed" id="t-feed"></ul>${controlsBlock("transport")}</section>
        </div>
      </div>`;
    wireControls(body);
    wireTestbook(body);
    loadTestbook();
    window.addEventListener("resize", onResize);
  }
  // transport Test Book ------------------------------------------------
  async function loadTestbook() {
    try { S.tb = await getJSON("/api/transport/testbook"); S.tbErr = null; }
    catch (e) { S.tbErr = e.message; }
    renderTestbook();
  }
  function renderTestbook() {
    const host = $("tb-list"); if (!host) return;
    const key = JSON.stringify([S.tb, S.tbErr, isOnline("transport")]);
    if (host._key === key) return;                          // unchanged: keep open details and focus
    host._key = key;
    if (!isOnline("transport")) { host.innerHTML = offline("transport"); $("tb-sum").textContent = ""; return; }
    const list = S.tb?.scenarios || [];
    if (!list.length) {
      $("tb-sum").textContent = "";
      host.innerHTML = `<div class="offline-note"><b>No Test Book found</b>The transport Brain reads its scenarios from brain\\axes\\transportation\\engine\\scenarios (catalog.json and run.py), next to its brain folder. Copy that folder there and restart the transport Brain.</div>`;
      return;
    }
    const running = S.tb.running || S.tb.starting, starting = !S.tb.running && S.tb.starting;
    const runs = list.filter(x => x.last), pass = runs.filter(x => x.last.pass).length;
    $("tb-sum").textContent = runs.length ? `${pass} of ${runs.length} passed on their last run` : "Not run yet";
    S.tbOpen = S.tbOpen || new Set();
    host.innerHTML = list.map(x => {
      const l = x.last, isRun = running === x.id;
      const res = isRun ? `<span class="pill watch"><span class="dot watch"></span>${starting ? "Preparing the city" : "Running"}</span>`
        : !l ? `<span class="pill offline">Not run</span>`
        : `<span class="pill ${l.pass ? "ok" : "critical"}"><span class="dot ${l.pass ? "ok" : "critical"}"></span>${l.pass ? "Pass" : "Fail"}</span>`;
      const checks = l?.checks || [];
      const ok = checks.filter(c => c.ok).length;
      const meta = [(x.layers || []).join(" + "), l ? `${ok} of ${checks.length} checks, ${hm(l.finished)}` : ""].filter(Boolean).join(". ");
      return `<div class="tb-row ${isRun ? "running" : ""}">
        <span class="tb-id">${esc(x.id)}</span>
        <div class="tb-main"><div class="tb-name">${esc(x.name)}</div><div class="tb-meta">${esc(meta)}</div>
          ${checks.length ? `<details data-tb="${esc(x.id)}" ${S.tbOpen.has(x.id) ? "open" : ""}><summary>Checks of the last run</summary><ul class="tb-checks">${checks.map(c =>
            `<li class="${c.ok ? "ok" : "bad"}"><span aria-hidden="true">${c.ok ? "✓" : "✗"}</span><span class="sr">${c.ok ? "passed" : "failed"}: </span>${esc(c.text)}</li>`).join("")}</ul>${l.log ? `<div class="tb-log">Log: logs\\${esc(l.log)}</div>` : ""}</details>` : ""}
        </div>
        ${res}
        <button class="btn ${isRun ? "" : "primary"}" type="button" data-tb-run="${esc(x.id)}" ${running ? "disabled" : ""}>${isRun ? (starting ? "Starting…" : "Running…") : "Run"}</button>
      </div>`;
    }).join("");
  }
  function wireTestbook(root) {
    root.addEventListener("toggle", e => {
      const d = e.target; if (!d.matches || !d.matches("details[data-tb]")) return;
      S.tbOpen = S.tbOpen || new Set();
      d.open ? S.tbOpen.add(d.dataset.tb) : S.tbOpen.delete(d.dataset.tb);
    }, true);
    root.addEventListener("click", async e => {
      const b = e.target.closest("button[data-tb-run]"); if (!b) return;
      b.disabled = true;
      try { await postJSON(`/api/transport/testbook/${b.dataset.tbRun}/run`, {}); toast(`Test Book ${b.dataset.tbRun} started: the city is prepared first, then the checks run`); }
      catch (err) { toast(`Test Book ${b.dataset.tbRun}: ${err.message.slice(0, 160)}`, true); b.disabled = false; }
      loadTestbook();
    });
  }

  function updateTransport() {
    if (!$("t-fig")) return;
    if (!isOnline("transport")) { $("t-lots").innerHTML = offline("transport"); return; }
    const f = S.overview?.axes?.transport?.figures || {}, st = S.tr?.state || {}, L = st.learning || {};
    $("t-fig").innerHTML = figure(f.vehicles, "vehicles on the roads") + figure(f.stopped, "standing still") +
      figure(`${fmt(f.mean_wait, 1)}<small>s</small>`, "mean wait") + figure(`${fmt(f.mean_speed, 1)}<small>m/s</small>`, "mean speed") +
      figure(f.emergency, "emergency vehicles") + figure(L.preemptions, "priority greens given") +
      figure(`${fmt(f.parking_free)}<small>/${fmt(f.parking_total)}</small>`, "parking spaces free");
    $("t-clock").textContent = `${st.clock || ""}, signals in ${L.mode || "–"} mode, episode ${fmt(L.episode)}`;
    drawMap($("tr-map"), { labels: true });
    const lots = st.lots || {}, names = Object.fromEntries((S.world?.lots || []).map(l => [l.id, l.name]));
    $("t-lots").innerHTML = Object.entries(lots).map(([id, l]) => {
      const used = l.total - l.free;
      return `<div class="lot ${l.free === 0 ? "full" : ""}"><span><b>${esc(id)}</b> <span class="muted">${esc(names[id] || "")}</span></span>
        <span class="track"><span class="used" style="display:block;width:${100 * used / Math.max(1, l.total)}%"></span></span><span class="n">${fmt(l.free)} free</span></div>`;
    }).join("");
    const h = S.tr?.training?.history || [];
    const roll = (k, n = 10) => h.map((r, i) => { const w = h.slice(Math.max(0, i - n + 1), i + 1).map(x => x[k]).filter(v => v != null); return [r.window, w.length ? w.reduce((a, b) => a + b, 0) / w.length : null]; });
    lineChart($("t-learn"), [
      { name: "All vehicles", color: "var(--transport)", points: roll("mean_wait") },
      { name: "Emergency", color: "var(--critical)", points: roll("emerg_wait") },
    ], { label: "Mean waiting time per training window, 10-window rolling mean", unit: "s", height: 220, xfmt: t => `window ${fmt(t)}`, digits: 1, right: 40, empty: "The first training window is still running." });
    $("t-feed").innerHTML = feedList(filteredFeed(new Set(["transport"])).slice(-100));
  }

  // demo controls (relayed by the gateway to the axis simulations) --------------
  function controlsBlock(axis) {
    const b = (ctl, label, body, cls, id) => `<button class="btn ${cls || ""}" type="button" data-ctl="${ctl}" ${id ? `data-id="${esc(id)}"` : ""} data-body='${esc(JSON.stringify(body || {}))}'>${esc(label)}</button>`;
    let rows = "";
    if (axis === "waste") rows = `
      <div class="ctl-row"><span class="lbl">Attacks and incidents</span>${b("waste_air.control", "Fake a full reading on b06", { action: "spoof", sensor_id: "b06", value: 98 }, "danger")}${b("waste_air.control", "Stop the fake", { action: "unspoof", sensor_id: "b06" })}${b("waste_air.control", "Real fast fill on b04", { action: "surge", sensor_id: "b04" })}</div>
      <div class="ctl-row"><span class="lbl">Partners</span>${b("waste_air.control", "Guardian down", { action: "guardian_down" }, "danger")}${b("waste_air.control", "Guardian back", { action: "guardian_up" })}${b("waste_air.control", "Twin refuses dispatches", { action: "twin_offline" }, "danger")}${b("waste_air.control", "Twin accepts again", { action: "twin_online" })}</div>`;
    if (axis === "air") rows = `
      <div class="ctl-row"><span class="lbl">Attacks</span>${b("waste_air.control", "Fake PM2.5 spike on AIR-05", { action: "air_spike", sensor_id: "AIR-05" }, "danger")}${b("waste_air.control", "Plant caps AIR-07", { action: "air_cap", sensor_id: "AIR-07" }, "danger")}${b("waste_air.control", "Stop the fakes", { action: "air_stop" })}</div>
      <div class="ctl-row"><span class="lbl">Real incident</span>${b("waste_air.control", "Industrial release in zone D", { action: "air_release", sensor_id: "D", value: 40 })}${b("waste_air.control", "Release over", { action: "air_release_stop" })}</div>`;
    if (axis === "energy") rows = `
      <div class="ctl-row"><span class="lbl">Test Book scenario</span><select class="input" id="e-scenario" aria-label="Energy scenario"><option>Loading…</option></select><button class="btn primary" type="button" id="e-start">Start scenario</button></div>
      <div class="ctl-row"><span class="lbl">Inject</span>${b("energy.inject", "Surge in Z4, +180 kW", { action: "ramp", args: { zone_id: "Z4", add_kw: 180, rise_s: 20 } })}${b("energy.inject", "Spoof the Z1 sensor", { action: "spoof", args: { sensor_id: "PL-Z1", value_kw: 900, duration_s: 30 } }, "danger")}${b("energy.inject", "Guardian down", { action: "guardian_mode", args: { mode: "silent" } }, "danger")}${b("energy.inject", "Guardian back", { action: "guardian_mode", args: { mode: "trusted" } })}</div>
      <div class="ctl-row"><span class="lbl">Simulation</span>${b("energy.control", "Pause", { paused: true })}${b("energy.control", "Resume", { paused: false })}${b("energy.control", "Speed ×1", { speed: 1 })}${b("energy.control", "Speed ×4", { speed: 4 })}${b("energy.control", "Speed ×10", { speed: 10 })}</div>`;
    if (axis === "transport") rows = `
      <div class="ctl-row"><span class="lbl">Tests</span>${b("transport.jam", "Start a traffic-jam test", { size: 300, seconds: 60 }, "danger")}${b("transport.scenario", "Duplicate RFID", {}, "", "duplicate")}${b("transport.scenario", "Wrong-space parking", {}, "", "wrongspace")}</div>
      <div class="ctl-row"><span class="lbl">Attacks</span>${b("transport.fake_sensor", "Lying sensor in P02", { lot_id: "P02", space: "A1", occupied: true, seconds: 25 }, "danger")}${b("transport.flood", "Sensor floods the network", { lot_id: "P05", space: "A2", per_second: 120, seconds: 6 }, "danger")}</div>
      <div class="ctl-row"><span class="lbl">Traffic level</span>${b("transport.controls", "Light, ×0.5", { traffic_level: 0.5 })}${b("transport.controls", "Normal, ×1", { traffic_level: 1 })}${b("transport.controls", "Heavy, ×1.8", { traffic_level: 1.8 })}</div>
      <div class="ctl-row"><span class="lbl">Simulation</span>${b("transport.controls", "Pause", { paused: true })}${b("transport.controls", "Resume", { paused: false })}</div>`;
    return `<details class="controls"><summary>Demo controls</summary><div class="ctl-grid">${rows}</div></details>`;
  }
  function wireControls(root) {
    root.addEventListener("click", async e => {
      const b = e.target.closest("button[data-ctl]");
      if (b) {
        b.disabled = true;
        try {
          await postJSON(`/api/control/${b.dataset.ctl}${b.dataset.id ? "/" + b.dataset.id : ""}`, JSON.parse(b.dataset.body || "{}"));
          toast(`${b.textContent}: sent`);
        } catch (err) { toast(`${b.textContent}: ${err.message.slice(0, 160)}`, true); }
        b.disabled = false; return;
      }
      if (e.target.id === "e-start") {
        const id = $("e-scenario").value;
        try { await postJSON(`/api/control/energy.scenario/${id}`, {}); toast(`Energy scenario ${id} started`); }
        catch (err) { toast(err.message.slice(0, 160), true); }
      }
    });
  }

  // ------------------------------------------------------------------ agents
  async function loadAgents(force) {
    if (S.agents && !force) return S.agents;
    try { S.agents = await getJSON("/api/agents"); } catch (e) { S.agents = S.agents || {}; }
    return S.agents;
  }
  function mountAgents() {
    $("title").textContent = "Agents";
    $("content").innerHTML = `
      <section class="situation"><span class="bar"></span><div><p>Three agents, ${"<span id='tool-count'>…</span>"} tools. Each keeps its own safety rules: no tool here can bypass a Guardian.</p></div></section>
      <div class="agent-cards" id="agent-cards"></div>
      <div id="runner"></div>`;
    loadAgents(true).then(a => {
      const n = Object.values(a).reduce((s, x) => s + (x.tools || []).length, 0);
      $("tool-count").textContent = n;
      $("agent-cards").innerHTML = Object.entries(a).map(([k, x]) => {
        const kinds = { read: 0, what_if: 0, act: 0 };
        (x.tools || []).forEach(t => kinds[t.kind] = (kinds[t.kind] || 0) + 1);
        return `<section class="panel agent-card"><div class="panel-head"><h2>${esc(AGENT_NAMES[k])}</h2><span class="spacer"></span>${pill(x.online ? "ok" : "offline")}</div>
          <div class="panel-body"><p>${esc(x.protocol)}</p><p>${kinds.read} read, ${kinds.what_if} what-if, ${kinds.act} act tools</p>${x.error ? `<p class="muted">${esc(x.error.slice(0, 160))}</p>` : ""}</div></section>`;
      }).join("");
      mountRunner($("runner"), S.toolSel.agent || "waste_air", null, true);
    });
  }

  function mountRunner(host, agent, axis, switchable) {
    host.innerHTML = `<div class="agents"><section class="panel"><div class="panel-head"><h2>Tools</h2><span class="spacer"></span>
      ${switchable ? `<select class="input" id="r-agent" aria-label="Agent">${Object.entries(AGENT_NAMES).map(([k, n]) => `<option value="${k}" ${k === agent ? "selected" : ""}>${esc(n)}</option>`).join("")}</select>` : ""}</div>
      <div class="tool-list" id="r-tools"><div class="feed-empty">Loading tools…</div></div></section>
      <section class="panel" id="r-detail"><div class="feed-empty">Pick a tool to see what it does and run it.</div></section></div>`;
    S.toolSel.agent = agent;
    if (switchable) $("r-agent").addEventListener("change", e => { S.toolSel = { agent: e.target.value }; mountRunner(host, e.target.value, null, true); });
    loadAgents().then(a => {
      const x = a[agent] || { tools: [] };
      const groups = { read: "Read", what_if: "What-if", act: "Act on the city" };
      const relevant = t => !axis || agent !== "waste_air" ? true : axis === "air" ? !/bin|truck|route/.test(t.name) : !/air/.test(t.name);
      const tools = (x.tools || []).filter(relevant);
      $("r-tools").innerHTML = !tools.length ? `<div class="feed-empty">${esc(x.error ? "Agent offline: " + x.error.slice(0, 120) : "No tool.")}</div>` :
        Object.entries(groups).map(([k, label]) => {
          const list = tools.filter(t => t.kind === k); if (!list.length) return "";
          return `<div class="tool-group"><span>${label}</span><span>${list.length}</span></div>` + list.map(t =>
            `<button class="tool" type="button" role="option" data-tool="${esc(t.name)}" aria-selected="${S.toolSel.tool === t.name}"><span class="tn"><span>${esc(t.title || t.name)}</span></span></button>`).join("");
        }).join("");
      $("r-tools").onclick = e => {
        const b = e.target.closest("button[data-tool]"); if (!b) return;
        $("r-tools").querySelectorAll(".tool").forEach(x => x.setAttribute("aria-selected", x === b));
        S.toolSel.tool = b.dataset.tool;
        showTool(agent, tools.find(t => t.name === b.dataset.tool));
      };
      const pre = tools.find(t => t.name === S.toolSel.tool) || tools[0];
      if (pre) { S.toolSel.tool = pre.name; $("r-tools").querySelector(`[data-tool="${CSS.escape(pre.name)}"]`)?.setAttribute("aria-selected", "true"); showTool(agent, pre); }
    });
  }

  function fieldFor(name, spec, required) {
    const id = "f-" + name;
    const label = `<label for="${id}">${esc(spec.title || name)}${required ? "" : ' <span class="muted">(optional)</span>'}</label>`;
    const hint = spec.description ? `<span class="hint">${esc(spec.description)}</span>` : "";
    let input;
    const type = Array.isArray(spec.type) ? spec.type[0] : spec.type;
    if (spec.enum) input = `<select class="input" id="${id}" data-name="${esc(name)}" data-type="enum">${required ? "" : `<option value="">—</option>`}${spec.enum.map(v => `<option>${esc(v)}</option>`).join("")}</select>`;
    else if (type === "boolean") input = `<select class="input" id="${id}" data-name="${esc(name)}" data-type="boolean">${required ? "" : `<option value="">—</option>`}<option value="true">true</option><option value="false">false</option></select>`;
    else if (type === "number" || type === "integer") input = `<input class="input" id="${id}" type="number" step="${type === "integer" ? 1 : "any"}" data-name="${esc(name)}" data-type="${type}" ${spec.minimum != null ? `min="${spec.minimum}"` : ""} ${spec.maximum != null ? `max="${spec.maximum}"` : ""} placeholder="${spec.default != null ? esc(spec.default) : ""}">`;
    else if (type === "array" || type === "object" || spec.anyOf) input = `<textarea class="input" id="${id}" data-name="${esc(name)}" data-type="json" placeholder="${type === "array" ? "[ ]" : "{ }"}"></textarea>`;
    else input = `<input class="input" id="${id}" type="text" data-name="${esc(name)}" data-type="string" placeholder="${spec.default != null ? esc(spec.default) : ""}">`;
    return `<div class="field">${label}${input}${hint}</div>`;
  }

  function showTool(agent, t) {
    const el = $("r-detail"); if (!t) return;
    const props = t.schema?.properties || {}, req = new Set(t.schema?.required || []);
    const fields = Object.entries(props).map(([n, s]) => fieldFor(n, s, req.has(n))).join("");
    const kindWord = { read: "Reads only", what_if: "Computes only, changes nothing", act: "Changes the city" }[t.kind] || t.kind;
    el.innerHTML = `<div class="panel-head"><h2>${esc(t.title || t.name)}</h2><span class="spacer"></span><span class="kind ${esc(t.kind)}">${esc(kindWord)}</span></div>
      <div class="panel-body"><div class="form">
        <p style="margin:0;color:var(--text-2)">${esc(t.description)}</p>
        <code class="muted" style="font-size:12px">${esc(AGENT_NAMES[agent])} agent, tool ${esc(t.name)}</code>
        ${fields || `<p class="muted" style="margin:0">This tool takes no argument.</p>`}
        <div><button class="btn ${t.kind === "act" ? "danger" : "primary"}" type="button" id="r-run">${t.kind === "act" ? "Run, this changes the city" : "Run"}</button> <span class="muted" id="r-status"></span></div>
        <div id="r-out"></div></div></div>`;
    let armed = false;
    $("r-run").addEventListener("click", async () => {
      if (t.kind === "act" && !armed) { armed = true; $("r-run").textContent = "Confirm: run it now"; setTimeout(() => { armed = false; const b = $("r-run"); if (b) b.textContent = "Run, this changes the city"; }, 5000); return; }
      armed = false;
      const args = {};
      try {
        el.querySelectorAll("[data-name]").forEach(inp => {
          const v = inp.value.trim(); if (v === "") return;
          const ty = inp.dataset.type;
          args[inp.dataset.name] = ty === "integer" ? parseInt(v, 10) : ty === "number" ? parseFloat(v) : ty === "boolean" ? v === "true" : ty === "json" ? JSON.parse(v) : v;
        });
      } catch (err) { $("r-status").textContent = "An argument is not valid JSON."; return; }
      $("r-run").disabled = true; $("r-status").textContent = "Running…";
      try {
        const res = await postJSON(`/api/agents/${agent}/call`, { tool: t.name, arguments: args });
        $("r-status").textContent = `${res.ok ? "Done" : "Refused"} in ${fmt(res.ms)} ms`;
        const summary = res.summary || res.result?.summary;
        $("r-out").innerHTML = (summary ? `<div class="summary-line">${esc(summary)}</div>` : "") +
          (res.ok ? "" : `<div class="summary-line" style="border-color:var(--critical)">${esc(res.error?.code || "error")}: ${esc(res.error?.message || "")}</div>`) +
          `<pre class="result">${esc(JSON.stringify(res.ok ? res.result : res.error, null, 2))}</pre>`;
      } catch (err) { $("r-status").textContent = err.message.slice(0, 200); }
      const b = $("r-run"); if (b) { b.disabled = false; b.textContent = t.kind === "act" ? "Run, this changes the city" : "Run"; }
    });
  }

  // ------------------------------------------------------------------ decision log
  function mountLog() {
    $("title").textContent = "Decision log";
    $("content").innerHTML = `<section class="panel">
      <div class="panel-head" style="flex-wrap:wrap"><h2>Every decision and alert</h2><span class="spacer"></span>
        <div class="chips" id="log-chips"></div>
        <select class="input" id="log-sev" aria-label="Minimum severity"><option value="info">All severities</option><option value="watch">Watch and above</option><option value="alert">Attention and above</option><option value="critical">Critical only</option></select>
        <input class="input" id="log-q" type="search" placeholder="Search" aria-label="Search the log"></div>
      <ul class="feed tall" id="log-feed"></ul></section>`;
    $("log-chips").innerHTML = Object.entries(DOMAINS).map(([k, d]) => `<button class="chip" type="button" data-axis="${k}" aria-pressed="${S.feedFilter.has(k)}"><i style="background:${d.hue}"></i>${d.name}</button>`).join("");
    $("log-chips").addEventListener("click", e => { const b = e.target.closest("button[data-axis]"); if (!b) return; const k = b.dataset.axis; S.feedFilter.has(k) ? S.feedFilter.delete(k) : S.feedFilter.add(k); b.setAttribute("aria-pressed", S.feedFilter.has(k)); updateLog(); });
    $("log-sev").value = S.logSeverity; $("log-q").value = S.logSearch;
    $("log-sev").addEventListener("change", e => { S.logSeverity = e.target.value; updateLog(); });
    $("log-q").addEventListener("input", e => { S.logSearch = e.target.value; updateLog(); });
  }
  function updateLog() { if ($("log-feed")) $("log-feed").innerHTML = feedList(filteredFeed(S.feedFilter, S.logSeverity, S.logSearch).slice(-1000), "Nothing matches these filters."); }

  // ------------------------------------------------------------------ City Brain orchestrator: top bar, source badge, Twin city map, timeline
  // The map is drawn in Twin coordinates (metres, x east, z south; 16 zones A-P of 190 m, 95 m blocks), the same
  // geometry and look as the Twin's own dashboard map (twin/tools/dashboard/web/views.js), with the emergency
  // dashboard's tools added: zoom and pan, layers, an inspector, and event -> action arrows.
  const CB_AXES = {
    emergency: { name: "Emergency", hue: "var(--emergency)" },
    transportation: { name: "Transportation", hue: "var(--transport)" },
    pollution: { name: "Pollution", hue: "var(--air)" },
    energy: { name: "Energy", hue: "var(--energy)" },
  };
  const FEED_EXTRA = { emergency: { name: "Emergency", hue: "var(--emergency)" } };
  const FAM_AXIS = {
    seismic: "emergency", flood: "emergency", weather: "emergency", incident: "emergency", water_valve: "emergency",
    traffic: "transportation", parking: "transportation", signal: "transportation",
    air: "pollution", waste: "pollution", noise: "pollution",
    grid: "energy", utility: "energy", power_switch: "energy", service_zone: "energy", power_meter: "energy",
    water_meter: "energy", reservoir: "energy",
  };
  const CB_STATUS = { active: "Active", contained: "Contained", blocked: "Blocked", resolved: "Resolved" };
  const ZONE_IDS = "ABCDEFGHIJKLMNOP".split("");
  S.cb = { view: null, map: null, tl: [], tlLast: 0, scen: null, sel: null, trace: null,
           layers: { traffic: true, devices: true, events: true, actions: true, links: true, incidents: true, situations: true },
           vb: { x: -400, y: -400, w: 800 }, items: {} };

  function cbSrcTag(src) { return src === "mock" ? '<span class="cb-src mock">MOCK</span>' : src === "live" ? '<span class="cb-src live">LIVE</span>' : ""; }

  // one Stop button for every kind of scenario (Brain Test Book, control-center, transport Test Book, energy)
  const RUN_KIND = { brain: "Brain", control_center: "Scenario", transport: "Transport", energy: "Energy" };
  function renderStop() {
    const btn = $("stopall"), info = $("runinfo"); if (!btn || btn.dataset.busy) return;
    const run = S.running || [];
    btn.disabled = !run.length; btn.classList.toggle("on", run.length > 0);
    btn.title = run.length ? "Stop every running scenario and put the city back" : "No scenario is running";
    info.innerHTML = run.length ? "<b>Running</b> " + run.map(r => esc(`${RUN_KIND[r.kind] || r.kind} ${r.id}${r.t != null ? ` · ${Math.round(r.t)} s` : ""}`)).join(", ") : "";
  }
  document.addEventListener("click", async e => {
    const btn = e.target.closest("#stopall"); if (!btn || btn.disabled) return;
    btn.disabled = true; btn.dataset.busy = "1"; btn.textContent = "Stopping…";
    try {
      const r = await postJSON("/api/scenarios/stop", {});
      if (!r.stopped.length && !r.errors.length) toast("No scenario was running");
      else toast((r.stopped.length ? "Stopped: " + r.stopped.join("; ") : "") + (r.errors.length ? ` Errors: ${r.errors.join("; ")}` : ""), !r.ok);
      S.running = []; S.cb.scen = null;
    } catch (err) { toast(`Stop failed: ${err.message.slice(0, 160)}`, true); }
    finally { delete btn.dataset.busy; btn.innerHTML = "&#9632; Stop scenario"; renderStop(); }
  });

  function renderBadge() {
    const el = $("srcbadge"); if (!el) return;
    const v = S.cb.view, st = v && v.status, online = v && v.link && v.link.online;
    let cls = "off", text = "Brain offline", tip = "The City Brain orchestrator (brain/run_brain.py) is not reachable.";
    if (online && st && st.source) {
      const up = st.twin && st.twin.connected;
      if (st.source === "live") { cls = up ? "live" : "down"; text = up ? "LIVE · Twin" : "LIVE · Twin unreachable"; }
      else { cls = "mock"; text = "MOCK data"; }
      tip = `Source: ${st.source} (configured ${st.configured_source}). Twin ${st.twin?.endpoint || ""}: ${up ? "connected" : (st.twin?.last_error || "not connected")}. `
        + `Guardian: ${st.guardian?.mode} ${st.guardian?.available ? "available" : "unavailable"}.` + (st.fallback_hint ? ` ${st.fallback_hint}` : "");
    }
    el.className = "srcbadge " + cls; el.textContent = text; el.title = tip;
  }

  function renderTop() {
    const host = $("cb-top"); if (!host) return;
    const top = S.cb.view?.top || {};
    const html = Object.entries(CB_AXES).map(([k, a]) => {
      const items = top[k] || [];
      const chips = items.length ? items.map(s => `<button type="button" class="cb-chip ${esc(s.status)} ${s.source === "mock" ? "is-mock" : ""}" data-sit="${esc(s.id)}"
          title="${esc(s.reason)}"><span class="dot ${s.severity === "critical" ? "critical" : s.severity === "alert" ? "alert" : s.severity === "watch" ? "watch" : "ok"}"></span>
          <span class="cb-chip-name">${esc(s.title)}</span><span class="cb-chip-meta">${esc(CB_STATUS[s.status] || s.status)} · ${esc(s.severity)}${s.source === "mock" ? " · mock" : ""}</span></button>`).join("")
        : `<span class="cb-none">Nothing to report</span>`;
      return `<div class="cb-col"><div class="cb-col-head"><span class="hue" style="background:${a.hue}"></span>${a.name}</div>${chips}</div>`;
    }).join("");
    if (host._html !== html) { host.innerHTML = html; host._html = html; }
  }
  document.addEventListener("click", e => {
    const b = e.target.closest && e.target.closest("[data-sit]"); if (!b) return;
    const id = b.dataset.sit, all = Object.values(S.cb.view?.top || {}).flat(), s = all.find(x => x.id === id);
    if (!s) return;
    S.cb.trace = s.trace_id; S.cb.sel = { kind: "situation", data: s };
    if (s.location) S.cb.vb = { x: s.location.x - 150, y: s.location.z - 150, w: 300 };
    if (S.route.view !== "city") location.hash = "#/city"; else { cbApplyVB(); cbDraw(); cbInspector(); cbTimeline(); }
  });

  // -- map
  const svgEl = (tag, attrs) => `<${tag} ${Object.entries(attrs).map(([k, v]) => `${k}="${esc(v)}"`).join(" ")}/>`;
  function cbBase() {
    let g = `<defs><marker id="cb-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="context-stroke"/></marker>
      <pattern id="cb-hatch" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="3" height="8" fill="rgba(248,113,113,.35)"/></pattern></defs>`;
    g += svgEl("rect", { x: -400, y: -400, width: 800, height: 800, class: "cb-ground" });
    ZONE_IDS.forEach((z, i) => {
      const col = i % 4, row = Math.floor(i / 4), x = -380 + col * 190, y = -380 + row * 190;
      g += svgEl("rect", { x, y, width: 190, height: 190, class: "cb-zone " + ((col + row) % 2 ? "a" : "b"), id: "cbz-" + z });
    });
    for (let r = 0; r < 8; r++) for (let c = 0; c < 8; c++)
      g += svgEl("rect", { x: -380 + c * 95 + 9, y: -380 + r * 95 + 9, width: 77, height: 77, rx: 3, class: "cb-block" });
    for (let i = 0; i <= 8; i++) {
      const v = -380 + i * 95;
      g += svgEl("line", { x1: v, y1: -380, x2: v, y2: 380, class: "cb-road" }) + svgEl("line", { x1: -380, y1: v, x2: 380, y2: v, class: "cb-road" });
    }
    for (let i = 0; i <= 8; i++) {
      const v = -380 + i * 95;
      g += svgEl("line", { x1: v, y1: -380, x2: v, y2: 380, class: "cb-lane" }) + svgEl("line", { x1: -380, y1: v, x2: 380, y2: v, class: "cb-lane" });
    }
    ZONE_IDS.forEach((z, i) => { const col = i % 4, row = Math.floor(i / 4); g += `<text class="cb-zl" x="${-380 + col * 190 + 8}" y="${-380 + row * 190 + 40}">${z}</text>`; });
    return `<g id="cb-base">${g}</g><g id="cb-dyn"></g>`;
  }
  function cbApplyVB() { const m = $("cb-map"); if (m) { const v = S.cb.vb; m.setAttribute("viewBox", `${v.x} ${v.y} ${v.w} ${v.w}`); } }
  function cbZoom(f, cx, cy) {
    const v = S.cb.vb, w = Math.min(800, Math.max(120, v.w * f));
    v.x = cx - (cx - v.x) * (w / v.w); v.y = cy - (cy - v.y) * (w / v.w); v.w = w;
    v.x = Math.min(Math.max(v.x, -400), 400 - w); v.y = Math.min(Math.max(v.y, -400), 400 - w); cbApplyVB();
  }
  function cbSetupZoom() {
    const m = $("cb-map"); if (!m) return;
    const pt = ev => { const r = m.getBoundingClientRect(), v = S.cb.vb; return { x: v.x + (ev.clientX - r.left) / r.width * v.w, y: v.y + (ev.clientY - r.top) / r.height * v.w }; };
    m.addEventListener("wheel", ev => { ev.preventDefault(); cbManual(); const p = pt(ev); cbZoom(ev.deltaY > 0 ? 1.15 : 1 / 1.15, p.x, p.y); }, { passive: false });
    let drag = null;
    m.addEventListener("pointerdown", ev => { drag = { x: ev.clientX, y: ev.clientY, vx: S.cb.vb.x, vy: S.cb.vb.y, moved: false }; });
    window.addEventListener("pointermove", ev => {
      if (!drag) return; const r = m.getBoundingClientRect(), dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
      if (!drag.moved && Math.hypot(dx, dy) < 4) return; if (!drag.moved) cbManual(); drag.moved = true;
      S.cb.vb.x = drag.vx - dx / r.width * S.cb.vb.w; S.cb.vb.y = drag.vy - dy / r.height * S.cb.vb.w; cbZoom(1, 0, 0);
    });
    window.addEventListener("pointerup", () => { if (drag && drag.moved) S.cb.justPanned = Date.now(); drag = null; });
    const mid = () => ({ x: S.cb.vb.x + S.cb.vb.w / 2, y: S.cb.vb.y + S.cb.vb.w / 2 });
    $("cb-zin").onclick = () => { cbManual(); const c = mid(); cbZoom(1 / 1.4, c.x, c.y); };
    $("cb-zout").onclick = () => { cbManual(); const c = mid(); cbZoom(1.4, c.x, c.y); };
    $("cb-zfit").onclick = () => { cbManual(); S.cb.vb = { x: -400, y: -400, w: 800 }; cbApplyVB(); };
    m.addEventListener("click", e => {
      if (Date.now() - (S.cb.justPanned || 0) < 200) return;
      const t = e.target.closest("[data-k]"); if (!t) return;
      S.cb.sel = S.cb.items[t.dataset.k]; S.cb.trace = S.cb.sel?.data?.trace_id || null; cbDraw(); cbInspector(); cbTimeline();
    });
  }
  function cbDraw() {
    const dyn = $("cb-dyn"); if (!dyn) return;
    const m = S.cb.map || {}, L = S.cb.layers, items = {}; let g = "", n = 0;
    const key = (kind, data) => { const k = "i" + (n++); items[k] = { kind, data }; return k; };
    const sel = S.cb.trace;
    const quar = m.quarantined || {};
    // A clean map: a zone is tinted only for a situation that needs attention (alert, critical or blocked), the
    // calm devices are drawn only when zoomed in, and only the recent events and actions that mattered are shown
    // (the latest per device / actuator, the last few minutes), instead of every reading of the last 200.
    const zoomedIn = S.cb.vb.w <= 420;
    if (L.situations) for (const s of m.situations || []) {
      if (!s.zone || s.status === "resolved") continue;
      if (s.status !== "blocked" && !["alert", "critical"].includes(s.severity)) continue;
      const i = ZONE_IDS.indexOf(s.zone), col = i % 4, row = Math.floor(i / 4), hue = (CB_AXES[s.axis] || {}).hue || "var(--muted)";
      g += `<rect class="cb-sit ${s.status === "blocked" ? "blocked" : ""}" x="${-380 + col * 190}" y="${-380 + row * 190}" width="190" height="190" style="fill:${s.status === "blocked" ? "url(#cb-hatch)" : hue}"><title>${esc(s.title)}</title></rect>`;
    }
    if (L.devices || L.traffic) g += twLayer(L.devices ? m : Object.assign({}, m, { devices: [] }), { families: null, vehicles: L.traffic, key, calm: zoomedIn, keep: new Set((m.spotlight || {}).devices || []) });
    if (L.devices) for (const d of m.devices || []) {
      const l = d.location, q = quar[d.id]; if (!l || !q) continue;
      g += `<g class="cb-quar"><circle cx="${l.x}" cy="${l.z}" r="9"/><text x="${l.x + 10}" y="${l.z - 8}">quarantined</text></g>`;
    }
    if (L.incidents) for (const i of (m.city || {}).incidents || []) {
      const l = i.location || {}; if (l.x == null || l.z == null) continue;
      g += `<circle class="cb-inc" data-k="${key("incident", i)}" cx="${l.x}" cy="${l.z}" r="11"><title>${esc(i.id)} ${esc(i.kind)}</title></circle>`;
    }
    if (L.incidents) for (const u of (m.city || {}).units || []) {
      const l = u.location; if (!l) continue;
      g += `<circle class="cb-unit" data-k="${key("unit", u)}" cx="${l.x}" cy="${l.z}" r="5"><title>${esc(u.unit)} ${esc(u.kind)} ${esc(u.status)}</title></circle>`;
    }
    const tsOf = x => Date.parse(x && x.ts) || 0;
    const acts = (m.actions || []).filter(it => it.action && it.action.location);
    const actNewest = Math.max(0, ...acts.map(it => tsOf(it.action)));
    const actLatest = {};
    for (const it of acts) {
      const a = it.action, on = sel && a.trace_id === sel;
      if (!on && actNewest - tsOf(a) > 180000) continue;          // older than 3 minutes
      actLatest[on ? "sel:" + a.action_id : a.target.actuator_id] = it;
    }
    const shownActs = Object.values(actLatest), actedTraces = new Set(shownActs.map(it => it.action.trace_id));
    const evs = (m.events || []).filter(it => it.event && it.event.location);
    const evNewest = Math.max(0, ...evs.map(it => tsOf(it.event)));
    const evLatest = {};
    for (const it of evs) {
      const e = it.event, on = sel && e.trace_id === sel;
      if (!on && !it.blocked && !actedTraces.has(e.trace_id)) continue;   // only readings the Brain acted on (or refused)
      if (!on && evNewest - tsOf(e) > 180000) continue;
      evLatest[on ? "sel:" + e.trace_id : e.origin.device_id] = it;
    }
    if (L.events) for (const it of Object.values(evLatest)) {
      const e = it.event, l = e.location; if (!l) continue;
      const ax = FAM_AXIS[e.origin.family] || "emergency", hue = CB_AXES[ax].hue, on = sel && e.trace_id === sel;
      g += `<circle class="cb-ev ${e.source === "mock" ? "mock" : ""} ${it.blocked ? "blocked" : ""} ${on ? "on" : ""}" data-k="${key("event", Object.assign({ blocked: it.blocked }, e))}"
        cx="${l.x}" cy="${l.z}" r="${on ? 9 : 7}" style="stroke:${hue}"><title>${esc(e.origin.device_id)} ${it.blocked ? "(ignored)" : ""}</title></circle>`;
      if (it.blocked) g += `<path class="cb-x" d="M${l.x - 5} ${l.z - 5} L${l.x + 5} ${l.z + 5} M${l.x + 5} ${l.z - 5} L${l.x - 5} ${l.z + 5}"/>`;
    }
    if (L.actions) for (const it of shownActs) {
      const a = it.action, to = a.location, from = a.event_location; if (!to) continue;
      const hue = (CB_AXES[a.agent] || {}).hue, on = sel && a.trace_id === sel, mock = a.source === "mock", bad = a.feedback && !a.feedback.ok;
      if (L.links && from && (Math.abs(from.x - to.x) > 2 || Math.abs(from.z - to.z) > 2))
        g += `<line class="cb-link ${mock ? "mock" : ""} ${on ? "on" : ""}" x1="${from.x}" y1="${from.z}" x2="${to.x}" y2="${to.z}" style="stroke:${hue}" marker-end="url(#cb-arrow)"/>`;
      const s = on ? 9 : 7, k = key("action", a);
      g += `<rect class="cb-act ${mock ? "mock" : ""} ${on ? "on" : ""} ${bad ? "bad" : ""}" data-k="${k}" x="${to.x - s / 2}" y="${to.z - s / 2}" width="${s}" height="${s}"
        transform="rotate(45 ${to.x} ${to.z})" style="fill:${hue}"><title>${esc(a.agent)}: ${esc(a.target.actuator_id)} ${esc(a.target.command)}</title></rect>`;
      if (!from || (Math.abs(from.x - to.x) <= 2 && Math.abs(from.z - to.z) <= 2))
        g += `<circle class="cb-act-ring ${mock ? "mock" : ""}" cx="${to.x}" cy="${to.z}" r="${s + 5}" style="stroke:${hue}"/>`;
    }
    S.cb.items = items;
    dyn.innerHTML = g + cbSpotLayer(m.spotlight);
  }

  function cbInspector() {
    const host = $("cb-insp"); if (!host) return;
    const it = S.cb.sel;
    if (!it) { host.innerHTML = `<div class="feed-empty">Click a marker on the map (event, action, device, incident) or an item of the top bar.</div>`; return; }
    const d = it.data || {};
    const row = (k, v) => v == null || v === "" ? "" : `<dt>${esc(k)}</dt><dd>${v}</dd>`;
    let head = "", rows = "";
    if (it.kind === "action") {
      head = `Action · ${esc(d.target?.actuator_id)} ${esc(d.target?.command)}`;
      rows = row("Agent", esc(d.agent)) + row("Action type", esc(d.action_type)) + row("Reason", esc(d.reason)) + row("Severity", esc(d.severity))
        + row("Twin answer", d.feedback ? (d.feedback.ok ? `applied: ${esc(d.feedback.applied)}` : `refused: ${esc(d.feedback.error?.message)}`) : "")
        + row("Trace", `<code>${esc(d.trace_id)}</code>`) + row("Source", cbSrcTag(d.source)) + row("Time", esc(hms(d.ts)));
    } else if (it.kind === "event") {
      head = `${d.blocked ? "Ignored reading" : d.type === "incident" ? "Incident" : "Reading"} · ${esc(d.origin?.device_id)}`;
      rows = row("Agent", esc(FAM_AXIS[d.origin?.family] || "")) + row("Zone", esc(d.location?.zone))
        + row("Values", esc(Object.entries(d.readings || {}).map(([m, r]) => `${m} ${r.value} ${r.unit}`).join(", ")))
        + row("Reason", d.blocked ? "The Guardian did not trust this data: no decision was taken (see the timeline)." : "")
        + row("Trace", `<code>${esc(d.trace_id)}</code>`) + row("Source", cbSrcTag(d.source)) + row("Time", esc(hms(d.ts)));
    } else if (it.kind === "situation") {
      head = `Situation · ${esc(d.title)}`;
      rows = row("Agent", esc(d.axis)) + row("Status", esc(CB_STATUS[d.status] || d.status)) + row("Severity", esc(d.severity)) + row("Reason", esc(d.reason))
        + row("Actions", esc(d.actions)) + row("Trace", `<code>${esc(d.trace_id)}</code>`) + row("Source", cbSrcTag(d.source)) + row("Since", esc(hms(d.started)));
    } else {
      head = `${it.kind[0].toUpperCase() + it.kind.slice(1)} · ${esc(d.id || d.unit || "")}`;
      rows = row("Family", esc(d.family || d.class || d.kind)) + row("Zone", esc(d.zone))
        + row("Now", esc(Object.entries(d.readings || {}).map(([k, v]) => `${k} ${twFmt(k, v)}`).join(", ")))
        + row("Speed", d.speed_mps != null ? `${fmt(d.speed_mps * 3.6)} km/h${d.stopped ? " (stopped)" : ""}` : "")
        + row("Unit", esc(d.unit || "")) + row("State", esc(d.state ? JSON.stringify(d.state) : d.phase || d.status || ""));
    }
    host.innerHTML = `<h3 class="cb-insp-h">${head}</h3><dl class="cb-kv">${rows}</dl><details open><summary>JSON</summary><pre class="cb-json">${esc(JSON.stringify(d, null, 2))}</pre></details>`;
  }

  // -- live timeline: one card per decision (reading -> Guardian verdict -> Brain decision -> command -> Twin answer),
  // written for a human: a headline, the reason, each command with the Twin's answer on the same line
  const TL_KINDS = { event: "Reading", verdict: "Guardian", decision: "Decision", a2a: "A2A", conflict: "Conflict", ignored: "Ignored", action: "Action", feedback: "Twin" };
  const TL_BANDS = ["North", "NorthCentral", "SouthCentral", "South"];
  // device codes in plain words (display only: the log keeps the exact ids)
  function tlHuman(text) {
    return String(text ?? "")
      .replace(/ZONE-Zone_(North|NorthCentral|SouthCentral|South)_([1-4])/g, (m, b, n) => `zone ${ZONE_IDS[TL_BANDS.indexOf(b) * 4 + Number(n) - 1]} power`)
      .replace(/SIG-Junction_r(\d)_c(\d)/g, "signal r$1c$2").replace(/Junction_r(\d)_c(\d)/g, "junction r$1c$2")
      .replace(/\bns_green\b/g, "north-south green").replace(/\bew_green\b/g, "east-west green")
      .replace(/\breset_cycle\b/g, "back to normal cycle").replace(/\ball_red\b/g, "all red")
      .replace(/\bcut_power\b/g, "cut power").replace(/\brestore_power\b/g, "restore power")
      .replace(/\bcut_water\b/g, "cut water").replace(/\brestore_water\b/g, "restore water");
  }
  function tlSplit(summary) {                // "[axis] Title: reason" -> {title, reason}
    const s = String(summary || "").replace(/^\[[a-z]+\]\s*/, "");
    const k = s.indexOf(": ");
    return k > 0 ? { title: s.slice(0, k), reason: s.slice(k + 2) } : { title: s, reason: "" };
  }
  function tlCard(t, rs) {
    const dec = rs.find(r => r.kind === "decision");
    const axis = (dec && dec.axis) || (rs.find(r => r.axis) || {}).axis || "";
    const ax = CB_AXES[axis] || { name: axis || "City Brain", hue: "var(--muted)" };
    const acts = rs.filter(r => r.kind === "action"), feeds = rs.filter(r => r.kind === "feedback");
    const refused = rs.filter(r => r.kind === "ignored" || r.kind === "conflict");
    const failed = feeds.filter(f => /refused/i.test(f.summary));
    const first = acts[0] || rs.find(r => r.kind !== "event" && r.kind !== "verdict") || rs[0];
    const head = dec ? tlSplit(dec.summary)
      : first.kind === "a2a" ? { title: String(first.summary).replace(/^A2A\s*/, "").split(/ \(| because /)[0].replace(/_/g, " "), reason: "" }
      : tlSplit(first.summary);
    const noAction = dec && /no action needed|situation closed/.test(dec.summary) && !acts.length;
    let badge = noAction ? ["No action needed", "ok"] : acts.length ? [`${acts.length} command${acts.length > 1 ? "s" : ""} sent`, failed.length ? "alert" : "ok"]
      : refused.length ? ["Not sent", "alert"] : ["Noted", "muted"];
    if (failed.length) badge = [`${acts.length - failed.length}/${acts.length} done, ${failed.length} refused`, "alert"];
    const t0 = new Date(rs[0].ts).getTime();
    const rel = r => { const d = Math.round((new Date(r.ts).getTime() - t0) / 1000); return d > 0 ? `+${d} s` : ""; };
    const used = new Set();
    const lines = [];
    const ev = rs.find(r => r.kind === "event");
    if (ev) lines.push(["k-event", "Data", tlHuman(ev.summary.replace(/^Reading /, "")), rel(ev)]);
    for (const r of rs.filter(r => r.kind === "verdict")) {
      const bad = /untrusted|quarantined|suspect|no verdict/i.test(r.summary);
      lines.push([bad ? "k-ignored" : "k-verdict", "Guardian", bad ? tlHuman(r.summary.replace(/^Guardian:\s*/, "")) : "data trusted", rel(r)]);
      break;
    }
    if (dec && head.reason) lines.push(["k-decision", "Why", tlHuman(head.reason.replace(/ -> no action needed$/, "").replace(/ -> situation closed$/, " (situation closed)")), rel(dec)]);
    for (const r of rs) {
      if (r.kind === "action") {
        const m = String(r.summary).match(/^\[[a-z]+\]\s*[\w ]+:\s*(.*?)(?: because (.*))?$/);
        const what = m ? m[1] : r.summary, because = m && m[2] ? m[2] : "";
        const f = feeds.find(x => !used.has(x.seq) && x.seq > r.seq && what.split(" ").slice(0, 2).every(w => x.summary.includes(w)));
        if (f) used.add(f.seq);
        const ok = f ? !/refused/i.test(f.summary) : null;
        const answer = ok === null ? '<span class="tl-wait">waiting for the Twin</span>' : ok ? '<span class="tl-ok">✓ done</span>'
          : `<span class="tl-bad">✗ refused: ${esc(tlHuman(f.summary.replace(/^Twin refused [^:]*:\s*/, "")))}</span>`;
        lines.push(["k-action", "Command", `<b class="tl-what">${esc(tlHuman(what))}</b> ${answer}${because && !dec ? `<span class="tl-why">${esc(tlHuman(because))}</span>` : ""}`, rel(r), true]);
      } else if (r.kind === "a2a") {
        if (/ (working|submitted): /.test(r.summary)) continue;         // bookkeeping: accepted, in progress
        lines.push(["k-a2a", / (completed|rejected|failed|canceled): /.test(r.summary) ? "Answer" : "Asks", tlHuman(r.summary.replace(/^A2A\s*/, "")), rel(r)]);
      } else if (r.kind === "conflict") {
        lines.push(["k-conflict", "Conflict", tlHuman(r.summary), rel(r)]);
      } else if (r.kind === "ignored") {
        lines.push(["k-ignored", "Not sent", tlHuman(r.summary.replace(/^\[[a-z]+\]\s*/, "")), rel(r)]);
      } else if (r.kind === "feedback" && !used.has(r.seq) && !acts.length) {
        lines.push(["k-feedback", "Twin", tlHuman(r.summary), rel(r)]);
      }
    }
    const mock = rs.some(r => r.source === "mock");
    return `<li class="cb-tl-trace ${t === S.cb.trace ? "on" : ""} ${mock ? "is-mock" : ""}" data-trace="${esc(t)}" style="--hue:${ax.hue}">
      <div class="cb-tl-head"><span class="tl-axis">${esc(ax.name)}</span><span class="tl-title">${esc(tlHuman(head.title))}</span>
        <span class="tl-badge ${badge[1]}">${esc(badge[0])}</span><span class="spacer"></span>${cbSrcTag(rs[0].source)}<time>${hms(rs[0].ts)}</time></div>
      <ol>${lines.map(([k, label, text, when, raw]) => `<li class="${k}"><b>${label}</b><span class="tl-text">${raw ? text : esc(text)}</span>${when ? `<time>${when}</time>` : ""}</li>`).join("")}</ol>
      <div class="tl-id">decision id <code>${esc(t)}</code> · click to show it on the map</div></li>`;
  }
  function cbTimeline() {
    const host = $("cb-tl"); if (!host) return;
    const filt = $("cb-tl-filter");
    if (filt && !filt._wired) {
      filt._wired = true;
      filt.addEventListener("click", e => { const b = e.target.closest("[data-tlf]"); if (!b) return; S.cb.tlAll = b.dataset.tlf === "all";
        filt.querySelectorAll("[data-tlf]").forEach(x => x.setAttribute("aria-pressed", String((x.dataset.tlf === "all") === !!S.cb.tlAll))); cbTimeline(); });
    }
    const traces = new Map(), loose = [];
    for (const r of S.cb.tl) {
      if (r.trace_id && TL_KINDS[r.kind]) { if (!traces.has(r.trace_id)) traces.set(r.trace_id, []); traces.get(r.trace_id).push(r); }
      else if (r.kind === "system" || r.kind === "error") loose.push(r);
    }
    const important = rs => rs.some(r => ["action", "a2a", "conflict", "ignored"].includes(r.kind) || ["alert", "critical"].includes(r.severity));
    let list = [...traces.entries()].filter(([, rs]) => rs.some(r => r.kind !== "event" && r.kind !== "verdict"))
      .filter(([, rs]) => S.cb.tlAll || important(rs))
      .map(([t, rs]) => ({ t, rs, last: rs[rs.length - 1].seq }));
    list = list.concat(loose.slice(-12).map(r => ({ t: null, rs: [r], last: r.seq }))).sort((a, b) => b.last - a.last).slice(0, 18);
    if (S.cb.trace && traces.has(S.cb.trace) && !list.some(x => x.t === S.cb.trace)) list.unshift({ t: S.cb.trace, rs: traces.get(S.cb.trace) });
    host.innerHTML = list.length ? list.map(({ t, rs }) => {
      if (!t) { const r = rs[0]; return `<li class="cb-tl-sys ${r.source === "mock" ? "is-mock" : ""}"><time>${hms(r.ts)}</time><span class="sev ${esc(r.severity)}"></span>${esc(tlHuman(r.summary))}</li>`; }
      return tlCard(t, rs);
    }).join("") : `<li class="feed-empty">${S.cb.tlAll ? "No decision yet. A calm city produces readings but no decision." : "No command or alert yet. Switch to “Everything” to see the routine checks too."}</li>`;
  }
  document.addEventListener("click", e => {
    const li = e.target.closest && e.target.closest("[data-trace]"); if (!li) return;
    S.cb.trace = li.dataset.trace;
    const a = (S.cb.map?.actions || []).find(x => x.action.trace_id === S.cb.trace);
    if (a) S.cb.sel = { kind: "action", data: a.action };
    cbDraw(); cbInspector(); cbTimeline();
  });

  function cbScen() {
    const host = $("cb-scen"); if (!host) return;
    const sc = (S.cb.scen && S.cb.scen.scenarios) || [], run = S.cb.view?.running;
    const live = S.cb.view?.status?.source === "live";
    const btn = s => {
      const mine = run && run.id === s.id, busy = mine && (run.state === "running" || run.state === "starting");
      const ok = (s.modes || ["mock"]).includes(live ? "live" : "mock");
      const moves = s.focus && s.focus.follow ? `<span class="scen-follow" title="the map and the Twin's camera follow its moving vehicle">${s.focus.follow === "traffic" ? "follows a car" : "follows the units"}</span>` : "";
      return `<button type="button" class="scen-btn ${busy ? "running" : ""}" data-cbscen="${esc(s.id)}" ${ok ? "" : "disabled"} title="${esc(s.description)}">
        <span class="scen-top"><span class="scen-id">${esc(s.id)}</span><span class="scen-title">${esc(s.name)}</span></span>
        <span class="scen-watch">${esc(s.interaction)}${moves}</span>
        <span class="scen-foot"><span>${mine ? esc(run.state === "running" ? `Running, ${run.t} of ${run.duration_s} s` : `Last run: ${(run.result || run.state || "").toUpperCase()} · logs/${run.log}`) : (ok ? `${s.duration_s} s` : "mock source only")}</span>${ok && !busy ? '<span class="scen-go">Run</span>' : ""}</span></button>`;
    };
    const byId = Object.fromEntries(sc.map(s => [s.id, s]));
    const groups = (S.cb.scen && S.cb.scen.groups) || [{ axis: "all", title: "Scenarios", scenarios: sc.map(s => s.id) }];
    const html = sc.length ? groups.map(g => `<div class="cb-book-col"><h3 style="--hue:${(CB_AXES[g.axis] || {}).hue || "var(--muted)"}">${esc(g.title || g.axis)}</h3>
        ${g.scenarios.filter(id => byId[id]).map(id => btn(byId[id])).join("")}</div>`).join("")
      : `<div class="feed-empty">The City Brain is not reachable: start it with <code>python brain/run_brain.py</code>.</div>`;
    if (host._html !== html) { host.innerHTML = html; host._html = html; }
  }
  document.addEventListener("click", async e => {
    const b = e.target.closest && e.target.closest("[data-cbscen]"); if (!b || b.disabled) return;
    try {
      await postJSON(`/api/brain/scenarios/${b.dataset.cbscen}/run`, {});
      toast(`Brain scenario ${b.dataset.cbscen} started: highlighted on the map (log in logs/)`);
      S.cb.followOff = false; S.cb.spotId = null;
      const m = $("cb-map"); if (m) m.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    catch (err) { toast(`Could not start ${b.dataset.cbscen}: ${String(err.message).slice(0, 160)}`, true); }
  });

  function mountCity() {
    $("title").textContent = "City map";
    const names = { traffic: "Vehicles", devices: "Devices needing attention", situations: "Alert zones", events: "Events acted on", actions: "Recent actions", links: "Event → action", incidents: "Incidents and units" };
    $("content").innerHTML = `
      <section class="panel"><div class="panel-head" style="flex-wrap:wrap"><h2>Twin city: what the Brain received and sent</h2><span class="spacer"></span>
        <div class="chips" id="cb-layers">${Object.entries(names).map(([k, n]) => `<button class="chip" type="button" data-layer="${k}" aria-pressed="${S.cb.layers[k]}">${n}</button>`).join("")}</div></div>
        <div class="cb-map-grid">
          <div class="cb-mapwrap"><svg id="cb-map" viewBox="-400 -400 800 800" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Twin city map">${cbBase()}</svg>
            <div class="tw-ov tw-ov-map" id="cb-ov"></div>
            <div class="cb-spot" id="cb-spot" hidden></div>
            <div class="cb-zoom"><button id="cb-zin" type="button" title="Zoom in">+</button><button id="cb-zout" type="button" title="Zoom out">−</button><button id="cb-zfit" type="button" title="Whole city">⤢</button></div></div>
          <aside class="cb-side"><div id="cb-insp"></div></aside>
        </div>
        <div class="cb-legend">${Object.values(CB_AXES).map(a => `<span><i style="background:${a.hue}"></i>${a.name}</span>`).join("")}
          <span><svg width="14" height="14"><circle cx="7" cy="7" r="5" fill="none" stroke="currentColor" stroke-width="2"/></svg>event received</span>
          <span><svg width="14" height="14"><rect x="3" y="3" width="8" height="8" transform="rotate(45 7 7)" fill="currentColor"/></svg>action sent</span>
          <span><svg width="22" height="10"><line x1="1" y1="5" x2="20" y2="5" stroke="currentColor" stroke-width="2"/></svg>event → action</span>
          <span><svg width="22" height="10"><line x1="1" y1="5" x2="20" y2="5" stroke="currentColor" stroke-width="2" stroke-dasharray="4 3"/></svg>mock data</span>
          <span><svg width="14" height="14"><path d="M3 3 L11 11 M11 3 L3 11" stroke="#f87171" stroke-width="2"/></svg>ignored (untrusted)</span>
          <span class="faint">Scroll to zoom (calm sensors appear when zoomed in), drag to pan. Only the last 3 minutes of events and actions are drawn. Coordinates are the Twin's (metres), zones A–P.</span></div></section>
      <section class="panel"><div class="panel-head"><h2>Brain Test Book</h2><span class="spacer"></span><span class="faint">three scenarios per axis · Run highlights it on the map and in the Twin, and follows its moving vehicle · one log per run in logs/</span></div><div class="cb-book" id="cb-scen"></div></section>
      <section class="panel"><div class="panel-head"><h2>Live timeline</h2><span class="faint">each card: what happened, why, the commands sent and the Twin's answer</span><span class="spacer"></span><div class="chips" id="cb-tl-filter"><button class="chip" type="button" data-tlf="important" aria-pressed="true">Actions &amp; alerts</button><button class="chip" type="button" data-tlf="all" aria-pressed="false">Everything</button></div></div><ul class="cb-tl" id="cb-tl"></ul></section>
      <section class="panel"><div class="panel-head"><h2>Agents talking to each other (A2A)</h2><span class="spacer"></span><span class="faint" id="cb-a2a-sum">one axis asks another to continue the scenario; the owner of the actuators decides how</span></div>
        <div class="cb-a2a"><ul class="cb-a2a-list" id="cb-a2a"><li class="feed-empty">No A2A message yet.</li></ul><div class="cb-a2a-cards" id="cb-a2a-cards"></div></div></section>`;
    $("cb-layers").addEventListener("click", e => { const b = e.target.closest("button[data-layer]"); if (!b) return; const k = b.dataset.layer; S.cb.layers[k] = !S.cb.layers[k]; b.setAttribute("aria-pressed", S.cb.layers[k]); cbDraw(); });
    cbSetupZoom(); cbApplyVB(); cbDraw(); cbInspector(); cbTimeline(); cbScen();
  }
  function updateCity() { cbDraw(); cbTimeline(); cbScen(); cbA2A(); if ($("cb-ov")) $("cb-ov").innerHTML = twOverlay(S.cb.map); cbSpot(); }

  // -- spotlight: the running scenario highlighted on the map, the map framing it and following its moving vehicle
  const zoneRect = z => { const i = ZONE_IDS.indexOf(z); return i < 0 ? null : { x: -380 + (i % 4) * 190, y: -380 + Math.floor(i / 4) * 190 }; };
  function cbManual() { if (S.cb.map && S.cb.map.spotlight) { S.cb.followOff = true; cbSpotBanner(S.cb.map.spotlight); } }
  function cbSpotLayer(sp) {
    if (!sp) return "";
    const hue = (CB_AXES[sp.axis] || {}).hue || "var(--focus)";
    let g = `<g class="cb-spotlayer" style="--hue:${hue}">`;
    if (sp.zones.length) for (const z of ZONE_IDS) if (!sp.zones.includes(z)) { const r = zoneRect(z); g += `<rect class="cb-spot-dim" x="${r.x}" y="${r.y}" width="190" height="190"/>`; }
    for (const z of sp.zones) { const r = zoneRect(z); if (r) g += `<rect class="cb-spot-zone" x="${r.x + 2}" y="${r.y + 2}" width="186" height="186" rx="4"><title>${esc(sp.scenario)}: zone ${z}</title></rect>`; }
    for (const i of sp.incidents || []) g += `<circle class="cb-spot-inc" cx="${i.x}" cy="${i.z}" r="16"><title>${esc(i.id)} ${esc(i.kind || "")}</title></circle>`;
    const f = sp.follow;
    if (f && f.x != null) g += `<g class="cb-spot-follow"><circle cx="${f.x}" cy="${f.z}" r="8"/><circle class="ring" cx="${f.x}" cy="${f.z}" r="8"/>
      <text x="${f.x + 11}" y="${f.z - 8}">${esc(f.unit || f.vehicle)}</text></g>`;
    return g + "</g>";
  }
  function cbSpotBanner(sp) {
    const el = $("cb-spot"); if (!el) return;
    if (!sp) { el.hidden = true; el.innerHTML = ""; return; }
    const run = S.cb.view?.running, t = run && run.id === sp.scenario ? `${Math.round(run.t)} / ${run.duration_s} s` : "";
    const f = sp.follow, who = f ? `${esc(f.unit || f.vehicle)}${f.type ? ` (${esc(f.type)}, ${esc((f.status || "").replace("_", " "))})` : ""}` : "";
    el.hidden = false;
    el.style.setProperty("--hue", (CB_AXES[sp.axis] || {}).hue || "var(--focus)");
    el.innerHTML = `<b>▶ ${esc(sp.scenario)}</b> ${esc(sp.name || "")} <span class="faint">${t}</span>
      <span class="cb-spot-row">${sp.zones.length ? `zone${sp.zones.length > 1 ? "s" : ""} ${esc(sp.zones.join(", "))}` : "whole city"}${f ? ` · following <b>${who}</b>` : (sp.devices.length ? ` · watching ${esc(sp.devices.join(", "))}` : "")}</span>
      ${sp.twin && sp.twin.enabled && sp.twin.note ? `<span class="cb-spot-row faint">Twin: ${esc(sp.twin.note)}</span>` : ""}
      ${S.cb.followOff ? `<button type="button" class="btn" id="cb-follow">${f ? "Follow again" : "Frame the scenario"}</button>` : ""}`;
    const b = $("cb-follow"); if (b) b.onclick = () => { S.cb.followOff = false; cbSpotBanner(sp); cbSpotCamera(sp, true); };
  }
  function cbSpotCamera(sp, force) {
    if (!sp) {
      if (S.cb.spotId) { S.cb.spotId = null; cbAnimate({ x: -400, y: -400, w: 800 }); }   // scenario over: back to the whole city
      return;
    }
    if (sp.scenario !== S.cb.spotId) { S.cb.spotId = sp.scenario; S.cb.followOff = false; force = true; }
    if (S.cb.followOff && !force) return;
    const f = sp.follow;
    let x, y, w;
    if (f && f.x != null) { w = 380; x = f.x - w / 2; y = f.z - w / 2; }
    else if (sp.zones.length) {
      const rs = sp.zones.map(zoneRect).filter(Boolean);
      const x0 = Math.min(...rs.map(r => r.x)), y0 = Math.min(...rs.map(r => r.y)), x1 = Math.max(...rs.map(r => r.x + 190)), y1 = Math.max(...rs.map(r => r.y + 190));
      w = Math.min(800, Math.max(x1 - x0, y1 - y0) + 60); x = (x0 + x1) / 2 - w / 2; y = (y0 + y1) / 2 - w / 2;
    } else return;
    x = Math.min(Math.max(x, -400), 400 - w); y = Math.min(Math.max(y, -400), 400 - w);
    cbAnimate({ x, y, w });
  }
  function cbAnimate(target) {
    S.cb.vbTarget = target;
    if (S.cb.anim) return;
    const step = () => {
      const v = S.cb.vb, t = S.cb.vbTarget;
      if (!t || !$("cb-map")) { S.cb.anim = null; return; }
      const k = 0.14;
      v.x += (t.x - v.x) * k; v.y += (t.y - v.y) * k; v.w += (t.w - v.w) * k;
      cbApplyVB();
      if (Math.abs(t.x - v.x) + Math.abs(t.y - v.y) + Math.abs(t.w - v.w) < 0.6) { Object.assign(v, t); cbApplyVB(); S.cb.anim = null; return; }
      S.cb.anim = requestAnimationFrame(step);
    };
    S.cb.anim = requestAnimationFrame(step);
  }
  function cbSpot() {
    const sp = (S.cb.map && S.cb.map.spotlight) || null;
    cbSpotBanner(sp);
    cbSpotCamera(sp, false);
  }
  const A2A_STATE = { submitted: "watch", working: "info", completed: "ok", rejected: "alert", failed: "alert", canceled: "muted" };
  function cbA2A() {
    const host = $("cb-a2a"), cards = $("cb-a2a-cards"), v = S.cb.a2a; if (!host || !v) return;
    const st = v.stats || {};
    $("cb-a2a-sum").textContent = `${fmt(st.sent || 0)} sent · ${fmt(v.open || 0)} open · ${fmt(st.completed || 0)} completed · ${fmt((st.rejected || 0) + (st.failed || 0))} refused or failed`;
    const hue = a => (CB_AXES[a] || {}).hue || "var(--muted)";
    const html = (v.tasks || []).slice(0, 30).map(t => `<li class="${t.trace_id === S.cb.trace ? "on" : ""}" data-trace="${esc(t.trace_id)}">
        <time>${hms(t.updated)}</time><span class="cb-a2a-who"><b style="color:${hue(t.from_agent)}">${esc(t.from_agent)}</b> → <b style="color:${hue(t.to_agent)}">${esc(t.to_agent)}</b></span>
        <code>${esc(t.skill)}</code><span class="cb-a2a-st ${A2A_STATE[t.state] || ""}">${esc(t.state)}</span>
        <span class="cb-a2a-txt">${esc(t.note || t.reason)}</span></li>`).join("") || `<li class="feed-empty">No A2A message yet: they start when one axis needs another (a fire, a flood, a quake, bad air).</li>`;
    if (host._html !== html) { host.innerHTML = html; host._html = html; }
    const ch = (v.agents || []).map(a => `<div><b style="color:${hue(a.axis)}">${esc(a.axis)}</b>${a.skills.length ? a.skills.map(s => `<code title="${esc(s.description)}">${esc(s.id)}</code>`).join("") : '<span class="faint">no skill</span>'}</div>`).join("");
    if (cards._html !== ch) { cards.innerHTML = `<div class="faint">Skills each agent offers</div>` + ch; cards._html = ch; }
  }
  async function pollBrain(view, tick) {
    const jobs = [getJSON("/api/brain").then(v => { S.cb.view = v; })];
    const live = view === "city" || view === "overview" || (DOMAINS[view] && S.route.sub === "twin");
    if (live) jobs.push(getJSON("/api/brain/map").then(v => { S.cb.map = v; }));
    if (view === "city" || (DOMAINS[view] && S.route.sub === "twin")) {
      jobs.push(getJSON(`/api/brain/timeline?after=${S.cb.tlLast}`).then(v => {
        if (v.last < S.cb.tlLast) S.cb.tl = [];
        S.cb.tl = S.cb.tl.concat(v.items.filter(r => r.seq > S.cb.tlLast)).slice(-3000); S.cb.tlLast = v.last;
      }));
      if (tick % 3 === 0 || !S.cb.scen) jobs.push(getJSON("/api/brain/scenarios").then(v => { S.cb.scen = v; }));
      if (view === "city") jobs.push(getJSON("/api/brain/a2a").then(v => { S.cb.a2a = v; }));
    }
    await Promise.allSettled(jobs);
    renderBadge(); renderTop();
  }

  // ------------------------------------------------------------------ live Twin: every device, vehicle and unit, as the Twin reports them now
  // Data: /api/brain/map (the orchestrator polls the Twin every second: read_all, list_vehicles, list_incidents,
  // list_emergency_units, list_parking_lots, info). Nothing here comes from a simulation of the axis Brains.
  const TW_UNITS = { pm2_5: "µg/m³", pm10: "µg/m³", nitrogen_dioxide: "µg/m³", db_a: "dBA", fill_level: "%", bin_weight: "kg",
    energy_level: "%", grid_demand: "kW", zone_power: "kW", power_supplied: "%", water_supplied: "%", power_demand: "kW",
    voltage: "V", water_pressure: "bar", occupancy: "%", speed: "km/h", vehicle_count: "", occupancy_percent: "%", denied_entries: "cars",
    surface_water_depth: "mm", shaking_intensity: "MMI", rainfall: "mm/h", wind_speed: "m/s", temperature: "°C", valve_open: "%" };
  const TW_MAIN = {
    waste: ["fill_level", "bin_weight"], air: ["pm2_5", "pm10", "nitrogen_dioxide"], noise: ["db_a"], weather: ["rainfall", "wind_speed", "temperature"],
    grid: ["energy_level", "grid_demand"], service_zone: ["power_supplied", "zone_power", "water_supplied"], power_meter: ["power_demand"],
    utility: ["voltage", "water_pressure"], power_switch: ["switch_closed", "powered"], water_valve: ["valve_open"],
    traffic: ["occupancy", "speed", "vehicle_count"], signal: ["ns_signal", "ew_signal"], parking: ["occupancy_percent", "denied_entries"],
    flood: ["surface_water_depth", "rise_rate"], seismic: ["shaking_intensity"], reservoir: ["water_level", "level_percent"],
  };
  const TW_DOMAIN = {
    waste: { families: ["waste"], axis: "pollution" },
    air: { families: ["air", "noise", "weather"], axis: "pollution" },
    energy: { families: ["grid", "service_zone", "power_meter", "utility", "power_switch", "water_valve", "reservoir"], axis: "energy" },
    transport: { families: ["traffic", "signal", "parking"], axis: "transportation" },
  };
  const AIR_LIMITS = { pm2_5: 15, pm10: 45, nitrogen_dioxide: 25 };
  const airScore = r => Math.max(0, ...Object.entries(AIR_LIMITS).map(([m, l]) => (r[m] ?? 0) / l * 100));
  function twLevel(d) {          // 0 normal, 1 watch, 2 alert, 3 critical (same thresholds as the agents)
    const r = d.readings || {};
    switch (d.family) {
      case "waste": return r.fill_level >= 90 ? 3 : r.fill_level >= 75 ? 1 : 0;
      case "air": { const s = airScore(r); return s >= 130 ? 3 : s >= 100 ? 2 : s >= 70 ? 1 : 0; }
      case "noise": return r.db_a >= 75 ? 1 : 0;
      case "grid": return r.energy_level < 15 ? 3 : r.energy_level < 25 ? 2 : 0;
      case "service_zone": return r.power_supplied < 50 ? 3 : r.water_supplied < 50 ? 2 : 0;
      case "utility": return r.voltage < 205 ? 1 : 0;
      case "power_switch": return r.switch_closed > 50 && r.powered < 50 ? 2 : 0;
      case "traffic": return r.occupancy >= 70 && r.speed <= 12 ? 2 : r.occupancy >= 50 ? 1 : 0;
      case "parking": return r.occupancy_percent >= 100 ? 2 : r.occupancy_percent >= 85 ? 1 : 0;
      case "flood": return r.surface_water_depth >= 300 ? 3 : r.surface_water_depth >= 150 ? 2 : 0;
      case "seismic": return r.shaking_intensity >= 6.5 ? 3 : r.shaking_intensity >= 5 ? 2 : 0;
      case "water_valve": return r.valve_open < 50 ? 1 : 0;
      default: return 0;
    }
  }
  const LVL_COLOR = ["var(--ok)", "var(--watch)", "var(--alert)", "var(--critical)"];
  const LVL_NAME = ["Normal", "Watch", "Attention", "Critical"];
  const SIG_COLOR = v => v >= 2 ? "#22c55e" : v >= 1 ? "#eab308" : "#ef4444";
  function twFmt(m, v) {
    if (v == null) return "–";
    if (m === "ns_signal" || m === "ew_signal") return v >= 2 ? "green" : v >= 1 ? "amber" : "red";
    if (m === "switch_closed") return v > 50 ? "closed" : "open";
    if (m === "powered") return v > 50 ? "powered" : "unpowered";
    return `${fmt(v, Math.abs(v) < 10 ? 1 : 0)}${TW_UNITS[m] ? " " + TW_UNITS[m] : ""}`;
  }

  // markup of the live city (devices by state, vehicles, units, outages) for any map that uses cbBase()
  function twLayer(m, opts) {
    const fam = opts.families ? new Set(opts.families) : null;
    let g = "";
    const devs = (m.devices || []).filter(d => d.location && d.family !== "lighting" && d.family !== "camera" && (!fam || fam.has(d.family)));
    for (const d of (m.devices || [])) {            // zones without power: dark veil, whatever the filter
      if (d.family !== "service_zone" || !d.zone || (d.readings?.power_supplied ?? 100) >= 50) continue;
      const i = ZONE_IDS.indexOf(d.zone), col = i % 4, row = Math.floor(i / 4);
      g += `<rect class="tw-outage" x="${-380 + col * 190}" y="${-380 + row * 190}" width="190" height="190"><title>Zone ${esc(d.zone)}: no power</title></rect>
            <text class="tw-outage-t" x="${-380 + col * 190 + 95}" y="${-380 + row * 190 + 100}">no power</text>`;
    }
    for (const d of devs) {
      const l = d.location, r = d.readings || {}, lv = twLevel(d);
      // the City map at full view keeps only what needs a look (and the signals and car parks); calm sensors appear when zoomed in
      if (opts.calm === false && !lv && !["signal", "parking"].includes(d.family) && !(opts.keep && opts.keep.has(d.id))) continue;
      const k = opts.key("device", d);
      if (d.family === "signal") {
        g += `<g class="tw-sig" data-k="${k}"><title>${esc(d.id)}: N-S ${twFmt("ns_signal", r.ns_signal)}, E-W ${twFmt("ew_signal", r.ew_signal)}</title>
          <line x1="${l.x}" y1="${l.z - 4}" x2="${l.x}" y2="${l.z + 4}" stroke="${SIG_COLOR(r.ns_signal)}"/>
          <line x1="${l.x - 4}" y1="${l.z}" x2="${l.x + 4}" y2="${l.z}" stroke="${SIG_COLOR(r.ew_signal)}"/></g>`;
      } else if (d.family === "parking") {
        const pct = Math.min(100, r.occupancy_percent ?? 0);
        g += `<g class="tw-lot" data-k="${k}"><title>${esc(d.id)}: ${fmt(r.occupancy)}/${fmt(r.capacity)} (${fmt(pct)} %), barrier ${esc(d.state?.barrier_mode || "")}</title>
          <rect x="${l.x - 7}" y="${l.z - 7}" width="14" height="14" rx="2" style="stroke:${LVL_COLOR[lv]}"/>
          <rect x="${l.x - 6}" y="${l.z + 6 - 12 * pct / 100}" width="12" height="${12 * pct / 100}" style="fill:${LVL_COLOR[lv]}"/>
          ${d.state?.barrier_mode === "closed" ? `<path d="M${l.x - 7} ${l.z - 7} L${l.x + 7} ${l.z + 7}" class="tw-closed"/>` : ""}</g>`;
      } else if (d.family === "service_zone" || d.family === "power_meter") {
        continue;
      } else {
        const big = opts.families && opts.families.length <= 3;
        g += `<circle class="tw-dev ${d.kind === "actuator" ? "act" : ""}" data-k="${k}" cx="${l.x}" cy="${l.z}" r="${big ? 4.2 : lv ? 3.6 : 2.4}"
          style="fill:${lv || big ? LVL_COLOR[lv] : "var(--muted)"}"><title>${esc(d.id)} · ${(TW_MAIN[d.family] || []).slice(0, 2).map(x => twFmt(x, r[x])).join(", ")}</title></circle>`;
      }
    }
    // green waves: the junctions held green for a responding unit, and a line from the unit to them
    const unitAt = {};
    for (const v of (m.city?.vehicles || [])) if (v.unit && v.location) unitAt[v.unit] = v.location;
    for (const w of (m.green_waves || [])) {
      const u = unitAt[w.unit];
      for (const [sig, cmd] of Object.entries(w.junctions || {})) {
        const mm = /^SIG-Junction_r(\d+)_c(\d+)$/.exec(sig); if (!mm) continue;
        const jx = (+mm[2] - 1) * 95 - 380, jz = (+mm[1] - 1) * 95 - 380;
        if (u) g += `<line class="tw-wave-link" x1="${u.x}" y1="${u.z}" x2="${jx}" y2="${jz}"/>`;
        g += `<g class="tw-wave"><title>${esc(sig)} held ${cmd === "ew_green" ? "east-west" : "north-south"} green for ${esc(w.unit)}${w.incident ? " → " + esc(w.incident) : ""}</title>
          <circle cx="${jx}" cy="${jz}" r="9"/><path d="${cmd === "ew_green" ? `M${jx - 12} ${jz} L${jx + 12} ${jz}` : `M${jx} ${jz - 12} L${jx} ${jz + 12}`}"/></g>`;
      }
    }
    if (opts.vehicles) for (const v of (m.city?.vehicles || [])) {
      const l = v.location; if (!l) continue;
      const cls = v.unit ? "unit" : (v.class || "car");
      if (opts.vehicles === "units" && !v.unit) continue;
      g += `<rect class="tw-veh ${esc(cls)} ${v.stopped ? "stopped" : ""}" data-k="${opts.key("vehicle", v)}" x="${l.x - 2.4}" y="${l.z - 1.4}" width="4.8" height="2.8"
        transform="rotate(${(v.heading_deg || 0) - 90} ${l.x} ${l.z})"><title>${esc(v.id)} ${esc(v.class)} ${fmt((v.speed_mps || 0) * 3.6)} km/h${v.unit ? " · " + esc(v.unit) : ""}</title></rect>`;
    }
    return g;
  }
  function twOverlay(m) {
    const i = m?.city?.info || {}, t = i.traffic || {};
    if (!m || !m.source) return "";
    return `<span class="${m.source === "live" ? "tw-live" : "tw-mock"}">${m.source === "live" ? "LIVE Twin" : "MOCK Twin"}</span>
      ${i.clock ? `<span>${esc(i.clock)}${i.day ? " · day " + esc(i.day) : ""}</span>` : ""}${i.weather ? `<span>${esc(i.weather)}</span>` : ""}
      <span>${fmt(m.city?.vehicles_total ?? (m.city?.vehicles || []).length)} vehicles${t.stopped_vehicles != null ? ` · ${fmt(t.stopped_vehicles)} stopped` : ""}</span>
      <span>${fmt((m.city?.incidents || []).length)} open incidents</span>`;
  }

  // -- the "Live Twin" tab of every domain page
  function mountTwinDomain(axis, body) {
    body.innerHTML = `
      <div class="tw-note">Live from the Twin through the City Brain orchestrator, refreshed every second. The ${esc(DOMAINS[axis].name.toLowerCase())} team's own Brain runs on its own simulation: see the <a href="#/${axis}/live">Axis Brain</a> tab.</div>
      <div class="figures" id="tw-fig"></div>
      <div class="tw-grid">
        <section class="panel"><div class="panel-head"><h2>Twin city</h2><span class="spacer"></span><span class="tw-ov" id="tw-ov"></span></div>
          <div class="cb-mapwrap tw-map"><svg id="tw-map" viewBox="-400 -400 800 800" xmlns="http://www.w3.org/2000/svg">${cbBase().replace('id="cb-dyn"', 'id="tw-dyn"').replace('id="cb-base"', 'id="tw-base"')}</svg></div></section>
        <div class="stack">
          <section class="panel"><div class="panel-head"><h2>Brain decisions on this axis</h2></div><ul class="feed" id="tw-dec"></ul></section>
          <section class="panel"><div class="panel-head"><h2>Devices, most urgent first</h2><span class="spacer"></span><span class="sub" id="tw-count"></span></div>
            <div class="table-scroll"><table class="data" id="tw-table"></table></div></section>
        </div>
      </div>`;
    updateTwinDomain(axis);
  }
  function twFigures(axis, devs, m) {
    const by = f => devs.filter(d => d.family === f), R = d => d.readings || {};
    const avg = (a, k) => a.length ? a.reduce((s, d) => s + (R(d)[k] || 0), 0) / a.length : null;
    const fig = (label, value, sub, lv = 0) => `<div class="figure ${["", "watch", "alert", "critical"][lv]}"><div class="v">${value}</div><div class="k">${esc(label)}${sub ? " · " + esc(sub) : ""}</div></div>`;
    if (axis === "waste") {
      const bins = by("waste"), full = bins.filter(d => R(d).fill_level >= 90), trucks = (m.city?.units || []).filter(u => u.kind === "garbage");
      const rounds = (m.city?.incidents || []).filter(i => i.kind === "garbage_collection");
      return [fig("Bins", fmt(bins.length), "weight sensors on the Twin"), fig("Full (≥ 90 %)", fmt(full.length), full.slice(0, 3).map(d => d.id).join(", "), full.length ? 3 : 0),
        fig("Mean fill", fmt(avg(bins, "fill_level")) + " %", "all bins"), fig("Truck rounds", fmt(rounds.length), `${fmt(trucks.length)} garbage trucks out`)];
    }
    if (axis === "air") {
      const air = by("air"), worst = air.slice().sort((a, b) => airScore(R(b)) - airScore(R(a)))[0], s = worst ? airScore(R(worst)) : 0;
      const noise = by("noise"), wx = by("weather");
      return [fig("Worst air risk score", fmt(s), worst ? `${worst.id}, zone ${worst.zone}` : "", s >= 130 ? 3 : s >= 100 ? 2 : s >= 70 ? 1 : 0),
        fig("Mean PM2.5", fmt(avg(air, "pm2_5"), 1) + " µg/m³", "guideline 15"), fig("Max noise", fmt(Math.max(0, ...noise.map(d => R(d).db_a || 0))) + " dBA", `${noise.length} monitors`),
        fig("Rain", fmt(Math.max(0, ...wx.map(d => R(d).rainfall || 0)), 1) + " mm/h", m.city?.info?.weather || "")];
    }
    if (axis === "energy") {
      const g = by("grid")[0], zones = by("service_zone"), dark = zones.filter(d => R(d).power_supplied < 50), lowV = by("utility").filter(d => R(d).voltage < 205);
      const lvl = g ? R(g).energy_level : null;
      return [fig("Grid reserve", fmt(lvl) + " %", g ? `${fmt(R(g).energy_available)} kWh available` : "", lvl == null ? 0 : lvl < 15 ? 3 : lvl < 25 ? 2 : 0),
        fig("Zones without power", fmt(dark.length), dark.map(d => d.zone).join(", "), dark.length ? 3 : 0),
        fig("Demand", fmt(g ? R(g).grid_demand : null) + " kW", "whole city"), fig("Low-voltage gateways", fmt(lowV.length), "under 205 V", lowV.length ? 1 : 0)];
    }
    const tr = by("traffic"), lots = by("parking"), veh = m.city?.vehicles || [], t = m.city?.info?.traffic || {};
    const free = lots.reduce((s, d) => s + Math.max(0, (R(d).capacity || 0) - (R(d).occupancy || 0)), 0), cap = lots.reduce((s, d) => s + (R(d).capacity || 0), 0);
    const jams = tr.filter(d => twLevel(d) >= 2), roadInc = (m.city?.incidents || []).filter(i => !["garbage_collection", "building_fire", "water_main_break"].includes(i.kind));
    return [fig("Vehicles", fmt(m.city?.vehicles_total ?? veh.length), `${fmt(t.stopped_vehicles ?? veh.filter(v => v.stopped).length)} stopped`),
      fig("Road incidents", fmt(roadInc.length), roadInc.slice(0, 2).map(i => i.kind).join(", "), roadInc.length ? 2 : 0),
      fig("Mean detector speed", fmt(avg(tr, "speed")) + " km/h", `${tr.length} detectors`), fig("Congested detectors", fmt(jams.length), jams.map(d => d.id).join(", "), jams.length ? 2 : 0),
      fig("Parking", `${fmt(free)} / ${fmt(cap)}`, "spaces free")];
  }
  function updateTwinDomain(axis) {
    const m = S.cb.map, spec = TW_DOMAIN[axis];
    if (!$("tw-fig")) return;
    if (!m || !m.devices) {
      $("tw-fig").innerHTML = `<div class="feed-empty">Waiting for the City Brain orchestrator (python brain/run_brain.py, port 8500)…</div>`; return;
    }
    const devs = m.devices.filter(d => spec.families.includes(d.family));
    $("tw-fig").innerHTML = twFigures(axis, devs, m).join("");
    $("tw-ov").innerHTML = twOverlay(m);
    const items = {}; let n = 0;
    $("tw-dyn").innerHTML = twLayer(m, { families: spec.families, vehicles: axis === "transport" ? true : axis === "waste" ? "units" : false,
                                         key: (kind, data) => { const k = "t" + (n++); items[k] = { kind, data }; return k; } });
    const rows = devs.slice().sort((a, b) => twLevel(b) - twLevel(a) || String(a.id).localeCompare(String(b.id)));
    $("tw-count").textContent = `${devs.length} devices`;
    $("tw-table").innerHTML = `<thead><tr><th>Device</th><th>Zone</th><th>Now</th><th>State</th></tr></thead><tbody>${rows.slice(0, 150).map(d => {
      const lv = twLevel(d), r = d.readings || {};
      const st = d.state ? Object.entries(d.state).filter(([k, v]) => typeof v !== "object").slice(0, 2).map(([k, v]) => `${k}: ${v}`).join(", ") : "";
      return `<tr><td><span class="dot ${["ok", "watch", "alert", "critical"][lv]}"></span> ${esc(d.id)}</td><td>${esc(d.zone)}</td>
        <td>${(TW_MAIN[d.family] || Object.keys(r).slice(0, 2)).filter(x => r[x] != null).map(x => `${esc(x.replace(/_/g, " "))} <b>${esc(twFmt(x, r[x]))}</b>`).join(" · ")}${d.family === "air" ? ` · score <b>${fmt(airScore(r))}</b>` : ""}</td>
        <td class="faint">${esc(LVL_NAME[lv])}${st ? " · " + esc(st) : ""}</td></tr>`;
    }).join("")}</tbody>`;
    const dec = (S.cb.tl || []).filter(r => r.axis === spec.axis && ["decision", "action", "ignored", "conflict", "feedback"].includes(r.kind)).slice(-40).reverse();
    $("tw-dec").innerHTML = dec.length ? dec.map(r => `<li class="feed-item"><time>${hms(r.ts)}</time><span class="sev ${esc(r.severity)}"></span>
      <span>${cbSrcTag(r.source)}<b>${esc(r.kind)}</b> ${esc(r.summary)}</span></li>`).join("") : `<li class="feed-empty">No decision yet on this axis: the city is calm here.</li>`;
  }

  // -- overview: the live city at a glance
  function updateLiveStrip() {
    const host = $("ov-live"); if (!host) return;
    const m = S.cb.map;
    if (!m || !m.devices) { host.innerHTML = `<div class="feed-empty">Live Twin: waiting for the City Brain orchestrator…</div>`; return; }
    const devs = m.devices;
    host.innerHTML = `<div class="tw-strip-head"><div class="tw-ov">${twOverlay(m)}</div><a class="btn ghost" href="#/city">City map</a></div>
      <div class="figures">${["transport", "air", "waste", "energy"].map(a => twFigures(a, devs.filter(d => TW_DOMAIN[a].families.includes(d.family)), m).slice(0, 2).join("")).join("")}</div>`;
  }

  // ------------------------------------------------------------------ mount and refresh
  function mount() {
    window.removeEventListener("resize", onResize);
    const { view, sub } = S.route;
    if (view === "overview") mountOverview();
    else if (view === "city") mountCity();
    else if (DOMAINS[view]) mountDomain(view, sub);
    else if (view === "agents") mountAgents();
    else if (view === "log") mountLog();
    renderNav(); refreshViews();
    $("content").focus({ preventScroll: true });
  }
  function refreshViews() {
    const { view, sub } = S.route;
    if (view === "overview") { updateOverview(); renderScen(); updateLiveStrip(); }
    if (view === "city") updateCity();
    if (DOMAINS[view]) {
      updateDomainHead(view);
      if (sub === "live") ({ waste: updateWaste, air: updateAir, energy: updateEnergy, transport: updateTransport })[view]();
      if (sub === "twin") updateTwinDomain(view);
    }
    if (view === "log") updateLog();
  }

  let tick = 0;
  async function poll() {
    tick++;
    const { view, sub } = S.route;
    const needWA = view === "overview" || view === "waste" || view === "air";
    const needEN = view === "overview" || view === "energy";
    const needTR = view === "overview" || view === "transport";
    const jobs = [
      getJSON("/api/overview").then(v => { S.overview = v; S.lastOk = Date.now(); }),
      getJSON(`/api/feed?after=${S.feedLast}&limit=500`).then(v => { if (v.last < S.feedLast) S.feed = []; const gone = new Set(v.items.map(i => i.replaces).filter(Boolean)); S.feed = S.feed.filter(i => !gone.has(i.seq)).concat(v.items).slice(-3000); S.feedLast = v.last; }),
    ];
    if (needWA && (tick % 2 === 0 || !S.wa)) jobs.push(getJSON("/api/waste_air").then(v => { S.wa = v; }));
    if (needEN) jobs.push(getJSON("/api/energy").then(v => { S.en = v; }));
    if (needTR && (tick % 2 === 0 || !S.tr)) jobs.push(getJSON("/api/transport").then(v => { S.tr = v; }));
    if (needTR && !S.world) jobs.push(getJSON("/api/transport/world").then(v => { if (v && v.bbox) S.world = v; }));
    if (view === "transport" && sub === "live" && tick % 3 === 0) jobs.push(loadTestbook());
    if (!S.config) jobs.push(getJSON("/api/config").then(v => { S.config = v; }));
    if (view === "overview") jobs.push(getJSON("/api/scenarios").then(v => { S.scen = v; }));
    jobs.push(pollBrain(view, tick));
    if (tick % 2 === 0 || !S.running) jobs.push(getJSON("/api/scenarios/running").then(v => { S.running = v.running || []; }));
    await Promise.allSettled(jobs);
    renderStop();
    if (DOMAINS[view] && sub !== "live") { renderNav(); updateDomainHead(view); }
    else { renderNav(); refreshViews(); }
    setLive();
    setTimeout(poll, 1000);
  }

  function clock() { $("clock").textContent = new Date().toLocaleTimeString([], { hour12: false }); setLive(); }
  $("theme").addEventListener("click", () => {
    const cur = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
    const next = cur === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("cb-theme", next); } catch (e) {}
    refreshViews();
  });

  S.route = parseRoute();
  mount();
  poll();
  setInterval(clock, 1000); clock();
})();
