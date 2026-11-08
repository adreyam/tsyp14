extends Node

signal vehicle_visual_departed(node: Node3D, vehicle_id: String)
## Emitted after every applied SUMO packet so incident/fleet presenters read the
## same authoritative state CityLife rendered this frame.
signal sumo_state_applied(state: Dictionary)
## A SUMO vehicle left the network into Godot custody (towed wreck, unit
## entering its depot). The node stays reserved until release_keyed_vehicle().
signal vehicle_handed_off(key: String, node: Node3D, handoff: Dictionary)

## SUMO is authoritative for every road vehicle and traffic light when the bridge
## runs. Every simulated vehicle is rendered: the packet's x/z is the body centre
## and len/wid/cls select a model normalized to exactly that geometry (see
## sumo/vehicle_classes.json). A local motion fallback keeps the scene useful when
## it is opened directly in the editor. Pedestrian animation remains native.

const VehicleLightRig := preload("res://scripts/incidents/vehicle_light_rig.gd")
const ProceduralVehicle := preload("res://scripts/vehicles/procedural_vehicle.gd")
const VEHICLE_CLASSES_PATH := "res://sumo/vehicle_classes.json"
const CIVILIAN_CLASSES := ["car", "taxi", "bus"]
const EMERGENCY_CLASSES := ["police", "ambulance", "fire", "tow", "utility", "garbage"]
## Live vehicles render on their own visual layer so the replay camera can show
## recorded ghosts without the live fleet (layer index 2 → bit value 2).
const VEHICLE_RENDER_LAYER := 2
## Single-lane SUMO overtaking moves a car into the opposite lane in one step.
## Lateral jumps in this band are blended as a lane change instead of rejected.
# Signal poles stand on the sidewalk corners, outside the 15 m junction box
# (lanes reach 5.75 m from the centre; 5.4 m poles stood in the lane).
const SIGNAL_CORNER_OFFSET := 8.4
const LANE_CHANGE_MIN_LATERAL := 1.1
const LANE_CHANGE_MAX_LATERAL := 6.8
const LANE_CHANGE_BLEND_RATE := 2.8
const LANE_CHANGE_MAX_YAW := deg_to_rad(22.0)

@export var traffic_speed := 13.0
@export var pedestrian_speed := 0.9
@export var signal_cycle_seconds := 9.0
@export var sumo_udp_port := 8814
@export var road_surface_y := 0.17
@export var sidewalk_surface_y := 0.17
@export var traffic_smoothing_hz := 18.0
## Vehicles are rendered this far behind the newest SUMO packet, between the
## two packets that bracket that moment (about 1.6 packets at 15 Hz), so
## motion is continuous instead of pulsing on every packet.
@export var interpolation_delay := 0.11


var _elapsed := 0.0
## Every pooled vehicle model: {node, cls, height, wheels, centre_offset, phase, half_extent}.
var _vehicles: Array[Dictionary] = []
var _pedestrians: Array[Dictionary] = []
var _sumo_udp := PacketPeerUDP.new()
## Render states keyed by unit id (emergency units) or SUMO id (everything else).
var _sumo_nodes_by_id: Dictionary = {}
var _retiring_vehicles: Array[Dictionary] = []
var _signals_by_name: Dictionary = {}
var _signal_lamps: Dictionary = {}
var _signal_states: Dictionary = {}
var _last_sumo_packet_msec := 0
var _pending_sumo_packet := PackedByteArray()
var _sumo_parse_task := -1
var _sumo_parsed_state: Variant = null
var _sumo_simulated_count := 0
var _sumo_stopped_count := 0
var _sumo_turning_count := 0
var _sumo_signal_approaches: Dictionary = {}
var _sumo_operator: Dictionary = {}
var _class_specs: Dictionary = {}
var _pool_parent: Node3D
var _grown_vehicle_count := 0
var _last_state: Dictionary = {}
var sumo_connected := false
# SUMO simulation time minus local time, tracked from the earliest-arriving
# packets (late packets never push the render clock back).
var _sim_clock_offset := 0.0
# Simulation time being rendered. It advances by the frame delta (exactly
# the display cadence) and is steered gently toward the packet clock, so a new
# estimate never jumps every car forward in one frame.
var _render_time := 0.0
var _sim_clock_valid := false
var _packet_sim_time := 0.0
var _packet_received_local_seconds := 0.0
## Simulated seconds that really pass per wall-clock second, measured from the
## packets themselves (0 while SUMO is paused). Under load SUMO may deliver less
## than the speed that was asked for; the clocks follow what is delivered.
var sim_rate := 1.0
var sim_paused := false
var _rate_samples: Array = []
var _packet_interval_sim := 1.0 / 15.0
var _effective_interpolation_delay := 0.11


func _ready() -> void:
	var runtime_port := OS.get_environment("CITY_SUMO_UDP_PORT")
	if runtime_port.is_valid_int():
		sumo_udp_port = int(runtime_port)
	# Vehicles must update before the follow camera in every render frame. Equal
	# priorities produced a one-frame sawtooth in third-person view when camera
	# traversal happened first and the car transformed afterwards.
	process_priority = -100
	_load_class_specs()
	var city := get_parent()
	_pool_parent = city.get_node_or_null("Vehicles") as Node3D
	for node in city.get_tree().get_nodes_in_group("vehicles") + city.get_tree().get_nodes_in_group("backdrop_vehicles"):
		if node is Node3D:
			var car := node as Node3D
			var visual_class := _class_for_scene(car.scene_file_path)
			var core_route := node.is_in_group("vehicles")
			var half_extent: float = 190.0 if core_route else float([380.0, 285.0, 190.0][int(node.get_meta("vehicle_index", node.get_instance_id())) % 3])
			# Every authored slot drives the one procedural fleet: same
			# modelling, materials and integrated lamps for every class.
			ProceduralVehicle.install(car, visual_class, _class_spec(visual_class), _vehicles.size())
			_register_pool_vehicle(car, visual_class, half_extent)
	for node in city.get_tree().get_nodes_in_group("pedestrians") + city.get_tree().get_nodes_in_group("backdrop_pedestrians"):
		if node is Node3D:
			var person := node as Node3D
			_ground_pedestrian_to_sidewalk(person)
			var walk_player := _play_authored_walk(person, _pedestrians.size())
			_pedestrians.append({"node": person, "origin": person.global_position, "phase": float(_pedestrians.size()) * 0.61, "walk_player": walk_player})
	_ensure_full_city_traffic_signals()
	for signal_node in get_tree().get_nodes_in_group("traffic_signals"):
		var traffic_signal := signal_node as Node3D
		var signal_name := _sumo_signal_id_for(traffic_signal)
		_place_signal_on_corner(traffic_signal, signal_name)
		_signals_by_name[signal_name] = traffic_signal
		_keep_authored_traffic_light(signal_name, traffic_signal)
	# A complete authoritative state is a relatively large JSON datagram. A local
	# 1 MiB socket queue prevents drops during a temporary render hitch without
	# adding latency to the visual interpolation path.
	var bind_error := _sumo_udp.bind(sumo_udp_port, "127.0.0.1", 1 << 20)
	if bind_error != OK:
		push_warning("SUMO UDP port %d could not be opened (%s); using local fallback" % [sumo_udp_port, error_string(bind_error)])
	_update_status_label()
	_update_signals()
	# SmartCityAssets attaches exhaust emitters in a deferred pass queued before
	# this one; assign layers afterwards so those particles move with their car.
	call_deferred("_assign_all_vehicle_layers")


func _process(delta: float) -> void:
	_elapsed += delta
	ProceduralVehicle.advance_lamp_clock(delta)
	_poll_sumo()
	if sumo_connected:
		_update_sumo_traffic(delta)
	else:
		_update_traffic_fallback()
	_update_retiring_vehicles(delta)
	_update_pedestrians()
	if not sumo_connected:
		_update_signals()


# --- Vehicle classes and pool -------------------------------------------------

func _load_class_specs() -> void:
	var file := FileAccess.open(VEHICLE_CLASSES_PATH, FileAccess.READ)
	if file == null:
		push_error("Vehicle class table is missing: %s" % VEHICLE_CLASSES_PATH)
		return
	var parsed: Variant = JSON.parse_string(file.get_as_text())
	if parsed is Dictionary:
		_class_specs = (parsed as Dictionary).get("classes", {}) as Dictionary


