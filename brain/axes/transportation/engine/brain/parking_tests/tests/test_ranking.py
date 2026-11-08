"""Tests for the Top-3 ranking.   From the backend folder:   python -m unittest -v tests.test_ranking

Needs `pip install httpx` (FastAPI's test client). Uses its own throw-away database.
"""
import os
import tempfile
import unittest
from datetime import timedelta

_DB = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["PARKING_DB_URL"] = f"sqlite:///{_DB}"
os.environ["PARKING_RANK_DEMO_EXTRA_LOTS"] = "1"

from fastapi.testclient import TestClient            # noqa: E402

from app.database import SessionLocal                # noqa: E402
from app.main import app                             # noqa: E402
from app.seed import reset_and_seed                  # noqa: E402
from app.services import forecast, ranking          # noqa: E402
from app.services.common import now                  # noqa: E402

DEST = {"lat": 36.8000, "lon": 10.1750}
ORIGIN = {"lat": 36.8200, "lon": 10.2100}


def cand(lot_id, **kw):
    base = dict(lot_id=lot_id, drive_min=10, walk_m=300, free_now=10, free_30=10, pressure=0.1, reputation=0.5)
    base.update(kw)
    return base


class MathTests(unittest.TestCase):
    def test_min_max_flips_cost(self):
        self.assertEqual(ranking.min_max([1, 2, 3], cost=True), [1.0, 0.5, 0.0])
        self.assertEqual(ranking.min_max([1, 2, 3], cost=False), [0.0, 0.5, 1.0])
        self.assertEqual(ranking.min_max([4, 4, 4], cost=False), [0.5, 0.5, 0.5])

    def test_critic_weights(self):
        cols = {"a": [0, .5, 1, .2], "b": [0, .5, 1, .2],           # identical -> share the weight
                "c": [1, 0, .3, .9], "d": [.5, .5, .5, .5]}         # constant -> weight 0
        w = ranking.critic_weights(cols)
        self.assertAlmostEqual(sum(w.values()), 1.0)
        self.assertEqual(w["d"], 0.0)
        self.assertAlmostEqual(w["a"], w["b"])
        self.assertGreater(w["c"], w["a"])                           # the independent criterion counts more

    def test_reputation_is_pulled_to_neutral(self):
        self.assertEqual(ranking.shrunk_reputation(0, 0), 0.5)
        self.assertAlmostEqual(ranking.shrunk_reputation(5, 1), 0.55)     # one 5-star review barely moves it
        self.assertAlmostEqual(ranking.shrunk_reputation(50, 10), 1.0)    # ten 5-star reviews: full effect
        self.assertAlmostEqual(ranking.shrunk_reputation(10, 10), 0.0)

    def test_dominant_lot_wins_and_top_n(self):
        lots = [cand("A"), cand("B", drive_min=5, walk_m=100, free_now=30, free_30=30, pressure=0.0, reputation=0.9),
                cand("C", drive_min=20), cand("D", free_now=2, free_30=1), cand("E", walk_m=900)]
        r = ranking.rank_lots(lots)
        self.assertEqual(len(r["ranking"]), 3)
        self.assertEqual(r["ranking"][0]["lot_id"], "B")
        self.assertEqual(r["weights_source"], "critic")
        for x in r["ranking"]:
            self.assertAlmostEqual(sum(x["contributions"].values()), x["score"], places=3)
            self.assertTrue(0 <= x["score"] <= 1)

    def test_few_candidates_use_default_weights(self):
        r = ranking.rank_lots([cand("A"), cand("B", drive_min=3)])
        self.assertEqual(r["weights_source"], "default")
        self.assertEqual(r["ranking"][0]["lot_id"], "B")

    def test_identical_lots_do_not_crash(self):
        r = ranking.rank_lots([cand("A"), cand("B"), cand("C")])
        self.assertEqual([x["lot_id"] for x in r["ranking"]], ["A", "B", "C"])   # deterministic tie-break

    def test_bad_values_rejected(self):
        with self.assertRaises(ValueError):
            ranking.rank_lots([cand("A", walk_m=float("nan"))])

    def test_empty(self):
        self.assertEqual(ranking.rank_lots([])["ranking"], [])


