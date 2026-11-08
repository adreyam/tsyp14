extends SceneTree

func _initialize() -> void:
	call_deferred("_test")

func _fail(message: String) -> void:
	printerr("VEHICLE CAMERA LIFECYCLE TEST FAILED: " + message)
	quit(1)

func _packet(vehicles: Array) -> Dictionary:
	return {
		"source": "SUMO 1.25.0 TraCI",
		"simulated_vehicle_count": vehicles.size(),
		"stopped_vehicle_count": 0,
		"turning_vehicle_count": 0,
		"vehicles": vehicles,
		"signals": [],
	}

func _test() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	root.add_child(city)
	await process_frame
	var life := city.get_node("CityLife")
	var camera := city.get_node("EstablishingCamera") as Camera3D
	life.call("_apply_sumo_state", _packet([{"id":"followed_car","x":-372.0,"z":0.0,"angle":90.0,"speed":8.0,"entry_boundary":true,"continuation_count":0}]))
	var mapped := life.get("_sumo_nodes_by_id") as Dictionary
	if not mapped.has("followed_car"):
		_fail("test car was not assigned to a rendered vehicle"); return
	var car := (mapped["followed_car"] as Dictionary).node as Node3D
	camera.call("_select_target", car)
	life.call("_apply_sumo_state", _packet([]))
	var retiring := life.get("_retiring_vehicles") as Array
	if retiring.size() != 1 or not car.visible or str(car.get_meta("sumo_trip_status", "")) != "leaving_city":
		_fail("missing SUMO id did not enter a visible retirement coast"); return
	if camera.call("get_selected_target") != car:
		_fail("camera detached before the visible exit completed"); return
	var before := car.global_position
	life.call("_update_retiring_vehicles", 1.0)
	if car.global_position.distance_to(before) < 5.5 or not car.visible:
		_fail("retiring car did not coast visibly toward the boundary"); return
	life.call("_update_retiring_vehicles", 1.21)
	if car.visible or camera.call("get_selected_target") != null:
		_fail("camera remained attached to a hidden pooled vehicle"); return
	if str(car.get_meta("sumo_trip_status", "")) != "departed":
		_fail("retired vehicle did not expose its completed lifecycle state"); return
	print("VEHICLE_CAMERA_LIFECYCLE_TEST_OK visible_coast=2.2s camera_detached_before_pool_hide")
	quit(0)
