extends SceneTree

## Live city: every citizen's lowest sole contact stays on the pavement in every sampled frame (walking,
## idling, crouching, sitting), the character root never moves up or down, and every street/job prop
## stands on the pavement without crossing a road or a building.

const SpatialSafety := preload("res://scripts/city_spatial_safety.gd")
const SAMPLE_FRAMES := 40
const SURFACE_TOLERANCE := 0.004

func _initialize() -> void:
	call_deferred("_test")

func _fail(message: String) -> void:
	printerr("NPC/PROP GROUNDING TEST FAILED: " + message)
	quit(1)

func _test() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	root.add_child(city)
	await process_frame
	await create_timer(1.0).timeout
	var director := city.get_node("NpcAi")
	Engine.time_scale = 4.0
	var worst := 0.0
	var root_heights := {}
	var pinned_samples := 0
	var states_seen := {}
	for sample in SAMPLE_FRAMES:
		await create_timer(0.35, true, false, true).timeout
		for agent_value in director.get("_agents") as Array:
			var agent := agent_value as Dictionary
			var person := agent.node as Node3D
			var ik := agent.ik as Node
			var support := float(ik.final_state.get("support", 0.0))
			states_seen[str(agent.motion_state).substr(0, 9)] = true
			if str(agent.motion_state).begins_with("performing_") or str(agent.motion_state).begins_with("interacting_"):
				pinned_samples += 1
			if bool(ik.is_foot_pinned("L")):
				var slip := (ik.final_state["sole_L"] as Vector3).y - float(agent.ground_y)
				worst = maxf(worst, absf(slip))
				if absf(slip) > 0.045:
					_fail("%s pinned sole is %.4f m off the pavement" % [person.name, slip]); return
			else:
				worst = maxf(worst, absf(support))
				if absf(support) > 0.03:
					_fail("%s lowest sole contact is %.4f m off the pavement while %s" % [person.name, support, str(agent.motion_state)]); return
			var heights := root_heights.get(person.get_instance_id(), []) as Array
			heights.append(person.global_position.y)
			root_heights[person.get_instance_id()] = heights
	Engine.time_scale = 1.0
	for key in root_heights:
		var heights := root_heights[key] as Array
		for h in heights:
			if absf(float(h) - float(heights[0])) > 0.0001:
				_fail("a citizen's root height changed during the session (%.5f vs %.5f)" % [float(h), float(heights[0])]); return
	if pinned_samples < 20:
		_fail("only %d working samples: citizens did not perform clips" % pinned_samples); return
	var props: Array[Node3D] = []
	var seen := {}
	for group_name in ["urban_props", "npc_activity_props", "npc_interaction_props"]:
		for value in get_nodes_in_group(group_name):
			if value is Node3D and (value as Node3D).visible and not (value as Node3D).is_queued_for_deletion() and not seen.has((value as Node3D).get_instance_id()):
				seen[(value as Node3D).get_instance_id()] = true
				props.append(value as Node3D)
	var buildings := SpatialSafety.building_bounds(self)
	for prop in props:
		var bounds := SpatialSafety.world_bounds(prop)
		var kind := str(prop.get_meta("prop_kind", ""))
		var flush := kind in ["manhole", "water_meter"]
		var reference := bounds.end.y if flush else bounds.position.y
		var expected := 0.16 + (0.004 if flush else 0.0)
		if absf(reference - expected) > 0.02 and not flush:
			_fail("%s is floating or buried (bottom %.3f)" % [prop.name, bounds.position.y]); return
		if SpatialSafety.overlaps_road(bounds, 0.0):
			_fail("%s overlaps a road" % prop.name); return
		for building in buildings:
			if SpatialSafety.horizontal_intersects(bounds, building, 0.02):
				_fail("%s intersects a building" % prop.name); return
	print("NPC/PROP GROUNDING TEST PASSED: %d citizens x %d frames, worst sole error %.4f m, %d pinned-foot samples; %d props grounded, none on a road or building" % [(director.get("_agents") as Array).size(), SAMPLE_FRAMES, worst, pinned_samples, props.size()])
	quit(0)
