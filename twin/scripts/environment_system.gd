extends Node

signal conditions_changed(snapshot: Dictionary)

const WorldTime := preload("res://scripts/world_time.gd")
const BinWeight := preload("res://scripts/props/bin_weight.gd")
## Automatic weather changes after this much simulated time, independent of the
## clock rate (a weather front passes every few minutes of play, not every six
## clock hours, which at real-time pace would never happen).
const WEATHER_CHANGE_SIM_SECONDS := 300.0
## The governor leaves Godot at the requested speed while SUMO delivers at least
## this fraction of it; below that the time scale follows what SUMO delivers.
const SPEED_TOLERANCE := 0.94
const SPEED_FOLLOW_RATE := 0.25
## Seconds are shown on the clock while it runs at no more than this rate.
const SECONDS_SHOWN_UP_TO_RATE := 4.0
const WEATHER_SEQUENCE := ["clear", "cloudy", "rain", "clear", "fog", "storm", "cloudy"]
const WEATHER_LABELS := {
	"clear":"Clear", "cloudy":"Cloudy", "rain":"Rain", "fog":"Fog", "storm":"Storm"
}
const WEATHER_TRAFFIC_FACTORS := {"clear":1.0, "cloudy":0.94, "rain":0.78, "fog":0.68, "storm":0.58}
const WEATHER_WALK_FACTORS := {"clear":1.0, "cloudy":1.0, "rain":0.84, "fog":0.90, "storm":0.68}

var simulation_minute := 9.0 * 60.0
var simulation_speed := 1.0
var weather := "clear"
var automatic_weather := true
var _weather_countdown := WEATHER_CHANGE_SIM_SECONDS
var _weather_index := 0
var _last_minute := 9 * 60
var _last_hour := 9
var _last_dispatched_day := -1
var _sensor_sample_sequence := 0
var _command_udp := PacketPeerUDP.new()
var _rain: GPUParticles3D
var _environment: Environment
var _sun: DirectionalLight3D
var _sky_fill: DirectionalLight3D
var _base_walking_speed := 1.55
var _surface_water_depth_mm := 0.0
var _surface_water_rise_rate := 0.0


var _window_night_applied := -1.0


func _ready() -> void:
	process_priority = -80
	WorldTime.reset()
	var port_text := OS.get_environment("CITY_SUMO_CONTROL_PORT")
	_command_udp.connect_to_host("127.0.0.1", int(port_text) if port_text.is_valid_int() else 8815)
	var city := get_parent() as Node3D
	var world := city.get_node_or_null("WorldEnvironment") as WorldEnvironment
	_environment = world.environment if world != null else null
	_sun = city.get_node_or_null("Sun") as DirectionalLight3D
	_sky_fill = city.get_node_or_null("SkyFill") as DirectionalLight3D
	_create_precipitation(city)
	RenderingServer.global_shader_parameter_set(&"city_night_factor", 0.0)
	_apply_weather_side_effects()
	_apply_environment()


func _process(delta: float) -> void:
	if delta <= 0.0:
		return
	var life := get_parent().get_node_or_null("CityLife")
	WorldTime.paused = life != null and bool(life.get("sim_paused"))
	_govern_time_scale(life, delta)
	# Delta is simulated time (Godot's time scale tracks SUMO), so the clock moves
	# in step with everything else; it stands still while SUMO is paused.
	simulation_minute += WorldTime.advance_minutes(delta)
	if not WorldTime.paused:
		_update_surface_water(delta)
		if automatic_weather:
			_weather_countdown -= delta
			if _weather_countdown <= 0.0:
				_weather_countdown = WEATHER_CHANGE_SIM_SECONDS
				_weather_index = (_weather_index + 1) % WEATHER_SEQUENCE.size()
				set_weather(WEATHER_SEQUENCE[_weather_index], true)
	var absolute_minute := int(floor(simulation_minute))
	if absolute_minute != _last_minute:
		_last_minute = absolute_minute
		var hour := int(fposmod(simulation_minute, 1440.0) / 60.0)
		if hour != _last_hour:
			_last_hour = hour
			_apply_city_activity_context()
		_maybe_dispatch_night_collection()
	_apply_environment()