class ForecastTests(unittest.TestCase):
    def setUp(self):
        reset_and_seed()
        self.db = SessionLocal()

    def tearDown(self):
        self.db.close()

    def test_levels(self):
        r, lvl, _ = forecast.predict_occupancy_rate(self.db, "P03", 0.4, 30)
        self.assertEqual((round(r, 2), lvl), (0.4, 2))                      # no history -> persistence
        r, lvl, _ = forecast.predict_occupancy_rate(self.db, "P03", None, 30)
        self.assertEqual((r, lvl), (0.5, 4))                                # nothing at all -> 50 %

    def test_seasonal_change_level_1(self):
        t = now().replace(hour=8, minute=0, second=0, microsecond=0)
        for week in range(1, 5):                                            # same weekday, previous weeks
            for k in range(8):
                d = t - timedelta(weeks=week)
                forecast.record_snapshot(self.db, "P03", 10, 8, d + timedelta(minutes=k))          # 08:0x -> 20 % occupied
                forecast.record_snapshot(self.db, "P03", 10, 2, d + timedelta(minutes=30 + k))     # 08:3x -> 80 % occupied
        self.db.commit()
        r, lvl, _ = forecast.predict_occupancy_rate(self.db, "P03", 0.3, 30, at=t + timedelta(minutes=3))
        self.assertEqual(lvl, 1)
        self.assertAlmostEqual(r, 0.9, delta=0.02)                          # 0.3 + (0.8 - 0.2) = 0.9

    def test_lot_average_level_3(self):
        forecast.record_snapshot(self.db, "P03", 10, 5)
        forecast.record_snapshot(self.db, "P03", 10, 1)
        self.db.commit()
        r, lvl, _ = forecast.predict_occupancy_rate(self.db, "P03", None, 30)
        self.assertEqual(lvl, 3)
        self.assertAlmostEqual(r, 0.7)


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_cm = TestClient(app)
        cls.client = cls.client_cm.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_cm.__exit__(None, None, None)

    def setUp(self):
        reset_and_seed()

    def rec(self, **kw):
        body = {"origin": ORIGIN, "destination": DEST, **kw}
        return self.client.post("/api/city/recommend", json=body)

    def test_returns_three_ranked_lots_with_explanation(self):
        r = self.rec()
        self.assertEqual(r.status_code, 200)
        j = r.json()
        self.assertEqual([x["rank"] for x in j["ranking"]], [1, 2, 3])
        self.assertEqual(j["weights_source"], "critic")
        self.assertAlmostEqual(sum(j["weights"].values()), 1.0, places=2)
        top = j["ranking"][0]
        for key in ("score", "marks", "contributions", "drive_min", "walk_m", "free_now", "free_30", "details"):
            self.assertIn(key, top)
        self.assertEqual(top["details"]["forecast_level"], 2)               # fresh database: no history yet

    def test_full_lot_is_never_recommended(self):
        # P01 has 3 spaces: reserve all 3 -> full for the next driver
        for _ in range(3):
            self.assertEqual(self.client.post("/api/city/reserve", json={"lot_id": "P01", "eta_min": 5}).status_code, 200)
        self.assertEqual(self.client.post("/api/city/reserve", json={"lot_id": "P01", "eta_min": 5}).status_code, 409)
        j = self.rec(top_n=10).json()
        self.assertNotIn("P01", [x["lot_id"] for x in j["ranking"]])
        self.assertIn("P01", [x["lot_id"] for x in j["excluded"]])

    def test_competition_lowers_a_lot(self):
        before = {x["lot_id"]: x for x in self.rec(top_n=10).json()["ranking"]}
        for _ in range(4):
            self.client.post("/api/city/reserve", json={"lot_id": "P03", "eta_min": 5})
        after = {x["lot_id"]: x for x in self.rec(top_n=10).json()["ranking"]}
        self.assertGreater(after["P03"]["pressure"], before["P03"]["pressure"])
        self.assertEqual(after["P03"]["details"]["drivers_heading_there"], 4)
        self.assertLess(after["P03"]["free_30"], before["P03"]["free_30"])

    def test_cancel_and_redirect(self):
        rid = self.client.post("/api/city/reserve", json={"lot_id": "P03", "eta_min": 5}).json()["reservation_id"]
        self.assertEqual(self.client.post(f"/api/city/reservations/{rid}/cancel").json()["status"], "CANCELLED")
        j = self.rec(top_n=10).json()
        self.assertEqual(next(x for x in j["ranking"] if x["lot_id"] == "P03")["details"]["drivers_heading_there"], 0)
        first = self.rec().json()["ranking"][0]["lot_id"]
        redirected = self.rec(exclude=[first]).json()
        self.assertNotIn(first, [x["lot_id"] for x in redirected["ranking"]])

    def test_arrival_clears_the_reservation(self):
        self.client.post("/api/city/reserve", json={"lot_id": "P01", "eta_min": 5})
        hdr = {"X-Device-Key": "dev-device-key"}
        self.client.post("/api/gateways/GW-P01/events", headers=hdr, json={"type": "LANE_VEHICLE_DETECTED", "lane_id": "P01-ENT1"})
        self.client.post("/api/gateways/GW-P01/events", headers=hdr, json={"type": "LANE_RFID_READ", "lane_id": "P01-ENT1", "uid": "RFID_123"})
        lots = self.rec(top_n=10).json()["ranking"]
        p01 = next(x for x in lots if x["lot_id"] == "P01")
        self.assertEqual(p01["details"]["drivers_heading_there"], 0)         # the reservation became ARRIVED
        self.assertEqual(p01["free_now"], 2.0)                               # one space is now taken

    def test_feedback_moves_reputation_slowly(self):
        def rep():
            lots = self.rec(top_n=10).json()["ranking"]
            return next(x for x in lots if x["lot_id"] == "P03")["reputation"]
        a = rep()
        self.client.post("/api/city/lots/P03/feedback", json={"rating": 1})
        self.assertLess(rep(), a)
        self.assertEqual(self.client.post("/api/city/lots/P03/feedback", json={"rating": 9}).status_code, 422)

    def test_far_lot_is_dropped_by_walk_limit(self):
        j = self.rec(top_n=10).json()
        self.assertNotIn("P05", [x["lot_id"] for x in j["ranking"]])       # ~5.9 km from DEST
        self.assertTrue(any(x["lot_id"] == "P05" and "too far" in x["reason"] for x in j["excluded"]))
        j = self.rec(top_n=10, max_walk_m=20000).json()
        self.assertIn("P05", [x["lot_id"] for x in j["ranking"]])

    def test_candidates_endpoint_is_unranked_lot_data_only(self):
        r = self.client.post("/api/city/candidates", json={"origin": ORIGIN, "destination": DEST})
        self.assertEqual(r.status_code, 200)
        j = r.json()
        self.assertGreaterEqual(len(j["candidates"]), 3)
        c = j["candidates"][0]
        for key in ("lot_id", "lat", "lon", "drive_min", "walk_m", "free_now", "free_30", "pressure", "reputation"):
            self.assertIn(key, c)
        self.assertNotIn("score", c)                      # ranking is done in the car
        self.assertNotIn("ranking", j)
        self.assertEqual(self.client.post("/api/city/candidates", json={"origin": {"lat": 99, "lon": 0}, "destination": DEST}).status_code, 422)

    def test_input_validation(self):
        self.assertEqual(self.rec(origin={"lat": 99, "lon": 0}).status_code, 422)
        self.assertEqual(self.rec(top_n=0).status_code, 422)
        self.assertEqual(self.rec(weights={"drive_min": -1}).status_code, 422)
        self.assertEqual(self.client.post("/api/city/reserve", json={"lot_id": "NOPE", "eta_min": 1}).status_code, 404)

    def test_offline_gateway_can_be_excluded(self):
        j = self.rec(require_online=True, top_n=10).json()
        self.assertEqual(j["ranking"], [])                                   # no gateway has reported yet
        self.assertTrue(all("offline" in x["reason"] for x in j["excluded"]))

    def test_snapshot_endpoint_needs_admin_key(self):
        self.assertEqual(self.client.post("/api/city/snapshots").status_code, 401)
        r = self.client.post("/api/city/snapshots", headers={"X-Admin-Key": "dev-admin-key"})
        self.assertEqual(r.json()["lots_recorded"], 5)


if __name__ == "__main__":
    unittest.main()
