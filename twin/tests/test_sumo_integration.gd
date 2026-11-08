extends SceneTree


func _init() -> void:
	call_deferred("_test")


func _fail(message: String) -> void:
	printerr("SUMO INTEGRATION TEST FAILED: " + message)
	quit(1)


func _test() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate()
	var life := city.get_node("CityLife")
	var requested_port := OS.get_environment("SUMO_TEST_PORT")
	if not requested_port.is_empty():
		life.set("sumo_udp_port", int(requested_port))
	root.add_child(city)
	var deadline := Time.get_ticks_msec() + 10000
	while not bool(life.get("sumo_connected")) and Time.get_ticks_msec() < deadline:
		await create_timer(0.1).timeout
	if not bool(life.get("sumo_connected")):
		_fail("Godot did not receive the bundled SUMO TraCI stream within 10 seconds")
		return

	# Emergency units parked in their station bays are rendered in Godot
	# custody without a SUMO body; every SUMO vehicle must have a rendered node.
	var mapped := {}
	var all_rendered: Dictionary = life.get("_sumo_nodes_by_id")
	for key in all_rendered:
		if bool((all_rendered[key] as Dictionary).get("has_packet", false)):
			mapped[key] = all_rendered[key]
	var simulated := int(life.get("_sumo_simulated_count"))
	if simulated < 90 or mapped.size() != simulated:
		_fail("every simulated vehicle must be rendered: simulated=%d rendered=%d" % [simulated, mapped.size()])
		return
	# A deterministic dictionary's first car may correctly be stopped at one of
	# the protected red phases. Measure the mapped fleet instead of assuming that
	# arbitrary identity must move during this exact window.
	var start_positions := {}
	for vehicle_id in mapped:
		var render_state := mapped[vehicle_id] as Dictionary
		start_positions[str(vehicle_id)] = (render_state.node as Node3D).global_position
	await create_timer(0.8).timeout
	var advanced := 0
	for vehicle_id in mapped:
		var render_state := mapped[vehicle_id] as Dictionary
		if (render_state.node as Node3D).global_position.distance_to(start_positions.get(str(vehicle_id), Vector3.ZERO)) >= 0.15:
			advanced += 1
	if advanced < 5:
		_fail("only %d/%d SUMO-mapped vehicles advanced during the live sample" % [advanced, mapped.size()])
		return

	var visible_count := 0
	for node in get_nodes_in_group("vehicles") + get_nodes_in_group("backdrop_vehicles"):
		if (node as Node3D).visible:
			visible_count += 1
	# One visible model per rendered vehicle: SUMO vehicles plus units parked
	# in station bays; no spare pool model may be visible.
	if visible_count != all_rendered.size():
		_fail("expected exactly %d visible vehicle models (%d simulated + %d parked units), got %d" % [all_rendered.size(), mapped.size(), all_rendered.size() - mapped.size(), visible_count])
		return
	var status := city.get_node("CameraControls/SumoStatus") as Label
	if not status.text.begins_with("SUMO LIVE"):
		_fail("on-screen status does not report a live SUMO connection: %s" % status.text)
		return
	if get_nodes_in_group("traffic_signals").size() != 49:
		_fail("the 49 synchronized Godot traffic signals are missing")
		return
	print("SUMO INTEGRATION TEST PASSED: TraCI drives all %d simulated vehicles as rendered models (%d advanced in sample) and synchronizes 49 traffic signals" % [mapped.size(), advanced])
	quit(0)