func set_simulation_speed(multiplier: float) -> void:
	simulation_speed = clampf(multiplier, 0.25, 8.0)
	Engine.time_scale = simulation_speed
	_send_command({"command":"set_speed", "multiplier":simulation_speed})
	conditions_changed.emit(get_conditions())


func set_clock_rate(clock_seconds_per_sim_second: float) -> void:
	## How fast the city clock runs relative to simulated time (see WorldTime).
	WorldTime.clock_rate = clampf(clock_seconds_per_sim_second, 1.0, 600.0)
	conditions_changed.emit(get_conditions())


func _govern_time_scale(life: Node, delta: float) -> void:
	## SUMO is authoritative. At high speeds it can fall short of the speed that
	## was asked for (each simulated step must be computed, not skipped); Godot's
	## time scale then follows what is really delivered, so people, crews, timers
	## and the clock stay in step with the traffic instead of racing ahead of it.
	if life == null or not bool(life.get("sumo_connected")) or bool(life.get("sim_paused")):
		return
	var delivered := float(life.get("sim_rate"))
	var target := simulation_speed
	if delivered < simulation_speed * SPEED_TOLERANCE:
		target = clampf(delivered, 0.25, simulation_speed)
	Engine.time_scale = lerpf(Engine.time_scale, target, 1.0 - exp(-SPEED_FOLLOW_RATE * 6.0 * delta / maxf(Engine.time_scale, 0.25)))
	if absf(Engine.time_scale - target) < 0.02:
		Engine.time_scale = target


func set_time_of_day(minute_of_day: int) -> void:
	var day := int(floor(simulation_minute / 1440.0))
	simulation_minute = float(day * 1440 + clampi(minute_of_day, 0, 1439))
	_last_minute = int(simulation_minute)
	_last_hour = int(minute_of_day / 60)
	var society := get_parent().get_node_or_null("NpcAi/SocietyKernel")
	if society != null and society.has_method("set_time_of_day"):
		society.call("set_time_of_day", minute_of_day)
	_apply_city_activity_context()
	_apply_environment()
	_maybe_dispatch_night_collection()
	conditions_changed.emit(get_conditions())


func set_weather(next_weather: String, preserve_automatic := false) -> void:
	if not WEATHER_LABELS.has(next_weather):
		return
	weather = next_weather
	if not preserve_automatic:
		automatic_weather = false
	_apply_weather_side_effects()
	conditions_changed.emit(get_conditions())


func set_automatic_weather(enabled: bool) -> void:
	automatic_weather = enabled
	if enabled:
		_weather_countdown = WEATHER_CHANGE_SIM_SECONDS
	conditions_changed.emit(get_conditions())


func get_conditions() -> Dictionary:
	var minute_of_day := int(fposmod(simulation_minute, 1440.0))
	return {
		"simulation_minute":simulation_minute,
		"day":int(floor(simulation_minute / 1440.0)) + 1,
		"minute_of_day":minute_of_day,
		"time_text":"%02d:%02d" % [int(minute_of_day / 60), minute_of_day % 60],
		"clock_text":WorldTime.format_clock(simulation_minute, WorldTime.clock_rate <= SECONDS_SHOWN_UP_TO_RATE),
		"clock_rate":WorldTime.clock_rate,
		"paused":WorldTime.paused,
		"speed_delivered":Engine.time_scale,
		"period":_period_name(minute_of_day),
		"is_night":_night_factor(minute_of_day) > 0.55,
		"night_factor":_night_factor(minute_of_day),
		"weather":weather,
		"weather_label":str(WEATHER_LABELS[weather]),
		"automatic_weather":automatic_weather,
		"speed":simulation_speed,
		"traffic_factor":float(WEATHER_TRAFFIC_FACTORS[weather]),
	}


