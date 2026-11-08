"""Train the traffic-signal agents (emergency-vehicle priority switched on), with a saved history.

    python training/train.py --episodes 100 --live          # first run: starts from scratch, opens the live page
    python training/train.py --episodes 100 --live          # run it again: continues from the saved episode, Q-tables and exploration
    python training/train.py --episodes 100 --fresh         # throw the saved history away and start over

--episodes is "how many MORE episodes in this session". Stop any time with Ctrl+C (or the page's Save & stop): the history is saved.
"""

import argparse
import os
import sys
import threading
import webbrowser

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
os.chdir(ROOT)  # all paths below are relative to the project folder

from training.core import DEFAULTS, Trainer  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--episodes", type=int, default=50, help="episodes to train in this session (default 50)")
    ap.add_argument("--run-dir", default="runs/main", help="where the saved training lives (default runs/main)")
    ap.add_argument("--fresh", action="store_true", help="ignore and delete the saved training in --run-dir")
    ap.add_argument("--live", action="store_true", help="show the live training page in your browser")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--live-speed", type=float, default=30.0, help="live page speed: simulated seconds per real second (0 = as fast as possible)")
    ap.add_argument("--no-browser", action="store_true", help="with --live: do not open the browser automatically")
    ap.add_argument("--sumo-gui", action="store_true", help="also open the sumo-gui window (slow; the web page is usually enough)")
    ap.add_argument("--save-every", type=int, default=2, help="save the checkpoint every N episodes (default 2)")
    ap.add_argument("--eval-every", type=int, default=10, help="compare with fixed-time signals every N episodes (default 10, 0 = never)")
    ap.add_argument("--no-priority", dest="priority", action="store_false", help="train WITHOUT emergency priority (default: priority on)")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--alpha", type=float, default=None)
    ap.add_argument("--gamma", type=float, default=None)
    ap.add_argument("--eps-decay", type=float, default=None)
    ap.add_argument("--eps-min", type=float, default=None)
    ap.add_argument("--n-eval", type=int, default=None, help="validation scenarios per evaluation (default 3)")
    a = ap.parse_args()

    over = dict(data_dir=a.data_dir, seed=a.seed, alpha=a.alpha, gamma=a.gamma, eps_decay=a.eps_decay, eps_min=a.eps_min, n_eval=a.n_eval,
                emergency_priority=None if a.priority else False)
    live = None
    if a.live:
        from training.live import LiveView
        live = LiveView(DEFAULTS["net_file"], port=a.port, speed=a.live_speed)
    t = Trainer(a.run_dir, over, fresh=a.fresh, live=live, gui=a.sumo_gui)
    if t.resumed_from:
        print(f"Resuming '{t.name}' from episode {t.episode} ({t.q_states} learned states, exploration {t.epsilon(t.episode):.3f}).")
    else:
        print(f"Starting a new run '{t.name}' (emergency priority {'ON' if t.cfg['emergency_priority'] else 'OFF'}).")
    if live:
        url = live.start()
        print(f"Live page: {url}   (leave this window open)")
        if not a.no_browser:
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        t.train(a.episodes, eval_every=a.eval_every, save_every=a.save_every, stop=(lambda: live.stop_requested) if live else (lambda: False))
        if live and not live.stop_requested:
            print("Training session finished. The live page stays up for 20 s...")
            import time
            time.sleep(20)
    finally:
        t.close()
        if live:
            live.stop()


if __name__ == "__main__":
    main()
