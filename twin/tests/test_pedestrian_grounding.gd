extends SceneTree


const SIDEWALK_SURFACE_Y := 0.17


func _init() -> void:
	call_deferred("_test")


func _fail(message: String) -> void:
	printerr("PEDESTRIAN GROUNDING TEST FAILED: " + message)
	quit(1)


func _test() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate()
	root.add_child(city)
	await process_frame
	var life := city.get_node("CityLife")
	var pedestrians: Array = life.get("_pedestrians")
	if pedestrians.size() != 102:
		_fail("expected 102 active people, got %d" % pedestrians.size())
		return
	for state_value in pedestrians:
		var person := (state_value as Dictionary).node as Node3D
		if str(person.get_meta("model_family", "")) == "rocketbox_realistic":
			if absf(person.global_position.y - SIDEWALK_SURFACE_Y) > 0.01:
				_fail("%s Rocketbox foot origin is %.3f, expected sidewalk height %.3f" % [person.name, person.global_position.y, SIDEWALK_SURFACE_Y])
				return
			continue
		if person.scene_file_path.ends_with("Adventurer.glb"):
			_fail("unsafe Adventurer walk rig is still active on %s" % person.name)
			return
		var bounds: AABB = life.call("_vehicle_local_bounds", person)
		var lowest_y := person.global_position.y + bounds.position.y * person.global_transform.basis.get_scale().y
		if absf(lowest_y - SIDEWALK_SURFACE_Y) > 0.03:
			_fail("%s lowest mesh height is %.3f, expected sidewalk height %.3f" % [person.name, lowest_y, SIDEWALK_SURFACE_Y])
			return
	print("PEDESTRIAN GROUNDING TEST PASSED: all 32 Rocketbox citizens use their authored foot origin and all legacy hidden people remain mesh-grounded")
	quit(0)