func get_sensor_snapshot() -> Array[Dictionary]:
	_sensor_sample_sequence += 1
	var result: Array[Dictionary] = []
	var conditions := get_conditions()
	var vehicles := get_tree().get_nodes_in_group("vehicles") + get_tree().get_nodes_in_group("backdrop_vehicles")
	var active_vehicles: Array[Node3D] = []
	for value in vehicles:
		if value is Node3D and (value as Node3D).visible:
			active_vehicles.append(value as Node3D)
	var power_ratio := 1.0
	var water_ratio := 1.0
	var power_demand_kw := 0.0
	var power_outages := 0
	var water_outages := 0
	var utilities := get_parent().get_node_or_null("UtilityResources")
	if utilities != null and utilities.has_method("get_utility_status"):
		var status := utilities.call("get_utility_status") as Dictionary
		var power := status.get("power", {}) as Dictionary
		power_ratio = float(power.get("available_kwh", 1.0)) / maxf(float(power.get("capacity_kwh", 1.0)), 0.001)
		power_demand_kw = float(power.get("demand_kw", 0.0))
		var water := status.get("water", {}) as Dictionary
		water_ratio = float(water.get("current_m3", 1.0)) / maxf(float(water.get("capacity_m3", 1.0)), 0.001)
		water_outages = (status.get("water_outage_blocks", []) as Array).size()
		power_outages = (status.get("manual_power_clusters", []) as Array).size() + int(not str(status.get("power_shed_cluster", "")).is_empty())
	# Positions and metadata are read once per snapshot, not once per sensor (this runs at the MCP tick rate).
	var vehicle_xz := PackedVector2Array()
	var vehicle_speed := PackedFloat32Array()
	var vehicle_live := PackedByteArray()
	var vehicle_pm := PackedFloat32Array()
	for vehicle in active_vehicles:
		vehicle_xz.append(Vector2(vehicle.global_position.x, vehicle.global_position.z))
		vehicle_live.append(1 if vehicle.has_meta("sumo_speed") else 0)
		vehicle_speed.append(float(vehicle.get_meta("sumo_speed", 9.0)))
		vehicle_pm.append(float(vehicle.get_meta("estimated_pm_factor", 0.35)))
	var active_people: Array[Dictionary] = []
	var person_xz := PackedVector2Array()
	var person_moving := PackedByteArray()
	var npc := get_parent().get_node_or_null("NpcAi")
	if npc != null and npc.has_method("get_agent_debug"):
		for state_value in npc.call("get_agent_debug") as Array[Dictionary]:
			var state := state_value as Dictionary
			var person := state.get("node") as Node3D
			if person != null and person.visible:
				active_people.append(state)
				person_xz.append(Vector2(person.global_position.x, person.global_position.z))
				person_moving.append(1 if float(state.get("last_speed", 0.0)) > 0.08 else 0)
	for value in get_tree().get_nodes_in_group("sensors"):
		if not value is Node3D:
			continue
		var sensor := value as Node3D
		var sensor_id := str(sensor.get_meta("sensor_id", sensor.name))
		var metrics := sensor.get_meta("sensed_metrics", []) as Array
		if metrics.is_empty():
			continue
		var local_count := 0
		var local_stopped := 0
		var local_speed_total := 0.0
		var local_emission_load := 0.0
		var sensor_xz := Vector2(sensor.global_position.x, sensor.global_position.z)
		for index in vehicle_xz.size():
			var distance := vehicle_xz[index].distance_to(sensor_xz)
			if distance > 82.0:
				continue
			local_count += 1
			var speed := vehicle_speed[index]
			local_speed_total += speed
			local_stopped += int(vehicle_live[index] == 1 and speed < 0.15)
			var proximity := 1.0 - distance / 82.0
			local_emission_load += proximity * (vehicle_pm[index] + (1.2 if speed < 0.15 else 0.15))
		var local_average_speed := local_speed_total / maxf(1.0, float(local_count))
		var local_people := 0
		var local_moving_people := 0
		for index in person_xz.size():
			if person_xz[index].distance_to(sensor_xz) <= 62.0:
				local_people += 1
				local_moving_people += person_moving[index]
		var live_context := {
			"vehicle_count":local_count, "stopped":local_stopped, "average_speed":local_average_speed,
			"emission_load":local_emission_load, "people":local_people, "moving_people":local_moving_people,
			"power_ratio":power_ratio, "water_ratio":water_ratio, "power_demand_kw":power_demand_kw,
			"power_outages":power_outages, "water_outages":water_outages,
			"sensor_node":sensor,
		}
		var readings := {}
		for metric_value in metrics:
			var metric := str(metric_value)
			readings[metric] = _sensor_reading(metric, sensor_id, conditions, live_context)
		result.append({
			"id":sensor_id,
			"name":sensor.name,
			"type":str(sensor.get_meta("asset_type", "sensor")).replace("_", " ").capitalize(),
			"position":sensor.global_position,
			"node":sensor,
			"sample_sequence":_sensor_sample_sequence,
			"readings":readings,
		})
	result.sort_custom(func(a: Dictionary, b: Dictionary) -> bool: return str(a.id) < str(b.id))
	return result


