extends SceneTree

const OUTPUT_DIR := "res://screenshots/fix79_camera_power_qa"


func _initialize() -> void:
	call_deferred("_render")


func _save_view(viewport: SubViewport, filename: String) -> int:
	for _unused in 4:
		await process_frame
	return viewport.get_texture().get_image().save_png(ProjectSettings.globalize_path("%s/%s" % [OUTPUT_DIR, filename]))


func _frame_meter(camera: Camera3D, control_room: Node, meter: Node3D) -> void:
	var bounds: AABB = control_room.call("_global_model_bounds", meter)
	var centre := bounds.get_center()
	var radius := maxf(bounds.size.length() * 0.5, 0.9)
	camera.look_at_from_position(centre + Vector3(1.55, 0.95, 1.85).normalized() * radius * 4.2, centre, Vector3.UP)


func _render() -> void:
	root.size = Vector2i(1600, 900)
	DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path(OUTPUT_DIR))
	var control_room := (load("res://scenes/ControlRoom.tscn") as PackedScene).instantiate()
	root.add_child(control_room)
	for _unused in 45:
		await process_frame
	var city := control_room.get("_city") as Node3D
	var viewport := control_room.get("_city_viewport") as SubViewport
	var camera := city.get_node("EstablishingCamera") as Camera3D
	(city.get_node("CityLife") as Node).process_mode = Node.PROCESS_MODE_DISABLED
	(city.get_node("NpcAi") as Node).process_mode = Node.PROCESS_MODE_DISABLED
	var environment := city.get_node("WorldEnvironment") as WorldEnvironment
	environment.environment.fog_enabled = false
	environment.environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	environment.environment.ambient_light_color = Color(0.92, 0.95, 1.0)
	environment.environment.ambient_light_energy = 0.80
	(city.get_node("Sun") as DirectionalLight3D).light_energy = 1.55

	var vehicle := get_nodes_in_group("vehicles")[0] as Node3D
	camera.call("_select_target", vehicle)
	camera.call("_toggle_first_person")
	camera.set("_first_person_yaw", PI)
	camera.set("_first_person_pitch", deg_to_rad(-3.0))
	camera.call("_apply_first_person_view")
	print("FIX79_RENDER|vehicle_forward|save=", await _save_view(viewport, "01_vehicle_first_person_forward.png"))
	camera.set("_first_person_yaw", 0.0)
	camera.set("_first_person_pitch", deg_to_rad(-4.0))
	camera.call("_apply_first_person_view")
	print("FIX79_RENDER|vehicle_look_back|save=", await _save_view(viewport, "02_vehicle_first_person_look_back_exterior.png"))

	var person := get_nodes_in_group("pedestrians")[0] as Node3D
	camera.call("_select_target", person)
	camera.call("_toggle_first_person")
	camera.set("_first_person_yaw", 0.0)
	camera.set("_first_person_pitch", deg_to_rad(-2.0))
	camera.call("_apply_first_person_view")
	print("FIX79_RENDER|person_look_back|save=", await _save_view(viewport, "03_person_first_person_look_back_exterior.png"))

	var street_device := city.get_node("TrafficCameraSystem/TrafficCamera_CAM_07") as Node3D
	var feed_camera := street_device.get_node("FeedCamera") as Camera3D
	feed_camera.current = true
	print("FIX79_RENDER|street_feed|save=", await _save_view(viewport, "04_street_camera_selected.png"))
	var escape_event := InputEventKey.new()
	escape_event.keycode = KEY_ESCAPE
	escape_event.pressed = true
	camera.call("_unhandled_input", escape_event)
	print("FIX79_RENDER|street_escape|save=", await _save_view(viewport, "05_street_camera_escape_zoom_out.png"))

	control_room.call("_toggle_infrastructure_highlight", "power")
	var job_power_meter: Node3D
	for meter_value in get_nodes_in_group("npc_meter_work_props"):
		var meter := meter_value as Node3D
		if meter != null and str(meter.get_meta("meter_kind", "")) == "power":
			job_power_meter = meter
			break
	if job_power_meter == null:
		printerr("FIX79 RENDER FAILED: no technician power meter")
		quit(1)
		return
	camera.call("_clear_target_follow")
	camera.current = true
	_frame_meter(camera, control_room, job_power_meter)
	print("FIX79_RENDER|job_power_meter_frame|save=", await _save_view(viewport, "06_job_power_meter_highlight.png"))
	var zone_power_meter := get_nodes_in_group("power_service_meters")[0] as Node3D
	_frame_meter(camera, control_room, zone_power_meter)
	print("FIX79_RENDER|zone_power_meter_frame|save=", await _save_view(viewport, "07_zone_power_meter_highlight.png"))
	var interaction_power_meter: Node3D
	for meter_value in get_nodes_in_group("npc_interaction_props"):
		var meter := meter_value as Node3D
		if meter != null and str(meter.get_meta("activity_prop_kind", "")) == "power_meter":
			interaction_power_meter = meter
			break
	if interaction_power_meter == null:
		printerr("FIX79 RENDER FAILED: no utility-briefing power meter")
		quit(1)
		return
	interaction_power_meter.visible = true
	_frame_meter(camera, control_room, interaction_power_meter)
	print("FIX79_RENDER|interaction_power_meter_frame|save=", await _save_view(viewport, "08_interaction_power_meter_highlight.png"))
	print("FIX79_CAMERA_POWER_RENDER_COMPLETE|frames=8")
	quit(0)
