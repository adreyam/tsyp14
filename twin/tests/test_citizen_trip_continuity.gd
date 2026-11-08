extends SceneTree


func _init() -> void:
	call_deferred("_test")


func _fail(message: String) -> void:
	printerr("CITIZEN TRIP CONTINUITY TEST FAILED: " + message)
	Engine.time_scale = 1.0
	quit(1)


func _test() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	root.add_child(city)
	await process_frame
	await create_timer(0.7).timeout
	var director := city.get_node("NpcAi")
	var opening := director.call("get_society_debug") as Dictionary
	if int(opening.trips_total) != 4 or int(opening.trips_active) != 4:
		_fail("opening commuters are not represented by four authoritative trips")
		return
	var previous_positions := {}
	var largest_sample_step := 0.0
	var largest_step_citizen := ""
	var largest_step_state := ""
	Engine.time_scale = 30.0
	# Observe every physics update instead of using a real-time timer.  A timer
	# callback can be delayed by a slow CI/import frame and then fold several
	# perfectly legal walking updates into one apparent 10+ metre "teleport".
	# Per-tick sampling is both stricter and deterministic: at 30x time and the
	# default 60 Hz physics rate, a 1.55 m/s citizen can move at most 0.775 m.
	# 720 physics updates preserve the original six simulated-minute window.
	for sample in 720:
		await physics_frame
		for state_value in director.call("get_agent_debug"):
			var state := state_value as Dictionary
			var citizen_id := str(state.citizen_id)
			var position := (state.node as Node3D).global_position
			if previous_positions.has(citizen_id):
				var previous_position := previous_positions[citizen_id] as Vector3
				# Root-only foot grounding legitimately changes Y as authored poses
				# crouch or stride. Trip continuity governs map-plane movement; the
				# dedicated grounding audit validates vertical foot contact.
				var sample_step := Vector2(position.x, position.z).distance_to(Vector2(previous_position.x, previous_position.z))
				if sample_step > largest_sample_step:
					largest_sample_step = sample_step
					largest_step_citizen = citizen_id
					largest_step_state = str(state.get("motion_state", "unknown"))
			previous_positions[citizen_id] = position
	Engine.time_scale = 1.0
	var summary := director.call("get_society_debug") as Dictionary
	if int(summary.trips_completed) < 1:
		_fail("no validated sidewalk arrival committed to the trip ledger after an accelerated traversal")
		return
	if int(summary.trips_total) <= 4:
		_fail("completed activities did not create subsequent scheduled trips")
		return
	# Allow the exact configured per-tick walk distance plus a small scheduling
	# and station-arrival epsilon.  This is substantially tighter than the old
	# real-time sampling ceiling and catches any station-scale transform jump.
	if largest_sample_step > 0.90:
		_fail("citizen %s moved %.3f m in one physics tick while %s" % [largest_step_citizen, largest_sample_step, largest_step_state])
		return
	if not (summary.invariant_errors as Array).is_empty():
		_fail("trip execution broke society invariants: %s" % str(summary.invariant_errors))
		return
	var saw_trip_completion := false
	for event_value in director.call("get_society_recent_events", 1000):
		if str((event_value as Dictionary).kind) == "trip_completed":
			saw_trip_completion = true
			break
	if not saw_trip_completion:
		_fail("arrival was not preserved in the causal event ledger")
		return
	var spatial := director.call("get_spatial_debug") as Dictionary
	if int(spatial.minimum_observed_prop_workers) < 10:
		_fail("long trips broke the continuous real-work presentation contract")
		return
	print("CITIZEN_TRIP_CONTINUITY_TEST_OK completed=%d total=%d active=%d max_physics_step=%.3fm prop_worker_min=%d" % [
		int(summary.trips_completed),
		int(summary.trips_total),
		int(summary.trips_active),
		largest_sample_step,
		int(spatial.minimum_observed_prop_workers)
	])
	quit(0)
