extends Node

## Godot custody of emergency units outside the SUMO network: parked in their
## station bay, crew boarding, the validated pull-out to the kerb, the merge
## into SUMO's lane position, the turn-in back to the bay and crew alighting.
## A unit's model never appears or disappears in view: it is the same pooled
## node from bay to street and back (CityLife custody API, keyed by unit id).

const DepotPaths := preload("res://scripts/incidents/depot_paths.gd")
const RoadGeometry := preload("res://scripts/incidents/road_geometry.gd")
const DoorTransit := preload("res://scripts/incidents/vehicle_door_transit.gd")
const DEPOTS_PATH := "res://sumo/emergency_depots.json"
const PERSON_WAIT_SECONDS := 4.0
const LIGHT_BAR := 1
const LIGHT_SIREN := 4
const LIGHT_AMBER := 8
const CREW_BY_KIND := {"police": ["police", "police"], "ambulance": ["paramedic", "paramedic"], "fire": ["firefighter", "firefighter", "firefighter"], "tow": ["tow_operator"], "utility": ["utility_worker", "utility_worker"], "garbage": ["sanitation_worker", "sanitation_worker"]}
## Pulling out of the station bay (a unit is answering a call): brisk, not a crawl.
const DRIVE_SPEED := 5.6
## Crews hurry to their vehicle when called out.
const CREW_HURRY_SPEED := 2.2
const MERGE_SECONDS := 1.6
## Pull-out pauses with the nose at the kerb: the curve parameter where the
## vehicle front first reaches this lateral offset from the road centreline.
const HOLD_FRONT_OFFSET := 5.4

var manager: Node
var life: Node
var depots: Dictionary = {}
var units: Dictionary = {}
var _station_root: Node3D


func initialize(owner_manager: Node) -> void:
	manager = owner_manager
	life = manager.life
	_station_root = Node3D.new()
	_station_root.name = "Stations"
	manager.add_child(_station_root)
	var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(DEPOTS_PATH))
	if not parsed is Dictionary:
		push_error("Emergency depot layout missing: %s" % DEPOTS_PATH)
		return
	for depot_value in (parsed as Dictionary).get("depots", []):
		var depot := depot_value as Dictionary
		depot["edge_data"] = RoadGeometry.parse_edge(str(depot.edge))
		depots[str(depot.id)] = depot
		_dress_station(depot)
		for bay_value in depot.get("bays", []):
			var bay := bay_value as Dictionary
			var unit_id := str(bay.unit)
			units[unit_id] = {"id": unit_id, "kind": _kind_for_class(str(bay["class"])), "cls": str(bay["class"]), "depot": depot, "bay": bay, "status": "garaged", "phase": "parked", "crew": [], "t": 0.0, "lights": 0}
			var node := life.call("acquire_keyed_vehicle", unit_id, str(bay["class"])) as Node3D
			if node != null:
				life.call("place_keyed_vehicle", unit_id, Vector3(float(bay.x), 0.0, float(bay.z)), float(bay.yaw))
				node.set_meta("vehicle_unit", unit_id)
				node.set_meta("vehicle_lights", 0)
				life.call("ensure_light_rig", node)
		for patrol_value in depot.get("patrol_units", []):
			var patrol := patrol_value as Dictionary
			units[str(patrol.unit)] = {"id": str(patrol.unit), "kind": _kind_for_class(str(patrol["class"])), "cls": str(patrol["class"]), "depot": depot, "bay": {}, "status": "patrol", "phase": "on_network", "crew": [], "t": 0.0, "lights": 0}


func _kind_for_class(visual_class: String) -> String:
	return visual_class


