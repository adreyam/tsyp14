"""Training core: tabular Q-learning for the signals of a grid, emergency priority switched on, with a saved history.

Everything that makes a run resumable lives in the run folder (default runs/main):
    checkpoint.pkl     Q-tables of every signal, episode counter, random-number state, settings (saved every few episodes,
                       on Ctrl+C, and at the end; checkpoint.prev.pkl is the previous good copy)
    best.pkl           the Q-tables with the best score on the held-out validation scenarios
    history.jsonl      one line per training episode (wait, reward, ambulance metrics, epsilon, ...)
    evals.jsonl        one line per evaluation: the agent against the fixed-time signals on the validation scenarios
    baseline.json      the fixed-time results (computed once, reused)
Start the same command again and training continues from the saved episode with the saved Q-tables and epsilon.
"""

import csv
import json
import os
import pickle
import random
import sys
import time
import xml.etree.ElementTree as ET
from statistics import mean

import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import sumo_rl  # noqa: E402
from sumo_rl import PreemptionConfig  # noqa: E402

DEFAULTS = dict(
    net_file="sumo_rl/nets/2x2grid/2x2.net.xml",
    data_dir="data/mock_traffic",
    episode_seconds=1200,
    delta_time=5,
    yellow_time=2,
    min_green=5,
    max_green=50,
    alpha=0.15,        # learning rate
    gamma=0.95,        # discount
    eps_start=1.0,     # exploration at episode 0 ...
    eps_min=0.03,      # ... decays per episode down to this
    eps_decay=0.97,
    emergency_priority=True,
    seed=1,
    n_eval=3,          # validation scenarios per evaluation
)
KEEP_ON_RESUME = ("net_file", "episode_seconds", "delta_time", "yellow_time", "min_green", "max_green", "emergency_priority")


class TabularQ:
    """One Q-table (state -> value of each green phase) for one signal."""

    def __init__(self, n_actions, alpha, gamma):
        self.n, self.alpha, self.gamma, self.q = n_actions, alpha, gamma, {}

    def values(self, s):
        v = self.q.get(s)
        if v is None:
            v = self.q[s] = np.zeros(self.n)
        return v

    def act(self, s, eps, rng):
        if eps > 0 and rng.random() < eps:
            return int(rng.integers(self.n))
        v = self.values(s)
        return int(rng.choice(np.flatnonzero(v == v.max())))

    def learn(self, s, a, r, s2):
        v = self.values(s)
        v[a] += self.alpha * (r + self.gamma * self.values(s2).max() - v[a])


def queue_bucket(x):
    return 0 if x <= 0 else 1 if x < 0.15 else 2 if x < 0.35 else 3


def make_encoder(env):
    """State = (current green phase, min-green passed?, queue level 0-3 on each approach road)."""
    groups = {}
    for ts_id, ts in env.traffic_signals.items():
        by_edge = {}
        for i, lane in enumerate(ts.lanes):
            by_edge.setdefault(lane.rsplit("_", 1)[0], []).append(i)
        groups[ts_id] = list(by_edge.values())

    def encode(ts_id, obs):
        ts = env.traffic_signals[ts_id]
        n, L = ts.num_green_phases, len(ts.lanes)
        queue = obs[n + 1 + L : n + 1 + 2 * L]
        return (int(np.argmax(obs[:n])), int(obs[n]), *[queue_bucket(float(np.mean(queue[g]))) for g in groups[ts_id]])

    return encode


def parse_tripinfo(path):
    amb, other = [], []
    if os.path.exists(path):
        for t in ET.parse(path).getroot().iter("tripinfo"):
            rec = {k: float(t.get(k)) for k in ("duration", "timeLoss", "waitingTime", "waitingCount")}
            (amb if t.get("id").startswith("amb") else other).append(rec)
    return amb, other


def trip_metrics(path):
    amb, other = parse_tripinfo(path)
    m = lambda rows, k: round(mean(r[k] for r in rows), 2) if rows else None  # noqa: E731
    return dict(arrived=len(other), mean_wait=m(other, "waitingTime"), mean_timeloss=m(other, "timeLoss"), mean_trip=m(other, "duration"),
                amb_n=len(amb), amb_wait=m(amb, "waitingTime"), amb_stops=m(amb, "waitingCount"), amb_trip=m(amb, "duration"))


def read_jsonl(path):
    rows = []
    if os.path.exists(path):
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        pass  # a half-written last line after a crash
    return rows