func _class_spec(visual_class: String) -> Dictionary:
	return _class_specs.get(visual_class, _class_specs.get("car", {"length": 4.7, "width": 2.1, "fit": "uniform"})) as Dictionary


func _class_for_scene(scene_path: String) -> String:
	var lowered := scene_path.to_lower()
	if "police" in lowered:
		return "police"
	if "ambulance" in lowered:
		return "ambulance"
	if "firetruck" in lowered:
		return "fire"
	if "truck-flat" in lowered:
		return "tow"
	if lowered.ends_with("/bus.glb"):
		return "bus"
	if "taxi" in lowered:
		return "taxi"
	return "car"


func _register_pool_vehicle(car: Node3D, visual_class: String, half_extent: float) -> Dictionary:
	var centre_offset := _fit_vehicle_to_class(car, visual_class)
	# Imported assets do not share a common origin: some roots are at tyre level
	# while others sit above the body. Ground every model from its real lowest
	# vertex instead of retaining the authored scene Y coordinate.
	var ground_height := _ground_vehicle_to_road(car)
	car.set_meta("vehicle_class", visual_class)
	var entry := {
		"node": car,
		"cls": visual_class,
		"height": ground_height,
		"half_extent": half_extent,
		"phase": float(_vehicles.size()) * 31.0,
		"wheels": _prepare_vehicle_wheels(car),
		"centre_offset": centre_offset,
	}
	_vehicles.append(entry)
	return entry


func _fit_vehicle_to_class(vehicle: Node3D, visual_class: String) -> Vector3:
	## Normalizes the model to the class's SUMO length (and width for stretched
	## service vehicles) and returns the horizontal offset of its body centre from
	## the node origin, in the node's yaw frame and final scale.
	var spec := _class_spec(visual_class)
	vehicle.scale = Vector3.ONE
	var bounds := _vehicle_local_bounds(vehicle)
	if bounds.size.z < 0.001 or bounds.size.x < 0.001:
		return Vector3.ZERO
	var length := float(spec.get("length", 4.7))
	var width := float(spec.get("width", 2.1))
	var scale_z := length / bounds.size.z
	var scale_x := scale_z
	if str(spec.get("fit", "uniform")) == "stretch":
		scale_x = width / bounds.size.x
	vehicle.scale = Vector3(scale_x, scale_x, scale_z)
	return Vector3(bounds.get_center().x * scale_x, 0.0, bounds.get_center().z * scale_z)


func _instantiate_pool_vehicle(visual_class: String) -> Dictionary:
	if _pool_parent == null:
		return {}
	var car := Node3D.new()
	ProceduralVehicle.install(car, visual_class, _class_spec(visual_class), _vehicles.size())
	_grown_vehicle_count += 1
	car.name = "PooledVehicle_%s_%03d" % [visual_class, _grown_vehicle_count]
	car.visible = false
	car.set_meta("vehicle_index", 1000 + _grown_vehicle_count)
	car.set_meta("model_family", "pooled_%s" % visual_class)
	_pool_parent.add_child(car)
	car.add_to_group("vehicles")
	if visual_class in EMERGENCY_CLASSES:
		car.add_to_group("emergency_vehicles")
		car.add_to_group("%s_vehicles" % visual_class)
	var entry := _register_pool_vehicle(car, visual_class, 190.0)
	_assign_vehicle_layer(car)
	return entry


func _acquire_pool_entry(visual_class: String) -> Dictionary:
	var used := {}
	for render_value in _sumo_nodes_by_id.values():
		used[((render_value as Dictionary).node as Node3D).get_instance_id()] = true
	for retiring_state in _retiring_vehicles:
		used[(retiring_state.node as Node3D).get_instance_id()] = true
	for vehicle_state in _vehicles:
		var node := vehicle_state.node as Node3D
		if str(vehicle_state.cls) == visual_class and not used.has(node.get_instance_id()):
			return vehicle_state
	# The fleet is fully rendered, so the pool grows instead of leaving a
	# simulated vehicle invisible.
	return _instantiate_pool_vehicle(visual_class)


func _assign_all_vehicle_layers() -> void:
	for vehicle_state in _vehicles:
		_assign_vehicle_layer(vehicle_state.node as Node3D)


func _assign_vehicle_layer(vehicle: Node3D) -> void:
	for value in vehicle.find_children("*", "VisualInstance3D", true, false):
		(value as VisualInstance3D).layers = VEHICLE_RENDER_LAYER


# --- SUMO stream --------------------------------------------------------------

func _update_traffic_fallback() -> void:
	for state in _vehicles:
		var car := state.node as Node3D
		if not str(state.cls) in CIVILIAN_CLASSES or _is_node_reserved(car):
			continue
		car.visible = true
		var path := _rectangle_path(_elapsed * traffic_speed + state.phase, state.half_extent)
		car.global_position = Vector3(path.position.x, state.height, path.position.z)
		car.rotation.y = atan2(path.direction.x, path.direction.z)


func _is_node_reserved(node: Node3D) -> bool:
	for render_value in _sumo_nodes_by_id.values():
		if (render_value as Dictionary).node == node:
			return true
	return false


func _poll_sumo() -> void:
	# Keep only the newest UDP datagram: draining first and decoding once gives
	# traffic the freshest authoritative state with no growing backlog. The
	# ~35 KB JSON decode runs on a worker thread so a packet frame costs the
	# main thread no more than any other frame; its result is applied on the
	# next frame (a constant one-frame delay the interpolation absorbs).
	while _sumo_udp.get_available_packet_count() > 0:
		_pending_sumo_packet = _sumo_udp.get_packet()
	var latest_state: Variant = null
	if _sumo_parse_task != -1 and WorkerThreadPool.is_task_completed(_sumo_parse_task):
		WorkerThreadPool.wait_for_task_completion(_sumo_parse_task)
		_sumo_parse_task = -1
		var parsed: Variant = _sumo_parsed_state
		_sumo_parsed_state = null
		if parsed is Dictionary and parsed.get("source", "") == "SUMO 1.25.0 TraCI":
			latest_state = parsed
	if _sumo_parse_task == -1 and not _pending_sumo_packet.is_empty():
		var packet := _pending_sumo_packet
		_pending_sumo_packet = PackedByteArray()
		_sumo_parse_task = WorkerThreadPool.add_task(_parse_sumo_packet.bind(packet), false, "SUMO packet decode")
	if latest_state is Dictionary:
		_apply_sumo_state(latest_state)
	elif sumo_connected and Time.get_ticks_msec() - _last_sumo_packet_msec > 2500:
		_disconnect_sumo()


func _measure_sim_rate(local_seconds: float, sim_time: float, paused: bool) -> void:
	## Rate over a sliding window of about two seconds, so bursts of packets and
	## single slow ticks do not register as a change of speed.
	sim_paused = paused
	if paused:
		sim_rate = 0.0
		_rate_samples.clear()
		return
	_rate_samples.append([local_seconds, sim_time])
	while _rate_samples.size() > 2 and local_seconds - float((_rate_samples[1] as Array)[0]) > 2.0:
		_rate_samples.pop_front()
	var oldest := _rate_samples[0] as Array
	var span := local_seconds - float(oldest[0])
	if span >= 0.8:
		sim_rate = maxf(0.0, (sim_time - float(oldest[1])) / span)


func _parse_sumo_packet(packet: PackedByteArray) -> void:
	_sumo_parsed_state = JSON.parse_string(packet.get_string_from_utf8())


func _exit_tree() -> void:
	if _sumo_parse_task != -1:
		WorkerThreadPool.wait_for_task_completion(_sumo_parse_task)
		_sumo_parse_task = -1


func _render_key(vehicle_data: Dictionary) -> String:
	var unit := str(vehicle_data.get("unit", ""))
	return unit if not unit.is_empty() else str(vehicle_data.get("id", ""))


