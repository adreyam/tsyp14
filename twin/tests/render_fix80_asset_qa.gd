extends SceneTree

const OUTPUT_DIR := "res://screenshots/fix80_asset_map_smoke_qa"


func _initialize() -> void:
	call_deferred("_render")


func _save_view(viewport: Viewport, filename: String, settle_frames := 8) -> int:
	for _unused in settle_frames:
		await process_frame
	return viewport.get_texture().get_image().save_png(ProjectSettings.globalize_path("%s/%s" % [OUTPUT_DIR, filename]))


func _frame_target(camera: Camera3D, room: Node, target: Node3D, direction := Vector3(1.5, 0.9, 1.7), distance_scale := 3.2) -> void:
	var bounds := room.call("_global_model_bounds", target) as AABB
	var centre := bounds.get_center()
	var radius := maxf(bounds.size.length() * 0.5, 0.8)
	camera.look_at_from_position(centre + direction.normalized() * radius * distance_scale, centre, Vector3.UP)


func _render() -> void:
	root.size = Vector2i(1600, 900)
	DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path(OUTPUT_DIR))
	var room := (load("res://scenes/ControlRoom.tscn") as PackedScene).instantiate()
	root.add_child(room)
	for _unused in 55:
		await process_frame
	var city := room.get("_city") as Node3D
	var viewport := room.get("_city_viewport") as SubViewport
	var camera := city.get_node("EstablishingCamera") as Camera3D
	(city.get_node("CityLife") as Node).process_mode = Node.PROCESS_MODE_DISABLED
	(city.get_node("NpcAi") as Node).process_mode = Node.PROCESS_MODE_DISABLED
	for person_value in get_nodes_in_group("pedestrians") + get_nodes_in_group("backdrop_pedestrians"):
		if person_value is Node3D:
			(person_value as Node3D).visible = false
	var environment := city.get_node("WorldEnvironment") as WorldEnvironment
	environment.environment.fog_enabled = false
	environment.environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	environment.environment.ambient_light_color = Color(0.91, 0.94, 1.0)
	environment.environment.ambient_light_energy = 0.78
	(city.get_node("Sun") as DirectionalLight3D).light_energy = 1.48
	camera.current = true
	camera.call("_clear_target_follow")

	var asset_tabs := room.get("_control_tabs") as TabContainer
	asset_tabs.current_tab = asset_tabs.get_tab_idx_from_control(asset_tabs.get_node("Asset Map"))
	print("FIX80_RENDER|asset_map_ui|save=", await _save_view(root, "01_asset_map_ui.png", 10))

	var traffic_detector := get_nodes_in_group("traffic_sensors")[0] as Node3D
	room.call("_toggle_asset_highlight", "traffic_detectors")
	_frame_target(camera, room, traffic_detector)
	print("FIX80_RENDER|traffic_detector|save=", await _save_view(viewport, "02_traffic_radar_model_and_frame.png"))

	var air_monitor := get_nodes_in_group("air_quality_monitors")[0] as Node3D
	room.call("_toggle_asset_highlight", "air_quality")
	_frame_target(camera, room, air_monitor, Vector3(1.4, 0.8, -1.8), 3.6)
	print("FIX80_RENDER|air_monitor|save=", await _save_view(viewport, "03_air_quality_model_and_frame.png"))

	var weather := get_nodes_in_group("weather_stations")[0] as Node3D
	room.call("_toggle_asset_highlight", "weather")
	_frame_target(camera, room, weather, Vector3(1.5, 0.8, 1.6), 3.5)
	print("FIX80_RENDER|weather_station|save=", await _save_view(viewport, "04_weather_station_model_and_frame.png"))

	var light := get_nodes_in_group("smart_street_lights")[0] as Node3D
	room.call("_toggle_asset_highlight", "street_lights")
	_frame_target(camera, room, light, Vector3(1.5, 0.7, 1.6), 2.8)
	print("FIX80_RENDER|street_light|save=", await _save_view(viewport, "05_external_smart_light_and_frame.png"))

	var police_station := get_nodes_in_group("police_facilities")[0] as Node3D
	room.call("_toggle_asset_highlight", "police_stations")
	_frame_target(camera, room, police_station, Vector3(1.6, 0.7, 1.8), 2.1)
	print("FIX80_RENDER|police_station|save=", await _save_view(viewport, "06_police_facility_and_frame.png"))

	var ambulance := get_nodes_in_group("ambulance_vehicles")[0] as Node3D
	for vehicle_value in get_nodes_in_group("vehicles") + get_nodes_in_group("backdrop_vehicles"):
		if vehicle_value is Node3D:
			(vehicle_value as Node3D).visible = vehicle_value == ambulance
	room.call("_toggle_asset_highlight", "ambulances")
	_frame_target(camera, room, ambulance, Vector3(1.4, 0.55, 1.9), 2.6)
	print("FIX80_RENDER|ambulance|save=", await _save_view(viewport, "07_external_ambulance_and_frame.png"))

	var smoky := get_nodes_in_group("smoky_vehicles")[0] as Node3D
	for vehicle_value in get_nodes_in_group("vehicles") + get_nodes_in_group("backdrop_vehicles"):
		if vehicle_value is Node3D:
			(vehicle_value as Node3D).visible = vehicle_value == smoky
	room.call("_toggle_asset_highlight", "smoky_vehicles")
	var local_bounds := (city.get_node("CityLife") as Node).call("_vehicle_local_bounds", smoky) as AABB
	var exhaust_local := Vector3(local_bounds.get_center().x, local_bounds.position.y + local_bounds.size.y * 0.32, local_bounds.position.z - 0.2)
	var exhaust_world := smoky.to_global(exhaust_local)
	var camera_local := exhaust_local + Vector3(local_bounds.size.x * 0.85 + 2.0, 1.7, -4.4)
	camera.look_at_from_position(smoky.to_global(camera_local), exhaust_world + Vector3(0, 0.7, 0), Vector3.UP)
	print("FIX80_RENDER|black_exhaust|save=", await _save_view(viewport, "08_black_exhaust_smoke_and_frame.png", 45))
	print("FIX80_ASSET_RENDER_COMPLETE|frames=8")
	quit(0)
