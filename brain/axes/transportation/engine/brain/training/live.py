"""Live view of the training: a small web server (standard library only) that shows the simulation as it is being trained.

The training thread publishes a snapshot after every simulated second (vehicle positions, signal colours, who has priority) and
after every episode (history and evaluations). The browser page polls them. The page can also pause, change the speed or ask for
"save and stop".
"""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import sumolib

HERE = os.path.dirname(os.path.abspath(__file__))


class LiveView:
    def __init__(self, net_file, port=8765, speed=20.0):
        self.port, self.speed, self.paused, self.stop_requested = port, float(speed), False, False
        self.lock = threading.Lock()
        self.snapshot = json.dumps({"vehicles": [], "t": 0, "signals": {}, "phase": "starting"})
        self.hist, self.evals = [], []
        self.meta = dict(episode=0, target=0, scenario="", profile="", label="", epsilon=1.0, q_states=0, run="", resumed_from=None, emergency=True)
        self.messages, self.events = [], []
        self.map_json = json.dumps(self._map(net_file))
        self.env = None
        self.trainer = None
        self._tick = 0
        self._server = None

    # ----------------------------------------------------------------- map
    @staticmethod
    def _map(net_file):
        net = sumolib.net.readNet(net_file, withInternal=False)
        edges = [{"id": e.getID(), "shape": [[round(x, 1), round(y, 1)] for x, y in e.getShape()], "lanes": e.getLaneNumber()} for e in net.getEdges()]
        tls = [{"id": n.getID(), "x": round(n.getCoord()[0], 1), "y": round(n.getCoord()[1], 1)} for n in net.getNodes() if n.getType() == "traffic_light"]
        xs = [p[0] for e in edges for p in e["shape"]]
        ys = [p[1] for e in edges for p in e["shape"]]
        return dict(edges=edges, tls=tls, bbox=[min(xs), min(ys), max(xs), max(ys)])

    # ------------------------------------------------------ hooks from trainer
    def attach(self, env, trainer):
        self.env, self.trainer = env, trainer
        self.meta.update(run=trainer.name, resumed_from=trainer.resumed_from, emergency=bool(trainer.cfg["emergency_priority"]))
        self.hist, self.evals = trainer.history, trainer.evals
        original = env._sumo_step

        def paced_step():
            original()
            self._after_step()

        env._sumo_step = paced_step

    def set_target(self, episode, n):
        self.meta.update(episode=episode, target=episode + n)

    def begin_episode(self, trainer, scenario, label, eps):
        self.meta.update(episode=trainer.episode + (1 if label == "train" else 0), scenario=scenario["scenario"], profile=scenario["profile"], label=label,
                         epsilon=round(eps, 3), q_states=trainer.q_states)
        self.messages, self.events = [], []

    def end_episode(self):
        pass

    def publish(self, trainer):
        self.meta.update(q_states=trainer.q_states, saved_episode=trainer.episode)

    # ---------------------------------------------------------- per-step work
    def _after_step(self):
        while self.paused and not self.stop_requested:
            time.sleep(0.1)
        env = self.env
        sumo = getattr(env, "sumo", None)
        if sumo is None:
            return
        try:
            self._tick += 1
            ids = sumo.vehicle.getIDList()
            vehicles = []
            for v in ids:
                x, y = sumo.vehicle.getPosition(v)
                kind = sumo.vehicle.getTypeID(v)
                vehicles.append([round(x, 1), round(y, 1), round(sumo.vehicle.getAngle(v)), "A" if v.startswith("amb") else ("T" if "truck" in kind else "C"),
                                 round(sumo.vehicle.getSpeed(v), 1)])
            signals = {}
            pre = env.preemption
            for ts_id, ts in env.traffic_signals.items():
                signals[ts_id] = dict(state=sumo.trafficlight.getRedYellowGreenState(ts_id), priority=bool(ts.preempted),
                                      stage=pre.controllers[ts_id].mode if pre is not None and ts_id in pre.controllers else None)
            if pre is not None:
                self.messages = pre.message_log[-8:]
                self.events = pre.events[-8:]
            snap = dict(t=sumo.simulation.getTime(), vehicles=vehicles, signals=signals, phase="running", halting=sum(1 for v in vehicles if v[4] < 0.1),
                        sent=getattr(pre, "messages_sent", 0), lost=getattr(pre, "messages_lost", 0), messages=_jsonable(self.messages), events=_jsonable(self.events))
            with self.lock:
                self.snapshot = json.dumps(snap, default=str)
        except Exception:
            return  # the connection is closing between episodes
        if self.speed > 0:
            time.sleep(1.0 / self.speed)

    # ------------------------------------------------------------------ server
    def start(self):
        view = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, body, ctype="application/json"):
                data = body.encode() if isinstance(body, str) else body
                self.send_response(200)
                self.send_header("Content-Type", ctype + "; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                p = self.path.split("?")[0]
                if p in ("/", "/index.html"):
                    with open(os.path.join(HERE, "dashboard.html"), "rb") as fh:
                        self._send(fh.read(), "text/html")
                elif p == "/map":
                    self._send(view.map_json)
                elif p == "/snapshot":
                    with view.lock:
                        self._send(view.snapshot)
                elif p == "/progress":
                    tr = view.trainer
                    self._send(json.dumps(dict(meta=view.meta, speed=view.speed, paused=view.paused, stopping=view.stop_requested, hist=view.hist[-400:],
                                               evals=view.evals, baseline=(tr.baseline()["summary"] if tr and os.path.exists(tr.paths["base"]) else None)), default=str))
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    cmd = json.loads(self.rfile.read(n) or b"{}")
                except ValueError:
                    cmd = {}
                if "speed" in cmd:
                    view.speed = max(0.0, float(cmd["speed"]))
                if "paused" in cmd:
                    view.paused = bool(cmd["paused"])
                if cmd.get("stop"):
                    view.stop_requested = True
                self._send("{}")

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.port}/"

    def stop(self):
        if self._server:
            self._server.shutdown()


def _jsonable(rows):
    return json.loads(json.dumps(rows, default=str))
