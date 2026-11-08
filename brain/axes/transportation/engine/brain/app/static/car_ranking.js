/* car_ranking.js - the Top-3 ranking, run INSIDE THE CAR.
 *
 * Same maths as backend/app/services/ranking.py (min-max -> CRITIC -> weighted sum) for the six
 * city criteria, plus a seventh one, "routine", built from the driver's frequent places.
 * The frequent places are only ever read here, in the car; they are never sent to the backend.
 *
 * Works in a browser (window.CarRanking) and in Node (require) so it can be tested.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.CarRanking = factory();
})(typeof self !== "undefined" ? self : this, function () {
  const CRITERIA = ["drive_min", "walk_m", "free_now", "free_30", "pressure", "reputation"];
  const COST = new Set(["drive_min", "walk_m", "pressure", "routine_m"]);
  const DEFAULT_WEIGHTS = { drive_min: 0.20, walk_m: 0.15, free_now: 0.20, free_30: 0.20, pressure: 0.15, reputation: 0.10 };
  const DEFAULT_ROUTINE_WEIGHT = 0.10;   // used only when CRITIC cannot run (< 3 lots)
  const ROUTINE_CAP = 0.25;              // the routine criterion can never decide more than a quarter of the score
  const MIN_FOR_CRITIC = 3;

  function haversineM(lat1, lon1, lat2, lon2) {
    const r = 6371000, rad = (d) => (d * Math.PI) / 180;
    const p1 = rad(lat1), p2 = rad(lat2), dp = p2 - p1, dl = rad(lon2 - lon1);
    const a = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
    return 2 * r * Math.asin(Math.sqrt(a));
  }

  function minMax(values, cost) {
    const lo = Math.min(...values), hi = Math.max(...values);
    if (hi - lo < 1e-12) return values.map(() => 0.5);
    return values.map((v) => ((cost ? hi - v : v - lo)) / (hi - lo));
  }
  const mean = (xs) => xs.reduce((a, b) => a + b, 0) / xs.length;
  const std = (xs) => { const m = mean(xs); return Math.sqrt(mean(xs.map((x) => (x - m) ** 2))); };
  function corr(a, b) {
    const sa = std(a), sb = std(b);
    if (sa < 1e-12 || sb < 1e-12) return 0;
    const ma = mean(a), mb = mean(b);
    return mean(a.map((x, i) => (x - ma) * (b[i] - mb))) / (sa * sb);
  }
  function criticWeights(cols) {
    const names = Object.keys(cols), info = {};
    for (const j of names) {
      let conflict = 0;
      for (const k of names) if (k !== j) conflict += 1 - corr(cols[j], cols[k]);
      info[j] = std(cols[j]) * conflict;
    }
    const total = names.reduce((s, j) => s + info[j], 0), w = {};
    for (const j of names) w[j] = total < 1e-12 ? 1 / names.length : info[j] / total;
    return w;
  }
  const round = (x, n) => Math.round(x * 10 ** n) / 10 ** n;

  /* ---- the driver's frequent places -------------------------------------------------------
   * place = { label, lat, lon, weight (1-3), schedule: [{days: "weekdays"|"weekend"|"all", from: "HH:MM", to: "HH:MM"}] }
   * when  = { dow: 0-6 (0 = Sunday), minutes: minutes since midnight }
   * A place counts only while one of its schedule windows is open.                          */
  const toMin = (hhmm) => { const [h, m] = hhmm.split(":").map(Number); return h * 60 + m; };
  function isActive(place, when) {
    const weekend = when.dow === 0 || when.dow === 6;
    return (place.schedule || [{ days: "all", from: "00:00", to: "24:00" }]).some((w) => {
      const dayOk = w.days === "all" || (w.days === "weekend" ? weekend : !weekend);
      return dayOk && when.minutes >= toMin(w.from) && when.minutes < toMin(w.to);
    });
  }
  function activePlaces(places, when) { return (places || []).filter((p) => isActive(p, when)); }

  /* weighted mean distance (m) from a lot to the places that matter right now */
  function routineMetres(lot, active) {
    const wsum = active.reduce((s, p) => s + p.weight, 0);
    return active.reduce((s, p) => s + p.weight * haversineM(lot.lat, lot.lon, p.lat, p.lon), 0) / wsum;
  }

  function capWeight(w, name, cap) {
    if (w[name] <= cap) return false;
    const excess = w[name] - cap, others = Object.keys(w).filter((k) => k !== name);
    const osum = others.reduce((s, k) => s + w[k], 0);
    w[name] = cap;
    for (const k of others) w[k] += osum > 0 ? excess * (w[k] / osum) : excess / others.length;
    return true;
  }

  /* candidates: lots as returned by POST /api/city/candidates
   * options:    { topN = 3, places = [], when }                                              */
  function rank(candidates, options = {}) {
    const topN = options.topN ?? 3;
    const active = options.places && options.when ? activePlaces(options.places, options.when) : [];
    const useRoutine = active.length > 0;
    const lots = candidates.map((c) => ({ ...c }));
    if (useRoutine) for (const c of lots) c.routine_m = Math.round(routineMetres(c, active));
    const names = useRoutine ? [...CRITERIA, "routine_m"] : CRITERIA;
    if (!lots.length) return { ranking: [], weights: {}, weights_source: "none", routine: { used: false, active: [] } };

    for (const c of lots) for (const k of names) if (!Number.isFinite(c[k])) throw new Error(`lot ${c.lot_id}: ${k} missing`);
    const marks = {};
    for (const k of names) marks[k] = minMax(lots.map((c) => c[k]), COST.has(k));

    let w, source;
    if (lots.length >= MIN_FOR_CRITIC) { w = criticWeights(marks); source = "critic"; }
    else {
      w = { ...DEFAULT_WEIGHTS }; source = "default";
      if (useRoutine) { for (const k of CRITERIA) w[k] *= 1 - DEFAULT_ROUTINE_WEIGHT; w.routine_m = DEFAULT_ROUTINE_WEIGHT; }
    }
    const capped = useRoutine ? capWeight(w, "routine_m", ROUTINE_CAP) : false;

    const out = lots.map((c, i) => {
      const raw = {}; let total = 0;
      for (const k of names) { raw[k] = w[k] * marks[k][i]; total += raw[k]; }
      const contributions = {}, m = {};
      for (const k of names) { contributions[k] = round(raw[k], 4); m[k] = round(marks[k][i], 4); }
      return { ...c, score: round(Math.min(1, total), 4), marks: m, contributions };
    });
    out.sort((a, b) => b.score - a.score || b.free_now - a.free_now || a.drive_min - b.drive_min || (a.lot_id < b.lot_id ? -1 : 1));
    out.forEach((r, i) => (r.rank = i + 1));
    const wr = {}; for (const k of names) wr[k] = round(w[k], 4);
    return { ranking: out.slice(0, topN), weights: wr, weights_source: source,
             routine: { used: useRoutine, active: active.map((p) => p.label), cap_applied: capped } };
  }

  return { CRITERIA, haversineM, minMax, criticWeights, activePlaces, isActive, routineMetres, rank, ROUTINE_CAP };
});
