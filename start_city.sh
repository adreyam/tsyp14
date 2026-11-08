#!/usr/bin/env bash
# One command for the whole triplet on one machine (spec: "starts the whole city with one command").
#
#   ./start_city.sh            live: the Godot Twin + City Brain orchestrator (source live) + the three axis Brains
#                                    (waste and air, energy, transport) + control center
#   ./start_city.sh --mock     the same without Godot: the orchestrator uses its Twin and Guardian stand-ins
#   ./start_city.sh --no-axes  without the three axis engines (brain/axes/*/engine; only the orchestrator's pages work)
#   SKIP_INSTALL=1 ./start_city.sh   skip the pip installs (faster once everything is installed)
#
# Control center: http://127.0.0.1:8765  (City map: #/city)   Orchestrator API: http://127.0.0.1:8500/v1/status
# Every service writes its own log in logs/. Ctrl+C stops everything this script started.
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
PY="${PYTHON:-python3}"
MODE=live; AXES=1
for a in "$@"; do case "$a" in --mock) MODE=mock ;; --no-axes) AXES=0 ;; --all) AXES=1 ;; esac; done
LOGS="$ROOT/logs"; mkdir -p "$LOGS"
AXES_DIR="$ROOT/brain/axes"
ENERGY="$AXES_DIR/energy/engine/brain"
TRANSPORT="$AXES_DIR/transportation/engine/brain"
WASTEAIR="$AXES_DIR/pollution/engine/brain"
CONTROL_CENTER="$ROOT/brain/control_center"
missing=""
for a in emergency transportation pollution energy; do [ -d "$AXES_DIR/$a/engine" ] || missing="$missing axes/$a/engine"; done
[ -d "$CONTROL_CENTER" ] || missing="$missing control_center"
if [ -n "$missing" ]; then
  echo "The brain/ folder still has the old layout (missing:$missing): run   bash brain/reorganize.sh   once, then start again."; exit 1
fi
if [ -d "$ROOT/brain/TSYP14BRAIN-v3" ]; then
  echo "Note: brain/TSYP14BRAIN-v3 still holds leftover files (not used); run   bash brain/reorganize.sh   again to file them under brain/axes/energy/docs."
fi
export BRAIN_TOKEN="${BRAIN_TOKEN:-tsyp14-brain-dev-token}"
export ORCHESTRATOR_TOKEN="${ORCHESTRATOR_TOKEN:-tsyp14-control-center-token}"
export PYTHONIOENCODING=utf-8 NO_BROWSER=1
PIDS=()
run() { # name dir command...   (each service: own process, own log)
  local name="$1" dir="$2"; shift 2
  (cd "$dir" && exec "$@") >"$LOGS/$name.log" 2>&1 &
  PIDS+=($!); echo "  started $name (log: logs/$name.log)"
}
stop() { echo; echo "Stopping..."; kill "${PIDS[@]}" 2>/dev/null; wait 2>/dev/null; exit 0; }
trap stop INT TERM

pip_install() { # requirements file
  [ -f "$1" ] || return 0
  $PY -m pip install --quiet -r "$1" 2>/dev/null || $PY -m pip install --quiet --break-system-packages -r "$1" \
    || echo "  warning: could not install $1 (see the service log if it does not start)"
}

# ------------------------------------------------------------------ packages
if [ "${SKIP_INSTALL:-0}" != 1 ]; then
  echo "Installing Python packages (SKIP_INSTALL=1 to skip)..."
  pip_install "$ROOT/brain/requirements.txt"
  if [ "$AXES" = 1 ]; then
    pip_install "$ENERGY/requirements.txt"
    pip_install "$TRANSPORT/requirements_city.txt"
    pip_install "$WASTEAIR/requirements.txt"
  fi
fi

