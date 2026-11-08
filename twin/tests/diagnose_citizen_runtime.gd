extends SceneTree

func _initialize() -> void:
	call_deferred("_run")


func _percentile(values: Array[float], fraction: float) -> float:
	if values.is_empty():
		return 0.0
	var ordered := values.duplicate()
	ordered.sort()
	return float(ordered[clampi(int(round((ordered.size() - 1) * fraction)), 0, ordered.size() - 1)])


func _run() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	root.add_child(city)
	await process_frame
	await create_timer(0.5).timeout
	var director := city.get_node("NpcAi") as Node
	var initial_pairs := {}
	var initial_pair_positions := {}
	for state_value in director.call("get_agent_debug"):
		var state := state_value as Dictionary
		if not str(state.interaction_id).is_empty():
			initial_pairs[str(state.citizen_id)] = str(state.interaction_id)
			initial_pair_positions[str(state.citizen_id)] = (state.node as Node3D).global_position
	Engine.time_scale = 5.0
	var frame_ms: Array[float] = []
	var last_position := {}
	var last_direction := {}
	var travel_distance := {}
	var reversals := {}
	var maximum_yield_run := {}
	var yield_run := {}
	var inert_samples := 0
	var inert_details := {}
	var latest_state := {}
	var performing_samples := 0
	var released_samples := 0
	var released_walking_samples := 0
	var last_tick := Time.get_ticks_usec()
	for frame in 360:
		await physics_frame
		var now := Time.get_ticks_usec()
		if frame > 20:
			frame_ms.append(float(now - last_tick) / 1000.0)
		last_tick = now
		if frame % 4 != 0:
			continue
		for state_value in director.call("get_agent_debug"):
			var state := state_value as Dictionary
			var citizen_id := str(state.citizen_id)
			latest_state[citizen_id] = state
			var position := (state.node as Node3D).global_position
			if last_position.has(citizen_id):
				var displacement := position - (last_position[citizen_id] as Vector3)
				displacement.y = 0.0
				travel_distance[citizen_id] = float(travel_distance.get(citizen_id, 0.0)) + displacement.length()
				if displacement.length() > 0.025:
					var direction := displacement.normalized()
					if last_direction.has(citizen_id) and direction.dot(last_direction[citizen_id] as Vector3) < -0.35:
						reversals[citizen_id] = int(reversals.get(citizen_id, 0)) + 1
					last_direction[citizen_id] = direction
			last_position[citizen_id] = position
			var motion := str(state.motion_state)
			if motion.begins_with("yielding_"):
				yield_run[citizen_id] = int(yield_run.get(citizen_id, 0)) + 1
				maximum_yield_run[citizen_id] = maxi(int(maximum_yield_run.get(citizen_id, 0)), int(yield_run[citizen_id]))
			else:
				yield_run[citizen_id] = 0
			if motion.begins_with("performing_"):
				performing_samples += 1
				if not bool(state.stationary_action_readable):
					inert_samples += 1
					var detail_key := "%s|%s|%s|%s" % [citizen_id, str(state.role), str(state.animation_clip), str(state.station_id)]
					inert_details[detail_key] = int(inert_details.get(detail_key, 0)) + 1
			if initial_pairs.has(citizen_id) and str(state.interaction_id).is_empty():
				released_samples += 1
				if motion.begins_with("walking_") or motion.begins_with("yielding_"):
					released_walking_samples += 1
	Engine.time_scale = 1.0
	var total_reversals := 0
	var worst_reversals := 0
	var worst_yield_samples := 0
	var worst_yield_citizen := ""
	for citizen_id in initial_pairs.keys():
		total_reversals += int(reversals.get(citizen_id, 0))
		worst_reversals = maxi(worst_reversals, int(reversals.get(citizen_id, 0)))
		var citizen_yield := int(maximum_yield_run.get(citizen_id, 0))
		if citizen_yield > worst_yield_samples:
			worst_yield_samples = citizen_yield
			worst_yield_citizen = str(citizen_id)
		if latest_state.has(citizen_id):
			var pair_state := latest_state[citizen_id] as Dictionary
			var pair_position := (pair_state.node as Node3D).global_position
			var initial_position := initial_pair_positions[citizen_id] as Vector3
			print("CITIZEN_RUNTIME_PAIR citizen=%s event=%s displacement=%.3f travelled=%.3f motion=%s station=%s blocker=%s position=%s waypoint=%s" % [str(citizen_id), str(initial_pairs[citizen_id]), pair_position.distance_to(initial_position), float(travel_distance.get(citizen_id, 0.0)), str(pair_state.motion_state), str(pair_state.station_id), str(pair_state.last_block_reason), str(pair_position), str(pair_state.next_waypoint)])
	print("CITIZEN_RUNTIME_DIAGNOSTIC frame_p50_ms=%.2f frame_p95_ms=%.2f released_walk=%d/%d pair_reversals=%d worst_pair_reversals=%d worst_yield=%s:%d inert_station_samples=%d/%d" % [
		_percentile(frame_ms, 0.50), _percentile(frame_ms, 0.95), released_walking_samples, released_samples,
		total_reversals, worst_reversals, worst_yield_citizen, worst_yield_samples, inert_samples, performing_samples
	])
	if not inert_details.is_empty():
		print("CITIZEN_RUNTIME_INERT_DETAILS ", JSON.stringify(inert_details))
	if not worst_yield_citizen.is_empty() and latest_state.has(worst_yield_citizen):
		var worst_state := latest_state[worst_yield_citizen] as Dictionary
		print("CITIZEN_RUNTIME_WORST_YIELD citizen=%s role=%s motion=%s station=%s reversals=%d yield_events=%d blocker=%s position=%s waypoint=%s" % [
			worst_yield_citizen, str(worst_state.role), str(worst_state.motion_state), str(worst_state.station_id),
			int(worst_state.avoidance_reversals), int(worst_state.yield_events), str(worst_state.last_block_reason), str((worst_state.node as Node3D).global_position), str(worst_state.next_waypoint)
		])
		var worst_position := (worst_state.node as Node3D).global_position
		for citizen_id in latest_state.keys():
			if str(citizen_id) == worst_yield_citizen:
				continue
			var neighbor := latest_state[citizen_id] as Dictionary
			var distance := worst_position.distance_to((neighbor.node as Node3D).global_position)
			if distance < 3.0:
				print("CITIZEN_RUNTIME_NEIGHBOR citizen=%s distance=%.2f motion=%s station=%s position=%s waypoint=%s" % [str(citizen_id), distance, str(neighbor.motion_state), str(neighbor.station_id), str((neighbor.node as Node3D).global_position), str(neighbor.next_waypoint)])
		for obstacle_value in director.get("_static_obstacles") as Array:
			var obstacle := obstacle_value as Dictionary
			var obstacle_node := obstacle.get("node") as Node3D
			if is_instance_valid(obstacle_node) and str(worst_state.last_block_reason).ends_with(str(obstacle_node.name)):
				print("CITIZEN_RUNTIME_BLOCKER node=%s kind=%s position=%s radius=%.2f bounds=%s" % [str(obstacle_node.name), str(obstacle.kind), str(obstacle.get("position", Vector3.ZERO)), float(obstacle.get("radius", 0.0)), str(obstacle.get("bounds", AABB()))])
	quit(0)
