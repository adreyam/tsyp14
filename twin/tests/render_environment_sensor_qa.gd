extends SceneTree

const CitySpatialSafety = preload("res://scripts/city_spatial_safety.gd")


func _init() -> void:
	call_deferred("_render")


func _render() -> void:
	root.size = Vector2i(1600, 900)
	var room := (load("res://scenes/ControlRoom.tscn") as PackedScene).instantiate()
	root.add_child(room)
	for _unused in 45:
		await process_frame
	room.call("_sample_sensors")
	var tabs := room.get("_control_tabs") as TabContainer
	tabs.current_tab = 0
	for _unused in 5:
		await process_frame
	var control_result := root.get_texture().get_image().save_png("/tmp/city_environment_controls.png")
	var world_clock := room.get("_world_clock") as Control
	world_clock.call("set_expanded", true)
	for _unused in 4:
		await process_frame
	var clock_result := root.get_texture().get_image().save_png("/tmp/city_environment_clock_expanded.png")
	world_clock.call("set_expanded", false)
	tabs.current_tab = tabs.get_tab_idx_from_control(tabs.get_node("Sensors"))
	var readings := room.get("_sensor_reading_grid") as GridContainer
	if readings.get_child_count() > 0:
		(readings.get_child(0) as Button).emit_signal("pressed")
	var environment := room.get("_environment_system") as Node
	environment.call("set_time_of_day", 22 * 60)
	environment.call("set_weather", "rain")
	room.set("_last_sensor_sample_minute", -99999)
	room.call("_sample_sensors")
	for _unused in 20:
		await process_frame
	room.call("_show_overview")
	for _unused in 12:
		await process_frame
	var full_city_lighting_result := (room.get("_city_viewport") as SubViewport).get_texture().get_image().save_png("/tmp/city_environment_full_lighting.png")
	var ui_result := root.get_texture().get_image().save_png("/tmp/city_environment_sensor_ui.png")
	tabs.current_tab = tabs.get_tab_idx_from_control(tabs.get_node("NPC Intent"))
	for _unused in 4:
		await process_frame
	var society_result := root.get_texture().get_image().save_png("/tmp/city_environment_society_ui.png")
	room.call("_select_sensor", "WX-01", true)
	for _unused in 8:
		await process_frame
	var city_viewport := room.get("_city_viewport") as SubViewport
	var focus_result := city_viewport.get_texture().get_image().save_png("/tmp/city_sensor_focus.png")
	var city_result := city_viewport.get_texture().get_image().save_png("/tmp/city_environment_night_rain.png")
	room.call("_show_street")
	for _unused in 12:
		await process_frame
	var street_result := city_viewport.get_texture().get_image().save_png("/tmp/city_environment_night_street.png")
	var street_lights := get_nodes_in_group("smart_street_lights")
	var city := room.get("_city") as Node3D
	var city_camera := city.get_node_or_null("EstablishingCamera") as Camera3D
	var captured_profiles := {}
	var profile_capture_count := 0
	if city_camera != null:
		for value in get_nodes_in_group("building_night_lights"):
			var fixture := value as Node3D
			var profile_name := str(fixture.get_meta("model_profile", ""))
			if profile_name.is_empty() or captured_profiles.has(profile_name):
				continue
			var source_path := NodePath(str(fixture.get_meta("source_building", "")))
			var building := root.get_node_or_null(source_path) as Node3D
			if building == null:
				continue
			captured_profiles[profile_name] = true
			profile_capture_count += 1
			var bounds := CitySpatialSafety.world_bounds(building)
			var outward := fixture.get_meta("mounting_outward", Vector3.BACK) as Vector3
			var radius := maxf(bounds.size.x, maxf(bounds.size.z, bounds.size.y * 0.65))
			city_camera.global_position = bounds.get_center() + outward * maxf(12.0, radius * 1.55) + Vector3.UP * bounds.size.y * 0.08
			city_camera.look_at(bounds.get_center() + Vector3.UP * bounds.size.y * 0.03, Vector3.UP)
			city_camera.current = true
			for _unused in 3:
				await process_frame
			city_viewport.get_texture().get_image().save_png("/tmp/building_night_profile_%02d.png" % profile_capture_count)
	if not street_lights.is_empty() and city_camera != null:
		var street_light := street_lights[0] as Node3D
		var target := street_light.global_position + Vector3(0.0, 3.4, 0.0)
		city_camera.global_position = target + Vector3(9.0, 4.0, 10.0)
		city_camera.look_at(target, Vector3.UP)
		city_camera.current = true
		for _unused in 10:
			await process_frame
	var light_result := city_viewport.get_texture().get_image().save_png("/tmp/city_integrated_street_light.png")
	var building_fixtures := get_nodes_in_group("building_night_lights")
	if not building_fixtures.is_empty() and city_camera != null:
		var building_fixture := building_fixtures[0] as Node3D
		var outward := building_fixture.get_meta("mounting_outward", Vector3.FORWARD) as Vector3
		city_camera.global_position = building_fixture.global_position + outward * 7.0 + Vector3.UP * 2.2
		city_camera.look_at(building_fixture.global_position + Vector3.DOWN * 0.15, Vector3.UP)
		for _unused in 10:
			await process_frame
	var building_light_result := city_viewport.get_texture().get_image().save_png("/tmp/city_building_sconce.png")
	print("ENVIRONMENT_RENDER|controls=", control_result, "|clock=", clock_result, "|full_city_lighting=", full_city_lighting_result, "|ui=", ui_result, "|society=", society_result, "|focus=", focus_result, "|city=", city_result, "|street=", street_result, "|profiles=", profile_capture_count, "|light=", light_result, "|building_light=", building_light_result)
	quit()
