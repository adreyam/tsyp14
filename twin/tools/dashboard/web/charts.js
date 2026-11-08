"use strict";
/* Small canvas charting kit: line/area charts with axes, gridlines, tick labels, a hover crosshair with a tooltip, legends and
   sparklines. No dependencies. Series are plain {label, color, unit, xs, ys}; Chart.plot draws them, Chart.drawHistory slices
   rolling time histories to a window. */

const PALETTE = ["#38bdf8", "#fbbf24", "#4ade80", "#a78bfa", "#fb7185", "#fb923c", "#2dd4bf", "#e879f9"];

function niceNumber(range, round) {
  const exp = Math.floor(Math.log10(range)), f = range / 10 ** exp;
  const nf = round ? (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) : (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10);
  return nf * 10 ** exp;
}
function niceTicks(lo, hi, count = 5) {
  if (!(hi > lo)) { const pad = Math.abs(lo) * 0.05 || 0.5; lo -= pad; hi += pad; }
  const step = niceNumber(niceNumber(hi - lo, false) / (count - 1), true);
  const start = Math.floor(lo / step) * step, end = Math.ceil(hi / step) * step, ticks = [];
  for (let v = start; v <= end + step * 0.5; v += step) ticks.push(+v.toPrecision(12));
  return { ticks, lo: start, hi: end, step };
}
function tickLabel(v, step) {
  const a = Math.abs(v);
  if (a >= 10000) return (v / 1000).toFixed(a >= 100000 ? 0 : 1) + "k";
  const decimals = step >= 1 ? 0 : Math.min(4, Math.ceil(-Math.log10(step)));
  return v.toFixed(decimals);
}
function bisect(xs, x) { let lo = 0, hi = xs.length; while (lo < hi) { const mid = (lo + hi) >> 1; if (xs[mid] < x) lo = mid + 1; else hi = mid; } return lo; }
function fmtValue(v, unit = "") {
  if (v == null || Number.isNaN(v)) return "–";
  const a = Math.abs(v), text = a >= 1000 ? v.toFixed(0) : a >= 100 ? v.toFixed(1) : a >= 10 ? v.toFixed(2) : v.toFixed(3);
  return unit ? `${text} ${unit}` : text;
}