func _apply_sumo_state(state: Dictionary) -> void:
	var packet_msec := Time.get_ticks_msec()
	var local_seconds := float(Time.get_ticks_usec()) / 1000000.0
	var next_packet_sim_time := float(state.get("time", local_seconds))
	var packet_sim_span := next_packet_sim_time - _packet_sim_time
	_packet_sim_time = next_packet_sim_time
	_packet_received_local_seconds = local_seconds
	if not _sim_clock_valid:
		_effective_interpolation_delay = interpolation_delay
		_render_time = _packet_sim_time - _effective_interpolation_delay
		_sim_clock_valid = true
	elif packet_sim_span > 0.001 and packet_sim_span < 2.0:
		# At accelerated speeds one published packet spans several fully executed
		# SUMO steps. Buffer slightly more than that simulation-time span so the
		# renderer always has two authoritative samples to interpolate between.
		_packet_interval_sim = lerpf(_packet_interval_sim, packet_sim_span, 0.35)
		_effective_interpolation_delay = clampf(maxf(interpolation_delay, _packet_interval_sim * 1.12), interpolation_delay, 0.75)
	_measure_sim_rate(local_seconds, next_packet_sim_time, bool((state.get("operator", {}) as Dictionary).get("paused", false)))
	_last_sumo_packet_msec = packet_msec
	_last_state = state
	_sumo_simulated_count = int(state.get("simulated_vehicle_count", 0))
	_sumo_stopped_count = int(state.get("stopped_vehicle_count", 0))
	_sumo_turning_count = int(state.get("turning_vehicle_count", 0))
	_sumo_operator = state.get("operator", {}) as Dictionary
	var first_packet := not sumo_connected
	sumo_connected = true
	var handoffs: Dictionary = {}
	for handoff_value in state.get("handoffs", []):
		if handoff_value is Dictionary:
			var handoff := handoff_value as Dictionary
			handoffs[str(handoff.get("id", ""))] = handoff
			if not str(handoff.get("unit", "")).is_empty():
				handoffs[str(handoff.get("unit", ""))] = handoff
	var current: Dictionary = {}
	for vehicle_data_value in state.get("vehicles", []):
		if not vehicle_data_value is Dictionary:
			continue
		var vehicle_data := vehicle_data_value as Dictionary
		var key := _render_key(vehicle_data)
		if not key.is_empty():
			current[key] = vehicle_data
	# Release departed slots before assigning new SUMO agents, so a slot is never
	# blank for a packet and nothing visibly pops.
	for existing_key in _sumo_nodes_by_id.keys():
		if current.has(existing_key):
			continue
		var old_state: Dictionary = _sumo_nodes_by_id[existing_key]
		if bool(old_state.get("claimed", false)):
			continue
		var handoff := handoffs.get(existing_key, handoffs.get(str(old_state.get("sumo_id", "")), {})) as Dictionary
		if not handoff.is_empty():
			# The vehicle is leaving SUMO into Godot custody: keep the node exactly
			# where it is, reserved, and let the presenter take over.
			old_state.claimed = true
			old_state.has_packet = false
			_sumo_nodes_by_id[existing_key] = old_state
			vehicle_handed_off.emit(str(existing_key), old_state.node as Node3D, handoff)
			continue
		_begin_vehicle_retirement(str(existing_key), old_state)
		_sumo_nodes_by_id.erase(existing_key)

	for key in current:
		var vehicle_data := current[key] as Dictionary
		var vehicle_id := str(vehicle_data.get("id", ""))
		var visual_class := str(vehicle_data.get("cls", "car"))
		if not _sumo_nodes_by_id.has(key):
			# Refuse an unknown civilian whose first position is in the interior:
			# civilians only ever enter at a perimeter gate. Emergency units enter
			# from depots and are pre-claimed by their presenter.
			if not first_packet and not bool(vehicle_data.get("entry_boundary", true)) and str(vehicle_data.get("unit", "")).is_empty():
				continue
			var free_state := _acquire_pool_entry(visual_class)
			if free_state.is_empty():
				continue
			_sumo_nodes_by_id[key] = _new_render_state(free_state, packet_msec)
		var render_state: Dictionary = _sumo_nodes_by_id[key]
		_update_render_state(render_state, vehicle_data, packet_msec, first_packet)
		_sumo_nodes_by_id[key] = render_state

	if first_packet:
		_hide_unassigned_pool_vehicles()
	_sumo_signal_approaches.clear()
	for signal_data in state.get("signals", []):
		if signal_data is Dictionary:
			var signal_id := str(signal_data.get("id", ""))
			if not signal_id.is_empty():
				_sumo_signal_approaches[signal_id] = (signal_data.get("approaches", {}) as Dictionary).duplicate(true)
			_apply_sumo_signal(signal_data)
	_update_status_label()
	sumo_state_applied.emit(state)


func _hide_unassigned_pool_vehicles() -> void:
	# In SUMO mode a model is visible only while it represents a simulated
	# vehicle or a claimed unit; spare pool models never sit in the street.
	for vehicle_state in _vehicles:
		var node := vehicle_state.node as Node3D
		if not _is_node_reserved(node) and not _retiring_vehicles.any(func(retiring: Dictionary) -> bool: return retiring.node == node):
			node.visible = false


func _new_render_state(pool_entry: Dictionary, packet_msec: int) -> Dictionary:
	var node := pool_entry.node as Node3D
	return {
		"node": node,
		"cls": str(pool_entry.cls),
		"height": float(pool_entry.height),
		"centre_offset": pool_entry.centre_offset,
		"wheels": pool_entry.wheels,
		"position": node.global_position,
		"angle": node.rotation.y,
		"sample_time": 0.0,
		# Recent packets [sim time, centre, yaw], oldest first.
		"history": [],
		"render_centre": node.global_position,
		"render_yaw": node.rotation.y,
		"speed": 0.0,
		"packet_msec": packet_msec,
		"velocity": Vector3.ZERO,
		"lateral_offset": Vector3.ZERO,
		"pose": Vector3.ZERO,
		"has_packet": false,
		"needs_initialization": true,
		"claimed": false,
		"sumo_id": "",
		"continuation_count": 0,
	}


static func _sample_pose(sample: Array) -> Vector3:
	return sample[3] as Vector3 if sample.size() > 3 else Vector3.ZERO


func _update_render_state(render_state: Dictionary, vehicle_data: Dictionary, packet_msec: int, first_packet: bool) -> void:
	var vehicle_id := str(vehicle_data.get("id", ""))
	var target_position := Vector3(float(vehicle_data.get("x", 0.0)), float(render_state.height), float(vehicle_data.get("z", 0.0)))
	# Every active external vehicle has its nose on local +Z. The city and SUMO
	# grids share X/Y -> X/Z orientation, so SUMO heading is the Godot yaw.
	var target_angle := deg_to_rad(float(vehicle_data.get("angle", 0.0)))
	var reported_speed := float(vehicle_data.get("speed", 0.0))
	# The incident slide offset travels in the same history samples as the
	# position: applied from the newest packet while the position plays back
	# ~0.1 s behind, an impact or settle made the car lurch and snap back.
	var pose_value: Variant = vehicle_data.get("pose", null)
	var packet_pose := Vector3.ZERO
	if pose_value is Array and (pose_value as Array).size() >= 3:
		var pose_array := pose_value as Array
		packet_pose = Vector3(float(pose_array[0]), float(pose_array[1]), float(pose_array[2]))
	if bool(render_state.has_packet):
		# Validation and velocity are based on simulation time, not wall time.
		# At 8x a normal car legitimately moves eight times farther between the
		# same two wall-clock broadcasts.
		var packet_seconds := maxf(_packet_sim_time - float(render_state.sample_time), 0.001)
		var packet_displacement := target_position - (render_state.position as Vector3)
		var forward := Vector3(sin(target_angle), 0.0, cos(target_angle))
		var longitudinal := forward * packet_displacement.dot(forward)
		var lateral := packet_displacement - longitudinal
		lateral.y = 0.0
		var maximum_valid_displacement := maxf(1.25, packet_seconds * 45.0)
		var is_lane_change := lateral.length() >= LANE_CHANGE_MIN_LATERAL and lateral.length() <= LANE_CHANGE_MAX_LATERAL and longitudinal.length() <= maximum_valid_displacement
		if is_lane_change:
			# Keep the car where it is laterally and let the offset decay: the
			# opposite-lane move SUMO made in one step is rendered as a steer.
			render_state.lateral_offset = (render_state.lateral_offset as Vector3) - lateral
		if is_lane_change or packet_displacement.length() <= maximum_valid_displacement:
			# After a lane change every buffered sample is moved into the new
			# lane: the decaying offset alone carries the steer.
			var history := render_state.history as Array
			if is_lane_change:
				for sample in history:
					sample[1] = (sample[1] as Vector3) + lateral
			history.append([_packet_sim_time, target_position, target_angle, packet_pose])
			while history.size() > 12:
				history.pop_front()
			render_state.sample_time = _packet_sim_time
			var measured_velocity := longitudinal / packet_seconds
			render_state.velocity = (render_state.velocity as Vector3).lerp(measured_velocity, 0.72)
			render_state.position = target_position
			render_state.angle = target_angle
			render_state.speed = reported_speed
			render_state.packet_msec = packet_msec
		# A larger update is an identity/transport glitch and must never be
		# rendered as a visual teleport or a backwards snap.
	else:
		render_state.position = target_position
		render_state.history = [[_packet_sim_time, target_position, target_angle, packet_pose]]
		render_state.sample_time = _packet_sim_time
		render_state.angle = target_angle
		render_state.speed = reported_speed
		render_state.packet_msec = packet_msec
		render_state.velocity = Vector3.ZERO
		render_state.has_packet = true
	render_state.pose = packet_pose
	render_state.sumo_id = vehicle_id
	var vehicle_node := render_state.node as Node3D
	render_state.continuation_count = int(vehicle_data.get("continuation_count", render_state.get("continuation_count", 0)))
	if (bool(render_state.needs_initialization) or first_packet) and not bool(render_state.claimed):
		render_state.render_centre = target_position
		render_state.render_yaw = target_angle
		render_state.lateral_offset = Vector3.ZERO
		_place_vehicle(render_state)
		render_state.needs_initialization = false
	vehicle_node.visible = true
	vehicle_node.set_meta("sumo_vehicle_id", vehicle_id)
	vehicle_node.set_meta("sumo_trip_status", "continuing" if int(render_state.continuation_count) > 0 else "active")
	vehicle_node.set_meta("sumo_continuation_count", int(render_state.continuation_count))
	vehicle_node.set_meta("sumo_turning", bool(vehicle_data.get("turning", false)))
	vehicle_node.set_meta("sumo_stop_reason", str(vehicle_data.get("stop_reason", "moving")))
	vehicle_node.set_meta("sumo_road", str(vehicle_data.get("road", "")))
	vehicle_node.set_meta("sumo_speed", reported_speed)
	var lights := int(vehicle_data.get("lights", 0))
	var signals := int(vehicle_data.get("sig", 0))
	vehicle_node.set_meta("vehicle_lights", lights)
	vehicle_node.set_meta("vehicle_signals", signals)
	# Brake lamps and indicators follow SUMO's own signals; warning lights
	# also need the rig for the light they cast on the street.
	ProceduralVehicle.set_lamps(vehicle_node, lights, signals)
	if lights != 0:
		ensure_light_rig(vehicle_node)
	vehicle_node.set_meta("vehicle_role", str(vehicle_data.get("role", "")))
	vehicle_node.set_meta("vehicle_unit", str(vehicle_data.get("unit", "")))


