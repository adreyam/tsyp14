#!/usr/bin/env bash
set -euo pipefail

# run_city.sh remains untouched. This alternate launcher starts the same SUMO
# bridge and NPC planner, then opens one Godot application containing the city
# viewport and dock-style control room together.
bundle_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# Optional web dashboard, started in parallel with the city:
#   ./run_control_room.sh --dashboard            (http://127.0.0.1:8870/, opened in your browser)
#   ./run_control_room.sh --dashboard=9000       (another port)     --no-browser   (do not open the browser)
# It is an MCP client: everything it shows reaches it through the MCP interface and nothing else.
dashboard=0
dashboard_open=1
dashboard_port="${CITY_DASHBOARD_PORT:-8870}"
[[ "${CITY_DASHBOARD:-0}" == "1" ]] && dashboard=1
forwarded=()
for argument in "$@"; do
    case "$argument" in
        --dashboard) dashboard=1 ;;
        --dashboard=*) dashboard=1; dashboard_port="${argument#--dashboard=}" ;;
        --no-browser) dashboard_open=0 ;;
        *) forwarded+=("$argument") ;;
    esac
done
set -- "${forwarded[@]+"${forwarded[@]}"}"
sumo_root="$bundle_dir/runtime/sumo"
sumo_lib="$sumo_root/usr/lib/x86_64-linux-gnu:$sumo_root/usr/lib/x86_64-linux-gnu/blas:$sumo_root/usr/lib/x86_64-linux-gnu/lapack:$sumo_root/usr/lib"
export SUMO_HOME="$sumo_root/usr/share/sumo"
export LD_LIBRARY_PATH="$sumo_lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
#export PYTHONHOME="$sumo_root/usr"
bundled_python="$sumo_root/usr/bin/python3.14"
godot="$bundle_dir/runtime/linux-x86_64/godot"
"$bundle_dir/install_launcher.sh" || true
# On a Wayland desktop run through XWayland: there the window's own icon (the TSYP14 logo) reaches the dock and task switcher.
# CITY_DISPLAY_DRIVER=wayland (or any other driver name) overrides this.
display_args=()
if [[ -n "${WAYLAND_DISPLAY:-}" && -n "${DISPLAY:-}" ]]; then
    display_args=(--display-driver "${CITY_DISPLAY_DRIVER:-x11}")
fi

echo "Launching City Ground Zero — unified Live Operations Control Room"
echo "Project: $bundle_dir"
echo "Mode: one application / direct city viewport / no image transfer"

if command -v nvidia-smi >/dev/null 2>&1 && timeout --kill-after=1s 2s nvidia-smi -L >/dev/null 2>&1; then
    export __NV_PRIME_RENDER_OFFLOAD=1
    export __GLX_VENDOR_LIBRARY_NAME=nvidia
    export __VK_LAYER_NV_optimus=NVIDIA_only
    echo "Renderer preference: NVIDIA PRIME offload"
else
    echo "Renderer preference: system Vulkan GPU (Intel Vulkan remains hardware accelerated)"
fi

bridge_pid=""
npc_pid=""
godot_pid=""
dashboard_pid=""
cli_pid=""
bridge_log="/tmp/city_ground_zero_sumo_${UID}_$$.log"
npc_log="/tmp/city_ground_zero_npc_${UID}_$$.log"
bridge_ready="/tmp/city_ground_zero_sumo_ready_${UID}_$$"
free_port() {
    "$bundled_python" -c 'import socket,sys; kind=socket.SOCK_DGRAM if sys.argv[1]=="udp" else socket.SOCK_STREAM; s=socket.socket(type=kind); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()' "$1"
}
export CITY_SUMO_UDP_PORT="$(free_port udp)"
export CITY_SUMO_CONTROL_PORT="$(free_port udp)"
export CITY_NPC_PLANNER_PORT="$(free_port tcp)"
export CITY_SIM_API_PORT="$(free_port tcp)"
export CITY_SIM_API_READY_FILE="/tmp/city_ground_zero_api_ready_${UID}_$$"
rm -f "$CITY_SIM_API_READY_FILE"
cleanup() {
    # All children are owned by this launcher.  This makes the city window and
    # MCP terminal one lifecycle: closing either one tears down the other and
    # both simulation bridges.
    for task_pid in "$cli_pid" "$dashboard_pid" "$godot_pid" "$bridge_pid" "$npc_pid"; do
        if [[ -n "$task_pid" ]] && kill -0 "$task_pid" 2>/dev/null; then
            kill "$task_pid" 2>/dev/null || true
            wait "$task_pid" 2>/dev/null || true
        fi
    done
}
trap cleanup EXIT INT TERM