class Chart {
  /* opts: height (px), zeroBased, normalize (each series scaled to its own range), area, yUnit, xLabel(x)->text, fixedY [lo, hi] */
  constructor(host, opts = {}) {
    this.host = host; this.opts = Object.assign({ height: 220, area: true, zeroBased: false }, opts);
    host.classList.add("chart"); host.style.height = this.opts.height + "px";
    this.canvas = document.createElement("canvas"); host.append(this.canvas);
    this.tip = document.createElement("div"); this.tip.className = "tip"; this.tip.style.display = "none"; host.append(this.tip);
    this.hoverX = null; this.last = null;
    this.canvas.addEventListener("mousemove", e => { const r = this.canvas.getBoundingClientRect(); this.hoverX = e.clientX - r.left; this.hoverY = e.clientY - r.top; this.redraw(); });
    this.canvas.addEventListener("mouseleave", () => { this.hoverX = null; this.tip.style.display = "none"; this.redraw(); });
    new ResizeObserver(() => this.redraw()).observe(host);
  }
  /* series: [{label, color, unit, xs, ys}] ; view: {xMin, xMax, xTicks?:[{x,label}]} */
  plot(series, view) { this.last = { series, view }; this.draw(); }
  redraw() { if (this.last) this.draw(); }
  /* Rolling histories: hist is a Map key -> {t:[sec], v:[number]}; series entries carry .key; the window ends at "now". */
  drawHistory(hist, series, windowSec, now = performance.now() / 1000) {
    const out = series.map(s => {
      const h = hist.get(s.key) || { t: [], v: [] }, from = bisect(h.t, now - windowSec);
      return { ...s, xs: h.t.slice(from).map(t => t - now), ys: h.v.slice(from) };
    });
    const ticks = []; const steps = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600]; const step = steps.find(s => windowSec / s <= 6) || 600;
    for (let x = 0; x >= -windowSec; x -= step) ticks.push({ x, label: x === 0 ? "now" : `-${x >= 60 ? (x / 60) + "m" : x + "s"}` });
    this.plot(out, { xMin: -windowSec, xMax: 0, xTicks: ticks });
  }
  draw() {
    const { series, view } = this.last, dpr = window.devicePixelRatio || 1, w = this.host.clientWidth, h = this.opts.height;
    if (!w) return;
    const c = this.canvas; if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); }
    const g = c.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
    const normalize = this.opts.normalize && series.length > 1;
    let lo = Infinity, hi = -Infinity;
    for (const s of series) {
      const from = bisect(s.xs, view.xMin);
      let sl = Infinity, sh = -Infinity;
      for (let i = from; i < s.ys.length; i++) { const v = s.ys[i]; if (v < sl) sl = v; if (v > sh) sh = v; }
      s._lo = sl; s._hi = sh; if (sl < lo) lo = sl; if (sh > hi) hi = sh;
    }
    if (this.opts.fixedY) { lo = this.opts.fixedY[0]; hi = this.opts.fixedY[1]; }
    else if (!isFinite(lo)) { lo = 0; hi = 1; }
    else if (this.opts.zeroBased) lo = Math.min(0, lo);
    const pad = (hi - lo) * 0.08; if (!this.opts.fixedY && !this.opts.zeroBased) { lo -= pad; hi += pad; } else if (!this.opts.fixedY) hi += pad;
    const yt = normalize ? niceTicks(0, 100, 5) : niceTicks(lo, hi, Math.max(3, Math.floor(h / 48)));
    const left = 46, right = 14, top = 10, bottom = 26, pw = w - left - right, ph = h - top - bottom;
    const X = x => left + (x - view.xMin) / (view.xMax - view.xMin || 1) * pw;
    const Y = (v, s) => { const [a, b] = normalize ? [s._lo === s._hi ? s._lo - 1 : s._lo, s._hi === s._lo ? s._hi + 1 : s._hi] : [yt.lo, yt.hi]; return top + ph - (v - a) / (b - a || 1) * ph; };
    g.font = "11px Inter, system-ui, sans-serif"; g.textBaseline = "middle";
    g.strokeStyle = "#1b2a3d"; g.lineWidth = 1; g.fillStyle = "#6f819a"; g.textAlign = "right";
    for (const t of yt.ticks) { const y = Math.round(top + ph - (t - yt.lo) / (yt.hi - yt.lo || 1) * ph) + 0.5; g.beginPath(); g.moveTo(left, y); g.lineTo(left + pw, y); g.stroke(); g.fillText(normalize ? t + "%" : tickLabel(t, yt.step), left - 8, y); }
    g.textAlign = "center"; g.textBaseline = "top";
    for (const t of view.xTicks || []) { const x = Math.round(X(t.x)) + 0.5; g.strokeStyle = "#16233a"; g.beginPath(); g.moveTo(x, top); g.lineTo(x, top + ph); g.stroke(); g.fillText(t.label, Math.min(Math.max(x, left + 8), w - 14), top + ph + 8); }
    if (this.opts.yUnit && !normalize) { g.save(); g.fillStyle = "#6f819a"; g.textAlign = "left"; g.fillText(this.opts.yUnit, 6, 0); g.restore(); }
    g.save(); g.beginPath(); g.rect(left, top - 2, pw, ph + 4); g.clip();
    series.forEach((s, si) => {
      const from = Math.max(0, bisect(s.xs, view.xMin) - 1), n = s.ys.length - from; if (n < 1) return;
      const stride = Math.max(1, Math.floor(n / (pw * 1.5))), pts = [];
      for (let i = from; i < s.ys.length; i += stride) pts.push([X(s.xs[i]), Y(s.ys[i], s)]);
      const lastIdx = s.ys.length - 1; if ((lastIdx - from) % stride) pts.push([X(s.xs[lastIdx]), Y(s.ys[lastIdx], s)]);
      const color = s.color || PALETTE[si % PALETTE.length];
      if (this.opts.area && (series.length === 1 || s.area)) {
        const grad = g.createLinearGradient(0, top, 0, top + ph); grad.addColorStop(0, color + "55"); grad.addColorStop(1, color + "00");
        g.beginPath(); g.moveTo(pts[0][0], top + ph); pts.forEach(p => g.lineTo(p[0], p[1])); g.lineTo(pts[pts.length - 1][0], top + ph); g.closePath(); g.fillStyle = grad; g.fill();
      }
      g.beginPath(); pts.forEach((p, i) => (i ? (s.step ? (g.lineTo(p[0], pts[i - 1][1]), g.lineTo(p[0], p[1])) : g.lineTo(p[0], p[1])) : g.moveTo(p[0], p[1])));
      g.strokeStyle = color; g.lineWidth = 1.8; g.lineJoin = "round"; g.stroke();
      const lp = pts[pts.length - 1]; g.fillStyle = color; g.beginPath(); g.arc(lp[0], lp[1], 3, 0, 7); g.fill();
    });
    g.restore();
    if (this.hoverX != null && this.hoverX >= left && this.hoverX <= left + pw) {
      const xv = view.xMin + (this.hoverX - left) / pw * (view.xMax - view.xMin);
      g.strokeStyle = "#8aa4c4aa"; g.lineWidth = 1; g.setLineDash([4, 3]); g.beginPath(); g.moveTo(this.hoverX, top); g.lineTo(this.hoverX, top + ph); g.stroke(); g.setLineDash([]);
      const rows = []; let tLabel = "";
      series.forEach((s, si) => {
        if (!s.xs.length) return;
        let i = bisect(s.xs, xv); if (i > 0 && (i === s.xs.length || xv - s.xs[i - 1] < s.xs[i] - xv)) i--;
        if (Math.abs(s.xs[i] - xv) > (view.xMax - view.xMin) * 0.2) return;
        const color = s.color || PALETTE[si % PALETTE.length], y = Y(s.ys[i], s);
        g.fillStyle = "#0a0f16"; g.strokeStyle = color; g.lineWidth = 2; g.beginPath(); g.arc(X(s.xs[i]), y, 4, 0, 7); g.fill(); g.stroke();
        rows.push(`<div><i style="background:${color}"></i>${s.label}: <b>${fmtValue(s.ys[i], s.unit)}</b></div>`); tLabel = this.opts.xLabel ? this.opts.xLabel(s.xs[i]) : s.xs[i].toFixed(2);
      });
      if (rows.length) {
        this.tip.innerHTML = `<div class="t">${tLabel}</div>${rows.join("")}`; this.tip.style.display = "block";
        const tw = this.tip.offsetWidth; this.tip.style.left = Math.min(this.hoverX + 14, w - tw - 4) + "px"; this.tip.style.top = Math.max(4, (this.hoverY || 20) - 10) + "px";
      } else this.tip.style.display = "none";
    }
  }
}

