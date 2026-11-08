extends SceneTree


func _init() -> void:
	call_deferred("_run")


func _fail(message: String) -> void:
	Engine.time_scale = 1.0
	printerr("SENSOR DASHBOARD TEST FAILED: " + message)
	quit(1)


func _run() -> void:
	var room := (load("res://scenes/ControlRoom.tscn") as PackedScene).instantiate()
	root.add_child(room)
	await process_frame
	await process_frame
	room.call("_sample_sensors")
	var tabs := room.get("_control_tabs") as TabContainer
	var grid := room.get("_sensor_reading_grid") as GridContainer
	var device_grid := room.get("_sensor_device_grid") as GridContainer
	var search := room.get("_sensor_search") as LineEdit
	if tabs == null or tabs.get_node_or_null("Sensors") == null:
		_fail("control room has no Sensors tab")
		return
	var world_clock := room.get("_world_clock") as Control
	var clock_overlay := room.get("_clock_overlay") as PanelContainer
	if world_clock == null or clock_overlay == null or clock_overlay.get_parent().name != "CityViewportFrame":
		_fail("control room has no interactive clock overlay on the simulation canvas")
		return
	if bool(world_clock.get("expanded")) or clock_overlay.size.x > 220.0:
		_fail("simulation clock does not start in its compact overlay state")
		return
	var open_click := InputEventMouseButton.new()
	open_click.button_index = MOUSE_BUTTON_LEFT
	open_click.pressed = true
	open_click.position = Vector2(30, 30)
	world_clock.call("_gui_input", open_click)
	await process_frame
	if not bool(world_clock.get("expanded")) or clock_overlay.size.x < 300.0:
		_fail("clickable simulation clock did not expand")
		return
	var accidental_times: Array[int] = []
	world_clock.time_selected.connect(func(minute: int) -> void: accidental_times.append(minute))
	var held_motion := InputEventMouseMotion.new()
	held_motion.button_mask = MOUSE_BUTTON_MASK_LEFT
	held_motion.position = Vector2(world_clock.size.x * 0.75, world_clock.size.y * 0.5)
	world_clock.call("_gui_input", held_motion)
	if not accidental_times.is_empty():
		_fail("the click that expanded the clock also selected a time")
		return
	var open_release := InputEventMouseButton.new()
	open_release.button_index = MOUSE_BUTTON_LEFT
	open_release.pressed = false
	world_clock.call("_gui_input", open_release)
	var close_click := InputEventMouseButton.new()
	close_click.button_index = MOUSE_BUTTON_LEFT
	close_click.pressed = true
	close_click.position = Vector2(world_clock.size.x - 10.0, 10.0)
	world_clock.call("_gui_input", close_click)
	await process_frame
	if bool(world_clock.get("expanded")) or clock_overlay.size.x > 220.0:
		_fail("expanded simulation clock did not return to compact mode")
		return
	world_clock.emit_signal("time_selected", 19 * 60 + 25)
	await process_frame
	if int((room.get("_environment_system") as Node).call("get_conditions").minute_of_day) != 19 * 60 + 25:
		_fail("world clock did not drive the shared simulation time")
		return
	if not is_equal_approx(float(room.get("_left_width")), 220.0) or not is_equal_approx(float(room.get("_right_width")), 380.0):
		_fail("sidebars do not default to their minimum draggable widths")
		return
	if (room.get("_sensor_catalog") as Array).size() != 88:
		_fail("dashboard catalog does not contain all 88 physical streams")
		return
	if device_grid == null or search == null or device_grid.get_child_count() != 8:
		_fail("direct sensor browser did not show its first page of eight device buttons")
		return
	if tabs.get_node("Sensors").find_children("*", "OptionButton", true, false).size() != 0:
		_fail("Sensors still uses a selector instead of direct device buttons")
		return
	tabs.current_tab = tabs.get_tab_idx_from_control(tabs.get_node("Sensors"))
	await process_frame
	var detail := room.get("_sensor_detail_label") as Label
	if detail == null or device_grid.global_position.y >= detail.global_position.y:
		_fail("device buttons are still placed below the selected readings/graph")
		return
	if grid == null or grid.get_child_count() == 0:
		_fail("selected sensor has no clickable reading cards")
		return
	search.text = "AIR-"
	room.call("_on_sensor_search_changed", "AIR-")
	room.call("_select_sensor", "AIR-01", false)
	if (room.get("_sensor_visible_ids") as Array).size() != 8 or device_grid.get_child_count() != 8:
		_fail("search did not produce eight direct air-monitor buttons")
		return
	var first_reading := grid.get_child(0) as Button
	first_reading.emit_signal("pressed")
	await process_frame
	if str(room.get("_selected_sensor_metric")).is_empty():
		_fail("clicking a reading did not select its historical graph")
		return
	var environment := room.get("_environment_system") as Node
	environment.call("set_time_of_day", int((environment.call("get_conditions") as Dictionary).minute_of_day) + 5)
	room.set("_last_sensor_sample_minute", -99999)
	room.call("_sample_sensors")
	var chart := room.get("_sensor_chart") as Control
	if chart == null or (chart.get("samples") as Array).size() < 2:
		_fail("graph did not retain historical samples for this simulation instance")
		return
	if not chart.clip_contents:
		_fail("graph drawing is not clipped to its own panel")
		return
	environment.call("set_time_of_day", int((environment.call("get_conditions") as Dictionary).minute_of_day) - 360)
	room.set("_last_sensor_sample_minute", -99999)
	room.call("_sample_sensors")
	await process_frame
	for point in chart.get("last_plotted_points") as PackedVector2Array:
		if point.x < 57.0 or point.x > chart.size.x - 17.0:
			_fail("graph history escaped its plot bounds after clock/state changes")
			return
	var plotted := chart.get("samples") as Array
	var distinct_values := {}
	for plotted_point in plotted:
		distinct_values[snappedf((plotted_point as Vector2).y, 0.01)] = true
	if distinct_values.size() < 2:
		_fail("live graph retained timestamps but not changing telemetry values")
		return
	var air_sensor := get_nodes_in_group("air_quality_monitors")[0] as Node3D
	var smoky_vehicle := get_nodes_in_group("smoky_vehicles")[0] as Node3D
	var original_vehicle_position := smoky_vehicle.global_position
	var live_before := (chart.get("samples") as Array)[-1] as Vector2
	smoky_vehicle.global_position = air_sensor.global_position + Vector3(1.5, 0.0, 0.0)
	smoky_vehicle.set_meta("sumo_speed", 0.0)
	room.set("_last_sensor_sample_msec", 0)
	room.call("_sample_sensors")
	var live_after := (chart.get("samples") as Array)[-1] as Vector2
	smoky_vehicle.global_position = original_vehicle_position
	if live_after.y <= live_before.y + 4.0:
		_fail("open live graph did not react immediately to a real emission source entering sensor range")
		return
	room.call("_select_sensor", "WX-01", true)
	await process_frame
	var city := room.get("_city") as Node3D
	var camera := city.get_node("EstablishingCamera") as Camera3D
	var focused := camera.call("get_selected_target") as Node3D
	var focus_holder := room.get("_focus_highlight_holder") as Node3D
	if focused == null or str(focused.get_meta("sensor_id", "")) != "WX-01" or focus_holder == null or focus_holder.get_node_or_null("FocusTag") == null:
		_fail("choosing a sensor did not frame, outline and tag its physical city model")
		return
	camera.call("_orbit_camera", Vector2(8.0, -3.0))
	if room.get("_focus_highlight_holder") == null:
		_fail("orbiting the focused sensor incorrectly removed its outline")
		return
	var vehicle := get_nodes_in_group("vehicles")[0] as Node3D
	camera.call("_select_target", vehicle, true)
	if room.get("_focus_highlight_holder") != null:
		_fail("selecting an excluded vehicle left an object outline/tag active")
		return
	var valve := get_nodes_in_group("water_valves")[0] as Node3D
	camera.call("_select_target", valve, true)
	if room.get("_focus_highlight_holder") == null:
		_fail("directly selecting a registered non-sensor city object did not outline/tag it")
		return
	for operational_group in ["power_service_meters", "water_service_meters", "power_poles", "power_lines", "water_reservoirs", "npc_meter_work_props"]:
		var targets := get_nodes_in_group(operational_group)
		if targets.is_empty():
			_fail("operational click audit found no %s objects" % operational_group)
			return
		var operational_target := targets[0] as Node3D
		if not bool(operational_target.get_meta("highlightable", false)):
			_fail("%s objects are not enabled for direct city clicking" % operational_group)
			return
		var selectable_mesh := operational_target as MeshInstance3D if operational_target is MeshInstance3D else null
		if selectable_mesh == null:
			var rendered_meshes := operational_target.find_children("*", "MeshInstance3D", true, false)
			selectable_mesh = rendered_meshes[0] as MeshInstance3D if not rendered_meshes.is_empty() else null
		if selectable_mesh == null or camera.call("_selectable_ancestor", selectable_mesh) != operational_target:
			_fail("%s is tagged but its rendered model is still not click-selectable" % operational_group)
			return
		camera.call("_select_target", operational_target, true)
		var operational_holder := room.get("_focus_highlight_holder") as Node3D
		if operational_holder == null or operational_holder.get_node_or_null("FocusTag") == null:
			_fail("clicking %s did not retain its outline and tag" % operational_group)
			return
	camera.call("_clear_target_follow")
	if room.get("_focus_highlight_holder") != null:
		_fail("releasing city focus did not clear the object's outline/tag")
		return
	room.call("_set_simulation_speed", 4.0)
	if not is_equal_approx(Engine.time_scale, 4.0):
		_fail("dashboard 4x action did not accelerate the shared simulation")
		return
	room.call("_set_simulation_speed", 1.0)
	Engine.time_scale = 1.0
	print("SENSOR DASHBOARD TEST PASSED: searchable button-based 88-device network, bounded live graphs, operational object focus, guarded clock expansion and synchronized speed controls")
	quit()
