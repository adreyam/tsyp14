#!/usr/bin/env bash
set -euo pipefail
bundle_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
sumo_root="$bundle_dir/runtime/sumo"
sumo_lib="$sumo_root/usr/lib/x86_64-linux-gnu:$sumo_root/usr/lib/x86_64-linux-gnu/blas:$sumo_root/usr/lib/x86_64-linux-gnu/lapack:$sumo_root/usr/lib"
export SUMO_HOME="$sumo_root/usr/share/sumo"
export LD_LIBRARY_PATH="$sumo_lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONHOME="$sumo_root/usr"
bundled_python="$sumo_root/usr/bin/python3.14"
"$bundle_dir/install_launcher.sh" || true
# On a Wayland desktop run through XWayland: there the window's own icon (the TSYP14 logo) reaches the dock and task switcher.
# CITY_DISPLAY_DRIVER=wayland (or any other driver name) overrides this.
display_args=()
if [[ -n "${WAYLAND_DISPLAY:-}" && -n "${DISPLAY:-}" ]]; then
    display_args=(--display-driver "${CITY_DISPLAY_DRIVER:-x11}")
fi

echo "Launching City Ground Zero — bundled SUMO traffic + authored walking"
echo "Project: $bundle_dir"

# Godot Forward+ is Vulkan GPU rendering. Prefer the discrete NVIDIA renderer
# whenever its driver is actually installed; otherwise retain the working Intel
# Vulkan renderer rather than forcing a broken offload configuration.
# A damaged NVIDIA kernel driver can make nvidia-smi block indefinitely. The
# probe is strictly optional and must never prevent the city from launching.
if command -v nvidia-smi >/dev/null 2>&1 && timeout --kill-after=1s 2s nvidia-smi -L >/dev/null 2>&1; then
    export __NV_PRIME_RENDER_OFFLOAD=1
    export __GLX_VENDOR_LIBRARY_NAME=nvidia
    export __VK_LAYER_NV_optimus=NVIDIA_only
    echo "Renderer preference: NVIDIA PRIME offload"
else
    echo "Renderer preference: system Vulkan GPU (NVIDIA probe unavailable/timed out; Intel Vulkan remains hardware accelerated)"
fi

bridge_pid=""
npc_pid=""
bridge_log="/tmp/city_ground_zero_sumo_${UID}_$$.log"
npc_log="/tmp/city_ground_zero_npc_${UID}_$$.log"
bridge_ready="/tmp/city_ground_zero_sumo_ready_${UID}_$$"
free_port() {
    "$bundled_python" -c 'import socket,sys; kind=socket.SOCK_DGRAM if sys.argv[1]=="udp" else socket.SOCK_STREAM; s=socket.socket(type=kind); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()' "$1"
}
export CITY_SUMO_UDP_PORT="$(free_port udp)"
export CITY_SUMO_CONTROL_PORT="$(free_port udp)"
export CITY_NPC_PLANNER_PORT="$(free_port tcp)"
cleanup_bridge() {
    if [[ -n "$bridge_pid" ]] && kill -0 "$bridge_pid" 2>/dev/null; then
        kill "$bridge_pid" 2>/dev/null || true
        wait "$bridge_pid" 2>/dev/null || true
    fi
    if [[ -n "$npc_pid" ]] && kill -0 "$npc_pid" 2>/dev/null; then
        kill "$npc_pid" 2>/dev/null || true
        wait "$npc_pid" 2>/dev/null || true
    fi
}
trap cleanup_bridge EXIT INT TERM

if [[ "${SUMO_BRIDGE_DISABLED:-0}" != "1" ]]; then
    # Interpolated Godot traffic is smooth at 15 Hz. This lower transport rate
    # avoids filling UDP receive buffers on an occasional slow render frame.
    "$bundled_python" "$bundle_dir/scripts/sumo_bridge.py" --rate 15 --port "$CITY_SUMO_UDP_PORT" --control-port "$CITY_SUMO_CONTROL_PORT" --ready-file "$bridge_ready" >"$bridge_log" 2>&1 &
    bridge_pid=$!
    echo "SUMO bridge: running (PID $bridge_pid, log $bridge_log)"
    # Prevent the initial fallback-to-SUMO replacement from popping cars all
    # across the view: Godot opens only after SUMO has completed its warm-up.
    for _ in {1..150}; do
        [[ -s "$bridge_ready" ]] && break
        if ! kill -0 "$bridge_pid" 2>/dev/null; then
            echo "SUMO bridge stopped during startup; see $bridge_log" >&2
            break
        fi
        sleep 0.1
    done
    if [[ ! -s "$bridge_ready" ]]; then
        echo "SUMO failed to become ready; the city was not launched with fake traffic." >&2
        tail -n 30 "$bridge_log" >&2 || true
        exit 1
    fi
fi

# The local NPC bridge always provides deterministic sidewalk plans. It uses
# OpenRouter only when OPENROUTER_API_KEY is supplied by the launch environment;
# no key is stored in this project or passed to Godot.
"$bundled_python" "$bundle_dir/scripts/npc_ai_bridge.py" --port "$CITY_NPC_PLANNER_PORT" >"$npc_log" 2>&1 &
npc_pid=$!
echo "NPC planner: running (PID $npc_pid, log $npc_log)"

"$bundle_dir/runtime/linux-x86_64/godot" "${display_args[@]}" --path "$bundle_dir" "$@"