# ------------------------------------------------------------------ SUMO for the transport Brain: system, else the Twin's bundled one
if ! command -v sumo >/dev/null 2>&1 && [ -z "${SUMO_HOME:-}" ]; then
  for d in /usr/share/sumo /usr/local/share/sumo /opt/sumo; do [ -d "$d" ] && export SUMO_HOME="$d" && break; done
fi
TWIN_SUMO="$ROOT/twin/runtime/sumo/usr"
if ! command -v sumo >/dev/null 2>&1 && [ -z "${SUMO_HOME:-}" ] && [ -e "$TWIN_SUMO/bin/sumo" ]; then
  export PATH="$TWIN_SUMO/bin:$PATH"
  export LD_LIBRARY_PATH="$TWIN_SUMO/lib/x86_64-linux-gnu:$TWIN_SUMO/lib/x86_64-linux-gnu/blas:$TWIN_SUMO/lib/x86_64-linux-gnu/lapack:$TWIN_SUMO/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  echo "SUMO: using the Twin's bundled SUMO ($TWIN_SUMO/bin/sumo)"
fi

# ------------------------------------------------------------------ the Twin
if [ "$MODE" = live ]; then
  PORT_FILE="${TMPDIR:-/tmp}/city_ground_zero_api_port"
  rm -f "$PORT_FILE"
  echo "Twin: starting twin/run_control_room.sh"
  (cd "$ROOT/twin" && CITY_NO_CLI=1 exec ./run_control_room.sh) >"$LOGS/twin-console.log" 2>&1 &
  PIDS+=($!)
  echo -n "Twin: waiting for the device API"
  for _ in $(seq 1 240); do [ -s "$PORT_FILE" ] && break; sleep 0.5; echo -n "."; done
  echo
  if [ -s "$PORT_FILE" ]; then echo "Twin: device API on 127.0.0.1:$(cat "$PORT_FILE")"
  else echo "Twin: not ready yet (see logs/twin-console.log); the orchestrator keeps retrying and shows 'Twin unreachable'"; fi
fi

# ------------------------------------------------------------------ City Brain orchestrator + the three axis Brains
echo "Starting the Brains:"
run brain-console "$ROOT/brain" $PY run_brain.py --source "$MODE"
if [ "$AXES" = 1 ]; then
  run axis-energy          "$ENERGY"    env PYTHONPATH="src:." $PY -m sim.server --port 8400 --speed 4
  run axis-transport-twin  "$TRANSPORT" env LOGS_DIR="$AXES_DIR/transportation/engine/logs" BRAIN_URL=http://127.0.0.1:8000 $PY -m mocks.mock_twin
  run axis-transport-guard "$TRANSPORT" env LOGS_DIR="$AXES_DIR/transportation/engine/logs" $PY -m mocks.mock_guardian
  run axis-transport       "$TRANSPORT" env PORT=8000 $PY -m citybrain.server
  run axis-waste-air       "$WASTEAIR"  $PY -m demo.live --no-browser
fi
sleep 3
run control-center "$CONTROL_CENTER" $PY server.py

# ------------------------------------------------------------------ health check: say clearly which service is down and where its log is
echo
echo "Checking the services (up to 60 s)..."
$PY - "$AXES" <<'EOF'
import socket, sys, time
services = [("City Brain orchestrator", 8500, "brain-console"), ("Control center", 8765, "control-center")]
if sys.argv[1] == "1":
    services += [("Waste and air Brain", 8100, "axis-waste-air"), ("Energy Brain", 8400, "axis-energy"),
                 ("Transport Brain", 8000, "axis-transport")]
def up(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False
deadline = time.time() + 60
while time.time() < deadline and not all(up(p) for _, p, _ in services):
    time.sleep(2)
for name, port, log in services:
    ok = up(port)
    print(f"  {'UP  ' if ok else 'DOWN'} {name:26} :{port}" + ("" if ok else f"   -> read logs/{log}.log"))
EOF
echo
echo "Control center: http://127.0.0.1:8765   (Ctrl+C here stops everything)"
wait
