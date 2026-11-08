extends SceneTree


func _init() -> void:
	call_deferred("_run")


func _fail(message: String) -> void:
	Engine.time_scale = 1.0
	printerr("ACCELERATED VEHICLE INTERPOLATION TEST FAILED: " + message)
	quit(1)


func _packet(sim_time: float, x: float) -> Dictionary:
	return {
		"time":sim_time,
		"simulated_vehicle_count":1,
		"stopped_vehicle_count":0,
		"turning_vehicle_count":0,
		"operator":{"speed":8, "paused":false},
		"vehicles":[{"id":"speed_test", "x":x, "z":0.0, "angle":90.0, "speed":13.0, "cls":"car", "entry_boundary":true}],
		"signals":[],
	}


func _run() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	root.add_child(city)
	await process_frame
	await process_frame
	var life := city.get_node("CityLife")
	Engine.time_scale = 8.0
	var sim_time := 100.0
	var packet_x := -30.0
	life.call("_apply_sumo_state", _packet(sim_time, packet_x))
	var render_state := (life.get("_sumo_nodes_by_id") as Dictionary)["speed_test"] as Dictionary
	var previous := (render_state.node as Node3D).global_position
	var maximum_frame_step := 0.0
	var total_travel := 0.0
	for packet_index in 8:
		sim_time += 8.0 / 15.0
		packet_x += 13.0 * (8.0 / 15.0)
		life.call("_apply_sumo_state", _packet(sim_time, packet_x))
		render_state = (life.get("_sumo_nodes_by_id") as Dictionary)["speed_test"] as Dictionary
		if (render_state.history as Array).size() < 2:
			_fail("a legitimate accelerated SUMO packet was rejected")
			return
		for _frame in 4:
			life.call("_update_sumo_traffic", 8.0 / 60.0)
			var current := (render_state.node as Node3D).global_position
			var step := Vector2(current.x - previous.x, current.z - previous.z).length()
			maximum_frame_step = maxf(maximum_frame_step, step)
			total_travel += step
			previous = current
	if maximum_frame_step > 2.8:
		_fail("8x rendering teleported %.2f m in one 60 Hz wall frame" % maximum_frame_step)
		return
	if total_travel < 20.0:
		_fail("8x rendering failed to advance continuously: %.2f m" % total_travel)
		return
	Engine.time_scale = 1.0
	print("ACCELERATED VEHICLE INTERPOLATION TEST PASSED: max_frame_step=%.2fm travel=%.1fm" % [maximum_frame_step, total_travel])
	quit()
