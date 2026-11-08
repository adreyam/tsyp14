extends SceneTree


func _init() -> void:
	call_deferred("_test")


func _test() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	root.add_child(city)
	await process_frame
	await process_frame
	var grid := city.get_node_or_null("PowerGrid") as Node3D
	if grid == null:
		_fail("City does not contain the PowerGrid scene")
		return
	var status := grid.call("get_grid_status") as Dictionary
	if int(status.get("sources", 0)) != 2 or int(status.get("nodes", 0)) != 19 or int(status.get("edges", 0)) != 24:
		_fail("power topology is incomplete: %s" % status)
		return
	if int(status.get("powered_consumers", 0)) != 9 or int(status.get("total_consumers", 0)) != 9:
		_fail("not every reservoir and existing power switch is energized: %s" % status)
		return
	if get_nodes_in_group("power_sources").size() != 2 or get_nodes_in_group("power_poles").size() < 140 or get_nodes_in_group("power_lines").size() < 500:
		_fail("power grid lacks the required physical source, pole, or cable network")
		return
	var road_surfaces := _road_surfaces()
	for site_value in get_nodes_in_group("water_reservoir_sites"):
		if not bool((site_value as Node3D).get_meta("power_online", false)):
			_fail("reservoir %s is disconnected" % (site_value as Node3D).name)
			return
	for switch_value in get_nodes_in_group("power_switches"):
		if not bool((switch_value as Node3D).get_meta("power_online", false)):
			_fail("power switch %s is disconnected" % (switch_value as Node3D).name)
			return
	for pole_value in get_nodes_in_group("power_poles"):
		var pole := pole_value as Node3D
		var pos := pole.global_position
		if _is_on_road_surface(pos, road_surfaces):
			_fail("pole %s intrudes into a live road or junction" % pole.name)
			return
		for site_value in get_nodes_in_group("water_reservoir_sites"):
			if pole.global_position.distance_to((site_value as Node3D).global_position) < 25.0:
				_fail("pole %s intrudes into reservoir equipment" % pole.name)
				return
		for switch_value in get_nodes_in_group("power_switches"):
			if pole.global_position.distance_to((switch_value as Node3D).global_position) < 6.0:
				_fail("pole %s intrudes into an existing power actuator" % pole.name)
				return
	for cable_value in get_nodes_in_group("power_lines"):
		if (cable_value as Node3D).global_position.y < 5.5:
			_fail("an overhead cable is too low for road clearance")
			return
	grid.call("set_switch_enabled", "PowerSwitch_02", false)
	status = grid.call("get_grid_status") as Dictionary
	if int(status.get("powered_consumers", 0)) != 8:
		_fail("opening a power switch did not update graph state")
		return
	grid.call("set_switch_enabled", "PowerSwitch_02", true)
	status = grid.call("get_grid_status") as Dictionary
	if int(status.get("powered_consumers", 0)) != 9:
		_fail("restoring a power switch did not re-energize its branch")
		return
	print("POWER GRID TEST PASSED: two sources, intersection-safe sidewalk corridors, exterior ring, underground junction links, six reservoirs, and three power switches are graph-connected")
	quit(0)


func _fail(message: String) -> void:
	push_error("POWER GRID TEST FAILED: " + message)
	quit(1)


func _road_surfaces() -> Array[MeshInstance3D]:
	var surfaces: Array[MeshInstance3D] = []
	for group_name in ["junctions", "backdrop_junctions"]:
		for value in get_nodes_in_group(group_name):
			if value is MeshInstance3D:
				surfaces.append(value as MeshInstance3D)
	for group_name in ["roads", "backdrop_roads"]:
		for value in get_nodes_in_group(group_name):
			var road := value as Node3D
			var surface := road.get_node_or_null("Surface") as MeshInstance3D
			if surface != null:
				surfaces.append(surface)
	return surfaces


func _is_on_road_surface(point: Vector3, surfaces: Array[MeshInstance3D]) -> bool:
	for surface in surfaces:
		var local_point := surface.global_transform.affine_inverse() * point
		var bounds := surface.get_aabb().grow(0.55)
		if local_point.x >= bounds.position.x and local_point.x <= bounds.end.x and local_point.z >= bounds.position.z and local_point.z <= bounds.end.z:
			return true
	return false