func _dress_station(depot: Dictionary) -> void:
	## Paved apron with painted bays and a station sign, so a parked unit sits
	## on an obvious service pad instead of an arbitrary lot.
	var apron := depot.get("apron", []) as Array
	if apron.size() == 4:
		var pad := MeshInstance3D.new()
		pad.name = "%s_Apron" % str(depot.id)
		var mesh := BoxMesh.new()
		mesh.size = Vector3(float(apron[2]), 0.03, float(apron[3]))
		pad.mesh = mesh
		var material := StandardMaterial3D.new()
		material.albedo_color = Color(0.36, 0.37, 0.39)
		material.roughness = 0.92
		pad.material_override = material
		pad.position = Vector3(float(apron[0]) + float(apron[2]) * 0.5, 0.165, float(apron[1]) + float(apron[3]) * 0.5)
		_station_root.add_child(pad)
		pad.add_to_group("emergency_stations")
	var edge := depot.edge_data as Dictionary
	for bay_value in depot.get("bays", []):
		var bay := bay_value as Dictionary
		var outline_material := StandardMaterial3D.new()
		outline_material.albedo_color = Color(0.95, 0.78, 0.12)
		outline_material.roughness = 0.6
		var length := float(bay.length) + 0.8
		for end_sign in [-1.0, 1.0]:
			var line := MeshInstance3D.new()
			var line_mesh := BoxMesh.new()
			line_mesh.size = Vector3(3.4, 0.012, 0.12)
			line.mesh = line_mesh
			line.material_override = outline_material
			var centre: Vector3 = Vector3(float(bay.x), 0.186, float(bay.z)) + (edge.direction as Vector3) * (length * 0.5 * float(end_sign))
			line.position = centre
			line.rotation.y = float(bay.yaw)
			_station_root.add_child(line)
	var label := Label3D.new()
	label.text = str(depot.label).to_upper()
	label.font_size = 64
	label.pixel_size = 0.012
	label.outline_size = 10
	label.modulate = Color(0.95, 0.97, 1.0)
	label.billboard = BaseMaterial3D.BILLBOARD_FIXED_Y
	var door := depot.door as Array
	label.position = Vector3(float(door[0]), 6.5, float(door[1]))
	label.name = "%s_Sign" % str(depot.id)
	_station_root.add_child(label)


# --- Bridge state -------------------------------------------------------------------

func on_state(state: Dictionary) -> void:
	for record_value in state.get("fleet", []):
		var record := record_value as Dictionary
		var unit_id := str(record.get("unit", ""))
		if not units.has(unit_id):
			continue
		var unit := units[unit_id] as Dictionary
		var previous := str(unit.status)
		unit.status = str(record.get("status", previous))
		unit.response = bool(record.get("response", false))
		unit.lights = int(record.get("lights", 0))
		if previous != unit.status:
			_on_status_changed(unit, previous)
	for handoff_value in state.get("handoffs", []):
		var handoff := handoff_value as Dictionary
		var unit_id := str(handoff.get("unit", ""))
		if str(handoff.get("reason", "")) == "depot_entry" and units.has(unit_id):
			var unit := units[unit_id] as Dictionary
			if str(unit.phase) == "on_network":
				_begin_turn_in(unit)


func on_request_completed(_completed: Dictionary) -> void:
	pass


func _on_status_changed(unit: Dictionary, previous: String) -> void:
	match str(unit.status):
		"mobilizing":
			if str(unit.phase) == "parked":
				_begin_boarding(unit)
		"garaged":
			# Mobilisation cancelled before insertion: crew goes back inside and
			# the unit returns to its bay along the same curve.
			if str(unit.phase) in ["boarding"]:
				_send_crew_to_door(unit)
				unit.phase = "parked"
			elif str(unit.phase) in ["pulling_out", "holding"]:
				unit.phase = "backing_in"
		"entering_depot":
			if str(unit.phase) == "on_network":
				_begin_turn_in(unit)


# --- Phases -------------------------------------------------------------------------------

func _door_points(unit: Dictionary, count: int) -> Array[Vector3]:
	var pose := life.call("get_keyed_pose", str(unit.id)) as Dictionary
	var centre := pose.get("centre", Vector3.ZERO) as Vector3
	var yaw := float(pose.get("yaw", 0.0))
	var spec := life.call("class_spec", str(unit.cls)) as Dictionary
	var half_width := float(spec.get("width", 2.2)) * 0.5 + 0.48
	var forward := Vector3(sin(yaw), 0.0, cos(yaw))
	var left := Vector3(cos(yaw), 0.0, -sin(yaw))
	var points: Array[Vector3] = []
	var slots := [[1.0, 0.55], [-1.0, 0.55], [1.0, -0.9], [-1.0, -0.9]]
	for index in count:
		var slot := slots[index % slots.size()] as Array
		points.append(centre + left * half_width * float(slot[0]) + forward * float(slot[1]))
	return points


