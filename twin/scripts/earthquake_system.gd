extends Node

## Earthquakes. An earthquake hits one zone (or the whole city) with a magnitude. It shakes the ground, which the zone's seismic
## sensors record as real acceleration waveforms (the same numbers the Sensors tab graphs and the MCP tools return), shakes the
## camera, and brings down a share of the buildings in the affected area with an animated collapse. clear_all() removes every
## trace: ruins vanish and the buildings stand again, as if the quake had never happened.

const Zones := preload("res://scripts/city_zones.gd")
const BuildingCollapse := preload("res://scripts/building_collapse.gd")
const SAMPLE_RATE := 20.0
const WAVE_SAMPLES := 40
const MAX_ACTIVE_COLLAPSES := 18
const MAX_CITYWIDE_COLLAPSES := 70
const NOISE_FLOOR := 0.0004
const METRICS := ["ground_acceleration", "ground_velocity", "shaking_intensity"]
const CITY := "ALL"

signal earthquake_started(event: Dictionary)
signal earthquake_cleared(removed_events: int, restored_buildings: int)
signal building_collapsed(building_name: String, zone: String)

var events: Array[Dictionary] = []
var _clock := 0.0
var _next_id := 1
var _sample_accumulator := 0.0
var _buffers: Dictionary = {}
var _queue: Array[Dictionary] = []
var _collapses: Array[Node3D] = []
var _impacts: Array[Dictionary] = []
var _tremor: Dictionary = {}
var _tremor_at := 0.0
var _rng := RandomNumberGenerator.new()
var _shake_camera: Camera3D

# --- control ----------------------------------------------------------------------------------

func trigger(zone: String, magnitude: float) -> Dictionary:
	var target := zone.to_upper()
	if target in ["CITY", "WHOLE", "WHOLE_CITY", "ALL"]:
		target = CITY
	if target != CITY and not Zones.is_zone(target):
		return {"error": {"code": "bad_zone", "message": "zone must be one of %s or ALL" % ", ".join(PackedStringArray(Zones.IDS))}}
	var strength := clampf(magnitude, 3.0, 8.0)
	var duration := clampf(7.0 + 3.5 * (strength - 4.0), 8.0, 26.0)
	var event := {
		"id": "EQ-%03d" % _next_id, "zone": target, "magnitude": snappedf(strength, 0.1), "status": "active",
		"started_at": _clock, "duration_s": snappedf(duration, 0.1), "planned": 0, "collapsed": 0, "collapsed_buildings": [],
		"epicentre": Zones.centre(target) if target != CITY else Vector3.ZERO,
	}
	_next_id += 1
	_rng.seed = 7919 * _next_id
	var candidates := _buildings_in(target)
	var share := _collapse_share(strength)
	candidates.shuffle()
	var count := mini(int(round(share * float(candidates.size()))), MAX_CITYWIDE_COLLAPSES if target == CITY else 999)
	var selected := candidates.slice(0, count)
	selected.sort_custom(func(a: Node3D, b: Node3D) -> bool: return a.global_position.distance_to(event.epicentre) < b.global_position.distance_to(event.epicentre))
	for index in selected.size():
		var building := selected[index] as Node3D
		building.set_meta("collapse_pending", true)
		var progress := float(index) / maxf(1.0, float(selected.size() - 1))
		_queue.append({"building": building, "at": _clock + 1.2 + progress * duration * 0.82 + _rng.randf_range(0.0, 1.0), "event": event.id})
	event.planned = selected.size()
	events.append(event)
	earthquake_started.emit(public_event(event))
	return public_event(event)

func clear_all() -> Dictionary:
	var restored := 0
	for collapse in _collapses:
		if is_instance_valid(collapse):
			(collapse as Node3D).call("restore")
			restored += 1
	for item in _queue:
		var building := item.building as Node3D
		if is_instance_valid(building):
			building.remove_meta("collapse_pending")
	var removed := events.size()
	_collapses.clear()
	_queue.clear()
	_impacts.clear()
	events.clear()
	_reset_camera()
	earthquake_cleared.emit(removed, restored)
	return {"cleared_events": removed, "restored_buildings": restored}

func active_events() -> Array[Dictionary]:
	var out: Array[Dictionary] = []
	for event in events:
		if str(event.status) == "active":
			out.append(event)
	return out

func public_event(event: Dictionary) -> Dictionary:
	return {
		"id": event.id, "zone": event.zone, "magnitude": event.magnitude, "status": event.status, "duration_s": event.duration_s,
		"age_s": snappedf(_clock - float(event.started_at), 0.1), "buildings_planned": event.planned,
		"buildings_collapsed": event.collapsed, "collapsed_buildings": (event.collapsed_buildings as Array).duplicate(),
	}

func public_events() -> Array:
	var out: Array = []
	for event in events:
		out.append(public_event(event))
	return out

func collapsed_count() -> int:
	var count := 0
	for collapse in _collapses:
		if is_instance_valid(collapse):
			count += 1
	return count

