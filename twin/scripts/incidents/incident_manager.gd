extends Node3D

## Root of everything incident-related in the city scene. Owns the shared
## services (static obstacle index, bridge command link, responder factory and
## the walkability rules every responder obeys) and the presenters that react
## to the bridge state CityLife applies each packet.

signal incident_event(event: Dictionary)

const WorldObstacleIndex := preload("res://scripts/incidents/world_obstacle_index.gd")
const BridgeLink := preload("res://scripts/incidents/bridge_link.gd")
const ResponderAgent := preload("res://scripts/incidents/responder_agent.gd")
const ResponderOutfits := preload("res://scripts/incidents/responder_outfits.gd")
const RoadGeometry := preload("res://scripts/incidents/road_geometry.gd")
const EmergencyFleetPresenter := preload("res://scripts/incidents/emergency_fleet_presenter.gd")
const IncidentScenePresenter := preload("res://scripts/incidents/incident_scene_presenter.gd")
const IncidentReplay := preload("res://scripts/incidents/incident_replay.gd")
const GarbageBins := preload("res://scripts/incidents/garbage_bins.gd")
const ZoneRoadblocks := preload("res://scripts/incidents/zone_roadblocks.gd")
const PEOPLE_LAYER := 4
const GridPlanner := preload("res://scripts/incidents/grid_planner.gd")
const HEARTBEAT_SECONDS := 2.0
const ROAD_CENTRES := [-380.0, -285.0, -190.0, -95.0, 0.0, 95.0, 190.0, 285.0, 380.0]
const RESPONDER_RADIUS := 0.34

var obstacles := WorldObstacleIndex.new()
var link := BridgeLink.new()
var life: Node
var npc_ai: Node
var fleet_presenter: Node
var scene_presenter: Node
var replay: Node3D
var garbage_bins: Node3D
## Barriers at the entrances of a zone closed after an earthquake or a flood.
var roadblocks: Node3D
var crew_container: Node3D
var last_state: Dictionary = {}
## Road areas responders may enter (closed lanes at incident scenes).
var open_road_areas: Array[Rect2] = []
## Oriented safe zones (around wrecks inside a junction).
var open_road_boxes: Array = []
var _responders: Array = []
var _spawn_counter := 0
var _ready_done := false
var _last_event_seq := 0
var _vehicle_box_cache: Array = []
var _vehicle_box_frame := -1
var _heartbeat_elapsed := 0.0
var _warm_models: Array[Resource] = []


func _ready() -> void:
	name = "Incidents"
	life = get_parent().get_node_or_null("CityLife")
	npc_ai = get_parent().get_node_or_null("NpcAi")
	crew_container = Node3D.new()
	crew_container.name = "ResponderCrews"
	add_child(crew_container)
	link.open()
	fleet_presenter = EmergencyFleetPresenter.new()
	fleet_presenter.name = "EmergencyFleet"
	add_child(fleet_presenter)
	scene_presenter = IncidentScenePresenter.new()
	scene_presenter.name = "IncidentScenes"
	add_child(scene_presenter)
	garbage_bins = GarbageBins.new()
	add_child(garbage_bins)
	roadblocks = ZoneRoadblocks.new()
	add_child(roadblocks)
	roadblocks.call("initialize", self)
	replay = IncidentReplay.new()
	add_child(replay)
	replay.call("setup", life)
	# NpcAi places citizen job props during its ready pass and SmartCityAssets
	# finishes device siting deferred; index the world after both.
	call_deferred("_late_ready")


func _process(delta: float) -> void:
	# The bridge only falls back to its own timeouts for Godot-owned phases
	# (crew boarding, depot driveways) when this heartbeat stops.
	_heartbeat_elapsed += delta
	if _heartbeat_elapsed >= HEARTBEAT_SECONDS:
		_heartbeat_elapsed = 0.0
		link.send_unreliable({"action": "heartbeat"})


func _late_ready() -> void:
	var central_trees: Array = []
	var central_buildings := get_parent().get_node_or_null("Buildings")
	if central_buildings != null:
		central_trees = central_buildings.find_children("Tree_*", "Node3D", true, false)
	obstacles.build(get_tree(), central_trees)
	if life != null and life.has_signal("sumo_state_applied"):
		life.connect("sumo_state_applied", _on_sumo_state)
	fleet_presenter.call("initialize", self)
	scene_presenter.call("initialize", self)
	_assign_people_layers()
	IncidentReplay.exclude_replay_layer(get_tree())
	_ready_done = true
	_prewarm_people()