func _begin_boarding(unit: Dictionary) -> void:
	var depot := unit.depot as Dictionary
	var door := depot.door as Array
	var door_point := Vector3(float(door[0]), 0.17, float(door[1]))
	var roles := CREW_BY_KIND.get(str(unit.kind), ["utility_worker"]) as Array
	var targets := _door_points(unit, roles.size())
	var vehicle := life.call("get_keyed_vehicle", str(unit.id)) as Node3D
	for index in roles.size():
		# Walk to the door each will climb in by.
		var slot := DoorTransit.slot_for(vehicle, index)
		if not slot.is_empty():
			targets[index] = slot.outside as Vector3
	var crew: Array = []
	var taken: Array[Vector3] = []
	for index in roles.size():
		# Each leaves the door from a free spot: walkable, outside every parked
		# vehicle (a bay can sit right beside the door) and apart from the
		# colleagues already out.
		var start := _free_spot_near(door_point, taken)
		taken.append(start)
		var agent := manager.call("spawn_responder", str(roles[index]), start, 0.0) as Node
		if agent == null:
			continue
		agent.connect("arrived", _on_crew_boarded.bind(unit))
		agent.set("speed", CREW_HURRY_SPEED)
		if not bool(agent.call("go_to", targets[index])):
			push_warning("%s crew member has no walkable route to its door" % str(unit.id))
		crew.append(agent)
	unit.crew = crew
	unit.phase = "boarding" if not crew.is_empty() else "pulling_out"
	if crew.is_empty():
		_prepare_pull_out(unit)


func _free_spot_near(centre: Vector3, taken: Array[Vector3]) -> Vector3:
	var candidates: Array[Vector3] = [centre]
	for radius in [0.7, 1.2, 1.7]:
		for step in 8:
			var angle := TAU * float(step) / 8.0
			candidates.append(centre + Vector3(cos(angle), 0.0, sin(angle)) * float(radius))
	for candidate in candidates:
		if bool(manager.call("walk_blocked", candidate)):
			continue
		var clear := true
		for other in taken:
			if Vector2(candidate.x - other.x, candidate.z - other.z).length() < 0.8:
				clear = false
				break
		if clear:
			return candidate
	return centre


func _on_crew_boarded(agent: Node, unit: Dictionary) -> void:
	if str(unit.phase) != "boarding":
		return
	# Open the door, climb in and shut it; the unit leaves once all are in.
	var vehicle := life.call("get_keyed_vehicle", str(unit.id)) as Node3D
	DoorTransit.board(vehicle, agent, DoorTransit.nearest_slot(vehicle, agent.call("position") as Vector3), func() -> void:
		(unit.crew as Array).erase(agent)
		manager.call("dispose_responder", agent)
		if str(unit.phase) == "boarding" and (unit.crew as Array).is_empty():
			_prepare_pull_out(unit))


func _prepare_pull_out(unit: Dictionary) -> void:
	var depot := unit.depot as Dictionary
	var edge := depot.edge_data as Dictionary
	var controls := DepotPaths.pull_out_curve(edge, unit.bay as Dictionary)
	unit.curve = controls
	unit.curve_length = DepotPaths.length(controls)
	unit.t = 0.0
	unit.hold_t = _hold_parameter(unit, controls, edge)
	unit.phase = "pulling_out"
	unit.speed = 0.0
	_set_custody_lights(unit)


func _hold_parameter(unit: Dictionary, controls: Array[Vector3], edge: Dictionary) -> float:
	var spec := life.call("class_spec", str(unit.cls)) as Dictionary
	var half_length := float(spec.get("length", 5.0)) * 0.5
	for sample in 101:
		var t := float(sample) / 100.0
		var front := DepotPaths.point(controls, t) + DepotPaths.tangent(controls, t) * half_length
		var lateral := (front - (edge.start as Vector3)).dot(edge.right as Vector3)
		if lateral <= HOLD_FRONT_OFFSET:
			return t
	return 0.6


func _begin_turn_in(unit: Dictionary) -> void:
	var depot := unit.depot as Dictionary
	var edge := depot.edge_data as Dictionary
	var controls := DepotPaths.turn_in_curve(edge, unit.bay as Dictionary)
	# Start from the exact pose the vehicle was handed over at.
	var pose := life.call("get_keyed_pose", str(unit.id)) as Dictionary
	if not pose.is_empty():
		controls[0] = Vector3((pose.centre as Vector3).x, 0.0, (pose.centre as Vector3).z)
	unit.curve = controls
	unit.curve_length = DepotPaths.length(controls)
	unit.t = 0.0
	unit.speed = 2.0
	unit.phase = "turning_in"
	_set_custody_lights(unit)