func _collapse_share(magnitude: float) -> float:
	if magnitude < 5.0:
		return 0.0
	return clampf((magnitude - 4.8) * 0.42, 0.0, 1.0)

func _buildings_in(zone: String) -> Array[Node3D]:
	var out: Array[Node3D] = []
	for group_name in ["buildings", "backdrop_buildings"]:
		for value in get_tree().get_nodes_in_group(group_name):
			var building := value as Node3D
			if building == null or building.has_meta("collapsed") or building.has_meta("collapse_pending") or not building.visible:
				continue
			if zone == CITY or Zones.zone_of_position(building.global_position) == zone:
				out.append(building)
	return out

# --- simulation -------------------------------------------------------------------------------

func _process(delta: float) -> void:
	_clock += delta
	_update_events()
	_start_due_collapses()
	_sample_accumulator += delta
	var step := 1.0 / SAMPLE_RATE
	var guard := 0
	while _sample_accumulator >= step and guard < 6:
		_sample_accumulator -= step
		guard += 1
		_sample_sensors()
	_shake_the_camera()

func _update_events() -> void:
	for event in events:
		if str(event.status) == "active" and _clock - float(event.started_at) > float(event.duration_s):
			event.status = "ended"

func _start_due_collapses() -> void:
	var running := 0
	for collapse in _collapses:
		if is_instance_valid(collapse) and not bool((collapse as Node3D).get("finished")):
			running += 1
	for item in _queue.duplicate():
		if running >= MAX_ACTIVE_COLLAPSES:
			break
		if _clock < float(item.at):
			continue
		_queue.erase(item)
		var building := item.building as Node3D
		if not is_instance_valid(building) or building.has_meta("collapsed"):
			continue
		var collapse := BuildingCollapse.new()
		add_child(collapse)
		collapse.call("setup", building, int(hash(str(building.name))) )
		collapse.connect("landed", _on_landed)
		collapse.call("play")
		building.set_meta("collapsed", true)
		building.remove_meta("collapse_pending")
		_collapses.append(collapse)
		running += 1
		for event in events:
			if event.id == item.event:
				event.collapsed = int(event.collapsed) + 1
				(event.collapsed_buildings as Array).append(str(building.name))
		building_collapsed.emit(str(building.name), Zones.zone_of_position(building.global_position))

func _on_landed(position: Vector3, strength: float) -> void:
	_impacts.append({"position": position, "at": _clock, "amplitude": 0.25 + strength * 0.9})

func _sensors() -> Array:
	return get_tree().get_nodes_in_group("seismic_sensors")

func _traffic_near(position: Vector3) -> int:
	var count := 0
	for value in get_tree().get_nodes_in_group("vehicles"):
		var vehicle := value as Node3D
		if vehicle != null and vehicle.visible and absf(vehicle.global_position.x - position.x) < 45.0 and absf(vehicle.global_position.z - position.z) < 45.0:
			count += 1
	return count

func _envelope(event: Dictionary) -> float:
	var age := _clock - float(event.started_at)
	var duration := float(event.duration_s)
	if age < 0.0 or age > duration + 4.0:
		return 0.0
	var rise := smoothstep(0.0, 1.2, age)
	var sustain := 1.0 if age < duration * 0.55 else exp(-(age - duration * 0.55) / (duration * 0.2))
	return rise * sustain

## Distance in metres from a point to the area the earthquake is shaking: 0 anywhere inside the affected zone (or anywhere in the
## city for a whole-city quake), otherwise the straight-line distance to the nearest edge of that zone.
func distance_to_affected(event: Dictionary, position: Vector3) -> float:
	if str(event.zone) == CITY:
		return 0.0
	var area := Zones.rect(str(event.zone))
	var dx := maxf(maxf(area.position.x - position.x, position.x - area.end.x), 0.0)
	var dz := maxf(maxf(area.position.y - position.z, position.z - area.end.y), 0.0)
	return Vector2(dx, dz).length()

## Peak ground acceleration (m/s2) at a point: the source strength of the quake inside the shaken area, falling off with the
## distance from that area (not from its middle), and reaching farther for larger magnitudes.
func peak_acceleration(event: Dictionary, position: Vector3) -> float:
	var magnitude := float(event.magnitude)
	var source := 0.5 * pow(10.0, 0.38 * (magnitude - 5.0))
	if str(event.zone) == CITY:
		return source * 0.9
	var reach := 90.0 * pow(10.0, 0.15 * (magnitude - 6.0))
	return source / (1.0 + pow(distance_to_affected(event, position) / reach, 1.6))

