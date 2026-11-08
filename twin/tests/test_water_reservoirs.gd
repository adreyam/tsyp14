extends SceneTree


func _init() -> void:
	call_deferred("_test")


func _test() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	root.add_child(city)
	await process_frame
	var reservoirs := get_nodes_in_group("water_reservoirs")
	var sites := get_nodes_in_group("water_reservoir_sites")
	if reservoirs.size() != 24 or sites.size() != 6:
		_fail("expected six interior reservoir sites containing 24 3D water assets, got %d sites / %d assets" % [sites.size(), reservoirs.size()])
		return
	for reservoir_value in reservoirs:
		var reservoir := reservoir_value as Node3D
		var bounds := _world_bounds(reservoir)
		# Ground tanks are deliberately low and broad; all reservoir models must
		# still have a substantial municipal-scale dimension.
		if maxf(bounds.size.x, maxf(bounds.size.y, bounds.size.z)) < 8.0:
			_fail("%s is too small to read as municipal infrastructure: %s" % [reservoir.name, bounds.size])
			return
		var site := reservoir.get_parent() as Node3D
		var parcel_extent := float(site.get_meta("parcel_half_extent", 0.0))
		if parcel_extent < 30.0 or absf(bounds.position.x - site.global_position.x) > parcel_extent or absf(bounds.end.x - site.global_position.x) > parcel_extent or absf(bounds.position.z - site.global_position.z) > parcel_extent or absf(bounds.end.z - site.global_position.z) > parcel_extent:
			_fail("%s escapes its dedicated road-clear utility parcel" % reservoir.name)
			return
		if bounds.position.y < -0.25:
			_fail("%s is below the landscape: y=%0.2f" % [reservoir.name, bounds.position.y])
			return
	for site_value in sites:
		var site := site_value as Node3D
		var park := city.get_node_or_null("CityBackdrop/OuterDistricts/%s" % str(site.get_meta("park_block", ""))) as Node3D
		if park == null or str(park.get_meta("zone", "")) != "water_utility":
			_fail("%s was not reserved as a water-utility parcel" % site.name)
			return
		for tree_value in park.find_children("*", "Node3D", true, false):
			var tree := tree_value as Node3D
			if tree.is_in_group("city_trees") and tree.visible:
				_fail("%s still intersects an active decorative tree" % site.name)
				return
	print("WATER RESERVOIR TEST PASSED: six interior utility parcels contain 24 grounded city-scale external water assets, clear of roads and prior park objects")
	quit(0)


func _world_bounds(target: Node3D) -> AABB:
	var result := AABB()
	var has_bounds := false
	for mesh_value in target.find_children("*", "MeshInstance3D", true, false):
		var mesh := mesh_value as MeshInstance3D
		if mesh.mesh == null:
			continue
		for corner in _corners(mesh.mesh.get_aabb()):
			var point := mesh.to_global(corner)
			result = AABB(point, Vector3.ZERO) if not has_bounds else result.expand(point)
			has_bounds = true
	return result


func _corners(bounds: AABB) -> Array[Vector3]:
	var start := bounds.position
	var end := bounds.end
	return [
		Vector3(start.x, start.y, start.z), Vector3(end.x, start.y, start.z),
		Vector3(start.x, end.y, start.z), Vector3(end.x, end.y, start.z),
		Vector3(start.x, start.y, end.z), Vector3(end.x, start.y, end.z),
		Vector3(start.x, end.y, end.z), Vector3(end.x, end.y, end.z),
	]


func _fail(message: String) -> void:
	push_error("WATER RESERVOIR TEST FAILED: " + message)
	quit(1)
