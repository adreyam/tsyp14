"""Online learning of the traffic signals inside the live city.

One Q-table is shared by all signals (they are the same kind of junction, so every signal's experience improves
the same table: 36x more learning data per minute than one table per signal). The table STARTS from the training saved
in runs/main (the 2x2 grid, 150 episodes): its four per-signal tables are merged into the shared table.

Time is cut into "training episodes" of WINDOW_S simulated seconds (the city never restarts). At the end of each
window: epsilon decays, a line is appended to runs/city/history.jsonl and the checkpoint is saved. Restarting the
program loads runs/city/checkpoint.pkl and continues from there.
"""
import json
import os
import pickle
import time
from statistics import mean

import numpy as np

from training.core import TabularQ, append_jsonl, make_encoder, read_jsonl

WINDOW_S = 600
EPS_WARM, EPS_COLD, EPS_MIN, EPS_DECAY = 0.15, 1.0, 0.02, 0.985
ALPHA, GAMMA = 0.15, 0.95
SAVE_EVERY = 2


def merge_tables(tables):
    """Average the action values of states that appear in several tables."""
    merged, count = {}, {}
    for t in tables:
        for s, v in t.items():
            if s in merged:
                merged[s] = merged[s] + v
                count[s] += 1
            else:
                merged[s], count[s] = np.array(v, dtype=float), 1
    return {s: v / count[s] for s, v in merged.items()}


