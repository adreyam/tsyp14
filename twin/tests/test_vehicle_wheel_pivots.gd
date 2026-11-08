extends SceneTree


func _init() -> void:
	call_deferred("_test")


func _fail(message: String) -> void:
	printerr("WHEEL PIVOT TEST FAILED: " + message)
	quit(1)


func _wheel_center_in_car(car: Node3D, pivot: Node3D) -> Vector3:
	var wheel_mesh := pivot.get_child(0) as MeshInstance3D
	return car.to_local(wheel_mesh.to_global(wheel_mesh.mesh.get_aabb().get_center()))


func _test() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate()
	root.add_child(city)
	await process_frame
	var life := city.get_node("CityLife")
	var vehicles: Array = life.get("_vehicles")
	if vehicles.size() < 68:
		_fail("expected at least the 68 authored vehicle models in the render pool, got %d" % vehicles.size())
		return
	var reference_centers: Dictionary = {}
	var rolling_vehicle_count := 0
	for state_value in vehicles:
		var state := state_value as Dictionary
		var car := state.node as Node3D
		var wheels: Array = state.wheels
		if wheels.size() != 4:
			_fail("%s has %d runtime wheel pivots; every procedural vehicle rolls on four" % [car.name, wheels.size()])
			return
		rolling_vehicle_count += 1
		for pivot_value in wheels:
			var pivot := pivot_value as Node3D
			if pivot.get_child_count() != 1 or not pivot.get_child(0) is MeshInstance3D:
				_fail("%s does not own exactly one wheel mesh" % pivot.name)
				return
			reference_centers[pivot.get_instance_id()] = _wheel_center_in_car(car, pivot)

	await create_timer(0.3).timeout
	for state_value in vehicles:
		var state := state_value as Dictionary
		var car := state.node as Node3D
		for pivot_value in state.wheels:
			var pivot := pivot_value as Node3D
			var initial := reference_centers[pivot.get_instance_id()] as Vector3
			if _wheel_center_in_car(car, pivot).distance_to(initial) > 0.002:
				_fail("%s wheel geometry orbited away from its axle center" % pivot.name)
				return
		var bounds := _car_world_bounds(car)
		var class_length := float((life.call("class_spec", str(state.cls)) as Dictionary).get("length", 4.7))
		if maxf(bounds.size.x, bounds.size.z) > class_length + 0.05:
			_fail("%s is longer than its simulated %s body: %s" % [car.name, state.cls, bounds.size])
			return
	if rolling_vehicle_count == 0:
		_fail("no active vehicle had verified rolling wheel pivots")
		return
	print("WHEEL PIVOT TEST PASSED: %d lane-sized vehicles roll on four axle-centred wheel pivots that never orbit away" % rolling_vehicle_count)
	quit(0)


func _car_world_bounds(car: Node3D) -> AABB:
	var result := AABB()
	var has_bounds := false
	for mesh_node in car.find_children("*", "MeshInstance3D", true, false):
		var mesh_instance := mesh_node as MeshInstance3D
		if mesh_instance.mesh == null:
			continue
		var mesh_bounds := mesh_instance.mesh.get_aabb()
		for corner in _aabb_corners(mesh_bounds):
			var point := mesh_instance.to_global(corner)
			result = AABB(point, Vector3.ZERO) if not has_bounds else result.expand(point)
			has_bounds = true
	return result


func _aabb_corners(bounds: AABB) -> Array[Vector3]:
	var start := bounds.position
	var end := bounds.end
	return [
		Vector3(start.x, start.y, start.z), Vector3(end.x, start.y, start.z),
		Vector3(start.x, end.y, start.z), Vector3(end.x, end.y, start.z),
		Vector3(start.x, start.y, end.z), Vector3(end.x, start.y, end.z),
		Vector3(start.x, end.y, end.z), Vector3(end.x, end.y, end.z),
	]