func _place_vehicle(render_state: Dictionary) -> void:
	## Puts the model so its body centre is at render_centre with yaw render_yaw.
	var vehicle_node := render_state.node as Node3D
	var yaw := float(render_state.render_yaw)
	var centre := render_state.render_centre as Vector3
	vehicle_node.rotation = Vector3(0.0, yaw, 0.0)
	var offset := Basis(Vector3.UP, yaw) * (render_state.centre_offset as Vector3)
	vehicle_node.global_position = Vector3(centre.x - offset.x, float(render_state.height), centre.z - offset.z)


func _find_free_vehicle_state() -> Dictionary:
	return _acquire_pool_entry("car")


func _begin_vehicle_retirement(vehicle_id: String, render_state: Dictionary) -> void:
	var vehicle_node := render_state.node as Node3D
	if not is_instance_valid(vehicle_node):
		return
	vehicle_node.set_meta("sumo_vehicle_id", vehicle_id)
	vehicle_node.set_meta("sumo_trip_status", "leaving_city")
	_retiring_vehicles.append({
		"node": vehicle_node,
		"vehicle_id": vehicle_id,
		"elapsed": 0.0,
		"angle": vehicle_node.rotation.y,
		"speed": maxf(float(render_state.get("speed", 0.0)), 6.0),
		"wheels": render_state.get("wheels", []),
	})


func _update_retiring_vehicles(delta: float) -> void:
	for index in range(_retiring_vehicles.size() - 1, -1, -1):
		var state := _retiring_vehicles[index]
		var vehicle_node := state.node as Node3D
		if not is_instance_valid(vehicle_node):
			_retiring_vehicles.remove_at(index)
			continue
		state.elapsed = float(state.elapsed) + delta
		var forward := Vector3(sin(float(state.angle)), 0.0, cos(float(state.angle)))
		vehicle_node.global_position += forward * float(state.speed) * delta
		for wheel_value in state.wheels:
			if is_instance_valid(wheel_value):
				(wheel_value as Node3D).rotate_object_local(Vector3.RIGHT, float(state.speed) * delta * 0.72)
		if float(state.elapsed) >= 2.2:
			vehicle_visual_departed.emit(vehicle_node, str(state.vehicle_id))
			vehicle_node.visible = false
			vehicle_node.set_meta("sumo_trip_status", "departed")
			_retiring_vehicles.remove_at(index)
		else:
			_retiring_vehicles[index] = state


func _update_sumo_traffic(delta: float) -> void:
	# Dead-reckon continuously between SUMO packets, then converge to the latest
	# validated authoritative position with a render-only critically damped
	# correction. Claimed vehicles are posed by their presenter instead.
	# Correction is a visual blend and therefore uses wall-frame time. Using the
	# accelerated physics delta made a legitimate multi-metre 8x target collapse
	# into one rendered frame even though the simulation samples were valid.
	var wall_delta := delta / maxf(Engine.time_scale, 0.001)
	var smoothing := 1.0 - exp(-traffic_smoothing_hz * wall_delta)
	# Anchor the simulation clock to the newest packet and advance it at the
	# same rate as Engine.time_scale. Using wall time plus a changing offset made
	# every accelerated packet look like a clock discontinuity.
	var local_now := float(Time.get_ticks_usec()) / 1000000.0
	var since_packet := maxf(0.0, local_now - _packet_received_local_seconds)
	var clock_target := _packet_sim_time + since_packet * Engine.time_scale - _effective_interpolation_delay
	_render_time += delta
	var clock_error := clock_target - _render_time
	# Correct drift continuously; never snap the shared render clock.
	_render_time += clampf(clock_error, -delta * 1.6, delta * 1.6)
	var render_time := _render_time
	var lane_decay := exp(-LANE_CHANGE_BLEND_RATE * delta)
	for render_value in _sumo_nodes_by_id.values():
		var render_state := render_value as Dictionary
		if bool(render_state.claimed) or not bool(render_state.has_packet):
			continue
		var target_centre: Vector3 = render_state.position
		var target_yaw := float(render_state.angle)
		var target_pose := render_state.pose as Vector3
		var speed := float(render_state.speed)
		var history := render_state.history as Array
		var bracketed := false
		for index in range(history.size() - 1, 0, -1):
			var older := history[index - 1] as Array
			var newer := history[index] as Array
			if render_time >= float(older[0]) and render_time <= float(newer[0]) and float(newer[0]) > float(older[0]):
				# Between the two packets around the render moment: exact,
				# continuous motion.
				var alpha := clampf((render_time - float(older[0])) / (float(newer[0]) - float(older[0])), 0.0, 1.0)
				target_centre = (older[1] as Vector3).lerp(newer[1] as Vector3, alpha)
				target_yaw = lerp_angle(float(older[2]), float(newer[2]), alpha)
				target_pose = _sample_pose(older).lerp(_sample_pose(newer), alpha)
				bracketed = true
				break
		if not bracketed and not history.is_empty() and render_time < float((history[0] as Array)[0]):
			target_centre = (history[0] as Array)[1]
			target_yaw = float((history[0] as Array)[2])
			target_pose = _sample_pose(history[0] as Array)
		elif not bracketed and history.size() >= 2:
			# A late packet: continue briefly along the last measured motion.
			var older := history[history.size() - 2] as Array
			var newer := history[history.size() - 1] as Array
			var span := maxf(float(newer[0]) - float(older[0]), 0.01)
			var ahead := clampf(render_time - float(newer[0]), 0.0, maxf(0.12, _packet_interval_sim * 1.25))
			# A vehicle SUMO has stopped (impact, held wreck) is never carried
			# on past its last position.
			if float(render_state.speed) < 0.05:
				ahead = 0.0
			target_centre = (newer[1] as Vector3) + ((newer[1] as Vector3) - (older[1] as Vector3)) / span * ahead
			target_yaw = float(newer[2])
			target_pose = _sample_pose(newer)
		# Lane-change blend: the lateral offset decays toward the new lane and the
		# body yaws into the steering direction, bounded and only while moving.
		var lateral_offset := render_state.lateral_offset as Vector3
		var previous_offset := lateral_offset
		lateral_offset *= lane_decay
		if lateral_offset.length() < 0.01:
			lateral_offset = Vector3.ZERO
		render_state.lateral_offset = lateral_offset
		target_centre += lateral_offset
		if speed > 1.0 and delta > 0.0:
			var lateral_rate := (lateral_offset - previous_offset) / delta
			# Local +X is the car's left; steering toward it is a positive yaw.
			var left := Vector3(cos(target_yaw), 0.0, -sin(target_yaw))
			target_yaw += clampf(atan2(lateral_rate.dot(left), speed), -LANE_CHANGE_MAX_YAW, LANE_CHANGE_MAX_YAW)
		# Incident rest pose (post-impact slide) from the bridge, in world space.
		target_centre += Vector3(target_pose.x, 0.0, target_pose.y)
		target_yaw += target_pose.z
		# The interpolated target is already continuous; only a hand-over from
		# a presenter pose or a large gap (a clock re-sync) is eased.
		if Time.get_ticks_msec() < int(render_state.get("blend_until_msec", 0)) or (render_state.render_centre as Vector3).distance_to(target_centre) > 1.5:
			render_state.render_centre = (render_state.render_centre as Vector3).lerp(target_centre, smoothing)
			render_state.render_yaw = lerp_angle(float(render_state.render_yaw), target_yaw, smoothing)
		else:
			render_state.render_centre = target_centre
			render_state.render_yaw = target_yaw
		_place_vehicle(render_state)
		var wheel_rotation := speed * delta * 0.72
		for wheel_value in render_state.wheels:
			(wheel_value as Node3D).rotate_object_local(Vector3.RIGHT, wheel_rotation)