func _prewarm_people() -> void:
	## Each crew avatar's first spawn loads its Rocketbox model and builds
	## its animation library (~0.1 s). Done once here, a frame at a time, it
	## no longer stalls the frame in which a crash dispatches its units.
	# Every role in every model it uses (uniform gear is built per role),
	# plus the civilian drivers.
	var jobs: Array = []
	for role in ResponderOutfits.MODELS_BY_ROLE:
		for index in (ResponderOutfits.MODELS_BY_ROLE[role] as Array).size():
			jobs.append([ResponderOutfits.model_path(str(role), index), str(role)])
	for model in IncidentScenePresenter.CIVILIAN_MODELS:
		jobs.append(["res://assets/rocketbox/%s/Export/%s.fbx" % [model, model], "civilian"])
	var holder := Node3D.new()
	holder.name = "PeoplePrewarm"
	holder.visible = false
	add_child(holder)
	for job in jobs:
		await get_tree().process_frame
		# Held for the session: a model nobody references is unloaded again
		# and the next spawn would pay the full load once more.
		_warm_models.append(load(str(job[0])))
		var agent := ResponderAgent.new()
		holder.add_child(agent)
		if agent.setup(holder, str(job[0]), str(job[1]), "prewarm", Vector3(0.0, -50.0, 0.0), 0.0):
			agent.dispose()
		else:
			agent.queue_free()
	holder.queue_free()


func _assign_people_layers() -> void:
	## Citizens (and what they hold) render on the live-people layer so the
	## replay camera, which shows recorded traffic only, never draws them.
	for group_name in ["pedestrians", "backdrop_pedestrians"]:
		for value in get_tree().get_nodes_in_group(group_name):
			for visual in (value as Node).find_children("*", "VisualInstance3D", true, false):
				(visual as VisualInstance3D).layers = PEOPLE_LAYER


func _on_sumo_state(state: Dictionary) -> void:
	last_state = state
	for completed in link.process(state):
		fleet_presenter.call("on_request_completed", completed)
	for event_value in state.get("events", []):
		var event := event_value as Dictionary
		var sequence := int(event.get("seq", 0))
		if sequence > _last_event_seq:
			_last_event_seq = sequence
			scene_presenter.call("on_event", event)
			replay.call("on_event", event)
			incident_event.emit(event)
	fleet_presenter.call("on_state", state)
	scene_presenter.call("on_state", state)
	roadblocks.call("on_state", state)


# --- Responders -----------------------------------------------------------------

func spawn_responder(role: String, position_value: Vector3, yaw: float) -> Node:
	_spawn_counter += 1
	var agent := ResponderAgent.new()
	crew_container.add_child(agent)
	if not agent.setup(crew_container, ResponderOutfits.model_path(role, _spawn_counter), role, "%s-%d" % [role, _spawn_counter], position_value, yaw):
		agent.queue_free()
		return null
	agent.dynamic_blocker = _responder_blocked
	agent.planner = plan_walk
	_responders.append(agent)
	if life != null:
		# Responders render on the live-people layer like citizens.
		for value in agent.body.find_children("*", "VisualInstance3D", true, false):
			(value as VisualInstance3D).layers = 4
	return agent


func spawn_civilian(model_path: String, position_value: Vector3, yaw: float) -> Node:
	## An ordinary person at an incident (a driver who stepped out): same
	## avatar contract and walk rules as responders, no uniform.
	_spawn_counter += 1
	var agent := ResponderAgent.new()
	crew_container.add_child(agent)
	if not agent.setup(crew_container, model_path, "civilian", "civilian-%d" % _spawn_counter, position_value, yaw):
		agent.queue_free()
		return null
	agent.dynamic_blocker = _responder_blocked
	agent.planner = plan_walk
	agent.speed = 1.3
	_responders.append(agent)
	for value in agent.body.find_children("*", "VisualInstance3D", true, false):
		(value as VisualInstance3D).layers = 4
	return agent


func dispose_responder(agent: Node) -> void:
	_responders.erase(agent)
	if is_instance_valid(agent):
		agent.call("dispose")


func responder_count() -> int:
	return _responders.size()


# --- Walkability: the rules every responder obeys --------------------------------

func in_carriageway(point: Vector3) -> bool:
	if absf(point.x) > 386.0 or absf(point.z) > 386.0:
		return false
	for centre in ROAD_CENTRES:
		if absf(point.x - centre) < RoadGeometry.KERB_OFFSET or absf(point.z - centre) < RoadGeometry.KERB_OFFSET:
			return true
	return false