static func _saturate(amount: float, spread: float) -> float:
	## 0 with no traffic, rising quickly at first and levelling off at 1 however dense the traffic gets.
	return 1.0 - exp(-maxf(0.0, amount) / maxf(spread, 0.001))


static func _air_release(sensor: Node3D, weight: float) -> float:
	## Extra µg/m³ from a release set on this monitor (device API: AIR-xx release): it rises over the first 15 %
	## of its duration, holds, then falls back to zero by the end.
	if sensor == null or not sensor.has_meta("air_release_ug"):
		return 0.0
	var duration := maxf(1.0, float(sensor.get_meta("air_release_seconds", 90.0)))
	var elapsed := float(Time.get_ticks_msec() - int(sensor.get_meta("air_release_start_msec", 0))) / 1000.0
	if elapsed >= duration:
		sensor.remove_meta("air_release_ug")
		return 0.0
	var share := elapsed / duration
	var envelope := clampf(share / 0.15, 0.0, 1.0) if share < 0.15 else (1.0 if share < 0.6 else clampf((1.0 - share) / 0.4, 0.0, 1.0))
	return float(sensor.get_meta("air_release_ug", 0.0)) * envelope * weight


func _sensor_reading(metric: String, sensor_id: String, conditions: Dictionary, live: Dictionary) -> Dictionary:
	var sensor_noise := sin(float((sensor_id.hash() & 1023)) * 0.071 + float(_sensor_sample_sequence) * 0.83) * 0.12
	var rain_strength := 0.0
	if weather == "rain": rain_strength = 4.2
	elif weather == "storm": rain_strength = 12.5
	var vehicle_count := int(live.vehicle_count)
	var stopped := int(live.stopped)
	var average_speed := float(live.average_speed)
	var emission_load := float(live.emission_load)
	var people := int(live.people)
	var moving_people := int(live.moving_people)
	if metric in BinWeight.METRICS:
		return (BinWeight.readings(live.sensor_node as Node3D) as Dictionary)[metric]
	if metric in ["ground_acceleration", "ground_velocity", "shaking_intensity"]:
		var seismograph := live.sensor_node as Node3D
		match metric:
			"ground_acceleration": return {"value": snappedf(float(seismograph.get_meta("seis_pga", 0.0)), 0.0001), "unit": "m/s²"}
			"ground_velocity": return {"value": snappedf(float(seismograph.get_meta("seis_pgv", 0.0)), 0.001), "unit": "mm/s"}
			_: return {"value": snappedf(float(seismograph.get_meta("seis_mmi", 1.0)), 0.1), "unit": "MMI"}
	var value := 0.0
	var unit := ""
	match metric:
		"vehicle_count": value = float(vehicle_count); unit = "vehicles"
		"speed": value = maxf(0.0, average_speed * 3.6 + sensor_noise); unit = "km/h"
		"occupancy": value = clampf(float(stopped) / maxf(1.0, float(vehicle_count)) * 72.0 + float(vehicle_count) * 3.5, 0.0, 100.0); unit = "%"
		# Traffic raises the readings but saturates: a jam around a sensor reads "moderate" (risk score up to ~80
		# with the WHO 2021 guidelines 15 / 45 / 25 µg/m³), never alert or critical on its own. Only a real event
		# (an AIR-xx release, the scenarios' pollution peak) pushes a zone past 100.
		"pm2_5": value = maxf(2.0, 5.5 + _saturate(emission_load, 4.0) * 6.5 - rain_strength * 0.25 + sensor_noise + _air_release(live.sensor_node as Node3D, 1.0)); unit = "µg/m³"
		"pm10": value = maxf(4.0, 10.0 + _saturate(emission_load, 4.0) * 14.0 - rain_strength * 0.35 + sensor_noise + _air_release(live.sensor_node as Node3D, 1.6)); unit = "µg/m³"
		"nitrogen_dioxide": value = maxf(2.0, 7.0 + _saturate(emission_load + float(stopped) * 0.5, 5.0) * 11.0 + sensor_noise + _air_release(live.sensor_node as Node3D, 0.5)); unit = "µg/m³"
		"vehicle_smoke": value = clampf(emission_load * 9.0 + sensor_noise, 0.0, 100.0); unit = "%"
		"db_a": value = 38.0 + float(vehicle_count) * 2.1 + average_speed * 0.42 + float(people) * 0.28 + (8.0 if weather == "storm" else 0.0) + sensor_noise; unit = "dBA"
		"peak_noise": value = 48.0 + float(vehicle_count) * 2.8 + float(stopped) * 1.4 + float(moving_people) * 0.35 + (12.0 if weather == "storm" else 0.0) + absf(sensor_noise); unit = "dBA"
		"traffic_noise": value = 35.0 + float(vehicle_count) * 2.0 + average_speed * 0.55 + sensor_noise; unit = "dBA"
		"wind_speed": value = 2.8 + (13.0 if weather == "storm" else 4.0 if weather == "rain" else 1.5 if weather == "cloudy" else 0.0) + absf(sensor_noise) * 3.0; unit = "m/s"
		"wind_direction": value = fposmod(210.0 + simulation_minute * 0.4 + sensor_noise * 18.0, 360.0); unit = "°"
		"rainfall": value = maxf(0.0, rain_strength + sensor_noise * (1.2 if rain_strength > 0.0 else 0.0)); unit = "mm/h"
		"temperature": value = 23.0 + sin((float(conditions.minute_of_day) / 1440.0 - 0.25) * TAU) * 6.0 - (2.5 if weather in ["rain", "storm"] else 0.0) + sensor_noise; unit = "°C"
		"surface_water_depth": value = maxf(0.0, _surface_water_depth_mm + sensor_noise * 0.08); unit = "mm"
		"rise_rate": value = _surface_water_rise_rate; unit = "mm/min"
		"voltage": value = 218.0 + float(live.power_ratio) * 13.0 - float(live.power_outages) * 34.0 - float(conditions.night_factor) * 1.7 - float(live.power_demand_kw) * 0.0008 + sensor_noise; unit = "V"
		"water_pressure": value = maxf(0.0, 2.1 + float(live.water_ratio) * 2.2 - float(live.water_outages) * 0.16 + sensor_noise * 0.05); unit = "bar"
		"cabinet_temperature": value = 25.0 + float(live.power_demand_kw) * 0.002 + float(conditions.night_factor) * -3.0 + sensor_noise; unit = "°C"
		"power_demand": value = float((live.sensor_node as Node3D).get_meta("reading_kw", 0.0)); unit = "kW"
		"water_flow": value = float((live.sensor_node as Node3D).get_meta("reading_m3h", 0.0)); unit = "m³/h"
		"supply_status": value = 100.0 if bool((live.sensor_node as Node3D).get_meta("service_online", false)) else 0.0; unit = "%"
		_: value = sensor_noise; unit = ""
	return {"value":snappedf(value, 0.1), "unit":unit}