func _send_crew_to_door(unit: Dictionary) -> void:
	var depot := unit.depot as Dictionary
	var door := depot.door as Array
	var door_point := Vector3(float(door[0]), 0.17, float(door[1]))
	for agent in unit.crew:
		if not is_instance_valid(agent):
			continue
		agent.call("go_to", door_point)
		if not agent.is_connected("arrived", _on_crew_entered_station):
			agent.connect("arrived", _on_crew_entered_station.bind(unit))


func _on_crew_entered_station(agent: Node, unit: Dictionary) -> void:
	(unit.crew as Array).erase(agent)
	manager.call("dispose_responder", agent)


func _begin_alighting(unit: Dictionary) -> void:
	var depot := unit.depot as Dictionary
	var door := depot.door as Array
	var door_point := Vector3(float(door[0]), 0.17, float(door[1]))
	var roles := CREW_BY_KIND.get(str(unit.kind), ["utility_worker"]) as Array
	var starts := _door_points(unit, roles.size())
	var vehicle := life.call("get_keyed_vehicle", str(unit.id)) as Node3D
	var per_door := DoorTransit.occupants_per_door(vehicle)
	var crew: Array = []
	for index in roles.size():
		var slot := DoorTransit.slot_for(vehicle, index)
		var start := starts[index] if slot.is_empty() else slot.outside as Vector3
		var agent := manager.call("spawn_responder", str(roles[index]), start, atan2(door_point.x - start.x, door_point.z - start.z)) as Node
		if agent == null:
			continue
		agent.connect("arrived", _on_crew_entered_station.bind(unit))
		var walk_in := func() -> void: agent.call("go_to", door_point)
		if slot.is_empty():
			walk_in.call()
		else:
			# Climb out through the cab door, then walk into the station.
			DoorTransit.alight(vehicle, agent, slot, 0.35 * float(index) + 1.1 * float(index / per_door), walk_in)
		crew.append(agent)
	unit.crew = crew


func _set_custody_lights(unit: Dictionary) -> void:
	var node := life.call("get_keyed_vehicle", str(unit.id)) as Node3D
	if node == null:
		return
	var lights := 0
	if str(unit.phase) in ["pulling_out", "holding", "merging"]:
		if bool(unit.get("response", false)) and str(unit.kind) not in ["tow", "utility", "garbage"]:
			lights = LIGHT_BAR | LIGHT_SIREN
		elif str(unit.kind) in ["tow", "utility", "garbage"]:
			lights = LIGHT_AMBER
	node.set_meta("vehicle_lights", lights)


# --- Per-frame driving ---------------------------------------------------------------------

func _process(delta: float) -> void:
	if life == null:
		return
	for unit_value in units.values():
		var unit := unit_value as Dictionary
		match str(unit.phase):
			"pulling_out":
				if bool(life.call("keyed_vehicle_has_packet", str(unit.id))):
					# The bridge has already put the unit on the road (its mobilisation time limit): blend into the
					# live vehicle now instead of finishing the pull-out, or the truck stays frozen at the station.
					unit.phase = "merging"
					unit.merge_elapsed = 0.0
					unit.merge_from_t = float(unit.t)
				elif _drive_curve(unit, delta, float(unit.hold_t)):
					unit.phase = "holding"
					unit.speed = 0.0
					manager.link.send({"action": "unit_at_curb", "unit": str(unit.id)})
			"holding":
				if bool(life.call("keyed_vehicle_has_packet", str(unit.id))):
					unit.phase = "merging"
					unit.merge_elapsed = 0.0
					unit.merge_from_t = float(unit.t)
			"merging":
				_merge(unit, delta)
			"turning_in":
				if _drive_curve(unit, delta, 1.0):
					unit.phase = "parked"
					var bay := unit.bay as Dictionary
					life.call("place_keyed_vehicle", str(unit.id), Vector3(float(bay.x), 0.0, float(bay.z)), float(bay.yaw))
					_set_custody_lights(unit)
					_begin_alighting(unit)
					manager.link.send({"action": "unit_parked", "unit": str(unit.id)})
			"backing_in":
				if _drive_curve(unit, delta, 0.0, true):
					unit.phase = "parked"
					_set_custody_lights(unit)