# --- Keyed custody API (emergency fleet presenter, incident scenes) ----------

func acquire_keyed_vehicle(key: String, visual_class: String) -> Node3D:
	## Reserves a model for a vehicle Godot poses before/after it exists in SUMO
	## (a garaged unit). It stays claimed until set_vehicle_claimed(key, false).
	if _sumo_nodes_by_id.has(key):
		return (_sumo_nodes_by_id[key] as Dictionary).node as Node3D
	var entry := _acquire_pool_entry(visual_class)
	if entry.is_empty():
		return null
	var render_state := _new_render_state(entry, Time.get_ticks_msec())
	render_state.claimed = true
	render_state.needs_initialization = false
	_sumo_nodes_by_id[key] = render_state
	(entry.node as Node3D).visible = true
	return entry.node as Node3D


func has_keyed_vehicle(key: String) -> bool:
	return _sumo_nodes_by_id.has(key)


func get_keyed_vehicle(key: String) -> Node3D:
	return (_sumo_nodes_by_id[key] as Dictionary).node as Node3D if _sumo_nodes_by_id.has(key) else null


func keyed_vehicle_has_packet(key: String) -> bool:
	return _sumo_nodes_by_id.has(key) and bool((_sumo_nodes_by_id[key] as Dictionary).has_packet)


func get_keyed_render_target(key: String) -> Dictionary:
	## The latest authoritative centre/yaw for a key (what CityLife would render).
	if not _sumo_nodes_by_id.has(key):
		return {}
	var render_state := _sumo_nodes_by_id[key] as Dictionary
	return {"centre": render_state.position, "yaw": float(render_state.angle), "speed": float(render_state.speed), "has_packet": bool(render_state.has_packet)}


func get_vehicle_dimensions(key: String) -> Vector2:
	if not _sumo_nodes_by_id.has(key):
		return Vector2.ZERO
	var spec := _class_spec(str((_sumo_nodes_by_id[key] as Dictionary).cls))
	return Vector2(float(spec.get("length", 4.7)), float(spec.get("width", 2.1)))


func place_keyed_vehicle(key: String, centre: Vector3, yaw: float) -> void:
	## Presenter-driven pose for a claimed vehicle; keeps the render state
	## coherent so releasing the claim blends from exactly this pose.
	if not _sumo_nodes_by_id.has(key):
		return
	var render_state := _sumo_nodes_by_id[key] as Dictionary
	render_state.render_centre = Vector3(centre.x, float(render_state.height), centre.z)
	render_state.render_yaw = yaw
	_place_vehicle(render_state)


func get_keyed_pose(key: String) -> Dictionary:
	if not _sumo_nodes_by_id.has(key):
		return {}
	var render_state := _sumo_nodes_by_id[key] as Dictionary
	return {"centre": render_state.render_centre, "yaw": float(render_state.render_yaw)}


func set_vehicle_claimed(key: String, claimed: bool) -> void:
	if not _sumo_nodes_by_id.has(key):
		return
	var render_state := _sumo_nodes_by_id[key] as Dictionary
	render_state.claimed = claimed
	if not claimed:
		render_state.lateral_offset = Vector3.ZERO
		render_state.needs_initialization = false
		# Blend from the presenter's last pose into the SUMO stream.
		render_state.blend_until_msec = Time.get_ticks_msec() + 700
	_sumo_nodes_by_id[key] = render_state


func release_keyed_vehicle(key: String) -> void:
	## Returns a claimed model to the pool, hidden. Only for vehicles that are
	## out of view in custody (inside a depot / unloaded at the tow yard).
	if not _sumo_nodes_by_id.has(key):
		return
	var node := (_sumo_nodes_by_id[key] as Dictionary).node as Node3D
	_sumo_nodes_by_id.erase(key)
	if is_instance_valid(node):
		node.visible = false
		node.set_meta("sumo_trip_status", "pooled")


func ensure_light_rig(vehicle: Node3D) -> void:
	## Warning lamps are built lazily, the first time a vehicle needs them.
	if vehicle.get_node_or_null("VehicleLightRig") != null:
		return
	var rig := VehicleLightRig.new()
	vehicle.add_child(rig)
	rig.setup(vehicle, str(vehicle.get_meta("vehicle_class", "car")), _vehicle_local_bounds(vehicle))
	_assign_vehicle_layer(vehicle)


func get_last_state() -> Dictionary:
	return _last_state


func class_spec(visual_class: String) -> Dictionary:
	return _class_spec(visual_class)


# --- Geometry helpers -------------------------------------------------------

func _ground_vehicle_to_road(vehicle: Node3D) -> float:
	var bounds := _vehicle_local_bounds(vehicle)
	# The visual road top is y=0.155; a tiny bias prevents asphalt z-fighting.
	var ground_height := road_surface_y - bounds.position.y * vehicle.scale.y
	vehicle.global_position.y = ground_height
	return ground_height


func _ground_pedestrian_to_sidewalk(person: Node3D) -> float:
	# Rocketbox skin AABBs are stored in bind-pose coordinates while their Bip01
	# skeleton supplies the upright deformation. All selected avatars are
	# authored feet-on-origin, so use that production rig contract directly.
	if person.scene_file_path.begins_with("res://assets/rocketbox/"):
		person.global_position.y = sidewalk_surface_y
		return sidewalk_surface_y
	var bounds := _vehicle_local_bounds(person)
	var ground_height := sidewalk_surface_y - bounds.position.y * person.global_transform.basis.get_scale().y
	person.global_position.y = ground_height
	return ground_height


func _vehicle_local_bounds(vehicle: Node3D) -> AABB:
	## Bounds in the vehicle's own frame, before its scale is applied.
	var result := AABB()
	var has_bounds := false
	for mesh_node in vehicle.find_children("*", "MeshInstance3D", true, false):
		var mesh_instance := mesh_node as MeshInstance3D
		if mesh_instance.mesh == null:
			continue
		var mesh_bounds := mesh_instance.mesh.get_aabb()
		for corner in _aabb_corners(mesh_bounds):
			var point := vehicle.to_local(mesh_instance.to_global(corner))
			result = AABB(point, Vector3.ZERO) if not has_bounds else result.expand(point)
			has_bounds = true
	return result