class OnlineLearner:
    def __init__(self, env, run_dir="runs/city", warm_from="runs/main", fresh=False, log=print):
        self.env, self.run_dir, self.log = env, run_dir, log
        os.makedirs(run_dir, exist_ok=True)
        self.p_ckpt = os.path.join(run_dir, "checkpoint.pkl")
        self.p_hist = os.path.join(run_dir, "history.jsonl")
        self.p_bench = os.path.join(run_dir, "benchmarks.jsonl")
        if fresh:
            for p in (self.p_ckpt, self.p_hist, self.p_bench):
                if os.path.exists(p):
                    os.remove(p)
        self.encode = make_encoder(env)
        self.n_actions = {ts: env.traffic_signals[ts].num_green_phases for ts in env.ts_ids}
        self.q = TabularQ(4, ALPHA, GAMMA)
        self.rng = np.random.default_rng(11)
        self.mode = "train"              # train | policy
        self.episode = 0                 # completed training windows of the city run
        self.sim_seconds_trained = 0.0
        self.eps0 = EPS_WARM
        self.warm = None
        self.history = []
        self.started_from = None
        self.prev = {}                   # ts -> (state, action)
        self._reset_window()
        self.last_saved_at = None
        if os.path.exists(self.p_ckpt):
            self._load()
        else:
            self._warm_start(warm_from)
        self.history = read_jsonl(self.p_hist)
        self.history = [h for h in self.history if h["episode"] <= self.episode]
        with open(self.p_hist, "w") as fh:
            for h in self.history:
                fh.write(json.dumps(h) + "\n")
        self.session_start_episode = self.episode

    # ------------------------------------------------------------------ start / resume
    def _warm_start(self, warm_from):
        path = os.path.join(warm_from, "checkpoint.pkl")
        if os.path.exists(path):
            with open(path, "rb") as fh:
                data = pickle.load(fh)
            self.q.q = merge_tables(list(data["q"].values()))
            ev = read_jsonl(os.path.join(warm_from, "evals.jsonl"))
            self.warm = {"from": warm_from, "episodes": data["episode"], "q_states": len(self.q.q),
                         "improvement_pct": ev[-1].get("improvement_pct") if ev else None,
                         "note": f"started from the saved 2x2-grid training ({data['episode']} episodes)"}
            self.eps0 = EPS_WARM
            self.started_from = self.warm
            self.log(f"Warm start from {warm_from}: {data['episode']} saved episodes, {len(self.q.q)} learned states.")
        else:
            self.eps0 = EPS_COLD
            self.started_from = {"from": None, "note": "no saved training found: learning from scratch"}
            self.log("No saved training found in runs/main: learning from scratch.")

    def _load(self):
        with open(self.p_ckpt, "rb") as fh:
            d = pickle.load(fh)
        self.q.q = d["q"]
        self.episode, self.eps0 = d["episode"], d["eps0"]
        self.sim_seconds_trained = d.get("sim_seconds", 0.0)
        self.warm = d.get("warm")
        self.started_from = d.get("warm") or {"from": None, "note": "from scratch"}
        self.rng.bit_generator.state = d["rng"]
        self.log(f"Resumed the city training at episode {self.episode} ({len(self.q.q)} learned states).")

    def save(self):
        d = dict(version=2, q=self.q.q, episode=self.episode, eps0=self.eps0, warm=self.warm, sim_seconds=self.sim_seconds_trained,
                 rng=self.rng.bit_generator.state, saved_at=time.time())
        tmp = self.p_ckpt + ".tmp"
        with open(tmp, "wb") as fh:
            pickle.dump(d, fh, protocol=pickle.HIGHEST_PROTOCOL)
        if os.path.exists(self.p_ckpt):
            os.replace(self.p_ckpt, self.p_ckpt.replace(".pkl", ".prev.pkl"))
        os.replace(tmp, self.p_ckpt)
        self.last_saved_at = time.time()

    # ------------------------------------------------------------------ per decision
    @property
    def epsilon(self):
        if self.mode != "train":
            return 0.0
        return max(EPS_MIN, self.eps0 * EPS_DECAY ** self.episode)

    def act(self, obs):
        """Actions for the signals that must decide now (those in `obs`)."""
        acts, eps = {}, self.epsilon
        self._cur = getattr(self, "_cur", {})
        for ts in obs:
            s = self.encode(ts, obs[ts])
            n = self.n_actions[ts]
            v = self.q.values(s)[:n]
            if eps > 0 and self.rng.random() < eps:
                a = int(self.rng.integers(n))
            else:
                a = int(self.rng.choice(np.flatnonzero(v == v.max())))
            acts[ts] = a
            self._cur[ts] = (s, a, self.env.traffic_signals[ts].preempted)
        return acts

    def learn(self, obs2, rew, sysinfo):
        cur = getattr(self, "_cur", {})
        for ts in obs2:
            s2 = self.encode(ts, obs2[ts])
            self.w_reward.append(float(rew.get(ts, 0.0)))
            if ts in cur and self.mode == "train" and ts in rew:
                s, a, was_pre = cur[ts]
                # while an emergency vehicle holds the signal the agent's action was ignored: nothing to learn from that step
                if not was_pre and not self.env.traffic_signals[ts].preempted:
                    self.q.learn(s, a, float(rew[ts]), s2)
        self.w_wait.append(sysinfo["mean_wait"])
        self.w_speed.append(sysinfo["mean_speed"])
        self.w_stopped.append(sysinfo["stopped"])

    # ------------------------------------------------------------------ windows
    def _reset_window(self):
        self.w_reward, self.w_wait, self.w_speed, self.w_stopped = [], [], [], []
        self.w_arrived = self.w_wait_trip = 0
        self.w_emerg, self.w_emerg_wait, self.w_emerg_trip = 0, 0.0, 0.0
        self.w_pre0 = None
        self.window_start_sim = None

    def vehicle_arrived(self, kind, wait, trip):
        if kind == "emergency":
            self.w_emerg += 1
            self.w_emerg_wait += wait
            self.w_emerg_trip += trip
        elif kind in ("car", "truck", "driver"):
            self.w_arrived += 1
            self.w_wait_trip += wait

    def maybe_close_window(self, sim_t, preemptions_total):
        if self.window_start_sim is None:
            self.window_start_sim, self.w_pre0 = sim_t, preemptions_total
            return None
        if sim_t - self.window_start_sim < WINDOW_S:
            return None
        row = dict(window=len(self.history) + 1, episode=self.episode + 1, mode=self.mode, sim_time=round(sim_t), epsilon=round(self.epsilon, 4), q_states=len(self.q.q),
                   mean_wait=round(mean(self.w_wait), 2) if self.w_wait else None,
                   mean_speed=round(mean(self.w_speed), 2) if self.w_speed else None,
                   mean_stopped=round(mean(self.w_stopped), 1) if self.w_stopped else None,
                   reward=round(sum(self.w_reward), 2), arrived=self.w_arrived,
                   trip_wait=round(self.w_wait_trip / self.w_arrived, 2) if self.w_arrived else None,
                   emerg_arrived=self.w_emerg,
                   emerg_wait=round(self.w_emerg_wait / self.w_emerg, 2) if self.w_emerg else None,
                   emerg_trip=round(self.w_emerg_trip / self.w_emerg, 1) if self.w_emerg else None,
                   preemptions=preemptions_total - self.w_pre0, finished_at=time.time())
        if self.mode == "train":
            self.episode += 1
            self.sim_seconds_trained += WINDOW_S
        else:
            row["episode"] = self.episode          # policy windows do not advance the training counter
        self.history.append(row)
        append_jsonl(self.p_hist, row)
        if self.mode == "train" and self.episode % SAVE_EVERY == 0:
            self.save()
        self._reset_window()
        return row
