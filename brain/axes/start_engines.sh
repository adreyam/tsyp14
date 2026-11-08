#!/usr/bin/env bash
# Start only the axis engines (the axis teams' own Brains, each on its own simulation), Linux / macOS / WSL.
#   energy          http://127.0.0.1:8400   brain/axes/energy/engine/brain
#   transportation  http://127.0.0.1:8000   brain/axes/transportation/engine/brain  (+ its mock Twin 9001 and mock Guardian 9002)
#   pollution       http://127.0.0.1:8100   brain/axes/pollution/engine/brain       (waste and air quality)
# The whole city (Twin + orchestrator + engines + control center) starts with ./start_city.sh at the triplet root.
set -u
AXES="$(cd "$(dirname "$0")" && pwd)"
LOGS="$AXES/../../logs"; mkdir -p "$LOGS"
PY="${PYTHON:-python3}"
export ORCHESTRATOR_TOKEN="${ORCHESTRATOR_TOKEN:-tsyp14-control-center-token}" PYTHONIOENCODING=utf-8 NO_BROWSER=1
PIDS=()
run() { local name="$1" dir="$2"; shift 2; (cd "$dir" && exec "$@") >"$LOGS/$name.log" 2>&1 & PIDS+=($!); echo "  $name (log: logs/$name.log)"; }
trap 'echo; kill "${PIDS[@]}" 2>/dev/null; wait 2>/dev/null; exit 0' INT TERM
TWIN_SUMO="$AXES/../../twin/runtime/sumo/usr"
if [ -z "${SUMO_HOME:-}" ] && ! command -v sumo >/dev/null 2>&1 && [ -e "$TWIN_SUMO/bin/sumo" ]; then
  export PATH="$TWIN_SUMO/bin:$PATH"
  export LD_LIBRARY_PATH="$TWIN_SUMO/lib/x86_64-linux-gnu:$TWIN_SUMO/lib/x86_64-linux-gnu/blas:$TWIN_SUMO/lib/x86_64-linux-gnu/lapack:$TWIN_SUMO/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
E="$AXES/energy/engine/brain"; T="$AXES/transportation/engine/brain"; P="$AXES/pollution/engine/brain"
run axis-energy          "$E" env PYTHONPATH="src:." $PY -m sim.server --port 8400 --speed 4
run axis-transport-twin  "$T" env LOGS_DIR="$AXES/transportation/engine/logs" BRAIN_URL=http://127.0.0.1:8000 $PY -m mocks.mock_twin
run axis-transport-guard "$T" env LOGS_DIR="$AXES/transportation/engine/logs" $PY -m mocks.mock_guardian
run axis-transport       "$T" env PORT=8000 $PY -m citybrain.server
run axis-waste-air       "$P" $PY -m demo.live --no-browser
echo "Axis engines started (Ctrl+C stops them)."
wait
