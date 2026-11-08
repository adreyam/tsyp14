extends SceneTree


func _init() -> void:
	call_deferred("_run")


func _fail(message: String) -> void:
	Engine.time_scale = 1.0
	printerr("ENVIRONMENT / SENSOR TEST FAILED: " + message)
	quit(1)


func _run() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	root.add_child(city)
	await process_frame
	await process_frame
	var environment := city.get_node_or_null("EnvironmentSystem")
	if environment == null:
		_fail("City has no EnvironmentSystem")
		return
	var sensors := environment.call("get_sensor_snapshot") as Array[Dictionary]
	if sensors.size() != 88:
		_fail("expected all 88 physical sensor streams, got %d" % sensors.size())
		return
	for sensor in sensors:
		if str(sensor.id).is_empty() or (sensor.readings as Dictionary).is_empty():
			_fail("sensor stream is missing identity or readings")
			return
	for required_id in ["CAM-01", "PWR-01", "WTR-01", "TRAF-01", "AIR-01", "NOISE-01", "WX-01", "FLOOD-01", "UTIL-01"]:
		var required_sensor := _sensor_by_id(sensors, required_id)
		if required_sensor.is_empty() or (required_sensor.readings as Dictionary).is_empty():
			_fail("physical map sensor %s is absent from live telemetry" % required_id)
			return
	var air_sensor := get_nodes_in_group("air_quality_monitors")[0] as Node3D
	var smoky_vehicle := get_nodes_in_group("smoky_vehicles")[0] as Node3D
	var original_vehicle_position := smoky_vehicle.global_position
	var pm_before := _reading_value(sensors, str(air_sensor.get_meta("sensor_id")), "pm2_5")
	smoky_vehicle.global_position = air_sensor.global_position + Vector3(2.0, 0.0, 0.0)
	smoky_vehicle.set_meta("sumo_speed", 0.0)
	var pm_after := _reading_value(environment.call("get_sensor_snapshot") as Array[Dictionary], str(air_sensor.get_meta("sensor_id")), "pm2_5")
	smoky_vehicle.global_position = original_vehicle_position
	if pm_after <= pm_before + 4.0:
		_fail("air sensor did not react to an actual high-emission vehicle arriving nearby")
		return
	var utilities := city.get_node("UtilityResources") as Node
	var utility_before := _reading_value(environment.call("get_sensor_snapshot") as Array[Dictionary], "UTIL-01", "voltage")
	var utility_status := utilities.call("get_utility_status") as Dictionary
	var cluster_name := str(((utility_status.service_clusters as Array)[0] as Dictionary).name)
	var power_meter_id := _meter_sensor_id("power_service_meters", cluster_name)
	var water_meter_id := _meter_sensor_id("water_service_meters", cluster_name)
	var power_meter_before := _reading_value(environment.call("get_sensor_snapshot") as Array[Dictionary], power_meter_id, "power_demand")
	utilities.call("set_manual_power_cluster", cluster_name, true)
	var utility_after := _reading_value(environment.call("get_sensor_snapshot") as Array[Dictionary], "UTIL-01", "voltage")
	var power_meter_after := _reading_value(environment.call("get_sensor_snapshot") as Array[Dictionary], power_meter_id, "power_demand")
	utilities.call("set_manual_power_cluster", cluster_name, false)
	if utility_after >= utility_before - 20.0:
		_fail("utility sensor did not react to a real power service cut")
		return
	if power_meter_before <= 0.0 or power_meter_after != 0.0:
		_fail("physical zone power meter did not report its real service cut")
		return
	var water_meter_before := _reading_value(environment.call("get_sensor_snapshot") as Array[Dictionary], water_meter_id, "water_flow")
	utilities.call("set_manual_water_cluster", cluster_name, true)
	var water_meter_after := _reading_value(environment.call("get_sensor_snapshot") as Array[Dictionary], water_meter_id, "water_flow")
	utilities.call("set_manual_water_cluster", cluster_name, false)
	if water_meter_before <= 0.0 or water_meter_after != 0.0:
		_fail("physical zone water meter did not report its real service cut")
		return

	environment.call("set_time_of_day", 22 * 60)
	await process_frame
	var night := environment.call("get_conditions") as Dictionary
	if not bool(night.is_night) or str(night.period) != "Night":
		_fail("22:00 did not activate night conditions")
		return
	var lit := 0
	for value in get_nodes_in_group("smart_street_lights"):
		var lamp := (value as Node3D).get_node_or_null("AdaptiveLuminaire") as Light3D
		if lamp != null and lamp.visible and lamp.light_energy >= 15.5:
			lit += 1
	if lit != 256:
		_fail("night lighting activated %d/256 street and pedestrian luminaires" % lit)
		return
	var building_lights := 0
	for value in get_nodes_in_group("building_night_lights"):
		var lamp := (value as Node3D).get_node_or_null("BuildingFacadeLight") as SpotLight3D
		if lamp != null and lamp.visible and lamp.light_energy > 0.5:
			building_lights += 1
	if building_lights != get_nodes_in_group("building_night_lights").size():
		_fail("night lighting did not activate every retained exterior fixture")
		return
	var interior_windows := 0
	for value in get_nodes_in_group("building_window_lights"):
		var window := value as GeometryInstance3D
		if window != null and window.visible and float(window.get_instance_shader_parameter("night_strength")) > 0.5:
			interior_windows += 1
	if interior_windows < 224:
		_fail("night lighting activated only %d authored interior window surfaces" % interior_windows)
		return
	var npc := city.get_node("NpcAi")
	var visible_at_night := 0
	for state in npc.call("get_agent_debug") as Array[Dictionary]:
		visible_at_night += int((state.node as Node3D).visible)
		if str(state.motion_state) == "off_duty_at_home":
			_fail("environment clock halted an embodied citizen's existing lifecycle")
			return
	if visible_at_night != 32:
		_fail("environment clock hid citizens instead of preserving their lifecycle: %d/32 visible" % visible_at_night)
		return

	environment.call("set_weather", "rain")
	await process_frame
	var rain := city.get_node_or_null("WeatherPrecipitation") as GPUParticles3D
	if rain == null or not rain.emitting or float((environment.call("get_conditions") as Dictionary).traffic_factor) >= 1.0:
		_fail("rain did not enable precipitation and safe traffic behavior")
		return
	if float(npc.get("walking_speed")) >= 1.55:
		_fail("rain did not reduce pedestrian walking speed")
		return

	environment.call("set_time_of_day", 12 * 60)
	await process_frame
	for value in get_nodes_in_group("smart_street_lights"):
		var lamp := (value as Node3D).get_node_or_null("AdaptiveLuminaire") as Light3D
		if lamp != null and lamp.visible:
			_fail("street lights stayed on at noon")
			return
	environment.call("set_simulation_speed", 4.0)
	if not is_equal_approx(Engine.time_scale, 4.0):
		_fail("4x control did not update the Godot simulation clock")
		return
	environment.call("set_simulation_speed", 1.0)
	Engine.time_scale = 1.0
	print("ENVIRONMENT / SENSOR TEST PASSED: synchronized clock, street and exact-polygon interior-window lighting, speed and all 88 map-linked sensor streams")
	quit()


func _reading_value(snapshot: Array[Dictionary], sensor_id: String, metric: String) -> float:
	for sensor in snapshot:
		if str(sensor.id) == sensor_id:
			return float(((sensor.readings as Dictionary)[metric] as Dictionary).value)
	return -99999.0


func _sensor_by_id(snapshot: Array[Dictionary], sensor_id: String) -> Dictionary:
	for sensor in snapshot:
		if str(sensor.id) == sensor_id:
			return sensor
	return {}


func _meter_sensor_id(group_name: String, cluster_name: String) -> String:
	for value in get_nodes_in_group(group_name):
		var meter := value as Node3D
		if str(meter.get_meta("service_cluster", "")) == cluster_name:
			return str(meter.get_meta("sensor_id", ""))
	return ""
