extends SceneTree

const HAND_BONES := ["Bip01 L Hand", "Bip01 R Hand"]

func _initialize() -> void:
	call_deferred("_audit")

func _closest_distance(point: Vector3, bounds: AABB) -> float:
	var closest := Vector3(
		clampf(point.x, bounds.position.x, bounds.end.x),
		clampf(point.y, bounds.position.y, bounds.end.y),
		clampf(point.z, bounds.position.z, bounds.end.z)
	)
	return point.distance_to(closest)

func _world_bounds(model: Node3D) -> AABB:
	var result := AABB()
	var found := false
	for value in model.find_children("*", "MeshInstance3D", true, false):
		var mesh := value as MeshInstance3D
		if mesh.mesh == null:
			continue
		var value_bounds := mesh.global_transform * mesh.mesh.get_aabb()
		result = value_bounds if not found else result.merge(value_bounds)
		found = true
	return result if found else AABB()

func _prop_for_station(station_id: String) -> Node3D:
	for value in get_nodes_in_group("npc_activity_props"):
		if value is Node3D and str((value as Node3D).get_meta("activity_station", "")) == station_id:
			return value as Node3D
	return null

func _minimum_hand_distance(person: Node3D, prop: Node3D) -> float:
	var skeleton := person.find_child("Skeleton3D", true, false) as Skeleton3D
	if skeleton == null:
		return INF
	var bounds := _world_bounds(prop)
	var result := INF
	for bone_name in HAND_BONES:
		var index := skeleton.find_bone(bone_name)
		if index >= 0:
			var point := skeleton.global_transform * skeleton.get_bone_global_pose(index).origin
			result = minf(result, _closest_distance(point, bounds))
	return result

func _minimum_visible_tool_distance(person: Node3D, prop: Node3D) -> float:
	var prop_bounds := _world_bounds(prop)
	var result := INF
	for value in person.find_children("*", "Node3D", true, false):
		var tool := value as Node3D
		if not tool.visible or not tool.is_in_group("npc_job_tools"):
			continue
		var tool_bounds := _world_bounds(tool)
		if tool_bounds.size == Vector3.ZERO:
			continue
		var corners := [
			tool_bounds.position,
			Vector3(tool_bounds.end.x, tool_bounds.position.y, tool_bounds.position.z),
			Vector3(tool_bounds.position.x, tool_bounds.end.y, tool_bounds.position.z),
			Vector3(tool_bounds.position.x, tool_bounds.position.y, tool_bounds.end.z),
			Vector3(tool_bounds.end.x, tool_bounds.end.y, tool_bounds.position.z),
			Vector3(tool_bounds.end.x, tool_bounds.position.y, tool_bounds.end.z),
			Vector3(tool_bounds.position.x, tool_bounds.end.y, tool_bounds.end.z),
			tool_bounds.end,
			tool_bounds.get_center(),
		]
		for corner in corners:
			result = minf(result, _closest_distance(corner, prop_bounds))
	return result

func _audit() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	root.add_child(city)
	await process_frame
	await create_timer(0.5).timeout
	var director := city.get_node("NpcAi")
	var minimum_by_clip := {}
	var maximum_by_clip := {}
	var samples_by_clip := {}
	var prop_by_clip := {}
	var minimum_tool_by_clip := {}
	var minimum_contact_by_clip_prop := {}
	Engine.time_scale = 5.0
	# At 5x society time, the earlier 35 ms cadence could jump over the short
	# watering-can contact phase. Sample densely enough to evaluate contact,
	# rather than pass/fail according to timer aliasing.
	for _sample in 400:
		await create_timer(0.015, true, false, true).timeout
		for value in director.call("get_agent_debug"):
			var state := value as Dictionary
			if not bool(state.guaranteed_prop_job) or not bool(state.prop_interaction_active):
				continue
			var prop := _prop_for_station(str(state.station_id))
			if prop == null:
				continue
			var clip := str(state.animation_clip)
			var distance := _minimum_hand_distance(state.node as Node3D, prop)
			var tool_distance := _minimum_visible_tool_distance(state.node as Node3D, prop)
			minimum_by_clip[clip] = minf(float(minimum_by_clip.get(clip, INF)), distance)
			minimum_tool_by_clip[clip] = minf(float(minimum_tool_by_clip.get(clip, INF)), tool_distance)
			var clip_prop_key := "%s|%s" % [clip, str(state.job_prop_kind)]
			var effective_distance := tool_distance if not is_inf(tool_distance) else distance
			minimum_contact_by_clip_prop[clip_prop_key] = minf(float(minimum_contact_by_clip_prop.get(clip_prop_key, INF)), effective_distance)
			maximum_by_clip[clip] = maxf(float(maximum_by_clip.get(clip, 0.0)), distance)
			samples_by_clip[clip] = int(samples_by_clip.get(clip, 0)) + 1
			prop_by_clip[clip] = str(state.job_prop_kind)
	Engine.time_scale = 1.0
	var clips := minimum_by_clip.keys()
	clips.sort()
	var failed := false
	for clip in clips:
		print("FIX75_HAND_CONTACT|clip=%s|prop=%s|min_m=%.3f|tool_min_m=%s|max_m=%.3f|samples=%d" % [str(clip), str(prop_by_clip[clip]), float(minimum_by_clip[clip]), "none" if is_inf(float(minimum_tool_by_clip[clip])) else "%.3f" % float(minimum_tool_by_clip[clip]), float(maximum_by_clip[clip]), int(samples_by_clip[clip])])
		# Hand bones end at the palm, so 0.42 m includes forearm/hand length and
		# the visible fingers. Every counted physical motion must enter that reach
		# envelope at least once during its authored action cycle.
		var tool_was_visible := not is_inf(float(minimum_tool_by_clip[clip]))
		var valid_contact := float(minimum_tool_by_clip[clip]) <= 0.12 if tool_was_visible else float(minimum_by_clip[clip]) <= 0.42
		if not valid_contact:
			printerr("FIX75 HAND/PROP CONTACT TEST FAILED: %s never reaches %s (hand %.3f m / tool %s)" % [str(clip), str(prop_by_clip[clip]), float(minimum_by_clip[clip]), "none" if is_inf(float(minimum_tool_by_clip[clip])) else "%.3f m" % float(minimum_tool_by_clip[clip])])
			failed = true
	var clip_prop_keys := minimum_contact_by_clip_prop.keys()
	clip_prop_keys.sort()
	for key_value in clip_prop_keys:
		var key := str(key_value)
		if key.begins_with("Farm_Watering|"):
			print("FIX78_TOOL_CONTACT_DETAIL|%s|min_m=%.3f" % [key, float(minimum_contact_by_clip_prop[key])])
	if clips.size() < 8:
		printerr("FIX75 HAND/PROP CONTACT TEST FAILED: only %d distinct physical clips were sampled" % clips.size())
		failed = true
	if failed:
		quit(1)
		return
	print("FIX78_HAND_OR_TOOL_PROP_CONTACT_TEST_OK clips=%d hand_reach_limit=0.42m tool_contact_limit=0.12m" % clips.size())
	quit(0)
