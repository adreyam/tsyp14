extends SceneTree

const SpatialSafety := preload("res://scripts/city_spatial_safety.gd")

func _init() -> void:
	call_deferred("_test")

func _fail(message: String) -> void:
	printerr("CITY SPATIAL SAFETY TEST FAILED: " + message)
	Engine.time_scale = 1.0
	quit(1)

func _unique_nodes(values: Array) -> Array[Node3D]:
	var output: Array[Node3D] = []
	var seen := {}
	for value in values:
		if value is Node3D and not seen.has((value as Node3D).get_instance_id()):
			seen[(value as Node3D).get_instance_id()] = true
			output.append(value as Node3D)
	return output

func _assert_world_placement(nodes: Array[Node3D], buildings: Array[AABB], label: String) -> bool:
	for node in nodes:
		if not node.visible:
			continue
		var bounds := SpatialSafety.world_bounds(node)
		if SpatialSafety.overlaps_road(bounds, 0.0):
			_fail("%s %s overlaps a road" % [label, node.name]); return false
		for building in buildings:
			if SpatialSafety.horizontal_intersects(bounds, building, 0.05):
				_fail("%s %s intersects a building" % [label, node.name]); return false
	return true

func _test() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	root.add_child(city)
	await process_frame
	await create_timer(0.5).timeout
	var director := city.get_node("NpcAi")
	var buildings := SpatialSafety.building_bounds(self)
	var trees := _unique_nodes(get_nodes_in_group("city_trees"))
	var urban_props := _unique_nodes(get_nodes_in_group("urban_props"))
	var job_props := _unique_nodes(get_nodes_in_group("npc_activity_props") + get_nodes_in_group("npc_interaction_props"))
	var smart_devices := _unique_nodes(get_nodes_in_group("smart_city_devices"))
	if not _assert_world_placement(urban_props, buildings, "urban prop"):
		return
	if not _assert_world_placement(job_props, buildings, "job prop"):
		return
	if not _assert_world_placement(smart_devices, buildings, "smart-city device"):
		return
	for tree in trees:
		if not tree.visible:
			continue
		for building in buildings:
			if SpatialSafety.horizontal_intersects(SpatialSafety.world_bounds(tree), building, 0.05):
				_fail("tree %s intersects a building" % tree.name); return
	var observed_walkers := {}
	var samples := 0
	Engine.time_scale = 5.0
	for sample in 150:
		await create_timer(0.035, true, false, true).timeout
		for state_value in director.call("get_agent_debug"):
			var state := state_value as Dictionary
			var person := state.node as Node3D
			for building in buildings:
				if SpatialSafety.point_inside_horizontal(person.global_position, building, 0.42):
					_fail("%s entered a building at sample %d" % [person.name, sample]); return
			if float(state.speed_mps) <= 0.05:
				continue
			observed_walkers[person.name] = true
			for tree in trees:
				if tree.visible and Vector2(person.global_position.x, person.global_position.z).distance_to(Vector2(tree.global_position.x, tree.global_position.z)) < 1.15:
					_fail("walking %s collided with tree %s" % [person.name, tree.name]); return
			for prop in urban_props + job_props + smart_devices:
				if str(prop.name).begins_with("SmartStreetLight"):
					# Lamp arms and heads hang over the footpath; walkers only have to clear the mast.
					if prop.visible and Vector2(person.global_position.x, person.global_position.z).distance_to(Vector2(prop.global_position.x, prop.global_position.z)) < 0.30:
						_fail("walking %s collided with lamp mast %s" % [person.name, prop.name]); return
					continue
				if prop.has_meta("activity_station") and str(prop.get_meta("activity_station")) == str(state.station_id):
					continue  # the person's own work prop: they walk up to it
				if prop.visible and SpatialSafety.point_inside_horizontal(person.global_position, SpatialSafety.world_bounds(prop), 0.08):
					_fail("walking %s collided with prop %s" % [person.name, prop.name]); return
		samples += 1
	Engine.time_scale = 1.0
	if observed_walkers.size() < 4:
		_fail("only %d pedestrians exercised the collision-safe route network" % observed_walkers.size()); return
	var debug := director.call("get_spatial_debug") as Dictionary
	print("CITY SPATIAL SAFETY TEST PASSED: %d samples, %d walkers, 0 building/road/prop/device/tree conflicts; tree repair=%s; prop repair=%s" % [samples, observed_walkers.size(), str(debug.tree_safety), str(debug.urban_prop_safety)])
	quit(0)
