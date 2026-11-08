"""
Local rehearsal of the City Ground Zero integration, without Godot:

  fake Godot device API  <-  Twin's REAL MCP server (vendored)  <-  City Brain (MCP client)
                                                                    -> mock Guardian (HTTP)
                                                                    -> timeline screen

    python -m demo.live_mcp            (from brain/)
    python scenarios/live_mcp_demo.py  (from the triplet root)

Bins fill quickly; the Brain orders empty_waste_bin for the red and orange ones.
Timeline: http://127.0.0.1:8100/timeline      Log: logs/LIVE-MCP.jsonl
"""
import os
import secrets
import sys
import threading
import time
import webbrowser
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

from mocks.fake_city_api import FakeCityApi
from mocks.mock_guardian import ScriptedGuardian
from mocks.serve import guardian_handler
from src.config import BrainSettings, ConfigError
from src.service import BrainService

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SERVER = os.path.join(HERE, "..", "mocks", "vendor", "city_mcp_server.py")


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    os.environ.setdefault("BRAIN_TOKEN", secrets.token_urlsafe(24))
    token = os.environ["BRAIN_TOKEN"]

    api = FakeCityApi(speed=1.5)
    city_port = api.serve(0)

    guardian = ScriptedGuardian()
    ns = SimpleNamespace(guardian=guardian, lock=threading.Lock(), brain_token=token)
    gsrv = ThreadingHTTPServer(("127.0.0.1", 0), guardian_handler(ns))
    gsrv.daemon_threads = True
    threading.Thread(target=gsrv.serve_forever, args=(0.1,), daemon=True).start()

    log_dir = os.path.join(ROOT, "logs") if os.path.isdir(os.path.join(ROOT, "scenarios")) \
        else os.path.join(HERE, "..", "logs")
    os.makedirs(log_dir, exist_ok=True)
    try:
        settings = BrainSettings(
            listen={"host": "127.0.0.1", "port": 8100},
            twin={"transport": "mcp_stdio",
                  "mcp": {"command": [sys.executable, SERVER],
                          "env": {"CITY_SIM_API_PORT": str(city_port)}}},
            guardian={"url": f"http://127.0.0.1:{gsrv.server_address[1]}"},
            # Demo time runs fast, so "orange" means full within the next 2 minutes.
            city={"decision": "crew", "reading_interval_s": 2, "crew_reorder_after_s": 30,
                  "pass_interval_minutes": {}, "fallback_pass_interval_minutes": 2},
            cycle_s=2.0, clock="twin",
            log_path=os.path.join(log_dir, "LIVE-MCP.jsonl"))
        service = BrainService(settings)
    except ConfigError as e:
        print(f"[live-mcp] {e}", file=sys.stderr)
        return 2
    service.serve(block=False)
    url = "http://127.0.0.1:8100/timeline"
    print(f"[live-mcp] fake city device API on 127.0.0.1:{city_port}")
    print(f"[live-mcp] Twin MCP server: {os.path.normpath(SERVER)} (Twin team's code)")
    print(f"[live-mcp] timeline : {url}")
    print(f"[live-mcp] log      : {settings.log_path}")
    print("[live-mcp] Ctrl+C to stop")
    if "--no-browser" not in argv:
        webbrowser.open(url)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()
        api.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