func _sample_sensors() -> void:
	var now := _clock
	if now >= _tremor_at:
		_tremor_at = now + 0.5
		for sensor in _sensors():
			_tremor[(sensor as Node).get_instance_id()] = NOISE_FLOOR + 0.00012 * float(mini(_traffic_near((sensor as Node3D).global_position), 8))
	for value in _sensors():
		var sensor := value as Node3D
		var key := sensor.get_instance_id()
		var buffer := _buffers.get(key, {"z": [], "e": [], "n": []}) as Dictionary
		var floor_noise := float(_tremor.get(key, NOISE_FLOOR))
		var vertical := _rng.randfn(0.0, floor_noise)
		var east := _rng.randfn(0.0, floor_noise)
		var north := _rng.randfn(0.0, floor_noise)
		var strongest := ""
		var strongest_amplitude := 0.0
		var strongest_distance := -1.0
		for event in events:
			var envelope := _envelope(event)
			if envelope <= 0.0:
				continue
			var amplitude := peak_acceleration(event, sensor.global_position) * envelope
			if amplitude > strongest_amplitude:
				strongest_amplitude = amplitude
				strongest = str(event.id)
				strongest_distance = distance_to_affected(event, sensor.global_position)
			var phase := float(hash("%s:%d" % [event.id, key]) % 628) / 100.0
			var wave := sin(TAU * 1.7 * now + phase) * 0.55 + sin(TAU * 3.4 * now + phase * 1.7) * 0.3 + sin(TAU * 6.1 * now + phase * 2.3) * 0.15
			var wave_cross := sin(TAU * 1.9 * now + phase + 1.1) * 0.6 + sin(TAU * 4.2 * now + phase * 0.6) * 0.4
			vertical += amplitude * 0.6 * wave
			east += amplitude * wave_cross
			north += amplitude * 0.9 * (wave * 0.5 + wave_cross * 0.5)
		for impact in _impacts:
			var age := now - float(impact.at)
			if age < 0.0 or age > 6.0:
				continue
			var distance := sensor.global_position.distance_to(impact.position as Vector3)
			var hit := float(impact.amplitude) * exp(-age / 1.3) * exp(-distance / 70.0) * sin(TAU * 7.0 * age)
			vertical += hit
			east += hit * 0.7
			north += hit * 0.5
			if absf(hit) > strongest_amplitude:
				strongest_amplitude = absf(hit)
		_push(buffer.z as Array, vertical)
		_push(buffer.e as Array, east)
		_push(buffer.n as Array, north)
		_buffers[key] = buffer
		_publish(sensor, buffer, strongest, strongest_distance)
	_impacts = _impacts.filter(func(impact: Dictionary) -> bool: return now - float(impact.at) < 7.0)

func _push(samples: Array, value: float) -> void:
	samples.append(snappedf(value, 0.0001))
	while samples.size() > WAVE_SAMPLES:
		samples.pop_front()

func _publish(sensor: Node3D, buffer: Dictionary, event_id: String, distance: float) -> void:
	var recent := int(SAMPLE_RATE)
	var peak := 0.0
	for axis in ["z", "e", "n"]:
		var samples := buffer[axis] as Array
		for index in range(maxi(0, samples.size() - recent), samples.size()):
			peak = maxf(peak, absf(float(samples[index])))
	var velocity := peak / (TAU * 2.0) * 1000.0
	var intensity := clampf(3.66 * log(maxf(peak * 100.0, 0.1)) / log(10.0) - 1.66, 1.0, 10.0)
	sensor.set_meta("seis_pga", peak)
	sensor.set_meta("seis_pgv", velocity)
	sensor.set_meta("seis_mmi", intensity)
	sensor.set_meta("seis_event", event_id)
	sensor.set_meta("seis_distance", distance)
	sensor.set_meta("seis_waveform", {
		"samplingRateHz": SAMPLE_RATE, "units": "m/s^2",
		"components": {"vertical_z": (buffer.z as Array).duplicate(), "east_west_e": (buffer.e as Array).duplicate(), "north_south_n": (buffer.n as Array).duplicate()},
	})

# --- camera -----------------------------------------------------------------------------------

func _shake_the_camera() -> void:
	var camera := get_viewport().get_camera_3d()
	if camera == null:
		return
	var level := 0.0
	for event in events:
		var envelope := _envelope(event)
		if envelope > 0.0:
			level = maxf(level, peak_acceleration(event, camera.global_position) * envelope)
	for impact in _impacts:
		var age := _clock - float(impact.at)
		if age >= 0.0 and age < 3.0:
			level = maxf(level, float(impact.amplitude) * 0.35 * exp(-age / 1.0) * exp(-camera.global_position.distance_to(impact.position as Vector3) / 120.0))
	if level <= 0.0005 and _shake_camera == null:
		return
	if level <= 0.0005:
		_reset_camera()
		return
	_shake_camera = camera
	var amount := clampf(level * 0.12, 0.0, 1.0)
	var sway := sin(_clock * 6.3) * 0.55 + sin(_clock * 11.7) * 0.25
	camera.h_offset = (sway + _rng.randf_range(-1.0, 1.0)) * amount
	camera.v_offset = (cos(_clock * 7.9) * 0.45 + _rng.randf_range(-1.0, 1.0)) * amount * 0.9

func _reset_camera() -> void:
	if is_instance_valid(_shake_camera):
		_shake_camera.h_offset = 0.0
		_shake_camera.v_offset = 0.0
	_shake_camera = null