func _update_surface_water(delta: float) -> void:
	var input_per_second := 0.52 if weather == "storm" else 0.19 if weather == "rain" else 0.0
	var drain_per_second := 0.04 + _surface_water_depth_mm * 0.018
	var previous := _surface_water_depth_mm
	_surface_water_depth_mm = clampf(_surface_water_depth_mm + (input_per_second - drain_per_second) * delta, 0.0, 42.0)
	_surface_water_rise_rate = (_surface_water_depth_mm - previous) / maxf(delta, 0.001) * 60.0 / maxf(WorldTime.clock_rate, 0.001)


func _apply_environment() -> void:
	var minute_of_day := int(fposmod(simulation_minute, 1440.0))
	var night := _night_factor(minute_of_day)
	var solar_angle := (float(minute_of_day) / 1440.0 - 0.25) * TAU
	var solar_height := sin(solar_angle)
	if _sun != null:
		_sun.rotation_degrees.x = -maxf(4.0, solar_height * 62.0)
		_sun.rotation_degrees.y = -38.0 + cos(solar_angle) * 72.0
		_sun.light_energy = maxf(0.0, solar_height) * (0.72 if weather in ["cloudy", "rain"] else 0.48 if weather == "storm" else 1.28)
		_sun.light_color = Color(1.0, 0.56 + maxf(0.0, solar_height) * 0.30, 0.42 + maxf(0.0, solar_height) * 0.48)
	if _sky_fill != null:
		_sky_fill.light_energy = lerpf(0.20, 0.14, night)
	if _environment != null:
		var weather_dim := 0.72 if weather == "storm" else 0.84 if weather in ["cloudy", "rain", "fog"] else 1.0
		_environment.background_energy_multiplier = lerpf(0.86 * weather_dim, 0.18, night)
		_environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR if night > 0.08 else Environment.AMBIENT_SOURCE_SKY
		_environment.ambient_light_energy = lerpf(0.52 * weather_dim, 0.62, night)
		_environment.ambient_light_color = Color(0.72, 0.79, 0.90).lerp(Color(0.26, 0.34, 0.52), night)
		_environment.tonemap_exposure = lerpf(0.94, 1.14, night)
		_environment.fog_density = 0.0055 if weather == "fog" else 0.0032 if weather == "storm" else 0.00145 if weather == "rain" else 0.00085 if weather == "cloudy" else 0.00065
		_environment.fog_light_color = Color(0.64, 0.70, 0.77).lerp(Color(0.13, 0.20, 0.32), night)
		_environment.fog_light_energy = lerpf(0.36, 0.62, night)
	RenderingServer.global_shader_parameter_set(&"city_night_factor", night)
	_update_lights(night)