func _aabb_corners(bounds: AABB) -> Array[Vector3]:
	var start := bounds.position
	var end := bounds.end
	return [
		Vector3(start.x, start.y, start.z), Vector3(end.x, start.y, start.z),
		Vector3(start.x, end.y, start.z), Vector3(end.x, end.y, start.z),
		Vector3(start.x, start.y, end.z), Vector3(end.x, start.y, end.z),
		Vector3(start.x, end.y, end.z), Vector3(end.x, end.y, end.z),
	]


func _prepare_vehicle_wheels(vehicle: Node3D) -> Array[Node3D]:
	var pivots: Array[Node3D] = []
	for descendant in vehicle.find_children("*", "MeshInstance3D", true, false):
		var wheel_mesh := descendant as MeshInstance3D
		if "wheel" not in wheel_mesh.name.to_lower() or wheel_mesh.mesh == null:
			continue
		# Some imported models name a body mesh "BackWheels" even though it has
		# no wheel-local geometry. It must remain baked.
		if wheel_mesh.mesh.get_aabb().get_center().length() < 0.4:
			continue
		# GLB wheel vertices are baked at their car-space location while their
		# MeshInstance origin sits at (0,0,0). A dedicated pivot at the mesh AABB
		# center gives each axle its real rotation center.
		var parent := wheel_mesh.get_parent() as Node3D
		if parent == null:
			continue
		var pivot := Node3D.new()
		pivot.name = "%s_Pivot" % wheel_mesh.name
		var center := wheel_mesh.mesh.get_aabb().get_center()
		pivot.position = parent.to_local(wheel_mesh.to_global(center))
		parent.add_child(pivot)
		wheel_mesh.reparent(pivot, true)
		pivots.append(pivot)
	return pivots


func _disconnect_sumo() -> void:
	sumo_connected = false
	for key in _sumo_nodes_by_id.keys():
		if not bool((_sumo_nodes_by_id[key] as Dictionary).get("claimed", false)):
			_sumo_nodes_by_id.erase(key)
	_retiring_vehicles.clear()
	_update_status_label()


func get_vehicle_debug() -> Array[Dictionary]:
	var output: Array[Dictionary] = []
	for vehicle_key in _sumo_nodes_by_id:
		var state := _sumo_nodes_by_id[vehicle_key] as Dictionary
		var vehicle_node := state.node as Node3D
		output.append({"vehicle_id":str(state.get("sumo_id", vehicle_key)),"key":str(vehicle_key),"class":str(state.cls),"claimed":bool(state.claimed),"node":state.node,"status":str(vehicle_node.get_meta("sumo_trip_status", "active")),"continuation_count":int(state.get("continuation_count", 0)),"turning":bool(vehicle_node.get_meta("sumo_turning", false)),"stop_reason":str(vehicle_node.get_meta("sumo_stop_reason", "moving")),"road":str(vehicle_node.get_meta("sumo_road", "")),"speed":float(state.get("speed", 0.0)),"position":vehicle_node.global_position})
	for state in _retiring_vehicles:
		output.append({"vehicle_id":str(state.vehicle_id),"node":state.node,"status":"leaving_city","continuation_count":0,"speed":float(state.speed),"position":(state.node as Node3D).global_position})
	return output


func get_transport_snapshot() -> Dictionary:
	var speed_total := 0.0
	var speed_samples := 0
	for render_value in _sumo_nodes_by_id.values():
		var render_state := render_value as Dictionary
		if not bool(render_state.has_packet):
			continue
		speed_total += float(render_state.get("speed", 0.0))
		speed_samples += 1
	return {
		"sumo_connected": sumo_connected,
		"simulated_vehicle_count": _sumo_simulated_count,
		"displayed_vehicle_count": speed_samples,
		"stopped_vehicle_count": _sumo_stopped_count,
		"turning_vehicle_count": _sumo_turning_count,
		"average_speed_mps": speed_total / float(maxi(1, speed_samples)),
		"sample_msec": Time.get_ticks_msec()
	}


## Last lamp state shown for every signal head, by SUMO signal id and approach ("r", "y", "G").
func signal_states() -> Dictionary:
	return _signal_states


func _apply_sumo_signal(signal_data: Dictionary) -> void:
	var signal_name := str(signal_data.get("id", ""))
	if not _signal_lamps.has(signal_name):
		return
	var approaches: Dictionary = signal_data.get("approaches", {})
	for approach in ["north", "south", "east", "west"]:
		# Preserve SUMO's authoritative state for each incoming road. Collapsing
		# this to "any green" made the perpendicular road look green too.
		_set_signal_lamp(signal_name, approach, str(approaches.get(approach, "r")))


func _update_status_label() -> void:
	var status := get_parent().get_node_or_null("CameraControls/SumoStatus") as Label
	if status == null:
		return
	if sumo_connected:
		status.text = "SUMO LIVE  •  %d simulated / %d visible  •  %d stopped  •  %d turning  •  49 directional signals" % [_sumo_simulated_count, _sumo_nodes_by_id.size(), _sumo_stopped_count, _sumo_turning_count]
		status.modulate = Color(0.55, 1.0, 0.66, 1.0)
	else:
		status.text = "SUMO STARTING  •  local traffic fallback active"
		status.modulate = Color(1.0, 0.84, 0.42, 1.0)


func _update_pedestrians() -> void:
	for state in _pedestrians:
		var person := state.node as Node3D
		# NpcAi owns both citizens and background performers. No pedestrian is ever
		# sent through the old sine/cosine orbit: that made the city look like every
		# character was blindly walking in a circle. CityLife still starts the
		# imported clips; the role director selects when they walk, interact or idle.
		if (person.is_in_group("pedestrians") or person.is_in_group("backdrop_pedestrians")) and get_parent().has_node("NpcAi"):
			continue
		var phase: float = _elapsed * pedestrian_speed + state.phase
		var origin := state.origin as Vector3
		# Matches the imported 1.67-second walk stride: roughly 1.7 m/s,
		# instead of the previous ~4 m/s slide across the sidewalk.
		var offset := Vector3(sin(phase) * 1.8, 0.0, cos(phase * 1.17) * 1.25)
		var direction := Vector3(cos(phase) * 1.8, 0.0, -sin(phase * 1.17) * 1.4625).normalized()
		person.global_position = origin + offset
		person.rotation.y = atan2(direction.x, direction.z)


func _play_authored_walk(person: Node3D, index: int) -> AnimationPlayer:
	var player := person.find_child("AnimationPlayer", true, false) as AnimationPlayer
	if player == null:
		push_error("Pedestrian %s has no AnimationPlayer" % person.name)
		return null
	# NpcAi installs the role-safe Rocketbox motion-capture library after all
	# citizens are registered. The base FBX intentionally contains only its mesh,
	# skin and skeleton, not a model-specific locomotion clip.
	if person.scene_file_path.begins_with("res://assets/rocketbox/"):
		return player
	for animation_name in player.get_animation_list():
		if animation_name.ends_with("|Walk") or animation_name.ends_with("|Walking"):
			var walk_clip := player.get_animation(animation_name)
			# The bundled GLB clips contain valid limb tracks but import with no
			# repeat mode. A walking citizen must loop rather than freeze at 1.67 s.
			walk_clip.loop_mode = Animation.LOOP_LINEAR
			player.play(animation_name)
			# Spread the crowds along the stride so the first frame is not a row
			# of identical wide-leg poses; retain a small natural cadence variance.
			player.seek(fposmod(float(index) * 0.173, walk_clip.length), true)
			player.speed_scale = 0.92 + float(index % 5) * 0.03
			return player
	push_error("Pedestrian %s has no authored Walk/Walking clip" % person.name)
	return null


func _update_signals() -> void:
	# The editor fallback has the exact same two-axis order as SUMO: north/south
	# and east/west are mutually exclusive, with yellow and all-red clearance.
	var fallback_cycle := signal_cycle_seconds * 2.0 + 8.0
	var fallback_time := fposmod(_elapsed, fallback_cycle)
	var states := {"north": "r", "south": "r", "east": "r", "west": "r"}
	if fallback_time < signal_cycle_seconds:
		states.north = "G"
		states.south = "G"
	elif fallback_time < signal_cycle_seconds + 3.0:
		states.north = "y"
		states.south = "y"
	elif fallback_time >= signal_cycle_seconds + 5.0 and fallback_time < signal_cycle_seconds * 2.0 + 5.0:
		states.east = "G"
		states.west = "G"
	elif fallback_time >= signal_cycle_seconds * 2.0 + 5.0:
		states.east = "y"
		states.west = "y"
	for signal_node in get_tree().get_nodes_in_group("traffic_signals"):
		var traffic_signal := signal_node as Node3D
		var signal_name := _sumo_signal_id_for(traffic_signal)
		for approach in ["north", "south", "east", "west"]:
			_set_signal_lamp(signal_name, approach, str(states[approach]))


