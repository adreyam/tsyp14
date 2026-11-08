extends SceneTree

func _init() -> void:
	call_deferred("_test")

func _fail(message: String) -> void:
	printerr("NPC CITY-LIFE TEST FAILED: " + message)
	quit(1)

func _test() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate()
	root.add_child(city)
	await process_frame
	await create_timer(1.0).timeout
	var director := city.get_node("NpcAi")
	var agents: Array = director.call("get_agent_debug")
	if agents.size() != get_nodes_in_group("pedestrians").size() or agents.size() != 32:
		_fail("not every registered citizen is controlled")
		return
	var actions := 0
	var moving := 0
	var roles: Dictionary = {}
	var stations: Dictionary = {}
	var authored_players := 0
	var active_people: Array[Node3D] = []
	for state_value in agents:
		var state := state_value as Dictionary
		var person := state.node as Node3D
		var player := person.find_child("AnimationPlayer", true, false) as AnimationPlayer
		if player == null or not bool(person.get_meta("npc_ai_controlled", false)):
			_fail("%s has no controlled authored character animation" % person.name)
			return
		if person.has_node("RoleAnimationRig"):
			_fail("%s still contains the rejected mannequin rig" % person.name)
			return
		if person.has_node("HiddenAnimationSource"):
			_fail("%s still contains the rejected retarget source" % person.name)
			return
		authored_players += 1
		if absf(person.global_position.y - float(person.get_meta("ground_y", person.global_position.y))) > 0.02:
			_fail("%s is not grounded" % person.name)
			return
		if (state.get("planner_payload", {}) as Dictionary).get("route", "") != "reserved_two_lane_sidewalk_graph":
			_fail("%s lacks a protected city-sidewalk route" % person.name)
			return
		roles[str(state.role)] = true
		stations[str(state.station_id)] = true
		if str(state.motion_state).begins_with("performing_") or str(state.motion_state).begins_with("interacting_"):
			actions += 1
			active_people.append(person)
			if not (player.current_animation.begins_with("rocketbox/") or player.current_animation.begins_with("rb/")):
				_fail("%s is not using a safe authored station animation" % person.name)
				return
		elif str(state.motion_state).begins_with("travelling_") or str(state.motion_state).begins_with("walking_") or str(state.motion_state).begins_with("yielding_"):
			moving += 1
	if actions < 22 or moving > 10:
		_fail("opening world is not activity-first (%d active, %d travelling)" % [actions, moving])
		return
	if roles.size() != 20 or stations.size() != 32 or authored_players != 32:
		_fail("incomplete authored citizen coverage: %d roles, %d stations, %d players" % [roles.size(),stations.size(),authored_players])
		return
	if get_nodes_in_group("npc_activity_props").size()!=64:
		_fail("every job does not have its own visible world asset")
		return
	for first_index in active_people.size():
		for second_index in range(first_index + 1, active_people.size()):
			if active_people[first_index].global_position.distance_to(active_people[second_index].global_position) < 1.2:
				_fail("%s and %s share the same work slot" % [active_people[first_index].name, active_people[second_index].name])
				return
	for backdrop_value in get_nodes_in_group("backdrop_pedestrians"):
		if (backdrop_value as Node3D).visible:
			_fail("obsolete backdrop loop remains visible")
			return
	print("NPC CITY-LIFE TEST PASSED: %d authored citizens active across %d staffed sites; %d travelling" % [actions, stations.size(), moving])
	quit()
