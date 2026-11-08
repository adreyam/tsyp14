extends SceneTree

func _init() -> void:
	call_deferred("_test")

func _fail(message: String) -> void:
	printerr("LIVING NPC TEST FAILED: " + message)
	quit(1)

func _test() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate()
	root.add_child(city)
	await process_frame
	await create_timer(1.0).timeout
	var director := city.get_node("NpcAi")
	var agents: Array = director.call("get_agent_debug")
	if agents.size() != 32:
		_fail("expected 32 registered citizens, got %d" % agents.size())
		return
	var active_actions := 0
	var paired_people := 0
	var travelling := 0
	var roles: Dictionary = {}
	var stations: Dictionary = {}
	var active_people: Array[Node3D] = []
	for state_value in agents:
		var state := state_value as Dictionary
		var citizen := state.node as Node3D
		var player := citizen.find_child("AnimationPlayer", true, false) as AnimationPlayer
		if player == null:
			_fail("%s does not have its authored animation player" % citizen.name)
			return
		if citizen.has_node("RoleAnimationRig"):
			_fail("%s still contains a mannequin replacement rig" % citizen.name)
			return
		if citizen.has_node("HiddenAnimationSource"):
			_fail("%s still contains the rejected retarget source" % citizen.name)
			return
		if str(state.get("role", "")).is_empty() or str(state.get("task", "")).is_empty() or str(state.get("station_id", "")).is_empty():
			_fail("%s has no concrete role, task, and station" % citizen.name)
			return
		if (state.get("planner_payload", {}) as Dictionary).get("route", "") != "reserved_two_lane_sidewalk_graph":
			_fail("%s is not assigned to the connected sidewalk network" % citizen.name)
			return
		roles[str(state.role)] = true
		stations[str(state.station_id)] = true
		if str(state.motion_state).begins_with("performing_") or str(state.motion_state).begins_with("interacting_"):
			active_actions += 1
			active_people.append(citizen)
			if str(state.motion_state).begins_with("interacting_"):
				paired_people += 1
				if str(state.get("interaction_partner", "")).is_empty() or int(state.get("station_capacity", 0)) != 2:
					_fail("%s has an incomplete paired-interaction reservation" % citizen.name)
					return
			if not (player.current_animation.begins_with("rocketbox/") or player.current_animation.begins_with("rb/")):
				_fail("%s reports work but is not using a safe authored animation" % citizen.name)
				return
		elif str(state.motion_state).begins_with("travelling_") or str(state.motion_state).begins_with("walking_") or str(state.motion_state).begins_with("yielding_"):
			travelling += 1
	if active_actions < 22:
		_fail("opening distribution has only %d active role actions" % active_actions)
		return
	if travelling > 10:
		_fail("opening distribution is dominated by travel (%d citizens)" % travelling)
		return
	if roles.size() != 20 or stations.size() != 32:
		_fail("role-world placement is too small: %d roles, %d stations" % [roles.size(), stations.size()])
		return
	if get_nodes_in_group("npc_activity_props").size()!=64:
		_fail("expected 64 matching world assets for 64 scheduled job stops")
		return
	# Tools and carried items belong to each citizen's ClipDirector (real-size models placed in a
	# real grip): every station that names a tool or item must be able to build it, and at least a few are
	# visibly in somebody's hands during the opening activities.
	const ItemFactory := preload("res://scripts/props/item_factory.gd")
	var stops_with_items := 0
	for station_value in (director.get("_stations") as Dictionary).values():
		var station := station_value as Dictionary
		for key in ["tool", "item"]:
			var kind := str(station.get(key, ""))
			if kind.is_empty():
				continue
			stops_with_items += 1
			var probe := ItemFactory.build(kind)
			if probe == null and not kind in ["meter_reader", "cup", "watering_can"]:
				_fail("station %s names an item that cannot be built: %s" % [str(station.id), kind])
				return
			if probe != null:
				probe.free()
	if stops_with_items < 10:
		_fail("expected at least 10 stops with a tool or carried item, got %d" % stops_with_items)
		return
	for first_index in active_people.size():
		for second_index in range(first_index + 1, active_people.size()):
			if active_people[first_index].global_position.distance_to(active_people[second_index].global_position) < 1.2:
				_fail("%s and %s overlap at work" % [active_people[first_index].name, active_people[second_index].name])
				return
	if paired_people != 12 or get_nodes_in_group("npc_interaction_props").size() != 6:
		_fail("expected six complete two-character interactions, got %d people / %d props" % [paired_people, get_nodes_in_group("npc_interaction_props").size()])
		return
	for backdrop_value in get_nodes_in_group("backdrop_pedestrians"):
		if (backdrop_value as Node3D).visible:
			_fail("old backdrop loop actor %s is still visible" % (backdrop_value as Node3D).name)
			return
	print("LIVING NPC TEST PASSED: %d active authored actions (%d in synchronized pairs), %d travelling, %d roles, %d stations" % [active_actions, paired_people, travelling, roles.size(), stations.size()])
	quit()
