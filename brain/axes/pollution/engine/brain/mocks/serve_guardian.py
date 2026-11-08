"""
Mock Guardian alone, over HTTP, for a run with the real Twin while the real
Guardian is not ready. Trusts every sensor, except the ones you distrust:

    python -m mocks.serve_guardian --port 8300
    curl -X POST http://127.0.0.1:8300/v1/control -d '{"action":"distrust","sensor_id":"WGT-T001"}'
    curl -X POST http://127.0.0.1:8300/v1/control -d '{"action":"trust","sensor_id":"WGT-T001"}'

Checks the Brain's token (BRAIN_TOKEN), like the real Guardian should.
"""
import argparse
import json
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

from mocks.mock_guardian import ScriptedGuardian
from mocks.serve import _send, _token, guardian_handler


def make_server(host: str, port: int, token: str):
    ns = SimpleNamespace(guardian=ScriptedGuardian(), lock=threading.Lock(), brain_token=token)
    base = guardian_handler(ns)

    class Handler(base):
        def do_POST(self):
            if self.path != "/v1/control":
                return super().do_POST()
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                sid, action = str(body["sensor_id"]), body["action"]
                with ns.lock:
                    if action == "distrust":
                        ns.guardian.distrust(sid, body.get("reason", "operator: suspected spoofing"))
                    elif action == "trust":
                        ns.guardian.trust(sid)
                    else:
                        raise ValueError(f"unknown action {action}")
            except (ValueError, KeyError, TypeError) as e:
                return _send(self, 400, {"error": str(e)})
            _send(self, 200, {"done": f"{action} {sid}"})

    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    return httpd, ns


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Mock Guardian alone (HTTP).")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8300)
    a = p.parse_args(argv)
    httpd, _ = make_server(a.host, a.port, _token("BRAIN_TOKEN"))
    print(f"[mock guardian] http://{a.host}:{a.port}/v1/trust-verdict  (control: POST /v1/control)")
    threading.Thread(target=httpd.serve_forever, args=(0.1,), daemon=True).start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