func _ensure_full_city_traffic_signals() -> void:
	# The authored scene originally supplied the downtown 5 x 5 only.  SUMO now
	# controls every 4-way city intersection (the 7 x 7 interior), so duplicate
	# that same native signal asset only onto the remaining 24 intersections.
	# The perimeter remains deliberately signal-free because it is an entry/exit
	# boundary rather than a full crossing.
	var signal_parent := get_parent().get_node_or_null("Actuators/TrafficSignals") as Node3D
	if signal_parent == null:
		push_error("TrafficSignals parent is missing; cannot synchronize city signals")
		return
	var template := signal_parent.get_node_or_null("TrafficSignal_r1_c1") as Node3D
	if template == null:
		push_error("Traffic signal template is missing; cannot synchronize city signals")
		return
	for signal_node_value in get_tree().get_nodes_in_group("traffic_signals"):
		var existing_signal := signal_node_value as Node3D
		if not existing_signal.has_meta("sumo_junction"):
			existing_signal.set_meta("sumo_junction", _sumo_signal_id_for(existing_signal))
	for row in range(2, 9):
		for column in range(2, 9):
			var sumo_id := "Junction_r%d_c%d" % [row, column]
			var found := false
			for signal_node_value in get_tree().get_nodes_in_group("traffic_signals"):
				var existing_signal := signal_node_value as Node3D
				if _sumo_signal_id_for(existing_signal) == sumo_id:
					found = true
					break
			if found:
				continue
			var generated := template.duplicate() as Node3D
			generated.name = "TrafficSignal_sumo_r%d_c%d" % [row, column]
			var corner_offset := Vector3(SIGNAL_CORNER_OFFSET, 0.15, -SIGNAL_CORNER_OFFSET) if (row + column) % 2 == 0 else Vector3(-SIGNAL_CORNER_OFFSET, 0.15, SIGNAL_CORNER_OFFSET)
			generated.position = Vector3(-475.0 + float(column) * 95.0, 0.0, -475.0 + float(row) * 95.0) + corner_offset
			generated.set_meta("sumo_junction", sumo_id)
			generated.set_meta("junction", sumo_id)
			generated.add_to_group("traffic_signals")
			signal_parent.add_child(generated)


func _place_signal_on_corner(traffic_signal: Node3D, sumo_id: String) -> void:
	## Keeps the assembly's diagonal (which corner it serves) but stands it on
	## the sidewalk corner instead of the junction asphalt.
	var parts := sumo_id.trim_prefix("Junction_r").split("_c")
	if parts.size() != 2:
		return
	var centre := Vector3(-475.0 + float(parts[1]) * 95.0, 0.0, -475.0 + float(parts[0]) * 95.0)
	var offset := traffic_signal.global_position - centre
	traffic_signal.global_position = Vector3(centre.x + signf(offset.x) * SIGNAL_CORNER_OFFSET, traffic_signal.global_position.y, centre.z + signf(offset.z) * SIGNAL_CORNER_OFFSET)


func _sumo_signal_id_for(traffic_signal: Node3D) -> String:
	var explicit_id := str(traffic_signal.get_meta("sumo_junction", ""))
	if explicit_id.begins_with("Junction_r"):
		return explicit_id
	# The 25 authored nodes use stable local 1..5 names.  They depict SUMO's
	# original central rows/columns 3..7, so translate the local name once.
	var parts := traffic_signal.name.split("_")
	if parts.size() == 3 and parts[0] == "TrafficSignal":
		var local_row := int(parts[1].trim_prefix("r"))
		var local_column := int(parts[2].trim_prefix("c"))
		if local_row >= 1 and local_row <= 5 and local_column >= 1 and local_column <= 5:
			return "Junction_r%d_c%d" % [local_row + 2, local_column + 2]
	return str(traffic_signal.get_meta("junction", traffic_signal.name))


func _keep_authored_traffic_light(signal_name: String, traffic_signal: Node3D) -> void:
	var source_head := traffic_signal.get_node_or_null("SignalHead") as MeshInstance3D
	# The imported traffic-light asset already has complete signal heads, lenses,
	# housing, and pole geometry. Do not add markers, panels, or substitute
	# lights. SUMO still controls right-of-way; this preserves the authored model.
	if source_head != null:
		source_head.visible = false
	var native_lens_materials := {
		"north": {"red": [], "amber": [], "green": []},
		"south": {"red": [], "amber": [], "green": []},
		"east": {"red": [], "amber": [], "green": []},
		"west": {"red": [], "amber": [], "green": []},
	}
	var authored_model := traffic_signal.get_node_or_null("SignalModel") as Node3D
	if authored_model != null:
		# A real junction needs two right-angle assemblies at opposite corners,
		# not four separate poles. Each native model contains a forward and a
		# perpendicular signal face; map those four faces to SUMO approaches.
		var opposite_model := _add_opposite_authored_traffic_light(signal_name, traffic_signal, authored_model)
		var assemblies := [
			{"model": authored_model, "primary": "south", "perpendicular": "east"},
			{"model": opposite_model, "primary": "north", "perpendicular": "west"},
		]
		for assembly_value in assemblies:
			var assembly := assembly_value as Dictionary
			var model := assembly.model as Node3D
			for mesh_value in model.find_children("*", "MeshInstance3D", true, false):
				var mesh := mesh_value as MeshInstance3D
				var face_lenses := _split_authored_traffic_light_lenses(mesh)
				for lens_colour in ["red", "amber", "green"]:
					((native_lens_materials[assembly.primary] as Dictionary)[lens_colour] as Array).append_array((face_lenses.primary as Dictionary)[lens_colour] as Array)
					((native_lens_materials[assembly.perpendicular] as Dictionary)[lens_colour] as Array).append_array((face_lenses.perpendicular as Dictionary)[lens_colour] as Array)
	_signal_lamps[signal_name] = native_lens_materials


func _set_signal_lamp(signal_name: String, approach: String, state: String) -> void:
	if not _signal_lamps.has(signal_name):
		return
	var signal_heads := _signal_lamps[signal_name] as Dictionary
	if not signal_heads.has(approach):
		return
	if not _signal_states.has(signal_name):
		_signal_states[signal_name] = {}
	(_signal_states[signal_name] as Dictionary)[approach] = state
	var native_lenses := signal_heads[approach] as Dictionary
	var is_green := state == "G" or state == "g"
	var is_amber := state == "y" or state == "Y"
	for material_value in native_lenses.green:
		var green_lens := material_value as StandardMaterial3D
		green_lens.emission = Color(0.005, 1.0, 0.05, 1.0)
		green_lens.emission_enabled = is_green
	for material_value in native_lenses.red:
		var red_lens := material_value as StandardMaterial3D
		red_lens.emission = Color(1.0, 0.015, 0.003, 1.0)
		red_lens.emission_enabled = not is_green and not is_amber
	for material_value in native_lenses.amber:
		var amber_lens := material_value as StandardMaterial3D
		amber_lens.emission = Color(1.0, 0.44, 0.004, 1.0)
		amber_lens.emission_enabled = is_amber


func _add_opposite_authored_traffic_light(signal_name: String, traffic_signal: Node3D, authored_model: Node3D) -> Node3D:
	var existing := traffic_signal.get_node_or_null("SignalModelOpposite") as Node3D
	if existing != null:
		return existing
	var parts := signal_name.split("_")
	var row := int(parts[1].trim_prefix("r"))
	var column := int(parts[2].trim_prefix("c"))
	# The matching right-angle assembly sits on the opposite intersection corner.
	var corner_delta := Vector3(-2.0 * SIGNAL_CORNER_OFFSET, 0.0, 2.0 * SIGNAL_CORNER_OFFSET) if (row + column) % 2 == 0 else Vector3(2.0 * SIGNAL_CORNER_OFFSET, 0.0, -2.0 * SIGNAL_CORNER_OFFSET)
	var opposite := authored_model.duplicate() as Node3D
	opposite.name = "SignalModelOpposite"
	opposite.position = authored_model.position + corner_delta
	opposite.rotation.y = authored_model.rotation.y + PI
	traffic_signal.add_child(opposite)
	return opposite