func _update_lights(night: float) -> void:
	for value in get_tree().get_nodes_in_group("smart_street_lights"):
		if not value is Node3D:
			continue
		var light_root := value as Node3D
		var mode := str(light_root.get_meta("light_mode", "auto"))
		var level := night
		if mode == "on":
			level = 1.0
		elif mode == "off":
			level = 0.0
		level *= float(light_root.get_meta("light_dim", 1.0))
		var lamp := light_root.get_node_or_null("AdaptiveLuminaire") as Light3D
		if lamp != null:
			lamp.light_energy = level * (20.0 if weather in ["rain", "fog", "storm"] else 16.0)
			lamp.visible = level > 0.04
		var glow := light_root.get_node_or_null("LuminaireGlow") as MeshInstance3D
		if glow != null:
			glow.visible = level > 0.04
		light_root.set_meta("light_level", snappedf(level, 0.01))
		light_root.set_meta("actuator_state", ("on_adaptive" if level > 0.3 else "daylight_off") if mode == "auto" else "manual_" + mode)
	for value in get_tree().get_nodes_in_group("building_night_lights"):
		if not value is Node3D:
			continue
		var fixture := value as Node3D
		var facade_light := fixture.get_node_or_null("BuildingFacadeLight") as SpotLight3D
		var window := fixture.get_node_or_null("BuildingLampGlow") as MeshInstance3D
		if facade_light != null:
			facade_light.light_energy = night * (6.2 if weather in ["rain", "fog", "storm"] else 5.0)
			facade_light.visible = night > 0.04
		if window != null:
			window.visible = night > 0.04
	# Thousands of window overlays: touch them only when the light level moved.
	if absf(night - _window_night_applied) > 0.004 or (night <= 0.04) != (_window_night_applied <= 0.04):
		_window_night_applied = night
		for value in get_tree().get_nodes_in_group("building_window_lights"):
			var window_glow := value as GeometryInstance3D
			if window_glow == null:
				continue
			window_glow.visible = night > 0.04
			window_glow.set_instance_shader_parameter("night_strength", night)
	for value in get_tree().get_nodes_in_group("building_sign_lights"):
		if value is GeometryInstance3D:
			(value as GeometryInstance3D).visible = night > 0.08
	for value in get_tree().get_nodes_in_group("vehicles") + get_tree().get_nodes_in_group("backdrop_vehicles"):
		if not value is Node3D:
			continue
		var vehicle := value as Node3D
		var headlight := vehicle.get_node_or_null("NightHeadlight") as SpotLight3D
		if headlight == null:
			headlight = SpotLight3D.new()
			headlight.name = "NightHeadlight"
			headlight.position = Vector3(0.0, 0.72, 2.15)
			headlight.rotation_degrees = Vector3(-7.0, 180.0, 0.0)
			headlight.light_color = Color(1.0, 0.88, 0.68)
			headlight.spot_range = 31.0
			headlight.spot_angle = 46.0
			headlight.shadow_enabled = false
			headlight.distance_fade_enabled = true
			headlight.distance_fade_begin = 120.0
			headlight.distance_fade_length = 60.0
			vehicle.add_child(headlight)
		headlight.light_energy = night * (4.8 if weather in ["rain", "fog", "storm"] else 3.6)
		headlight.visible = vehicle.visible and night > 0.08


