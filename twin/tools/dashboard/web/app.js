"use strict";
/* City Ground Zero dashboard — core. A pure MCP client: it speaks JSON-RPC 2.0 to the city's MCP server over MCP's HTTP+SSE transport
   (/mcp/sse + /mcp/message) and shows nothing but what MCP tool calls return. Everything live is ONE streaming tool call
   ("read_city", a progress notification every 100 ms); every control is an MCP tool call too. views.js draws the pages. */

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const html = (markup) => { const t = document.createElement("template"); t.innerHTML = markup.trim(); return t.content.firstElementChild; };
const fmt = (n, digits = 1) => (typeof n === "number" && isFinite(n) ? (Math.abs(n) >= 1000 ? n.toFixed(0) : n.toFixed(digits)) : (n ?? "–"));
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const nowSec = () => performance.now() / 1000;

class McpClient {
  constructor() { this.pending = new Map(); this.progress = new Map(); this.nextId = 0; this.nextToken = 0; this.endpoint = null; this.tools = []; this.onSession = () => {}; this.onLink = () => {}; this.onCall = () => {}; }
  connect() {
    this.source = new EventSource("/mcp/sse");
    this.source.addEventListener("endpoint", async event => {
      this.endpoint = event.data; this.pending.clear(); this.progress.clear();
      try { await this.handshake(); this.onLink(true); this.onSession(); } catch (error) { console.error(error); this.onLink(false); }
    });
    this.source.addEventListener("message", event => this.receive(JSON.parse(event.data)));
    this.source.onerror = () => this.onLink(false);
  }
  post(message) { return fetch(this.endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(message) }); }
  request(method, params) {
    const id = ++this.nextId;
    return new Promise((resolve, reject) => { this.pending.set(id, { resolve, reject, id }); this.post({ jsonrpc: "2.0", id, method, params }).catch(reject); });
  }
  notify(method, params) { return this.post({ jsonrpc: "2.0", method, params }); }
  async handshake() {
    this.info = await this.request("initialize", { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "city-dashboard", version: "2.0.0" } });
    await this.notify("notifications/initialized");
    this.tools = (await this.request("tools/list")).tools;
  }
  receive(message) {
    if (message.id !== undefined && message.id !== null && this.pending.has(message.id)) {
      const waiter = this.pending.get(message.id); this.pending.delete(message.id);
      if (message.error) waiter.reject(new Error(message.error.message)); else waiter.resolve(message.result);
    } else if (message.method === "notifications/progress") {
      const handler = this.progress.get(message.params.progressToken);
      if (handler) { try { const text = message.params.message; const parsed = JSON.parse(text); handler(parsed.data, parsed, text.length); } catch (error) { console.error(error); } }
    }
  }
  /* A one-shot call returning the tool's structured JSON (live data tools get once = true). */
  async call(name, args = {}) {
    const started = performance.now();
    const result = await this.request("tools/call", { name, arguments: { ...args, ...(this.accepts(name, "once") ? { once: true } : {}) } });
    const body = result.structuredContent;
    this.onCall({ tool: name, args, ok: !result.isError, result: body, ms: performance.now() - started });
    if (result.isError) throw new Error(body?.error?.message || "tool error");
    return body;
  }
  accepts(name, key) { const tool = this.tools.find(t => t.name === name); return !!(tool && tool.inputSchema.properties[key]); }
  /* A live stream: the tool sends each update as a progress notification until we cancel it (or the session ends). */
  stream(name, args, onUpdate) {
    const handle = { stopped: false, token: "", requestId: 0, retry: 0 };
    const start = () => {
      if (handle.stopped || !this.endpoint) return;
      const token = "p" + (++this.nextToken), id = this.nextId + 1;
      handle.token = token; handle.requestId = id;
      this.progress.set(token, onUpdate);
      this.request("tools/call", { name, arguments: args, _meta: { progressToken: token } })
        .catch(error => console.warn(`${name} stream ended: ${error.message}`))
        .finally(() => {
          this.progress.delete(token);
          if (!handle.stopped) handle.retry = setTimeout(start, 500);
        });
    };
    start();
    return { cancel: () => {
      handle.stopped = true; clearTimeout(handle.retry); this.progress.delete(handle.token);
      return handle.requestId ? this.notify("notifications/cancelled", { requestId: handle.requestId, reason: "dashboard" }) : Promise.resolve();
    } };
  }
}

const client = new McpClient();
const TICK = 0.1;                                   // seconds: the MCP tick — every live stream updates this often

