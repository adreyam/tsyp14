"""The car ranks in JavaScript, the server in Python: they must agree.
Run from the backend folder:  python tests/parity_check.py     (needs Node.js)
Compares 200 random cases for the six city criteria, and checks the 7th (routine) criterion
against an independent NumPy computation.
"""
import json, os, random, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.services import ranking

JS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "static", "car_ranking.js")


def rand_cands(rng, n):
    return [dict(lot_id=f"L{i}", lat=36.8 + rng.random() * .03, lon=10.17 + rng.random() * .03,
                 drive_min=rng.uniform(3, 25), walk_m=rng.uniform(50, 1900), free_now=float(rng.randint(1, 40)),
                 free_30=rng.uniform(0, 40), pressure=rng.uniform(0, 1), reputation=rng.random()) for i in range(n)]


def run_js(payload):
    code = "const R=require(process.argv[1]);const p=JSON.parse(require('fs').readFileSync(0,'utf8'));" \
           "console.log(JSON.stringify(p.cases.map(c=>R.rank(c.cands,c.opts))));"
    out = subprocess.run(["node", "-e", code, JS], input=json.dumps(payload), capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def main():
    rng = random.Random(11)
    cases = [{"cands": rand_cands(rng, rng.choice([1, 2, 3, 4, 5, 7])), "opts": {"topN": 10}} for _ in range(200)]
    js = run_js({"cases": cases})
    bad = 0
    for c, j in zip(cases, js):
        py = ranking.rank_lots(c["cands"], top_n=10)
        same_order = [x["lot_id"] for x in py["ranking"]] == [x["lot_id"] for x in j["ranking"]]
        wdiff = max(abs(py["weights"][k] - j["weights"][k]) for k in py["weights"])
        sdiff = max(abs(a["score"] - b["score"]) for a, b in zip(py["ranking"], j["ranking"]))
        if not same_order or wdiff > 2e-4 or sdiff > 2e-4 or py["weights_source"] != j["weights_source"]:
            bad += 1
    print(f"six-criteria parity: {200 - bad}/200 identical (order, weights, scores)")

    # 7th criterion vs independent numpy
    import numpy as np
    cands = rand_cands(rng, 6)
    places = [dict(label="School", lat=36.8, lon=10.172, weight=3, schedule=[{"days": "all", "from": "00:00", "to": "24:00"}]),
              dict(label="Cafe", lat=36.81, lon=10.19, weight=1, schedule=[{"days": "all", "from": "00:00", "to": "24:00"}])]
    j = run_js({"cases": [{"cands": cands, "opts": {"topN": 10, "places": places, "when": {"dow": 2, "minutes": 600}}}]})[0]
    names = list(ranking.CRITERIA) + ["routine_m"]
    for c in cands:
        d = [ranking.haversine_m(c["lat"], c["lon"], p["lat"], p["lon"]) for p in places]
        c["routine_m"] = round(sum(p["weight"] * x for p, x in zip(places, d)) / sum(p["weight"] for p in places))
    X = np.array([[c[k] for k in names] for c in cands])
    N = np.zeros_like(X)
    for i, k in enumerate(names):
        lo, hi = X[:, i].min(), X[:, i].max()
        N[:, i] = (hi - X[:, i]) / (hi - lo) if k in ("drive_min", "walk_m", "pressure", "routine_m") else (X[:, i] - lo) / (hi - lo)
    sd = N.std(axis=0); R = np.corrcoef(N.T); w = sd * (1 - R).sum(axis=0); w = w / w.sum()
    cap = 0.25
    if w[-1] > cap:
        ex = w[-1] - cap; w[:-1] += ex * w[:-1] / w[:-1].sum(); w[-1] = cap
    order = [cands[i]["lot_id"] for i in np.argsort(-(N @ w), kind="stable")]
    ok = order == [x["lot_id"] for x in j["ranking"]] and abs(w[-1] - j["weights"]["routine_m"]) < 2e-4
    print(f"seven-criteria (routine) vs numpy: {'identical' if ok else 'DIFFERENT'}  routine weight={j['weights']['routine_m']}  capped={j['routine']['cap_applied']}")
    sys.exit(0 if (bad == 0 and ok) else 1)


main()