func road_allowed(point: Vector3) -> bool:
	for area in open_road_areas:
		if area.has_point(Vector2(point.x, point.z)):
			return true
	for box in open_road_boxes:
		if point_in_box(point, box, 0.0):
			return true
	return false


func vehicle_boxes() -> Array:
	## Oriented footprints of every rendered road vehicle (cached per frame).
	var frame := Engine.get_physics_frames()
	if frame == _vehicle_box_frame:
		return _vehicle_box_cache
	_vehicle_box_frame = frame
	_vehicle_box_cache = []
	if life == null:
		return _vehicle_box_cache
	for render_value in (life.get("_sumo_nodes_by_id") as Dictionary).values():
		var render_state := render_value as Dictionary
		var node := render_state.node as Node3D
		if not is_instance_valid(node) or not node.visible:
			continue
		var spec := life.call("class_spec", str(render_state.cls)) as Dictionary
		var yaw := node.rotation.y
		var centre := node.global_position + Basis(Vector3.UP, yaw) * (render_state.centre_offset as Vector3)
		_vehicle_box_cache.append({"centre": centre, "yaw": yaw, "length": float(spec.get("length", 4.7)), "width": float(spec.get("width", 2.1)), "node": node})
	return _vehicle_box_cache


static func point_in_box(point: Vector3, box: Dictionary, margin: float) -> bool:
	var yaw := float(box.yaw)
	var local := point - (box.centre as Vector3)
	var forward := Vector3(sin(yaw), 0.0, cos(yaw))
	var side := Vector3(cos(yaw), 0.0, -sin(yaw))
	return absf(local.dot(forward)) <= float(box.length) * 0.5 + margin and absf(local.dot(side)) <= float(box.width) * 0.5 + margin


func plan_walk(from: Vector3, goal: Vector3, avoid: Array = [], extra_open: Array = []) -> PackedVector3Array:
	## avoid: points held by something that moves (a citizen standing in the
	## way): planned around with a 1 m clearance. extra_open: carriageway areas
	## to treat as walkable (asking "would an all-red hold open a path?").
	return GridPlanner.plan(from, goal, func(point: Vector3) -> bool:
		for spot in avoid:
			if Vector2(point.x - (spot as Vector3).x, point.z - (spot as Vector3).z).length() < 1.0:
				return true
		for area in extra_open:
			if (area as Rect2).has_point(Vector2(point.x, point.z)) and not obstacles.point_blocked(point, RESPONDER_RADIUS):
				var in_vehicle := false
				for box in vehicle_boxes():
					if (box.centre as Vector3).distance_to(point) < 8.0 and point_in_box(point, box, RESPONDER_RADIUS + 0.12):
						in_vehicle = true
						break
				if not in_vehicle:
					return false
		return walk_blocked(point))


func walk_blocked(point: Vector3, ignore_vehicle: Node3D = null) -> bool:
	## Static planning predicate: buildings/props/posts, open traffic lanes and
	## parked/stopped vehicle bodies (inflated by a body radius).
	if obstacles.point_blocked(point, RESPONDER_RADIUS):
		return true
	if in_carriageway(point) and not road_allowed(point):
		return true
	for box in vehicle_boxes():
		if (box.node as Node3D) != ignore_vehicle and (box.centre as Vector3).distance_to(point) < 8.0 and point_in_box(point, box, RESPONDER_RADIUS + 0.12):
			return true
	return false


func _responder_blocked(agent: Node, candidate: Vector3) -> bool:
	## Dynamic step check: never step into a vehicle, a colleague or a citizen.
	var current: Vector3 = agent.call("position")
	for box in vehicle_boxes():
		if (box.centre as Vector3).distance_to(candidate) < 8.0 and point_in_box(candidate, box, RESPONDER_RADIUS):
			if not point_in_box(current, box, RESPONDER_RADIUS):
				return true
	for other in _responders:
		if other == agent or not is_instance_valid(other):
			continue
		var other_position: Vector3 = other.call("position")
		var distance := Vector2(candidate.x - other_position.x, candidate.z - other_position.z).length()
		if distance < 0.72 and distance < Vector2(current.x - other_position.x, current.z - other_position.z).length():
			return true
	for value in get_tree().get_nodes_in_group("pedestrians"):
		var person := value as Node3D
		if person == null or not person.visible:
			continue
		var distance := Vector2(candidate.x - person.global_position.x, candidate.z - person.global_position.z).length()
		if distance < 0.9 and distance < Vector2(current.x - person.global_position.x, current.z - person.global_position.z).length():
			return true
	return false