/* ---------- state ---------- */
const S = {
  city: null, status: null, devices: new Map(), byFamily: new Map(), zones: null, version: 0, lastAt: 0,
  rate: { n: 0, since: nowSec(), perSec: 0, bytes: 0, lastBytes: 0, gap: 0 },
  lastCommand: null, commands: [],
};
const hist = new Map();                             // key -> {t:[], v:[]}: rolling time series (10 minutes at the 100 ms tick)
const tracked = new Set();                          // keys recorded on every tick
const HIST_MAX = 6000;
function track(key) { tracked.add(key); }
function record(key, value, t) {
  if (typeof value !== "number" || !isFinite(value)) return;
  let h = hist.get(key); if (!h) hist.set(key, h = { t: [], v: [] });
  h.t.push(t); h.v.push(value); if (h.t.length > HIST_MAX) { h.t.splice(0, 200); h.v.splice(0, 200); }
}
["vehicles", "stopped", "citizens", "parked", "grid:energy_level", "grid:grid_demand", "agg:air:pm2_5", "agg:noise:db_a", "agg:weather:temperature", "agg:weather:wind_speed",
 "agg:traffic:vehicle_count", "agg:traffic:speed", "agg:waste:bin_weight", "agg:waste:fill_level", "max:seismic:ground_acceleration", "agg:reservoir:water_level", "units_out", "incidents"].forEach(track);

function aggregate(devices) {
  const out = new Map();                            // "family:metric" -> {sum, n, min, max}
  for (const d of devices) for (const m in d.readings) {
    const v = d.readings[m].value; if (typeof v !== "number") continue;
    const key = d.family + ":" + m; let a = out.get(key); if (!a) out.set(key, a = { sum: 0, n: 0, min: Infinity, max: -Infinity });
    a.sum += v; a.n++; if (v < a.min) a.min = v; if (v > a.max) a.max = v;
  }
  return out;
}

function onCity(data, parsed, bytes) {
  const t = nowSec(), r = S.rate;
  S.city = data; S.status = data.status; S.version++; r.n++; r.bytes += bytes; r.lastBytes = bytes; r.gap = S.lastAt ? t - S.lastAt : 0; S.lastAt = t;
  if (t - r.since >= 1) { r.perSec = r.n / (t - r.since); r.kbps = r.bytes / (t - r.since) / 1024; r.n = 0; r.bytes = 0; r.since = t; }
  const list = data.devices ? data.devices.devices : [];
  S.devices = new Map(list.map(d => [d.id, d]));
  S.byFamily = new Map(); for (const d of list) { let a = S.byFamily.get(d.family); if (!a) S.byFamily.set(d.family, a = []); a.push(d); }
  const agg = aggregate(list), st = data.status || {};
  const own = { vehicles: st.traffic?.simulated_vehicles, stopped: st.traffic?.stopped_vehicles, citizens: st.citizens_shown, parked: data.parking?.parked,
    units_out: (data.units?.units || []).filter(u => !["garaged", "patrol", "idle"].includes(u.status)).length, incidents: (data.incidents?.incidents || []).length };
  for (const key of tracked) {
    let v;
    if (key in own) v = own[key];
    else if (key.startsWith("grid:")) v = S.devices.get("GRID-01")?.readings[key.slice(5)]?.value;
    else if (key.startsWith("agg:") || key.startsWith("max:") || key.startsWith("min:") || key.startsWith("sum:")) {
      const [kind, family, metric] = key.split(":"), a = agg.get(family + ":" + metric);
      if (a) v = kind === "agg" ? a.sum / a.n : kind === "sum" ? a.sum : kind === "max" ? a.max : a.min;
    } else if (key.startsWith("dev:")) { const [, id, metric] = key.split(":"); v = S.devices.get(id)?.readings[metric]?.value; }
    else if (key.startsWith("lot:")) v = (data.parking?.lots || []).find(l => l.id === key.slice(4))?.occupied;
    record(key, v, t);
  }
  if (typeof PL !== "undefined" && PL.tap) PL.tap(data, parsed, bytes);
  Boot.onData();
}

/* ---------- commands: every control is an MCP tool call ---------- */
let toastTimer = 0;
function toast(text, bad = false) {
  const n = $("#toast"); n.textContent = text; n.className = "toast show" + (bad ? " err" : ""); clearTimeout(toastTimer); toastTimer = setTimeout(() => n.classList.remove("show"), 3200);
}
async function run(tool, args = {}, label = "") {
  const entry = { at: new Date(), tool, args, ok: false };
  try {
    const result = await client.call(tool, args);
    entry.ok = true; entry.result = result; S.lastCommand = { tool, args, result };
    toast(result.applied ? String(result.applied) : (label || tool) + " — done");
    return result;
  } catch (error) { entry.error = error.message; S.lastCommand = { tool, args, error: error.message }; toast(error.message, true); }
  finally { S.commands.unshift(entry); S.commands.length = Math.min(S.commands.length, 40); Boot.onCommand(); }
}
client.onCall = () => {};

const Boot = { onData() {}, onCommand() {}, onSession() {} };