func _split_authored_traffic_light_lenses(mesh_instance: MeshInstance3D) -> Dictionary:
	# The imported asset is one mesh, but its red/green lens faces are already
	# distinct native triangles in that mesh. Split those existing triangles into
	# material surfaces—no new vertices, models, panels, or marker nodes.
	var result := {
		"primary": {"red": [], "amber": [], "green": []},
		"perpendicular": {"red": [], "amber": [], "green": []},
	}
	if mesh_instance.mesh == null or mesh_instance.mesh.get_surface_count() == 0:
		return result
	var source_material := mesh_instance.get_active_material(0)
	if not source_material is StandardMaterial3D:
		return result
	var source_standard := source_material as StandardMaterial3D
	if source_standard.albedo_texture == null:
		return result
	var source_arrays := mesh_instance.mesh.surface_get_arrays(0)
	var vertices: PackedVector3Array = source_arrays[Mesh.ARRAY_VERTEX]
	var uvs: PackedVector2Array = source_arrays[Mesh.ARRAY_TEX_UV]
	var indices: PackedInt32Array = source_arrays[Mesh.ARRAY_INDEX]
	if vertices.is_empty() or uvs.is_empty() or indices.is_empty():
		return result
	var texture_image := source_standard.albedo_texture.get_image()
	# Imported GPU textures may remain compressed in memory. Sampling them with
	# get_pixelv() emits one error per triangle (and can flood the terminal); a
	# one-time CPU decompression makes the native lens-face classification safe.
	if texture_image == null:
		return result
	if texture_image.is_compressed() and texture_image.decompress() != OK:
		return result
	var base_indices := PackedInt32Array()
	var primary_red_indices := PackedInt32Array()
	var primary_amber_indices := PackedInt32Array()
	var primary_green_indices := PackedInt32Array()
	var perpendicular_red_indices := PackedInt32Array()
	var perpendicular_amber_indices := PackedInt32Array()
	var perpendicular_green_indices := PackedInt32Array()
	for triangle_index in range(0, indices.size(), 3):
		var triangle_colour := Color.BLACK
		var triangle_uv := Vector2.ZERO
		for corner in 3:
			var uv := uvs[indices[triangle_index + corner]]
			triangle_uv += uv
			var pixel := Vector2i(clampi(int(uv.x * float(texture_image.get_width() - 1)), 0, texture_image.get_width() - 1), clampi(int(uv.y * float(texture_image.get_height() - 1)), 0, texture_image.get_height() - 1))
			triangle_colour += texture_image.get_pixelv(pixel)
		triangle_colour /= 3.0
		triangle_uv /= 3.0
		var triangle_centre := Vector3.ZERO
		for corner in 3:
			triangle_centre += vertices[indices[triangle_index + corner]]
		triangle_centre /= 3.0
		var is_red := _is_native_red_lens(triangle_colour)
		var is_green := _is_native_green_lens(triangle_colour)
		# This CC0 asset's true middle lens is a discrete UV-island. The yellow
		# street-pole housing shares a similar orange palette, so colour alone
		# would light the entire housing instead of its native middle lens.
		var is_amber := _is_native_amber_lens(triangle_colour, triangle_uv)
		# The asset's two right-angle heads are separate native mesh regions:
		# the forward face extends along local Z, the perpendicular one along X.
		var is_perpendicular := absf(triangle_centre.x) > absf(triangle_centre.z)
		var target_indices := base_indices
		if is_red:
			target_indices = perpendicular_red_indices if is_perpendicular else primary_red_indices
		elif is_green:
			target_indices = perpendicular_green_indices if is_perpendicular else primary_green_indices
		elif is_amber:
			target_indices = perpendicular_amber_indices if is_perpendicular else primary_amber_indices
		for corner in 3:
			target_indices.append(indices[triangle_index + corner])
		# Packed arrays are value types; assign each possibly edited target back.
		if is_red:
			if is_perpendicular:
				perpendicular_red_indices = target_indices
			else:
				primary_red_indices = target_indices
		elif is_green:
			if is_perpendicular:
				perpendicular_green_indices = target_indices
			else:
				primary_green_indices = target_indices
		elif is_amber:
			if is_perpendicular:
				perpendicular_amber_indices = target_indices
			else:
				primary_amber_indices = target_indices
		else:
			base_indices = target_indices
	var split_mesh := ArrayMesh.new()
	_add_authored_traffic_surface(split_mesh, source_arrays, base_indices, source_standard.duplicate(true) as StandardMaterial3D, Color.WHITE, false, result)
	_add_authored_traffic_surface(split_mesh, source_arrays, primary_red_indices, source_standard.duplicate(true) as StandardMaterial3D, Color(1.0, 0.015, 0.003, 1.0), false, result, "primary", "red")
	_add_authored_traffic_surface(split_mesh, source_arrays, primary_amber_indices, source_standard.duplicate(true) as StandardMaterial3D, Color(1.0, 0.44, 0.004, 1.0), false, result, "primary", "amber")
	_add_authored_traffic_surface(split_mesh, source_arrays, primary_green_indices, source_standard.duplicate(true) as StandardMaterial3D, Color(0.005, 1.0, 0.05, 1.0), false, result, "primary", "green")
	_add_authored_traffic_surface(split_mesh, source_arrays, perpendicular_red_indices, source_standard.duplicate(true) as StandardMaterial3D, Color(1.0, 0.015, 0.003, 1.0), false, result, "perpendicular", "red")
	_add_authored_traffic_surface(split_mesh, source_arrays, perpendicular_amber_indices, source_standard.duplicate(true) as StandardMaterial3D, Color(1.0, 0.44, 0.004, 1.0), false, result, "perpendicular", "amber")
	_add_authored_traffic_surface(split_mesh, source_arrays, perpendicular_green_indices, source_standard.duplicate(true) as StandardMaterial3D, Color(0.005, 1.0, 0.05, 1.0), false, result, "perpendicular", "green")
	mesh_instance.mesh = split_mesh
	mesh_instance.material_override = null
	return result


func _add_authored_traffic_surface(split_mesh: ArrayMesh, source_arrays: Array, indices: PackedInt32Array, material: StandardMaterial3D, emission_colour: Color, initially_lit: bool, result: Dictionary, face := "", lens_colour := "") -> void:
	if indices.is_empty():
		return
	var split_arrays := source_arrays.duplicate(true)
	split_arrays[Mesh.ARRAY_INDEX] = indices
	split_mesh.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, split_arrays)
	material.emission_enabled = initially_lit
	material.emission = emission_colour
	material.emission_energy_multiplier = 8.0
	split_mesh.surface_set_material(split_mesh.get_surface_count() - 1, material)
	if not face.is_empty() and not lens_colour.is_empty():
		((result[face] as Dictionary)[lens_colour] as Array).append(material)


func _is_native_red_lens(colour: Color) -> bool:
	return colour.r > 0.45 and colour.r > colour.g * 1.6 and colour.r > colour.b * 1.7


func _is_native_amber_lens(colour: Color, uv: Vector2) -> bool:
	return colour.r > 0.62 and colour.g > 0.32 and colour.r > colour.g * 1.1 and colour.g > colour.b * 1.3 and uv.x > 0.27 and uv.x < 0.36 and uv.y > 0.80


func _is_native_green_lens(colour: Color) -> bool:
	return colour.g > 0.24 and colour.g > colour.r * 1.3 and colour.g > colour.b * 1.2


func _set_signal_green(traffic_signal: Node3D, is_green: bool) -> void:
	var signal_name := _sumo_signal_id_for(traffic_signal)
	for approach in ["north", "south", "east", "west"]:
		_set_signal_lamp(signal_name, approach, "G" if is_green else "r")


func _rectangle_path(distance: float, half_extent: float) -> Dictionary:
	var side := half_extent * 2.0
	var perimeter := side * 4.0
	var value := fposmod(distance, perimeter)
	if value < side:
		return {"position": Vector3(-half_extent + value, 0.0, -half_extent), "direction": Vector3.RIGHT}
	if value < side * 2.0:
		return {"position": Vector3(half_extent, 0.0, -half_extent + value - side), "direction": Vector3.FORWARD}
	if value < side * 3.0:
		return {"position": Vector3(half_extent - (value - side * 2.0), 0.0, half_extent), "direction": Vector3.LEFT}
	return {"position": Vector3(-half_extent, 0.0, half_extent - (value - side * 3.0)), "direction": Vector3.BACK}