func _drive_curve(unit: Dictionary, delta: float, stop_t: float, reverse := false) -> bool:
	## Advances along the unit's curve at a capped, eased speed and yields to
	## any citizen or responder near the leading end. Returns true at stop_t.
	var controls := unit.curve as Array[Vector3]
	var length := maxf(float(unit.curve_length), 0.1)
	var t := float(unit.t)
	var direction := -1.0 if reverse else 1.0
	var leading := DepotPaths.point(controls, t) + DepotPaths.tangent(controls, t) * direction * 2.8
	var target_speed := DRIVE_SPEED * (0.5 if reverse else 1.0)
	if _person_near(leading, 3.0) and float(unit.get("blocked_s", 0.0)) < PERSON_WAIT_SECONDS:
		# Wait for a person in front (a crew member still walking to the door, a pedestrian), but never for ever:
		# after a few seconds they are taken as having stepped aside.
		unit.blocked_s = float(unit.get("blocked_s", 0.0)) + delta
		target_speed = 0.0
	else:
		if not _person_near(leading, 3.0):
			unit.blocked_s = 0.0
		# Ease into the stop point instead of braking hard on arrival.
		target_speed = minf(target_speed, maxf(0.6, absf(stop_t - t) * length * 1.2))
	unit.speed = move_toward(float(unit.speed), target_speed, delta * 2.4)
	t += direction * float(unit.speed) * delta / length
	var done := t >= stop_t if not reverse else t <= stop_t
	if done:
		t = stop_t
	unit.t = t
	life.call("place_keyed_vehicle", str(unit.id), DepotPaths.point(controls, t), DepotPaths.yaw_at(controls, t))
	var node := life.call("get_keyed_vehicle", str(unit.id)) as Node3D
	if node != null:
		# Citizens treat a vehicle crossing their sidewalk as an obstacle.
		node.set_meta("npc_dynamic_obstacle_radius", 2.6)
		if not done and not node.is_in_group("npc_dynamic_obstacles"):
			node.add_to_group("npc_dynamic_obstacles")
		elif done and str(unit.phase) != "pulling_out" and node.is_in_group("npc_dynamic_obstacles"):
			node.remove_from_group("npc_dynamic_obstacles")
	return done


func _merge(unit: Dictionary, delta: float) -> void:
	## Finishes the curve while blending into SUMO's live pose, then hands the
	## node back to CityLife with zero positional difference.
	var target := life.call("get_keyed_render_target", str(unit.id)) as Dictionary
	if target.is_empty():
		return
	unit.merge_elapsed = float(unit.merge_elapsed) + delta
	var progress := clampf(float(unit.merge_elapsed) / MERGE_SECONDS, 0.0, 1.0)
	var eased := progress * progress * (3.0 - 2.0 * progress)
	var controls := unit.curve as Array[Vector3]
	var curve_t := lerpf(float(unit.merge_from_t), 1.0, eased)
	var curve_point := DepotPaths.point(controls, curve_t)
	var curve_yaw := DepotPaths.yaw_at(controls, curve_t)
	var sumo_centre := target.centre as Vector3
	var blend := eased * eased
	var centre := curve_point.lerp(Vector3(sumo_centre.x, 0.0, sumo_centre.z), blend)
	var yaw := lerp_angle(curve_yaw, float(target.yaw), blend)
	life.call("place_keyed_vehicle", str(unit.id), centre, yaw)
	if progress >= 1.0:
		var node := life.call("get_keyed_vehicle", str(unit.id)) as Node3D
		if node != null and node.is_in_group("npc_dynamic_obstacles"):
			node.remove_from_group("npc_dynamic_obstacles")
		life.call("set_vehicle_claimed", str(unit.id), false)
		unit.phase = "on_network"


func _person_near(point: Vector3, radius: float) -> bool:
	for value in get_tree().get_nodes_in_group("pedestrians") + get_tree().get_nodes_in_group("incident_responders"):
		var person := value as Node3D
		if person != null and person.visible and Vector2(person.global_position.x - point.x, person.global_position.z - point.z).length() < radius:
			return true
	return false


func get_unit_view(unit_id: String) -> Dictionary:
	return units.get(unit_id, {}) as Dictionary
