"""A tag that is already parked in one lot arrives at another lot.   python -m unittest -v tests.test_duplicate"""
import os
import tempfile
import unittest

os.environ["PARKING_DB_URL"] = f"sqlite:///{os.path.join(tempfile.mkdtemp(), 'dup.db')}"

from fastapi.testclient import TestClient   # noqa: E402

from app.main import app                    # noqa: E402
from app.seed import reset_and_seed         # noqa: E402

DEV = {"X-Device-Key": "dev-device-key"}
ADM = {"X-Admin-Key": "dev-admin-key"}
TAG = "RFID_123"


class DuplicateEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cm = TestClient(app); cls.c = cls.cm.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.cm.__exit__(None, None, None)

    def setUp(self):
        reset_and_seed()

    def ev(self, gw, **body):
        r = self.c.post(f"/api/gateways/{gw}/events", headers=DEV, json=body)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def park_in_p02(self):
        self.ev("GW-P02", type="LANE_VEHICLE_DETECTED", lane_id="P02-ENT1")
        self.assertEqual(self.ev("GW-P02", type="LANE_RFID_READ", lane_id="P02-ENT1", uid=TAG)["lane_state"], "TICKET_SHOWN")
        self.ev("GW-P02", type="LANE_VEHICLE_CLEARED", lane_id="P02-ENT1")
        self.ev("GW-P02", type="SPACE_OCCUPANCY", space_code="A1", occupied=True, magnitude=1900)
        self.ev("GW-P02", type="SPACE_RFID_READ", space_code="A1", uid=TAG)

    def try_p01(self):
        self.ev("GW-P01", type="LANE_VEHICLE_DETECTED", lane_id="P01-ENT1")
        return self.ev("GW-P01", type="LANE_RFID_READ", lane_id="P01-ENT1", uid=TAG)

    def overview(self, lot):
        return self.c.get(f"/api/admin/overview?lot_id={lot}", headers=ADM).json()

    def test_second_lot_refuses_and_alerts_without_touching_first_session(self):
        self.park_in_p02()
        self.assertEqual(self.try_p01()["lane_state"], "BLOCKED")
        p01, p02 = self.overview("P01"), self.overview("P02")
        self.assertEqual(p01["sessions"], [])                                   # no session created at P01
        self.assertEqual([s["status"] for s in p02["sessions"]], ["PARKED"])    # P02 session untouched
        alert = next(a for a in p01["alerts"] if a["type"] == "DUPLICATE_ENTRY")
        self.assertEqual(alert["severity"], "CRITICAL")
        self.assertIn("lot P02", alert["message"])
        self.assertNotIn("OPEN_BARRIER", [c["type"] for c in self.c.get("/api/gateways/GW-P01/commands", headers=DEV).json()["commands"]])
        self.assertIn("already has an active parking session", self.c.get("/api/terminal/P01-ENT1").json()["message"])

    def test_admin_closes_stale_session_then_car_is_admitted(self):
        self.park_in_p02()
        self.ev("GW-P02", type="SPACE_OCCUPANCY", space_code="A1", occupied=False)   # left, exit never scanned
        self.assertEqual(self.try_p01()["lane_state"], "BLOCKED")
        ticket = self.overview("P02")["sessions"][0]["ticket_id"]
        r = self.c.post("/api/admin/manual-exit", headers=ADM, json={"ticket_id": ticket, "lane_id": "P02-EXIT1",
                        "manual_vehicle_check_confirmed": True, "note": "checked, car left"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.overview("P02")["sessions"], [])
        self.ev("GW-P01", type="LANE_VEHICLE_CLEARED", lane_id="P01-ENT1")
        self.assertEqual(self.try_p01()["lane_state"], "TICKET_SHOWN")
        self.assertEqual(self.overview("P01")["sessions"][0]["status"], "ASSIGNED")

    def test_blocked_tag_is_refused_even_when_alone(self):
        self.c.post("/api/admin/vehicles", headers=ADM, json={"id": "V001", "rfid": TAG, "active": False})
        self.assertEqual(self.try_p01()["lane_state"], "BLOCKED")
        self.assertTrue(any(a["type"] == "VEHICLE_DISABLED" for a in self.overview("P01")["alerts"]))

    # ---------------------------------------------------- administrator-approved visitor RFID card
    def refused_car_waiting(self):
        self.park_in_p02()
        self.assertEqual(self.try_p01()["lane_state"], "BLOCKED")        # the car stays at the gate

    def allow(self, headers=ADM, note="checked on site"):
        return self.c.post("/api/admin/lanes/P01-ENT1/allow-visitor-card", headers=headers, json={"note": note})

    def test_no_visitor_card_without_admin_approval(self):
        self.refused_car_waiting()
        r = self.c.post("/api/terminal/P01-ENT1/request-ticket")
        self.assertEqual(r.status_code, 409)
        self.assertFalse(self.c.get("/api/terminal/P01-ENT1").json()["show_request_button"])

    def test_approval_needs_admin_key_reason_and_a_waiting_duplicate(self):
        self.assertEqual(self.allow().status_code, 409)                  # nothing refused yet
        self.refused_car_waiting()
        self.assertEqual(self.allow(headers={}).status_code, 401)
        self.assertEqual(self.allow(note="x").status_code, 422)
        self.assertEqual(self.c.post("/api/admin/lanes/P01-EXIT1/allow-visitor-card", headers=ADM, json={"note": "abc"}).status_code, 409)

    def test_approved_car_gets_a_visitor_card_and_original_session_is_untouched(self):
        self.refused_car_waiting()
        r = self.allow()
        self.assertEqual(r.status_code, 200, r.text)
        t = self.c.get("/api/terminal/P01-ENT1").json()
        self.assertTrue(t["show_request_button"])
        self.assertEqual(self.c.post("/api/terminal/P01-ENT1/request-ticket").status_code, 200)
        cmds = [c["type"] for c in self.c.get("/api/gateways/GW-P01/commands", headers=DEV).json()["commands"]]
        self.assertIn("DISPENSE_CARD", cmds)
        # presenting the REGISTERED tag again does not work while a card is awaited
        self.assertEqual(self.ev("GW-P01", type="LANE_RFID_READ", lane_id="P01-ENT1", uid=TAG)["lane_state"], "AWAITING_CARD")
        card = next(c for c in self.overview("P01")["cards"] if c["status"] == "AVAILABLE")["uid"]
        self.assertEqual(self.ev("GW-P01", type="LANE_RFID_READ", lane_id="P01-ENT1", uid=card)["lane_state"], "TICKET_SHOWN")
        p01, p02 = self.overview("P01"), self.overview("P02")
        self.assertEqual(p01["sessions"][0]["category"], "VISITOR")
        self.assertEqual(p01["sessions"][0]["rfid"], card)
        self.assertEqual([s["status"] for s in p02["sessions"]], ["PARKED"])      # registered session untouched
        alert = next(a for a in p01["alerts"] if a["type"] == "DUPLICATE_ENTRY")  # still open, approval recorded
        self.assertIn("Visitor RFID card approved by the administrator: checked on site", alert["message"])

    def test_approval_is_one_shot(self):
        self.refused_car_waiting()
        self.allow()
        self.c.post("/api/terminal/P01-ENT1/request-ticket")
        card = next(c for c in self.overview("P01")["cards"] if c["status"] == "AVAILABLE")["uid"]
        self.ev("GW-P01", type="LANE_RFID_READ", lane_id="P01-ENT1", uid=card)
        self.ev("GW-P01", type="LANE_VEHICLE_CLEARED", lane_id="P01-ENT1")
        self.assertEqual(self.try_p01()["lane_state"], "BLOCKED")                 # same tag again: refused again
        self.assertEqual(self.allow().status_code, 200)                           # needs a NEW approval
        self.ev("GW-P01", type="LANE_VEHICLE_CLEARED", lane_id="P01-ENT1")
        self.assertEqual(self.allow().status_code, 409)                           # car gone: nothing to approve

    def test_visitor_leaves_with_the_card(self):
        self.refused_car_waiting()
        self.allow(); self.c.post("/api/terminal/P01-ENT1/request-ticket")
        card = next(c for c in self.overview("P01")["cards"] if c["status"] == "AVAILABLE")["uid"]
        self.ev("GW-P01", type="LANE_RFID_READ", lane_id="P01-ENT1", uid=card)
        self.ev("GW-P01", type="LANE_VEHICLE_CLEARED", lane_id="P01-ENT1")
        space = self.overview("P01")["sessions"][0]["space"]
        self.ev("GW-P01", type="SPACE_OCCUPANCY", space_code=space, occupied=True, magnitude=1900)
        self.ev("GW-P01", type="SPACE_RFID_READ", space_code=space, uid=card)
        self.assertEqual(self.overview("P01")["sessions"][0]["status"], "PARKED")
        self.ev("GW-P01", type="SPACE_OCCUPANCY", space_code=space, occupied=False)
        self.ev("GW-P01", type="LANE_VEHICLE_DETECTED", lane_id="P01-EXIT1")
        self.assertEqual(self.ev("GW-P01", type="LANE_RFID_READ", lane_id="P01-EXIT1", uid=card)["lane_state"], "AWAITING_CARD_RETURN")
        self.assertEqual(self.ev("GW-P01", type="CARD_RETURNED", lane_id="P01-EXIT1", uid=card)["lane_state"], "EXIT_AUTHORIZED")
        self.assertEqual(self.overview("P01")["sessions"], [])
        self.assertEqual([s["status"] for s in self.overview("P02")["sessions"]], ["PARKED"])


if __name__ == "__main__":
    unittest.main()