# The bridge log is kept with the project (twin/logs/sumo_bridge.log) so a failure can be read after the run.
mkdir -p "$bundle_dir/logs"
bridge_log="$bundle_dir/logs/sumo_bridge.log"
: >"$bridge_log"
# Watchdog: if the bridge ever stops while the city is open (traffic, incidents and units would freeze on
# "WAITING FOR SUMO BRIDGE"), it is started again on the same ports after a short warm-up; Godot reconnects by itself.
(
    child=""
    trap '[[ -n "$child" ]] && kill "$child" 2>/dev/null; exit 0' TERM INT
    warmup=90
    while true; do
        "$bundled_python" "$bundle_dir/scripts/sumo_bridge.py" --rate 15 --port "$CITY_SUMO_UDP_PORT" --control-port "$CITY_SUMO_CONTROL_PORT" --warmup "$warmup" --ready-file "$bridge_ready" >>"$bridge_log" 2>&1 &
        child=$!
        wait "$child"
        status=$?
        echo "[watchdog] $(date '+%H:%M:%S') SUMO bridge exited with status $status; restarting" >>"$bridge_log"
        warmup=10
        sleep 1
    done
) &
bridge_pid=$!
echo "SUMO bridge: running (PID $bridge_pid, log $bridge_log)"
for _ in {1..150}; do
    [[ -s "$bridge_ready" ]] && break
    if ! kill -0 "$bridge_pid" 2>/dev/null; then
        echo "SUMO bridge stopped during startup; see $bridge_log" >&2
        break
    fi
    sleep 0.1
done
if [[ ! -s "$bridge_ready" ]]; then
    echo "SUMO failed to become ready; the control room was not launched with fake traffic." >&2
    tail -n 30 "$bridge_log" >&2 || true
    exit 1
fi

"$bundled_python" "$bundle_dir/scripts/npc_ai_bridge.py" --port "$CITY_NPC_PLANNER_PORT" >"$npc_log" 2>&1 &
npc_pid=$!
echo "NPC planner: running (PID $npc_pid, log $npc_log)"

start_dashboard() {
    [[ "$dashboard" == "1" ]] || return 0
    local open_flag=()
    [[ "$dashboard_open" == "1" ]] && open_flag=(--open)
    "$bundled_python" "$bundle_dir/tools/dashboard/dashboard_server.py" --port "$dashboard_port" "${open_flag[@]+"${open_flag[@]}"}" >"/tmp/city_ground_zero_dashboard_${UID}_$$.log" 2>&1 &
    dashboard_pid=$!
    export CITY_DASHBOARD_URL="http://127.0.0.1:${dashboard_port}/"
    echo "Web dashboard: $CITY_DASHBOARD_URL  (an MCP client of the running city)"
}

# CITY_NO_CLI=1 keeps the old behaviour (the city window is the foreground process). Otherwise the city opens
# in its own window and this terminal becomes an MCP client of the live simulation once the device API is up.
if [[ "${CITY_NO_CLI:-0}" == "1" ]]; then
    start_dashboard
    "$godot" "${display_args[@]}" "$@" --path "$bundle_dir" --scene res://scenes/ControlRoom.tscn
    exit $?
fi

# The city's own log goes to a file so it cannot print over the terminal.
godot_log="/tmp/city_ground_zero_godot_${UID}_$$.log"
"$godot" "${display_args[@]}" "$@" --path "$bundle_dir" --scene res://scenes/ControlRoom.tscn >"$godot_log" 2>&1 &
godot_pid=$!
echo "City window: starting (PID $godot_pid, log $godot_log) — the MCP terminal opens here as soon as the simulation is live"
for _ in {1..600}; do
    [[ -s "$CITY_SIM_API_READY_FILE" ]] && break
    if ! kill -0 "$godot_pid" 2>/dev/null; then
        echo "The city exited during startup." >&2
        exit 1
    fi
    sleep 0.25
done
if [[ ! -s "$CITY_SIM_API_READY_FILE" ]]; then
    echo "The device API did not come up; the city window keeps running without the terminal." >&2
    wait "$godot_pid"
    exit $?
fi
export CITY_SIM_API_PORT="$(cat "$CITY_SIM_API_READY_FILE")"
export CITY_GODOT_PID="$godot_pid"
start_dashboard
# The terminal remains foreground-owned so it keeps the user's stdin.  Its
# line editor polls CITY_GODOT_PID while waiting for input, therefore closing
# the window exits this command promptly; Ctrl+C/exit here returns normally
# and the EXIT trap closes the city in the other direction.
set +e
"$bundled_python" "$bundle_dir/tools/mcp/city_cli.py"
wait_status=$?
set -e
if ! kill -0 "$godot_pid" 2>/dev/null; then
    echo "City window closed — closing the MCP terminal…"
else
    echo "MCP terminal closed — closing the city…"
fi
exit "$wait_status"