func _apply_weather_side_effects() -> void:
	if _rain != null:
		_rain.emitting = weather in ["rain", "storm"]
		_rain.amount_ratio = 1.0 if weather == "storm" else 0.46 if weather == "rain" else 0.0
	var npc := get_parent().get_node_or_null("NpcAi")
	if npc != null:
		_base_walking_speed = maxf(_base_walking_speed, float(npc.get("walking_speed")))
		npc.set("walking_speed", _base_walking_speed * float(WEATHER_WALK_FACTORS[weather]))
	_send_command({"command":"set_weather", "weather":weather, "traffic_factor":float(WEATHER_TRAFFIC_FACTORS[weather])})
	_apply_city_activity_context()


func _apply_city_activity_context() -> void:
	# SocietyKernel remains authoritative for agenda changes, travel, finite
	# interactions and reassignment. Time jumps are synchronized in
	# set_time_of_day(); this environment layer must never suspend embodied AI.
	pass


func _maybe_dispatch_night_collection() -> void:
	var day := int(floor(simulation_minute / 1440.0))
	var minute_of_day := int(fposmod(simulation_minute, 1440.0))
	if _garbage_round_already_called():
		_last_dispatched_day = day
	if minute_of_day >= 20 * 60 and _last_dispatched_day != day:
		_last_dispatched_day = day
		_send_command({"command":"incident", "action":"trigger", "type":"garbage_collection", "params":{"source":"automatic_night_schedule"}, "request":"night-garbage-%d" % day})


func _garbage_round_already_called() -> bool:
	var manager := get_parent().get_node_or_null("Incidents")
	if manager == null:
		return false
	var state := manager.get("last_state") as Dictionary
	for record_value in (state.get("incidents", []) as Array) + (state.get("resolved_incidents", []) as Array):
		if record_value is Dictionary and str((record_value as Dictionary).get("kind", "")) == "garbage_collection":
			return true
	return false


func _create_precipitation(city: Node3D) -> void:
	_rain = GPUParticles3D.new()
	_rain.name = "WeatherPrecipitation"
	_rain.amount = 1800
	_rain.lifetime = 1.7
	_rain.visibility_aabb = AABB(Vector3(-250, -40, -250), Vector3(500, 160, 500))
	var process := ParticleProcessMaterial.new()
	process.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_BOX
	process.emission_box_extents = Vector3(120.0, 2.0, 120.0)
	process.direction = Vector3(0.10, -1.0, 0.03)
	process.spread = 4.0
	process.initial_velocity_min = 42.0
	process.initial_velocity_max = 58.0
	process.gravity = Vector3(0.0, -15.0, 0.0)
	_rain.process_material = process
	var quad := QuadMesh.new()
	quad.size = Vector2(0.035, 1.4)
	quad.orientation = PlaneMesh.FACE_Z
	var material := StandardMaterial3D.new()
	material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	material.albedo_color = Color(0.62, 0.76, 0.94, 0.62)
	material.billboard_mode = BaseMaterial3D.BILLBOARD_ENABLED
	quad.material = material
	_rain.draw_pass_1 = quad
	_rain.position = Vector3(0.0, 74.0, 0.0)
	city.add_child.call_deferred(_rain)


func _night_factor(minute_of_day: int) -> float:
	var hour := float(minute_of_day) / 60.0
	if hour < 5.5 or hour >= 20.5:
		return 1.0
	if hour < 7.0:
		return 1.0 - smoothstep(5.5, 7.0, hour)
	if hour >= 19.0:
		return smoothstep(19.0, 20.5, hour)
	return 0.0


func _period_name(minute_of_day: int) -> String:
	var hour := int(minute_of_day / 60)
	if hour < 5: return "Night"
	if hour < 8: return "Dawn"
	if hour < 12: return "Morning"
	if hour < 17: return "Afternoon"
	if hour < 20: return "Evening"
	return "Night"


func _send_command(payload: Dictionary) -> void:
	_command_udp.put_packet(JSON.stringify(payload).to_utf8_buffer())
