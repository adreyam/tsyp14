extends SceneTree


const ROAD_SURFACE_Y := 0.17


func _init() -> void:
	call_deferred("_test")


func _fail(message: String) -> void:
	printerr("VEHICLE GROUNDING TEST FAILED: " + message)
	quit(1)


func _test() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate()
	root.add_child(city)
	await process_frame
	var life := city.get_node("CityLife")
	var vehicles: Array = life.get("_vehicles")
	if vehicles.size() < 68:
		_fail("expected at least 68 pooled vehicle models, got %d" % vehicles.size())
		return
	for state_value in vehicles:
		var state := state_value as Dictionary
		var car := state.node as Node3D
		var bounds: AABB = life.call("_vehicle_local_bounds", car)
		var lowest_vertex_y := car.global_position.y + bounds.position.y * car.global_transform.basis.get_scale().y
		if absf(lowest_vertex_y - ROAD_SURFACE_Y) > 0.025:
			_fail("%s lowest geometry is %.3f, expected road height %.3f" % [car.name, lowest_vertex_y, ROAD_SURFACE_Y])
			return
		# The rendered body must be exactly the simulated SUMO body: a visible
		# contact is then a simulated contact.
		var spec := life.call("class_spec", str(state.cls)) as Dictionary
		var scale := car.global_transform.basis.get_scale()
		var world_length := bounds.size.z * scale.z
		if absf(world_length - float(spec.length)) > 0.02:
			_fail("%s is %.3f m long, its SUMO %s body is %.2f m" % [car.name, world_length, state.cls, float(spec.length)])
			return
		if str(spec.get("fit", "uniform")) == "stretch" and absf(bounds.size.x * scale.x - float(spec.width)) > 0.02:
			_fail("%s is %.3f m wide, its SUMO %s body is %.2f m" % [car.name, bounds.size.x * scale.x, state.cls, float(spec.width)])
			return
		if bounds.size.x * scale.x > 3.0:
			_fail("%s is too wide for a 4.5 m lane: %.3f m" % [car.name, bounds.size.x * scale.x])
			return
	print("VEHICLE GROUNDING TEST PASSED: every pooled model rests on the road surface with its exact SUMO body length")
	quit(0)
