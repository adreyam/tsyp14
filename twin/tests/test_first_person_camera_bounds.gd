extends SceneTree


func _initialize() -> void:
	call_deferred("_test")


func _fail(message: String) -> void:
	printerr("FIRST PERSON CAMERA BOUNDS FAILED: " + message)
	quit(1)


func _test() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	root.add_child(city)
	await process_frame
	(city.get_node("CityLife") as Node).process_mode = Node.PROCESS_MODE_DISABLED
	(city.get_node("NpcAi") as Node).process_mode = Node.PROCESS_MODE_DISABLED
	var camera := city.get_node("EstablishingCamera") as Camera3D
	var targets := []
	for group_name in ["vehicles", "backdrop_vehicles", "pedestrians", "backdrop_pedestrians"]:
		targets.append_array(get_nodes_in_group(group_name))
	var seen := {}
	var people := 0
	var vehicles := 0
	var minimum_clearance := INF
	for target_value in targets:
		if not target_value is Node3D:
			continue
		var target := target_value as Node3D
		if seen.has(target.get_instance_id()):
			continue
		seen[target.get_instance_id()] = true
		var bounds: AABB = camera.call("_world_bounds", target)
		if bounds.size.length_squared() < 0.001:
			continue
		camera.call("_select_target", target)
		camera.call("_toggle_first_person")
		camera.call("_apply_first_person_view")
		# Growing the body/vehicle bounds turns the intended safety gap into an
		# assertion: merely landing one floating-point unit outside is not enough.
		if bounds.grow(0.10).has_point(camera.global_position):
			_fail("%s first-person origin has less than 0.10 m exterior clearance" % target.name)
			return
		minimum_clearance = minf(minimum_clearance, _distance_to_aabb(camera.global_position, bounds))
		var before_position := camera.global_position
		var before_direction := -camera.global_transform.basis.z
		var mouse_event := InputEventMouseMotion.new()
		mouse_event.relative = Vector2(215.0, -84.0)
		camera.call("_unhandled_input", mouse_event)
		if camera.global_position.distance_to(before_position) > 0.002:
			_fail("%s mouse-look moved the bounded camera origin" % target.name)
			return
		if (-camera.global_transform.basis.z).dot(before_direction) > 0.99:
			_fail("%s did not rotate in response to unmodified mouse movement" % target.name)
			return
		if bounds.grow(0.10).has_point(camera.global_position):
			_fail("%s mouse-look entered its rendered bounds" % target.name)
			return
		if camera.call("_target_kind", target) == "person":
			people += 1
		else:
			vehicles += 1
	if people == 0 or vehicles == 0:
		_fail("both citizen and vehicle target families were not audited")
		return
	print("FIRST_PERSON_CAMERA_BOUNDS_OK people=%d vehicles=%d min_exterior_clearance=%.3fm mouse_look=unmodified_360_yaw" % [people, vehicles, minimum_clearance])
	quit(0)


func _distance_to_aabb(point: Vector3, bounds: AABB) -> float:
	var dx := maxf(maxf(bounds.position.x - point.x, point.x - bounds.end.x), 0.0)
	var dy := maxf(maxf(bounds.position.y - point.y, point.y - bounds.end.y), 0.0)
	var dz := maxf(maxf(bounds.position.z - point.z, point.z - bounds.end.z), 0.0)
	return Vector3(dx, dy, dz).length()
