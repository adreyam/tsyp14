#!/usr/bin/env bash
set -euo pipefail
bundle_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
sumo_root="$bundle_dir/runtime/sumo"
sumo_lib="$sumo_root/usr/lib/x86_64-linux-gnu:$sumo_root/usr/lib/x86_64-linux-gnu/blas:$sumo_root/usr/lib/x86_64-linux-gnu/lapack:$sumo_root/usr/lib"
export SUMO_HOME="$sumo_root/usr/share/sumo"
export LD_LIBRARY_PATH="$sumo_lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONHOME="$sumo_root/usr"

godot="$bundle_dir/runtime/linux-x86_64/godot"
python="$sumo_root/usr/bin/python3.14"

"$sumo_root/usr/bin/sumo" --version >/dev/null
"$sumo_root/usr/bin/netconvert" --version >/dev/null
"$python" "$bundle_dir/scripts/test_sumo_behavior.py"
"$python" "$bundle_dir/scripts/test_sumo_lane_alignment.py"
"$python" "$bundle_dir/scripts/test_sumo_boundary_flows.py"
"$python" "$bundle_dir/scripts/test_sumo_signal_timing.py"
"$python" "$bundle_dir/scripts/test_sumo_stop_reasons.py"
"$python" "$bundle_dir/scripts/test_control_room_bridge.py"
"$python" "$bundle_dir/scripts/test_control_room_monitor.py"
"$python" "$bundle_dir/scripts/test_npc_planner.py"
"$python" "$bundle_dir/scripts/sumo_bridge.py" --test --test-steps 400
"$python" "$bundle_dir/scripts/test_sumo_visual_continuity.py"
"$python" "$bundle_dir/scripts/test_sumo_stable_visible_fleet.py"
"$python" "$bundle_dir/scripts/test_emergency_fleet.py"
"$python" "$bundle_dir/scripts/test_incidents.py"
"$python" "$bundle_dir/scripts/test_parking_lots.py"
"$godot" --headless --editor --path "$bundle_dir" --quit
"$godot" --headless --path "$bundle_dir" --quit-after 3
"$godot" --headless --path "$bundle_dir" --scene res://scenes/ControlRoom.tscn --quit-after 3
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_unified_control_room.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_smart_city_assets.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_sim_api.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_parking_lots.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_earthquakes.gd"
"$python" -m unittest discover -s "$bundle_dir/tests/mcp" -p "test_*.py"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_emergency_depots.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_asset_map_ui.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_npc_panel_layout.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_right_panel_spacing.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_vehicle_models_isolated.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_vehicle_wheel_pivots.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_vehicle_grounding.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_pedestrian_grounding.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_society_kernel.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_citizen_society_integration.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_citizen_trip_continuity.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_citizen_activity_quality.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_active_meter_workers_and_random_selection.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_npc_ai_sidewalks.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_living_npc_roles.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_npc_life_progression.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_npc_action_safety.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_guaranteed_prop_job_occupancy.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_city_spatial_safety.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_npc_pose_prop_grounding.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_crouch_transition_grounding.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_grounded_worktable_and_palm_grip.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/audit_fix75_hand_prop_contact.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_rocketbox_npc_quality.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_rocketbox_animation_transfer.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_npc_character_interactions.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_npc_prop_scale.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/audit_building_road_bounds.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/audit_npc_activity_sites.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_object_selection_camera.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_first_person_camera_bounds.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_traffic_camera_spatial_safety.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_traffic_visual_mapping.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_vehicle_camera_lifecycle.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_water_reservoirs.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_power_grid.gd"
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_utility_resources.gd"

bridge_log="/tmp/city_ground_zero_sumo_test_${UID}.log"
sumo_test_port="$("$python" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
sumo_control_test_port="$("$python" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
export SUMO_TEST_PORT="$sumo_test_port"
bridge_ready="/tmp/city_ground_zero_sumo_ready_${UID}_$$"
rm -f "$bridge_ready"
"$python" "$bundle_dir/scripts/sumo_bridge.py" --port "$sumo_test_port" --control-port "$sumo_control_test_port" --ready-file "$bridge_ready" >"$bridge_log" 2>&1 &
bridge_pid=$!
cleanup_bridge() {
    if kill -0 "$bridge_pid" 2>/dev/null; then
        kill "$bridge_pid" 2>/dev/null || true
        wait "$bridge_pid" 2>/dev/null || true
    fi
}
trap cleanup_bridge EXIT INT TERM
# Start Godot only once the bridge broadcasts (warm-up finished).
for _ in $(seq 1 600); do
    [[ -s "$bridge_ready" ]] && break
    kill -0 "$bridge_pid" 2>/dev/null || break
    sleep 0.1
done
[[ -s "$bridge_ready" ]] || { echo "SUMO bridge did not become ready"; tail -n 20 "$bridge_log"; exit 1; }
"$godot" --headless --path "$bundle_dir" --script "$bundle_dir/tests/test_sumo_integration.gd"
cleanup_bridge
trap - EXIT INT TERM

echo "City Ground Zero passed bundled Godot, SUMO, TraCI, scene, movement, and signal integration tests."