def append_jsonl(path, row):
    with open(path, "a") as fh:
        fh.write(json.dumps(row) + "\n")


class Trainer:
    def __init__(self, run_dir, overrides=None, fresh=False, live=None, gui=False, log=print):
        self.run_dir = run_dir
        self.name = os.path.basename(os.path.normpath(run_dir))
        self.live, self.gui, self.log = live, gui, log
        os.makedirs(run_dir, exist_ok=True)
        self.paths = {k: os.path.join(run_dir, v) for k, v in dict(ckpt="checkpoint.pkl", prev="checkpoint.prev.pkl", best="best.pkl", hist="history.jsonl",
                                                                  evals="evals.jsonl", base="baseline.json").items()}
        os.makedirs(".tmp_trips", exist_ok=True)  # relative path on purpose: SUMO is started with a command line that must not contain spaces
        if fresh:
            for p in self.paths.values():
                if os.path.exists(p):
                    os.remove(p)
        self.cfg = dict(DEFAULTS)
        self.episode, self.resumed_from, self.best_score, self.q_tables, self.rng_state = 0, None, None, None, None
        if os.path.exists(self.paths["ckpt"]):
            self._load_checkpoint(overrides or {})
        else:
            self.cfg.update({k: v for k, v in (overrides or {}).items() if v is not None})
        self.rng = np.random.default_rng(self.cfg["seed"])
        if self.rng_state is not None:
            self.rng.bit_generator.state = self.rng_state
        self.history = read_jsonl(self.paths["hist"])
        self.evals = read_jsonl(self.paths["evals"])
        self.history = [h for h in self.history if h["episode"] <= self.episode]  # drop lines of an episode that was not checkpointed
        self.evals = [e for e in self.evals if e["episode"] <= self.episode]
        self._rewrite_logs()
        self.manifest = self._read_manifest()
        self.splits = {s: [m for m in self.manifest if m["split"] == s] for s in ("train", "val", "test")}
        if not self.splits["train"]:
            raise SystemExit(f"No training scenarios in {self.cfg['data_dir']}. Run: python training/mock_data.py")
        self.env = self._make_env(fixed=False)
        self.env_fixed = None
        self.encode = make_encoder(self.env)
        self.agents = {ts: TabularQ(self.env.traffic_signals[ts].num_green_phases, self.cfg["alpha"], self.cfg["gamma"]) for ts in self.env.ts_ids}
        if self.q_tables:
            for ts, q in self.q_tables.items():
                if ts in self.agents:
                    self.agents[ts].q = q
        if live:
            live.attach(self.env, self)
        self.session_start_episode = self.episode

    # ------------------------------------------------------------ setup helpers
    def _read_manifest(self):
        path = os.path.join(self.cfg["data_dir"], "manifest.csv")
        if not os.path.exists(path):
            raise SystemExit(f"{path} not found. Create the mock data first: python training/mock_data.py")
        with open(path) as fh:
            return list(csv.DictReader(fh))

    def _rewrite_logs(self):
        for key, rows in (("hist", self.history), ("evals", self.evals)):
            with open(self.paths[key], "w") as fh:
                for r in rows:
                    fh.write(json.dumps(r) + "\n")

    def _make_env(self, fixed):
        c = self.cfg
        pre = PreemptionConfig() if c["emergency_priority"] else None
        env = sumo_rl.SumoEnvironment(
            net_file=c["net_file"], route_file=self.splits_first_file(),
            use_gui=self.gui and not fixed, num_seconds=c["episode_seconds"], delta_time=c["delta_time"], yellow_time=c["yellow_time"],
            min_green=c["min_green"], max_green=c["max_green"], fixed_ts=fixed, sumo_warnings=False, emergency_preemption=pre,
            additional_sumo_cmd="--no-step-log",
        )
        return env

    def splits_first_file(self):
        return self._read_manifest()[0]["file"]

    def _load_checkpoint(self, overrides):
        try:
            with open(self.paths["ckpt"], "rb") as fh:
                data = pickle.load(fh)
        except Exception as e:  # a corrupt file: fall back to the previous good copy
            self.log(f"checkpoint.pkl could not be read ({e}); trying checkpoint.prev.pkl")
            with open(self.paths["prev"], "rb") as fh:
                data = pickle.load(fh)
        self.cfg.update(data["config"])
        changed = [k for k in KEEP_ON_RESUME if overrides.get(k) is not None and overrides[k] != self.cfg[k]]
        if changed:
            raise SystemExit(f"These settings define the saved model and cannot change on resume: {changed}. Use --fresh to start a new run.")
        self.cfg.update({k: v for k, v in overrides.items() if v is not None})
        self.episode, self.q_tables, self.rng_state = data["episode"], data["q"], data["rng"]
        self.best_score, self.resumed_from = data.get("best_score"), data["episode"]

    # ------------------------------------------------------------------ saving
    def save_checkpoint(self):
        data = dict(version=1, config=self.cfg, episode=self.episode, q={ts: a.q for ts, a in self.agents.items()},
                    rng=self.rng.bit_generator.state, best_score=self.best_score, saved_at=time.time())
        tmp = self.paths["ckpt"] + ".tmp"
        with open(tmp, "wb") as fh:
            pickle.dump(data, fh, protocol=pickle.HIGHEST_PROTOCOL)
        if os.path.exists(self.paths["ckpt"]):
            os.replace(self.paths["ckpt"], self.paths["prev"])
        os.replace(tmp, self.paths["ckpt"])

    def save_best(self):
        with open(self.paths["best"], "wb") as fh:
            pickle.dump(dict(config=self.cfg, episode=self.episode, q={ts: a.q for ts, a in self.agents.items()}, score=self.best_score), fh)

    # -------------------------------------------------------------- schedules
    def epsilon(self, ep):
        c = self.cfg
        return max(c["eps_min"], c["eps_start"] * c["eps_decay"] ** ep)

    def pick(self, ep):
        train = self.splits["train"]
        epoch, i = divmod(ep, len(train))
        order = list(range(len(train)))
        random.Random(self.cfg["seed"] * 7919 + epoch).shuffle(order)
        return train[order[i]]

    @property
    def q_states(self):
        return sum(len(a.q) for a in self.agents.values())

    # ---------------------------------------------------------------- episodes
    def _trip_path(self, label):
        return f".tmp_trips/{self.name}_{label}.xml".replace(" ", "_")

    def run_episode(self, scenario, seed, eps, learn, label="train"):
        env = self.env
        trip = self._trip_path(label)
        env.additional_sumo_cmd = f"--tripinfo-output {trip} --no-step-log"
        env._route = scenario["file"]
        if self.live:
            self.live.begin_episode(self, scenario, label, eps)
        obs = env.reset(seed=seed)
        state = {ts: self.encode(ts, obs[ts]) for ts in env.ts_ids}
        done, reward_sum, steps, skipped, waits = False, 0.0, 0, 0, []
        t0 = time.time()
        while not done:
            acts, was_pre = {}, {}
            for ts in env.ts_ids:
                was_pre[ts] = env.traffic_signals[ts].preempted
                acts[ts] = self.agents[ts].act(state[ts], eps if learn or label == "explore" else 0.0, self.rng)
            obs, rew, dones, info = env.step(acts)
            done = dones["__all__"]
            steps += 1
            waits.append(info.get("system_mean_waiting_time", 0.0))
            for ts in obs:
                s2 = self.encode(ts, obs[ts])
                # while an emergency vehicle holds the signal the agent's action was ignored: nothing to learn from that step
                if learn and not was_pre[ts] and not env.traffic_signals[ts].preempted:
                    self.agents[ts].learn(state[ts], acts[ts], rew[ts], s2)
                elif learn:
                    skipped += 1
                state[ts] = s2
                reward_sum += float(rew[ts])
        pre = env.preemption
        starts = sum(e["event"] == "PREEMPT_START" for e in pre.events) if pre else 0
        env.close()
        m = trip_metrics(trip)
        m.update(reward=round(reward_sum, 2), steps=steps, preemptions=starts, skipped_updates=skipped, seconds=round(time.time() - t0, 1),
                 avg_system_wait=round(float(np.mean(waits)), 2))
        if self.live:
            self.live.end_episode()
        return m

    def run_fixed(self, scenario, seed):
        if self.env_fixed is None:
            self.env_fixed = self._make_env(fixed=True)
        env = self.env_fixed
        trip = self._trip_path("fixed")
        env.additional_sumo_cmd = f"--tripinfo-output {trip} --no-step-log"
        env._route = scenario["file"]
        env.reset(seed=seed)
        done = False
        while not done:
            _, _, dones, _ = env.step(None)
            done = dones["__all__"]
        env.close()
        return trip_metrics(trip)

    # -------------------------------------------------------------- evaluation
    def eval_set(self):
        val = self.splits["val"] or self.splits["train"]
        return [(val[i % len(val)], 50_000 + i) for i in range(self.cfg["n_eval"])]

    def baseline(self):
        if os.path.exists(self.paths["base"]):
            with open(self.paths["base"]) as fh:
                b = json.load(fh)
            if b.get("key") == [self.cfg["net_file"], self.cfg["episode_seconds"], [s["scenario"] for s, _ in self.eval_set()]]:
                return b
        self.log("Measuring the fixed-time signals on the validation scenarios (once)...")
        rows = [self.run_fixed(s, seed) for s, seed in self.eval_set()]
        b = dict(key=[self.cfg["net_file"], self.cfg["episode_seconds"], [s["scenario"] for s, _ in self.eval_set()]], rows=rows, summary=self._summ(rows))
        with open(self.paths["base"], "w") as fh:
            json.dump(b, fh)
        return b

    @staticmethod
    def _summ(rows):
        out = {}
        for k in ("mean_wait", "mean_timeloss", "amb_wait", "amb_stops", "arrived"):
            vals = [r[k] for r in rows if r.get(k) is not None]
            out[k] = round(mean(vals), 2) if vals else None
        return out

    def evaluate(self):
        base = self.baseline()["summary"]
        rows = []
        for i, (s, seed) in enumerate(self.eval_set()):
            rows.append(self.run_episode(s, seed, 0.0, learn=False, label="eval"))
        agent = self._summ(rows)
        ev = dict(episode=self.episode, agent=agent, fixed=base, improvement_pct=round(100 * (base["mean_wait"] - agent["mean_wait"]) / base["mean_wait"], 1)
                  if base["mean_wait"] else None, time=time.time())
        self.evals.append(ev)
        append_jsonl(self.paths["evals"], ev)
        if agent["mean_wait"] is not None and (self.best_score is None or agent["mean_wait"] < self.best_score):
            self.best_score = agent["mean_wait"]
            self.save_best()
            ev["best"] = True
        self.log(f"  evaluation @ episode {self.episode}: agent wait {agent['mean_wait']} s vs fixed-time {base['mean_wait']} s "
                 f"({ev['improvement_pct']:+} %), ambulance wait {agent['amb_wait']} s vs {base['amb_wait']} s")
        if self.live:
            self.live.publish(self)
        return ev

    # ---------------------------------------------------------------- training
    def train(self, n_episodes, eval_every=10, save_every=2, stop=lambda: False):
        if self.live:
            self.live.set_target(self.episode, n_episodes)
        if not self.evals and eval_every:
            self.evaluate()
        t_start, last_save = time.time(), self.episode
        try:
            for k in range(n_episodes):
                if stop():
                    break
                ep = self.episode
                scenario, eps = self.pick(ep), self.epsilon(ep)
                m = self.run_episode(scenario, self.cfg["seed"] * 100_000 + ep, eps, learn=True)
                row = dict(episode=ep + 1, scenario=scenario["scenario"], profile=scenario["profile"], epsilon=round(eps, 4), q_states=self.q_states,
                           finished_at=time.time(), **m)
                self.episode += 1
                self.history.append(row)
                append_jsonl(self.paths["hist"], row)
                done_k = k + 1
                eta = (time.time() - t_start) / done_k * (n_episodes - done_k)
                self.log(f"episode {self.episode:5d} | {scenario['profile']:13s} eps {eps:.3f} | wait {m['mean_wait']} s | ambulance wait {m['amb_wait']} s "
                         f"| priority x{m['preemptions']} | Q states {self.q_states} | {m['seconds']} s | session {done_k}/{n_episodes}, ETA {eta/60:.1f} min")
                if save_every and self.episode % save_every == 0:
                    self.save_checkpoint()
                if eval_every and self.episode % eval_every == 0:
                    self.evaluate()
                    self.save_checkpoint()
                if self.live:
                    self.live.publish(self)
        except KeyboardInterrupt:
            self.log("\nStopped by you. Saving the training so far...")
            try:
                self.env.close()
            except Exception:
                pass
        finally:
            self.save_checkpoint()
            self.log(f"Saved: {self.paths['ckpt']} (episode {self.episode}). Run the same command again to continue from here.")

    def close(self):
        for e in (self.env, self.env_fixed):
            try:
                if e is not None:
                    e.close()
            except Exception:
                pass