function legend(host, series) {
  host.className = "legend";
  host.innerHTML = series.map((s, i) => `<span><i style="background:${s.color || PALETTE[i % PALETTE.length]}"></i>${s.label}<b data-lv="${i}"></b></span>`).join("");
}
function legendValues(host, series, hist) {
  series.forEach((s, i) => { const h = hist.get(s.key), n = host.querySelector(`[data-lv="${i}"]`); if (n) n.textContent = h && h.v.length ? fmtValue(h.v[h.v.length - 1], s.unit) : ""; });
}

/* A tiny filled line with no axes (used on the KPI cards). */
function sparkline(canvas, ys, color = "#38bdf8") {
  const dpr = window.devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight; if (!w || ys.length < 2) return;
  if (canvas.width !== Math.round(w * dpr)) { canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr); }
  const g = canvas.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
  let lo = Math.min(...ys), hi = Math.max(...ys); if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
  const pts = ys.map((v, i) => [i / (ys.length - 1) * w, h - 4 - (v - lo) / (hi - lo) * (h - 10)]);
  const grad = g.createLinearGradient(0, 0, 0, h); grad.addColorStop(0, color + "44"); grad.addColorStop(1, color + "00");
  g.beginPath(); g.moveTo(0, h); pts.forEach(p => g.lineTo(p[0], p[1])); g.lineTo(w, h); g.fillStyle = grad; g.fill();
  g.beginPath(); pts.forEach((p, i) => (i ? g.lineTo(p[0], p[1]) : g.moveTo(p[0], p[1]))); g.strokeStyle = color; g.lineWidth = 1.6; g.stroke();
}
