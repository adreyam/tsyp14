extends SceneTree

## Ensures the control room uses the actual City scene in one SubViewport and
## selects its Camera3D directly—there is no intermediary image or stream.

func _init() -> void:
	call_deferred("_run")


func _run() -> void:
	var control_room := (load("res://scenes/ControlRoom.tscn") as PackedScene).instantiate()
	root.add_child(control_room)
	await process_frame
	await process_frame
	var city := control_room.get("_city") as Node3D
	if city == null:
		_fail("Control room did not instantiate the City scene")
		return
	# The normal desktop title bar remains available, but the dashboard should
	# occupy the usable desktop without opening as exclusive fullscreen.
	if DisplayServer.get_name().to_lower() != "headless" and DisplayServer.window_get_mode() != DisplayServer.WINDOW_MODE_MAXIMIZED:
		_fail("Control room does not launch in maximized desktop mode")
		return
	var city_viewport := control_room.get("_city_viewport") as SubViewport
	if city_viewport == null or city.get_parent() != city_viewport:
		_fail("City is not rendered by the control room's direct SubViewport")
		return
	var viewport_frame := control_room.find_child("CityViewportFrame", true, false) as SubViewportContainer
	if viewport_frame == null or not viewport_frame.stretch or control_room.find_children("*", "AspectRatioContainer", true, false).size() > 0:
		_fail("Simulation viewport does not fill its column at the window's own shape")
		return
	if control_room.get("_left_divider") == null or control_room.get("_right_divider") == null or control_room.get("_bottom_divider") == null:
		_fail("Control room is missing draggable viewport resize borders")
		return
	# Soft directional shadows, SSAO and SSIL resolve their sampling patterns
	# over time; without temporal AA the shadowed streets show a fixed stipple.
	# Moving cars and citizens carry per-object motion vectors, so TAA keeps
	# moving geometry stable.
	if not bool(ProjectSettings.get_setting("rendering/anti_aliasing/quality/use_taa", false)) or not city_viewport.use_taa:
		_fail("Render anti-aliasing is not temporally resolved for soft shadows and moving geometry")
		return
	var hud := city.get_node_or_null("CameraControls") as CanvasLayer
	if hud == null or hud.visible:
		_fail("Normal city HUD is not suppressed in the unified control room")
		return
	var buttons := control_room.get("_camera_buttons") as Array
	if buttons.size() != 16:
		_fail("Control room does not provide a full sixteen-camera grid")
		return
	var grid: GridContainer = null
	for candidate_value in control_room.find_children("*", "GridContainer", true, false):
		var candidate := candidate_value as GridContainer
		if candidate.get_child_count() == 16:
			grid = candidate
			break
	if grid == null or grid.columns != 4:
		_fail("Camera controls are not laid out as a complete four-column grid")
		return
	var control_tabs: TabContainer = null
	for tab_value in control_room.find_children("ControlTabs", "TabContainer", true, false):
		control_tabs = tab_value as TabContainer
		break
	if control_tabs == null or control_tabs.get_node_or_null("Utilities") == null or control_tabs.get_node_or_null("NPC Intent") == null:
		_fail("Control room does not provide dedicated Utilities and NPC Intent tabs")
		return
	var utility_scroll := control_tabs.get_node("Utilities") as ScrollContainer
	var utility_box := utility_scroll.find_child("UtilitiesContent", true, false) as VBoxContainer if utility_scroll != null else null
	if utility_box == null or utility_box.size.x > utility_scroll.size.x:
		_fail("Utilities content can overflow the responsive padded panel")
		return
	var utilities := city.get_node_or_null("UtilityResources") as Node
	if utilities == null:
		_fail("Control room city does not provide utility resource state")
		return
	var recharge_button: Button = null
	var auto_button: Button = null
	for button_value in control_tabs.get_node("Utilities").find_children("*", "Button", true, false):
		var button := button_value as Button
		if button.text == "RECHARGE ALL UTILITIES": recharge_button = button
		if button.text.begins_with("AUTO RECHARGE:"): auto_button = button
	if recharge_button == null or auto_button == null:
		_fail("Utilities tab is missing manual or automatic recharge control")
		return
	await create_timer(0.65).timeout
	var water_zone_grid := control_tabs.get_node("Utilities").find_child("WaterZoneToggles", true, false) as GridContainer
	var power_zone_grid := control_tabs.get_node("Utilities").find_child("PowerZoneToggles", true, false) as GridContainer
	if water_zone_grid == null or power_zone_grid == null or water_zone_grid.get_child_count() != 16 or power_zone_grid.get_child_count() != 16:
		_fail("Utilities tab does not provide separate full water and power action-button grids")
		return
	for action_button_value in water_zone_grid.get_children() + power_zone_grid.get_children():
		var action_button := action_button_value as Button
		if action_button == null or not action_button.toggle_mode or not action_button.text.contains("CUT"):
			_fail("utility service action is not presented as a visible toggle button")
			return
	var water_cluster_button: Button = null
	var power_cluster_button: Button = null
	var selected_cluster := ""
	for button_value in control_tabs.get_node("Utilities").find_children("*", "Button", true, false):
		var button := button_value as Button
		var cluster_name := str(button.get_meta("cluster_name", ""))
		if not cluster_name.is_empty() and (selected_cluster.is_empty() or cluster_name == selected_cluster):
			selected_cluster = cluster_name
			if str(button.get_meta("cluster_kind", "")) == "water": water_cluster_button = button
			if str(button.get_meta("cluster_kind", "")) == "power": power_cluster_button = button
	if water_cluster_button == null or power_cluster_button == null:
		_fail("Utilities tab is missing paired manual water/power cluster controls")
		return
	water_cluster_button.emit_signal("toggled", true)
	power_cluster_button.emit_signal("toggled", true)
	await process_frame
	var manual_status := utilities.call("get_utility_status") as Dictionary
	if not (manual_status.get("manual_water_clusters", []) as Array).has(selected_cluster) or not (manual_status.get("manual_power_clusters", []) as Array).has(selected_cluster):
		_fail("Utilities tab manual cluster buttons did not issue their service cuts")
		return
	water_cluster_button.emit_signal("toggled", false)
	power_cluster_button.emit_signal("toggled", false)
	await process_frame
	recharge_button.emit_signal("pressed")
	await process_frame
	var utility_status := utilities.call("get_utility_status") as Dictionary
	var utility_power := utility_status.get("power", {}) as Dictionary
	if float(utility_power.get("available_kwh", 0.0)) / maxf(float(utility_power.get("capacity_kwh", 0.0)), 0.01) < 0.995:
		_fail("Utilities tab manual recharge did not refill the power reserve")
		return
	auto_button.emit_signal("pressed")
	await process_frame
	if bool((utilities.call("get_utility_status") as Dictionary).get("auto_recharge_enabled", true)):
		_fail("Utilities tab automatic recharge toggle did not switch off")
		return
	auto_button.emit_signal("pressed")
	await process_frame
	if not bool((utilities.call("get_utility_status") as Dictionary).get("auto_recharge_enabled", false)):
		_fail("Utilities tab automatic recharge toggle did not switch on")
		return
	var follow_buttons := control_room.get("_follow_buttons") as Array
	if follow_buttons.size() != 2:
		_fail("Control room does not provide direct random NPC and car follow buttons")
		return
	var infrastructure_buttons := control_room.get("_infrastructure_buttons") as Array
	if infrastructure_buttons.size() != 2:
		_fail("Control room does not provide water and power infrastructure highlight controls")
		return
	(infrastructure_buttons[0] as Button).emit_signal("pressed")
	await process_frame
	var water_meter_count := city.get_tree().get_nodes_in_group("water_service_meters").size()
	if city.get_tree().get_nodes_in_group("water_infrastructure_frames").size() != 24 + water_meter_count:
		_fail("Water infrastructure highlight does not frame reservoirs and their service meters")
		return
	(infrastructure_buttons[1] as Button).emit_signal("pressed")
	await process_frame
	var highlighted_power_meters := control_room.call("_power_meter_highlight_targets") as Array[Node3D]
	var power_meter_count := highlighted_power_meters.size()
	if city.get_tree().get_nodes_in_group("power_infrastructure_frames").size() != city.get_tree().get_nodes_in_group("power_poles").size() + power_meter_count:
		_fail("Power infrastructure highlight does not frame every grid pole, zone meter, and technician work meter")
		return
	var npc_power_meter_count := 0
	for meter_value in city.get_tree().get_nodes_in_group("npc_meter_work_props"):
		if str((meter_value as Node3D).get_meta("meter_kind", "")) == "power":
			npc_power_meter_count += 1
	if npc_power_meter_count == 0 or power_meter_count <= city.get_tree().get_nodes_in_group("power_service_meters").size():
		_fail("Rendered technician power meters were omitted from the power highlight target set")
		return
	var highlighted_meter_ids := {}
	for meter in highlighted_power_meters:
		highlighted_meter_ids[meter.get_instance_id()] = true
	for group_name in ["npc_activity_props", "npc_interaction_props"]:
		for meter_value in city.get_tree().get_nodes_in_group(group_name):
			var rendered_meter := meter_value as Node3D
			if rendered_meter != null and str(rendered_meter.get_meta("activity_prop_kind", "")) == "power_meter" and not highlighted_meter_ids.has(rendered_meter.get_instance_id()):
				_fail("Visible power meter %s was omitted from the power highlight union" % rendered_meter.name)
				return
	for line_value in city.get_tree().get_nodes_in_group("power_lines"):
		if (line_value as GeometryInstance3D).material_overlay == null:
			_fail("Power infrastructure highlight does not visually mark cable spans")
			return
	(infrastructure_buttons[1] as Button).emit_signal("pressed")
	await process_frame
	if city.get_tree().get_nodes_in_group("power_infrastructure_frames").size() != 0:
		_fail("Power highlight did not toggle off")
		return
	(buttons[2] as Button).emit_signal("pressed")
	await process_frame
	var feed_camera := city.get_node_or_null("TrafficCameraSystem/TrafficCamera_CAM_03/FeedCamera") as Camera3D
	if feed_camera == null or not feed_camera.is_current():
		_fail("Selected physical traffic camera did not become the direct viewport camera")
		return
	(buttons[15] as Button).emit_signal("pressed")
	await process_frame
	var final_camera := city.get_node_or_null("TrafficCameraSystem/TrafficCamera_CAM_16/FeedCamera") as Camera3D
	if final_camera == null or not final_camera.is_current():
		_fail("Last camera-grid button did not open CAM-16")
		return
	var establishing_camera := city.get_node_or_null("EstablishingCamera") as Camera3D
	var street_device := final_camera.get_parent() as Node3D
	var street_centre: Vector3 = (establishing_camera.call("_world_bounds", street_device) as AABB).get_center()
	var escape_event := InputEventKey.new()
	escape_event.keycode = KEY_ESCAPE
	escape_event.pressed = true
	establishing_camera.call("_unhandled_input", escape_event)
	await process_frame
	if not establishing_camera.is_current():
		_fail("Escape from a selected street camera did not restore the establishing camera")
		return
	if establishing_camera.global_position.distance_to(street_centre) < 7.0 or establishing_camera.global_position.distance_to(street_centre) > 30.0:
		_fail("Escape from a selected street camera did not zoom out to a useful nearby exterior view")
		return
	if not "released" in _camera_status_text(control_room).to_lower():
		_fail("Control room did not report that Escape released the street-camera feed")
		return
	for device_value in city.get_tree().get_nodes_in_group("traffic_cameras"):
		var device := device_value as Node3D
		var lens := device.get_node_or_null("FeedCamera") as Camera3D
		var traffic_target := device.get_meta("traffic_target", Vector3.ZERO) as Vector3
		var lens_forward := -lens.global_transform.basis.z.normalized()
		var intended_direction := (traffic_target - lens.global_position).normalized()
		if lens_forward.dot(intended_direction) < 0.96:
			_fail("%s lens is not facing its assigned traffic junction" % device.name)
			return
	control_tabs.current_tab = 0
	(follow_buttons[0] as Button).emit_signal("pressed")
	await process_frame
	var city_camera := city.get_node_or_null("EstablishingCamera") as Camera3D
	var selected := city_camera.call("get_selected_target") as Node3D
	if selected == null or not selected.is_in_group("pedestrians") or selected.is_in_group("backdrop_pedestrians"):
		_fail("Random NPC follow did not select a job-capable non-backdrop citizen")
		return
	if control_tabs.current_tab != 0:
		_fail("Random NPC follow incorrectly opened the NPC intent tab")
		return
	city_camera.call("_select_target", selected, true)
	await process_frame
	var npc_tab_index := int(control_room.get("_npc_tab_index"))
	var npc_payload := control_room.get("_npc_payload") as RichTextLabel
	if control_tabs.current_tab != npc_tab_index or npc_payload == null or npc_payload.text.is_empty():
		_fail("Direct NPC selection did not open a populated NPC decision trace")
		return
	(follow_buttons[1] as Button).emit_signal("pressed")
	await process_frame
	selected = city_camera.call("get_selected_target") as Node3D
	if selected == null or not (selected.is_in_group("vehicles") or selected.is_in_group("backdrop_vehicles")):
		_fail("Random car follow did not select a visible traffic vehicle")
		return
	control_room.call("_return_to_city_view")
	await process_frame
	var overview := city.get_node_or_null("EstablishingCamera") as Camera3D
	if overview == null or not overview.is_current():
		_fail("Return-to-city did not restore the direct city camera")
		return
	print("UNIFIED CONTROL ROOM TEST PASSED: one process embeds the City scene and switches direct Camera3D nodes without a transferred video feed")
	quit(0)


func _camera_status_text(control_room: Node) -> String:
	var label := control_room.get("_camera_status") as Label
	return label.text if label != null else ""


func _fail(message: String) -> void:
	push_error(message)
	quit(1)
