extends Node

## Presentation of active incidents: crews, civilian drivers, damage, debris,
## fire/water effects, scene props and the tow winch. Everything here reacts
## to the bridge's authoritative incident state; it never moves a road
## vehicle except a wreck in Godot custody (being winched or carried).
##
## Each responder runs a short program of steps (walk / perform / loop /
## wait / board). Walking always uses the manager's planner and walk rules,
## so programs cannot send anyone through a vehicle, a building or open
## traffic.

const DoorTransit := preload("res://scripts/incidents/vehicle_door_transit.gd")
const SceneLayout := preload("res://scripts/incidents/scene_layout.gd")
const IncidentFx := preload("res://scripts/incidents/incident_fx.gd")
const BinWeight := preload("res://scripts/props/bin_weight.gd")
const VehicleDamage := preload("res://scripts/incidents/vehicle_damage.gd")
const RoadGeometry := preload("res://scripts/incidents/road_geometry.gd")
const CONE_SCENE := preload("res://assets/kenney/car_kit/cone.glb")
const DEBRIS_SCENES := [
	preload("res://assets/kenney/car_kit/debris-bumper.glb"), preload("res://assets/kenney/car_kit/debris-plate-a.glb"),
	preload("res://assets/kenney/car_kit/debris-plate-small-b.glb"), preload("res://assets/kenney/car_kit/debris-spoiler-a.glb"),
	preload("res://assets/kenney/car_kit/debris-bolt.glb"), preload("res://assets/kenney/car_kit/debris-door-window.glb"),
]
const CARGO_SCENE := preload("res://assets/kenney/car_kit/box.glb")
const CIVILIAN_MODELS := ["Male_Adult_01", "Female_Adult_01", "Male_Adult_05", "Female_Adult_05", "Business_Male_01", "Business_Female_01"]
const CREW_ROLES := {
	"traffic_control": ["police"], "investigation": ["police"], "medical": ["paramedic", "paramedic"],
	"medical_standby": ["paramedic"], "fire_suppression": ["firefighter", "firefighter", "firefighter"],
	"fire_attack": ["firefighter", "firefighter"], "fire_support": ["firefighter"], "fire_safety": ["firefighter"],
	"recovery": ["tow_operator"], "repair": ["utility_worker", "utility_worker"], "support": ["police"],
	"collection": ["sanitation_worker", "sanitation_worker"],
}
const GarbageBins := preload("res://scripts/incidents/garbage_bins.gd")
## Collection: the loader wheels the container at this pace, holding it this
## far in front of the body; the lifter cycle (raise, tip, shake, lower).
const WHEEL_SPEED := 0.85
const WHEEL_REACH := 1.0
const TIP_SECONDS := 7.5
const WINCH_SECONDS := 7.0
const BOARD_GIVE_UP_SECONDS := 60.0

var manager: Node
var life: Node
var scenes: Dictionary = {}
var _cargo: Dictionary = {}
# unit id -> time a crew_aboard report was last sent for a departing unit.
var _aboard_sent: Dictionary = {}
var _impound: Array = []
var _elapsed := 0.0


func initialize(owner_manager: Node) -> void:
	manager = owner_manager
	life = manager.life
	call_deferred("_prewarm_effects")


func _prewarm_effects() -> void:
	## Every incident effect is drawn once at start-up, just under the street
	## in front of the camera (in view, hidden by the ground): its shaders and
	## particle programs compile during loading instead of stalling the first
	## frames of a crash, where the stall made the impact visibly stutter.
	var camera := get_viewport().get_camera_3d()
	if camera == null:
		return
	var forward := -camera.global_transform.basis.z
	var spot := camera.global_position + forward * 30.0
	spot.y = -4.0
	var warm := Node3D.new()
	warm.name = "EffectPrewarm"
	add_child(warm)
	var effects: Array[Node3D] = [IncidentFx.impact_burst(Vector3(1, 0, 0), 8.0), IncidentFx.fire(2.0, 2.0, 2.0), IncidentFx.engine_smoke(), IncidentFx.engine_smoke(true), IncidentFx.hose_stream(), IncidentFx.geyser(), IncidentFx.puddle(1.5)]
	for scene_value in DEBRIS_SCENES + [CARGO_SCENE, CONE_SCENE]:
		effects.append((scene_value as PackedScene).instantiate() as Node3D)
	for effect in effects:
		warm.add_child(effect)
		effect.global_position = spot
		if effect.has_method("set_intensity"):
			effect.call("set_intensity", 1.0)
	# Kept (hidden, paused) rather than freed: freeing them would unload the
	# shared shaders and textures and the first crash would rebuild them.
	get_tree().create_timer(2.0).timeout.connect(func() -> void:
		warm.visible = false
		warm.process_mode = Node.PROCESS_MODE_DISABLED)


# --- Bridge state -----------------------------------------------------------------

func on_event(event: Dictionary) -> void:
	match str(event.get("kind", "")):
		"impact":
			_on_impact(event)


func on_state(state: Dictionary) -> void:
	var units_by_id: Dictionary = {}
	for record_value in state.get("fleet", []):
		units_by_id[str((record_value as Dictionary).unit)] = record_value
	var active: Dictionary = {}
	for record_value in state.get("incidents", []):
		var record := record_value as Dictionary
		var incident_id := str(record.get("id", ""))
		active[incident_id] = true
		if not scenes.has(incident_id):
			scenes[incident_id] = _new_scene(record)
		var scene := scenes[incident_id] as Dictionary
		scene.record = record
		_update_scene(scene, units_by_id)
	for incident_id in scenes.keys():
		if not active.has(incident_id):
			_close_scene(scenes[incident_id] as Dictionary)
	# A released unit waits at the scene until its whole crew is aboard.
	for unit_id in units_by_id:
		if str((units_by_id[unit_id] as Dictionary).get("status", "")) != "departing":
			_aboard_sent.erase(unit_id)
		elif not _crew_out(str(unit_id)) and _elapsed >= float(_aboard_sent.get(unit_id, -INF)) + 2.0:
			_aboard_sent[unit_id] = _elapsed
			manager.link.send({"action": "crew_aboard", "unit": str(unit_id)})
	for handoff_value in state.get("handoffs", []):
		var handoff := handoff_value as Dictionary
		if str(handoff.get("reason", "")) == "towed":
			_on_towed(handoff)
		elif str(handoff.get("reason", "")) == "depot_entry" and not str(handoff.get("cargo", "")).is_empty():
			_cargo_arriving(str(handoff.get("unit", "")), str(handoff.get("cargo", "")))
		elif str(handoff.get("reason", "")) == "left_city" and not str(handoff.get("cargo", "")).is_empty():
			_cargo_left_city(str(handoff.get("cargo", "")))
	_refresh_walk_areas()


func _new_scene(record: Dictionary) -> Dictionary:
	var root := Node3D.new()
	root.name = "Scene_%s" % str(record.get("id", "")).replace("-", "_")
	add_child(root)
	return {
		"id": str(record.get("id", "")), "record": record, "root": root, "crews": {}, "drivers": {},
		"props": [], "fx": {}, "cones_built": false, "closing": false, "acked": {}, "loading": {},
		"layout_vehicles": [], "safe_rects": [], "safe_boxes": [], "opened_at": _elapsed,
	}


# --- Per-frame scene logic -------------------------------------------------------------

func _update_scene(scene: Dictionary, units_by_id: Dictionary) -> void:
	var record := scene.record as Dictionary
	var on_scene: Array = []
	for unit_value in record.get("units", []):
		var unit := unit_value as Dictionary
		if str(unit.get("status", "")) in ["on_scene", "departing"]:
			on_scene.append(str(unit.unit))
	scene.layout_vehicles = SceneLayout.scene_vehicles(life, record, on_scene)
	_update_safe_zones(scene)
	_keep_posts_upstream(scene)
	_update_participants(scene)
	_update_effects(scene)
	for unit_value in record.get("units", []):
		var unit := unit_value as Dictionary
		var unit_id := str(unit.unit)
		var crews := scene.crews as Dictionary
		if str(unit.status) == "on_scene" and crews.has(unit_id) and bool((crews[unit_id] as Dictionary).get("boarding", false)) and _all_boarded(crews[unit_id] as Dictionary):
			# A collection truck at its next stop: the crew that boarded at the
			# last one gets out again.
			crews.erase(unit_id)
		if str(unit.status) == "on_scene" and not crews.has(unit_id) and _unit_in_place(unit_id):
			_deploy_crew(scene, unit_id, str(unit.role), str(unit.kind))
		elif crews.has(unit_id) and str(unit.status) not in ["on_scene"] and not bool((crews[unit_id] as Dictionary).get("boarding", false)):
			# The unit has been released: its crew boards before it moves away.
			_board_crew(scene, unit_id)
	_build_cones(scene)


func _all_boarded(crew: Dictionary) -> bool:
	for agent_state_value in crew.agents:
		var agent_state := agent_state_value as Dictionary
		if not bool(agent_state.get("boarded", false)) and is_instance_valid(agent_state.get("agent")):
			return false
	return true


func _update_safe_zones(scene: Dictionary) -> void:
	var record := scene.record as Dictionary
	var location := record.get("location", {}) as Dictionary
	var vehicles := scene.layout_vehicles as Array
	var rects: Array[Rect2] = []
	var boxes: Array = []
	var junction := str(location.get("junction", ""))
	var junction_scene := not junction.is_empty() and str(record.get("kind", "")) in ["red_light_tbone", "secondary_collision", "signal_failure"]
	if junction_scene:
		for vehicle in vehicles:
			if str((vehicle as Dictionary).kind) == "wreck":
				boxes.append(SceneLayout.wreck_zone_box(vehicle))
		var control := (record.get("scene", {}) as Dictionary).get("junction_control", {}) as Dictionary
		if str(control.get("phase", "")) == "hold_all_red":
			rects.append(SceneLayout.junction_rect(junction))
		# Units wait on the approaches: each gets its door strip and the kerb
		# band from it to the junction corner, so crews can get out and walk.
		var roads := _unit_roads()
		for vehicle_value in vehicles:
			var vehicle := vehicle_value as Dictionary
			if str(vehicle.kind) != "unit" or not roads.has(str(vehicle.key)):
				continue
			var edge := SceneLayout.frame_of(str(roads[str(vehicle.key)]))
			if edge.is_empty():
				continue
			rects.append_array(SceneLayout.midblock_safe_rects(edge, [vehicle]))
			var span := SceneLayout.vehicle_span(edge, vehicle)
			rects.append(SceneLayout.frame_rect(edge, span.x, RoadGeometry.LANE_LENGTH + 2.0, SceneLayout.KERB_INNER, SceneLayout.KERB_OUTER + 0.2))
	if not str(location.get("edge", "")).is_empty() and not vehicles.is_empty():
		var edge := SceneLayout.frame_of(str(location.edge))
		if not edge.is_empty():
			var same_edge: Array = []
			for vehicle in vehicles:
				var frame := SceneLayout.to_frame(edge, (vehicle as Dictionary).centre as Vector3)
				if frame.y > 0.0 and frame.y < RoadGeometry.LANE_WIDTH + 0.5 and frame.x > -4.0 and frame.x < RoadGeometry.LANE_LENGTH + 4.0:
					same_edge.append(vehicle)
			rects.append_array(SceneLayout.midblock_safe_rects(edge, same_edge))
	if str(record.get("kind", "")) == "water_main_break" and location.has("x"):
		var edge := SceneLayout.frame_of(str(location.get("edge", "")))
		if not edge.is_empty():
			var s := float(location.get("pos", 30.0))
			rects.append(SceneLayout.frame_rect(edge, s - 10.0, s + 6.0, SceneLayout.KERB_INNER, SceneLayout.KERB_OUTER + 0.2))
	scene.safe_rects = rects
	scene.safe_boxes = boxes


func _refresh_walk_areas() -> void:
	var rects: Array[Rect2] = []
	var boxes: Array = []
	for scene_value in scenes.values():
		var scene := scene_value as Dictionary
		rects.append_array(scene.safe_rects as Array)
		boxes.append_array(scene.safe_boxes as Array)
	manager.open_road_areas = rects
	manager.open_road_boxes = boxes


func _update_participants(scene: Dictionary) -> void:
	var record := scene.record as Dictionary
	var drivers := scene.drivers as Dictionary
	for participant_value in record.get("participants", []):
		var participant := participant_value as Dictionary
		var vehicle_id := str(participant.get("id", ""))
		var status := str(participant.get("status", ""))
		var node := life.call("get_keyed_vehicle", vehicle_id) as Node3D
		if node == null:
			continue
		# Damage follows the bridge record (zone + level) and is applied once.
		var level := float(participant.get("level", 0.0))
		if level > 0.05 and str(participant.get("zone", "")) != "" and float(node.get_meta("damage_level", 0.0)) + 0.05 < level and str(record.kind) in ["rear_end", "red_light_tbone", "unsafe_overtake", "secondary_collision"]:
			VehicleDamage.apply_crumple(node, str(participant.zone), level, hash(vehicle_id))
		# Civilian drivers step out once the vehicle is at rest (crashes and
		# breakdowns), unless injured: they stay seated for the paramedics.
		if status in ["wreck", "loading"] and not drivers.has(vehicle_id) and _elapsed - float(scene.opened_at) > 2.5 and not bool(participant.get("injured", false)):
			_spawn_driver(scene, vehicle_id, node)
		if status == "driver_returning" and not (scene.acked as Dictionary).has("driver_%s" % vehicle_id):
			_return_driver(scene, vehicle_id)
		if status == "loading" and not (scene.loading as Dictionary).has(vehicle_id):
			_begin_winch(scene, vehicle_id, str(participant.get("tow", "")))


func _update_effects(scene: Dictionary) -> void:
	var record := scene.record as Dictionary
	var kind := str(record.get("kind", ""))
	var fx := scene.fx as Dictionary
	var scene_state := record.get("scene", {}) as Dictionary
	var fire := scene_state.get("fire", {}) as Dictionary
	if kind in ["vehicle_fire", "breakdown", "lost_load"]:
		var participants := record.get("participants", []) as Array
		if participants.is_empty():
			return
		var vehicle_id := str((participants[0] as Dictionary).get("id", ""))
		var node := life.call("get_keyed_vehicle", vehicle_id) as Node3D
		if node == null:
			return
		var pose := SceneLayout.vehicle_pose(life, vehicle_id)
		var engine_bay := SceneLayout.end_point(pose, true, -1.1) + Vector3(0, 1.0, 0) if not pose.is_empty() else node.global_position
		if not fx.has("engine"):
			var smoke := IncidentFx.engine_smoke(false)
			(scene.root as Node3D).add_child(smoke)
			fx["engine"] = smoke
		var intensity := float(fire.get("intensity", 0.0))
		(fx.engine as Node3D).global_position = engine_bay
		fx.engine.call("set_intensity", 0.0 if intensity > 0.2 else (0.8 if kind != "lost_load" else 0.0))
		if kind == "vehicle_fire":
			if not fx.has("fire"):
				var flames := IncidentFx.fire(1.7, 1.6, 1.4)
				(scene.root as Node3D).add_child(flames)
				fx["fire"] = flames
			(fx.fire as Node3D).global_position = engine_bay
			fx.fire.call("set_intensity", intensity)
			if intensity > 0.05:
				VehicleDamage.scorch(node, clampf(intensity * 1.2, 0.0, 0.85))
			if bool(scene_state.get("extinguished", false)) and not fx.has("steam"):
				var steam := IncidentFx.engine_smoke(true)
				(scene.root as Node3D).add_child(steam)
				steam.global_position = engine_bay
				steam.set_intensity(0.9)
				fx["steam"] = steam
		if kind == "lost_load" and not bool(scene.get("debris_built", false)) and not pose.is_empty():
			_scatter_cargo(scene, pose, int(scene_state.get("debris_seed", 7)))
	elif kind == "building_fire":
		_update_building_fire(scene, fire)
	elif kind == "water_main_break":
		var water := scene_state.get("water", {}) as Dictionary
		var location := record.get("location", {}) as Dictionary
		if not fx.has("geyser") and location.has("x"):
			var geyser := IncidentFx.geyser()
			(scene.root as Node3D).add_child(geyser)
			geyser.global_position = Vector3(float(location.x), 0.17, float(location.z))
			var puddle := IncidentFx.puddle(1.0)
			(scene.root as Node3D).add_child(puddle)
			puddle.global_position = Vector3(float(location.x), 0.176, float(location.z))
			fx["geyser"] = geyser
			fx["puddle"] = puddle
		if fx.has("geyser"):
			var flow := float(water.get("flow", 1.0))
			fx.geyser.call("set_intensity", flow)
			# The puddle spreads while water flows, up to the kerb lane.
			var puddle := fx.puddle as MeshInstance3D
			var target_radius := clampf(1.0 + (_elapsed - float(scene.opened_at)) * 0.06, 1.0, 4.2)
			puddle.scale = Vector3.ONE * lerpf(puddle.scale.x, target_radius, 0.02)
	# Hoses: on whenever the bridge reports water on the fire.
	for crew_value in (scene.crews as Dictionary).values():
		var crew := crew_value as Dictionary
		for agent_state in crew.get("agents", []):
			if (agent_state as Dictionary).has("hose"):
				var hose := (agent_state as Dictionary).hose as Node3D
				var water_on := bool(fire.get("water", false)) or bool(scene_state.get("extinguished", false)) and float(fire.get("intensity", 0.0)) > 0.0
				hose.call("set_intensity", 1.0 if water_on and bool((agent_state as Dictionary).get("at_post", false)) else 0.0)


# --- Impact ----------------------------------------------------------------------------

func _on_impact(event: Dictionary) -> void:
	var point_value := event.get("point", [0.0, 0.0]) as Array
	var point := Vector3(float(point_value[0]), 0.9, float(point_value[1]))
	var energy := float(event.get("delta_v", 4.0))
	var incident_id := str(event.get("incident", ""))
	var parent: Node3D = (scenes[incident_id] as Dictionary).root if scenes.has(incident_id) else self as Node
	var burst := IncidentFx.impact_burst(Vector3(1, 0, 0), energy)
	add_child(burst)
	burst.global_position = point
	get_tree().create_timer(8.0).timeout.connect(burst.queue_free)
	# Debris pieces settle on the asphalt around the contact point.
	var rng := RandomNumberGenerator.new()
	rng.seed = hash(incident_id)
	var count := clampi(int(energy * 1.4), 3, 12)
	for index in count:
		var piece := (DEBRIS_SCENES[rng.randi() % DEBRIS_SCENES.size()] as PackedScene).instantiate() as Node3D
		var holder := scenes[incident_id] as Dictionary if scenes.has(incident_id) else {}
		if holder.is_empty():
			add_child(piece)
		else:
			(holder.root as Node3D).add_child(piece)
			(holder.props as Array).append(piece)
		var radius := rng.randf_range(0.6, 2.2 + energy * 0.2)
		var angle := rng.randf() * TAU
		piece.scale = Vector3.ONE * rng.randf_range(0.55, 0.9)
		piece.global_position = Vector3(point.x + cos(angle) * radius, 0.175, point.z + sin(angle) * radius)
		piece.rotation = Vector3(0.0, rng.randf() * TAU, 0.0)
		for value in piece.find_children("*", "VisualInstance3D", true, false):
			(value as VisualInstance3D).layers = IncidentFx.FX_LAYER


# --- Crews ---------------------------------------------------------------------------------

func _unit_in_place(unit_id: String) -> bool:
	## On the road with a live SUMO pose, or staged in its station bay.
	if bool(life.call("keyed_vehicle_has_packet", unit_id)):
		return true
	var view := manager.fleet_presenter.call("get_unit_view", unit_id) as Dictionary
	return str(view.get("phase", "")) == "parked" and bool(life.call("has_keyed_vehicle", unit_id))


func _walkable_near(goal: Vector3) -> Vector3:
	if not bool(manager.call("walk_blocked", goal)):
		return goal
	for radius in [0.6, 1.0, 1.5, 2.0, 2.5]:
		for step in 12:
			var angle := TAU * float(step) / 12.0
			var candidate := goal + Vector3(cos(angle), 0.0, sin(angle)) * float(radius)
			if not bool(manager.call("walk_blocked", candidate)):
				return candidate
	return goal


func _crew_out(unit_id: String) -> bool:
	## Anyone still to board this unit: its own crew, or a wreck's driver
	## riding along with the tow.
	for scene_value in scenes.values():
		var scene := scene_value as Dictionary
		var crews := scene.crews as Dictionary
		if crews.has(unit_id):
			for agent_state_value in (crews[unit_id] as Dictionary).agents:
				var agent_state := agent_state_value as Dictionary
				if not bool(agent_state.get("boarded", false)) and is_instance_valid(agent_state.get("agent")):
					return true
		for driver_value in (scene.drivers as Dictionary).values():
			var driver := driver_value as Dictionary
			var program := driver.get("program", []) as Array
			var step := int(driver.get("step", 0))
			if not bool(driver.get("boarded", false)) and is_instance_valid(driver.get("agent")) and step < program.size() and str((program[step] as Dictionary).get("board", "")) == unit_id:
				return true
	return false


func _kerb_side(scene: Dictionary, pose: Dictionary) -> float:
	## Door side (+1 vehicle left, -1 right) facing the kerb of the road the
	## vehicle stands on (a unit on a junction approach is not on the scene
	## edge).
	var road := str(_unit_roads().get(str(pose.get("key", "")), ((scene.record as Dictionary).get("location", {}) as Dictionary).get("edge", "")))
	var edge := SceneLayout.frame_of(road)
	if edge.is_empty():
		return -1.0
	var left := SceneLayout.to_frame(edge, SceneLayout.door_point(pose, 1.0)).y
	var right := SceneLayout.to_frame(edge, SceneLayout.door_point(pose, -1.0)).y
	return 1.0 if left > right else -1.0


func _deploy_crew(scene: Dictionary, unit_id: String, role: String, kind: String) -> void:
	var roles := CREW_ROLES.get(role, ["police"]) as Array
	var unit_pose := SceneLayout.vehicle_pose(life, unit_id)
	if unit_pose.is_empty():
		return
	var agents: Array = []
	var kerb := _kerb_side(scene, unit_pose)
	var vehicle := life.call("get_keyed_vehicle", unit_id) as Node3D
	var per_door := DoorTransit.occupants_per_door(vehicle, kerb)
	for index in roles.size():
		# Crew climb out on the kerb side, never into the open lane, through
		# the kerb-side doors (front, then rear), one after another.
		var side := kerb
		var slot := DoorTransit.slot_for(vehicle, index, side)
		var start := SceneLayout.door_point(unit_pose, side, 0.55 - 0.25 * float(index / 2)) if slot.is_empty() else slot.outside as Vector3
		var agent := manager.call("spawn_responder", str(roles[index]), start, float(unit_pose.yaw)) as Node
		if agent == null:
			continue
		var agent_state := {"agent": agent, "role": role, "index": index, "unit": unit_id, "program": [], "step": 0, "step_started": _elapsed, "door_side": side}
		agent_state.program = _program_for(scene, role, index, unit_pose, agent_state)
		agent.connect("arrived", _on_agent_arrived.bind(agent_state))
		agents.append(agent_state)
		if slot.is_empty():
			_start_step(agent_state)
		else:
			_alight_then_start(vehicle, agent_state, slot, 0.3 * float(index % per_door) + 1.2 * float(index / per_door))
	(scene.crews as Dictionary)[unit_id] = {"agents": agents, "kind": kind, "boarding": false}


func _traffic_post_program(edge: Dictionary, post: Vector3) -> Array:
	var upstream_face := post - (edge.direction as Vector3) * 10.0 if not edge.is_empty() else post
	return [{"walk": post, "traffic_post": true}, {"loop": ["Wave", "PresentRight", "Moderate"], "face": upstream_face, "period": 4.5}]


func _unit_roads() -> Dictionary:
	## unit id -> the SUMO road it currently stands on.
	var roads := {}
	for vehicle_value in (manager.get("last_state") as Dictionary).get("vehicles", []):
		var vehicle := vehicle_value as Dictionary
		var unit := str(vehicle.get("unit", ""))
		if not unit.is_empty() and not str(vehicle.get("road", "")).begins_with(":"):
			roads[unit] = str(vehicle.road)
	return roads


func _keep_posts_upstream(scene: Dictionary) -> void:
	## The officer directing traffic stands upstream of the whole block: when
	## another unit parks behind it, the post moves back (it would otherwise
	## stand at that unit's door and block its crew).
	if _elapsed < float(scene.get("next_post_check", 0.0)):
		return
	scene.next_post_check = _elapsed + 2.0
	var location := (scene.record as Dictionary).get("location", {}) as Dictionary
	if not str(location.get("junction", "")).is_empty():
		return
	var edge := SceneLayout.frame_of(str(location.get("edge", "")))
	var vehicles := scene.layout_vehicles as Array
	if edge.is_empty() or vehicles.is_empty():
		return
	var desired := SceneLayout.kerb_post(edge, vehicles, true)
	for crew_value in (scene.crews as Dictionary).values():
		if bool((crew_value as Dictionary).get("boarding", false)):
			continue
		for agent_state_value in (crew_value as Dictionary).agents:
			var agent_state := agent_state_value as Dictionary
			var program := agent_state.program as Array
			if program.is_empty() or not bool((program[0] as Dictionary).get("traffic_post", false)):
				continue
			if ((program[0] as Dictionary).walk as Vector3).distance_to(desired) <= 2.5:
				continue
			agent_state.program = _traffic_post_program(edge, desired)
			agent_state.step = 0
			_start_step(agent_state)


func _program_for(scene: Dictionary, role: String, index: int, unit_pose: Dictionary, agent_state: Dictionary) -> Array:
	var record := scene.record as Dictionary
	var vehicles := scene.layout_vehicles as Array
	var wrecks: Array = vehicles.filter(func(vehicle: Dictionary) -> bool: return str(vehicle.kind) == "wreck")
	var location := record.get("location", {}) as Dictionary
	var edge := SceneLayout.frame_of(str(location.get("edge", "")))
	var junction := str(location.get("junction", ""))
	var focus: Dictionary = wrecks[0] if not wrecks.is_empty() else unit_pose
	match role:
		"traffic_control", "support":
			var post: Vector3
			if not junction.is_empty() and str(record.kind) in ["red_light_tbone", "signal_failure", "secondary_collision"]:
				post = SceneLayout.junction_corner(junction, unit_pose.centre as Vector3)
				var centre := SceneLayout.junction_rect(junction).get_center()
				return [{"walk": post}, {"loop": ["Wave", "PresentRight", "Moderate", "PresentLeft"], "face": Vector3(centre.x, 0.17, centre.y), "period": 4.5}]
			post = SceneLayout.kerb_post(edge, vehicles, true) if not edge.is_empty() else SceneLayout.door_point(unit_pose, -1.0)
			return _traffic_post_program(edge, post)
		"investigation":
			var kerb_side := SceneLayout.door_point(focus, -1.0, 0.5)
			return [{"walk": kerb_side}, {"loop": ["DocumentsNote", "TalkNeutral", "DocumentsCheck"], "face": focus.centre, "period": 5.0}]
		"medical", "fire_safety":
			# Midblock both work from the kerb side (front and rear doors): the
			# other side faces the open lane. A junction box is fully controlled.
			var kerb := _kerb_side(scene, focus)
			var midblock := junction.is_empty() and not str(location.get("edge", "")).is_empty()
			var door := SceneLayout.door_point(focus, kerb, 0.62 if index == 0 else 0.22) if midblock else SceneLayout.door_point(focus, 1.0 if index == 0 else -1.0, 0.62)
			return [{"walk": door}, {"loop": ["InspectLow", "InspectLow", "WorkMid"] if index == 0 else ["DocumentsNote", "InspectLow"], "face": focus.centre, "period": 6.0}]
		"medical_standby":
			return [{"walk": SceneLayout.end_point(unit_pose, false, 1.4)}, {"loop": ["Idle_Neutral", "TalkNeutral"], "face": unit_pose.centre, "period": 6.0}]
		"fire_suppression", "fire_attack", "fire_support":
			return _fire_program(scene, role, index, unit_pose, focus, agent_state)
		"recovery":
			var front := SceneLayout.end_point(focus, true, 1.0)
			return [{"walk": front}, {"loop": ["InspectLow", "DocumentsCheck"], "face": focus.centre, "period": 5.0}]
		"repair":
			var target := Vector3(float(location.get("x", (unit_pose.centre as Vector3).x)), 0.17, float(location.get("z", (unit_pose.centre as Vector3).z)))
			if str(record.kind) == "signal_failure" and not junction.is_empty():
				target = SceneLayout.junction_corner(junction, (unit_pose.centre as Vector3) + Vector3(2.0, 0, 2.0))
			var work := target + Vector3(0.9 * float(index * 2 - 1), 0.0, 0.0)
			return [{"walk": work}, {"loop": ["InspectLow", "WorkMid", "DocumentsNote"] if index == 0 else ["WorkMid", "WorkTable"], "face": target, "period": 5.5}]
		"collection":
			return _collection_program(scene, index, unit_pose)
	return [{"loop": ["Idle_Neutral"], "face": unit_pose.centre, "period": 6.0}]


func _collection_program(scene: Dictionary, index: int, unit_pose: Dictionary) -> Array:
	## Loader (0): walks behind the container, wheels it out to the truck's
	## tail, the lifter tips it into the hopper, then pulls it back to its
	## place at the kerb. Operator (1): works the lifter controls at the kerb-
	## side rear corner.
	var location := (scene.record as Dictionary).get("location", {}) as Dictionary
	var bin_id := str(location.get("bin", ""))
	var bins := manager.get("garbage_bins") as Node
	# The container is wheeled up square behind the tail, its front 0.15 m
	# from the lifter.
	var hopper := SceneLayout.end_point(unit_pose, false, GarbageBins.DEPTH * 0.5 + 0.15)
	if bins == null or bins.call("bin_node", bin_id) == null:
		return [{"walk": hopper}, {"loop": ["Idle_Neutral"], "face": unit_pose.centre, "period": 6.0}]
	var home := (bins.call("home_of", bin_id) as Transform3D).origin
	var direction := Vector3(hopper.x - home.x, 0.0, hopper.z - home.z).normalized()
	var behind := Vector3(home.x, 0.17, home.z) - direction * WHEEL_REACH
	if index == 0:
		return [
			{"walk": behind}, {"grip_bin": bin_id, "face": home, "seconds": 0.8},
			{"wheel": hopper - direction * WHEEL_REACH, "bin": bin_id},
			# Hooked on: stand clear of the swing while the lifter cycles.
			{"step_to": hopper - direction * (WHEEL_REACH + 0.9), "backwards": true},
			{"tip_bin": bin_id, "face": hopper, "seconds": TIP_SECONDS},
			{"step_to": hopper - direction * WHEEL_REACH},
			{"wheel": behind, "bin": bin_id, "backwards": true},
			{"release_bin": bin_id, "seconds": 0.6}, {"stop_done": bin_id},
			{"loop": ["Idle_Neutral"], "face": unit_pose.centre, "period": 6.0},
		]
	# Beside the body ahead of the tail: clear of the container's path from
	# the kerb to the lifter.
	var controls := SceneLayout.door_point(unit_pose, _kerb_side(scene, unit_pose), 0.14)
	return [{"walk": controls}, {"loop": ["WorkMid", "Idle_Neutral"], "face": hopper, "period": 3.5}]


func _fire_program(scene: Dictionary, role: String, index: int, unit_pose: Dictionary, focus: Dictionary, agent_state: Dictionary) -> Array:
	var record := scene.record as Dictionary
	var target: Vector3
	if str(record.kind) == "building_fire":
		var points := _building_fire_points(scene)
		target = points[index % points.size()] if not points.is_empty() else (unit_pose.centre as Vector3)
	else:
		target = SceneLayout.end_point(focus, true, -1.1) + Vector3(0, 1.0, 0)
	if index >= 2 or role == "fire_support":
		# Pump operator at the engine's kerb-side panel.
		return [{"walk": SceneLayout.door_point(unit_pose, -1.0, 0.25)}, {"loop": ["WorkMid", "Moderate"], "face": unit_pose.centre, "period": 6.0}]
	# Nozzle crew: kerb side, 6–8 m from the fire, facing it.
	var from_unit := (unit_pose.centre as Vector3)
	var toward := Vector3(target.x - from_unit.x, 0.0, target.z - from_unit.z).normalized()
	var stand := Vector3(target.x, 0.17, target.z) - toward * (7.0 + float(index) * 0.6) + Vector3(toward.z, 0, -toward.x) * (1.0 * float(index * 2 - 1))
	var hose := IncidentFx.hose_stream()
	(scene.root as Node3D).add_child(hose)
	agent_state["hose"] = hose
	agent_state["hose_target"] = target
	return [{"walk": stand}, {"post": true}, {"loop": ["Idle_Neutral"], "face": target, "period": 20.0, "hold": "TrolleyIdle"}]


func _start_step(agent_state: Dictionary) -> void:
	var program := agent_state.program as Array
	if not is_instance_valid(agent_state.get("agent")):
		return
	var agent := agent_state.agent as Node
	var index := int(agent_state.step)
	if index >= program.size():
		return
	var step := program[index] as Dictionary
	agent_state.step_started = _elapsed
	if not step.has("wheel"):
		agent.set("walk_clip", "Walking")
		agent.set("walk_backwards", false)
		agent.set("speed", 1.45)
		agent_state.erase("wheeling")
	if step.has("grip_bin") or step.has("release_bin"):
		var bin_node := _bin(str(step.get("grip_bin", step.get("release_bin", ""))))
		if step.has("release_bin") and bin_node != null:
			bin_node.global_transform = (manager.get("garbage_bins") as Node).call("home_of", str(step.release_bin)) as Transform3D
		agent.call("perform", "TrolleyIdle" if step.has("grip_bin") else "Idle_Neutral", float(step.seconds), step.get("face", null))
		agent_state.advance_at = _elapsed + float(step.seconds)
	elif step.has("wheel"):
		var bin_node := _bin(str(step.bin))
		agent.set("walk_clip", "Walk_Carry_Loop")
		agent.set("walk_backwards", bool(step.get("backwards", false)))
		agent.set("speed", WHEEL_SPEED)
		if bin_node != null:
			agent_state.wheeling = {"bin": bin_node, "from_yaw": bin_node.global_rotation.y, "from": agent.call("position"), "to": step.wheel, "home_yaw": ((manager.get("garbage_bins") as Node).call("home_of", str(step.bin)) as Transform3D).basis.get_euler().y, "back": bool(step.get("backwards", false))}
		agent.call("walk_path", PackedVector3Array([agent.call("position"), step.wheel as Vector3]))
	elif step.has("step_to"):
		# A short straight step behind the truck (the lane there is closed).
		agent.set("walk_backwards", bool(step.get("backwards", false)))
		agent.set("speed", 0.9)
		agent.call("walk_path", PackedVector3Array([agent.call("position"), step.step_to as Vector3]))
	elif step.has("tip_bin"):
		var bin_node := _bin(str(step.tip_bin))
		agent.call("perform", "WorkMid", -1.0, step.get("face", null))
		if bin_node != null:
			agent_state.tipping = {"bin": bin_node, "t0": _elapsed, "base": bin_node.global_transform}
		agent_state.advance_at = _elapsed + float(step.seconds)
	elif step.has("stop_done"):
		var scene := _scene_of(agent_state)
		manager.link.send({"action": "service_stop_done", "incident": str(scene.get("id", "")), "bin": str(step.stop_done), "request": "stop-%s-%s-%d" % [str(scene.get("id", "")), str(step.stop_done), Time.get_ticks_msec()]})
		agent_state.step = index + 1
		_start_step(agent_state)
	elif step.has("walk"):
		if not _go_or_wait(agent_state, [step.walk as Vector3]):
			var junction := _junction_of(agent_state)
			if not junction.is_empty() and not _hold_would_help(agent_state, step.walk as Vector3):
				# Not reachable even under an all-red hold: take the nearest
				# junction corner instead of standing frozen.
				step.walk = SceneLayout.junction_corner(junction, step.walk as Vector3)
	elif step.has("post"):
		agent_state.at_post = true
		agent_state.step = index + 1
		_start_step(agent_state)
	elif step.has("loop"):
		agent_state.loop_index = 0
		_play_loop(agent_state)
	elif step.has("board"):
		var target_pose := SceneLayout.vehicle_pose(life, str(step.board))
		if target_pose.is_empty():
			_finish_boarding(agent_state)
		else:
			# Board on the side the crew got out (the kerb side midblock),
			# else the other; if neither is reachable yet, wait and retry (an
			# all-red hold is asked for when it would open the way).
			var side := float(agent_state.get("door_side", -1.0))
			_go_or_wait(agent_state, [SceneLayout.door_point(target_pose, side, 0.5), SceneLayout.door_point(target_pose, -side, 0.5)])


func _go_or_wait(agent_state: Dictionary, goals: Array) -> bool:
	## Walks to the first reachable of goals (each moved to the nearest
	## walkable spot). Otherwise retries in 2 s, asking a junction scene for an
	## all-red hold only when that would open a path.
	var agent := agent_state.agent as Node
	for goal_value in goals:
		if bool(agent.call("go_to", _walkable_near(goal_value as Vector3))):
			return true
	agent_state.retry_at = _elapsed + 2.0
	for goal_value in goals:
		if _hold_would_help(agent_state, goal_value as Vector3):
			_request_junction_hold(agent_state)
			break
	return false


func _hold_would_help(agent_state: Dictionary, goal: Vector3) -> bool:
	var junction := _junction_of(agent_state)
	if junction.is_empty():
		return false
	var path: PackedVector3Array = manager.call("plan_walk", (agent_state.agent as Node).call("position"), _walkable_near(goal), [], [SceneLayout.junction_rect(junction)])
	return not path.is_empty()


func _scene_of(agent_state: Dictionary) -> Dictionary:
	for scene_value in scenes.values():
		var scene := scene_value as Dictionary
		for crew_value in (scene.crews as Dictionary).values():
			if (crew_value as Dictionary).agents.has(agent_state):
				return scene
		for driver_value in (scene.drivers as Dictionary).values():
			if driver_value == agent_state:
				return scene
	return {}


func _junction_of(agent_state: Dictionary) -> String:
	var scene := _scene_of(agent_state)
	if scene.is_empty():
		return ""
	var record := scene.record as Dictionary
	if str(record.get("kind", "")) in ["red_light_tbone", "secondary_collision", "signal_failure"]:
		return str((record.get("location", {}) as Dictionary).get("junction", ""))
	return ""


func _request_junction_hold(agent_state: Dictionary) -> void:
	var scene := _scene_of(agent_state)
	if scene.is_empty() or _junction_of(agent_state).is_empty():
		return
	if _elapsed >= float(scene.get("next_hold_request", 0.0)):
		scene.next_hold_request = _elapsed + 30.0
		manager.link.send({"action": "hold_junction", "incident": scene.id, "seconds": 12.0})


func _play_loop(agent_state: Dictionary) -> void:
	var step := (agent_state.program as Array)[int(agent_state.step)] as Dictionary
	var clips := step.loop as Array
	var clip := str(step.get("hold", clips[int(agent_state.loop_index) % clips.size()]))
	agent_state.agent.call("perform", clip, -1.0, step.get("face", null))
	agent_state.next_loop_at = _elapsed + float(step.get("period", 5.0))


func _on_agent_arrived(agent: Node, agent_state: Dictionary) -> void:
	var program := agent_state.program as Array
	var index := int(agent_state.step)
	if index < program.size() and (program[index] as Dictionary).has("board"):
		_finish_boarding(agent_state)
		return
	agent_state.step = index + 1
	_start_step(agent_state)


func _alight_then_start(vehicle: Node3D, agent_state: Dictionary, slot: Dictionary, delay: float) -> void:
	agent_state.alighting = true
	DoorTransit.alight(vehicle, agent_state.agent as Node, slot, delay, func() -> void:
		agent_state.erase("alighting")
		agent_state.step_started = _elapsed
		_start_step(agent_state))


func _finish_boarding(agent_state: Dictionary) -> void:
	## Arrived at the vehicle: climb in through the nearest door first.
	if bool(agent_state.get("boarding_transit", false)) or bool(agent_state.get("boarded", false)):
		return
	var program := agent_state.program as Array
	var index := int(agent_state.step)
	var unit_id := str((program[index] as Dictionary).get("board", "")) if index < program.size() else ""
	var vehicle := life.call("get_keyed_vehicle", unit_id) as Node3D if not unit_id.is_empty() and bool(life.call("has_keyed_vehicle", unit_id)) else null
	var agent := agent_state.get("agent") as Node
	var slot := DoorTransit.nearest_slot(vehicle, agent.call("position") as Vector3) if vehicle != null and is_instance_valid(agent) else {}
	if slot.is_empty():
		_complete_boarding(agent_state)
		return
	agent_state.boarding_transit = true
	if agent_state.has("hose"):
		(agent_state.hose as Node).queue_free()
		agent_state.erase("hose")
	DoorTransit.board(vehicle, agent, slot, func() -> void: _complete_boarding(agent_state))


func _complete_boarding(agent_state: Dictionary) -> void:
	if agent_state.has("hose"):
		(agent_state.hose as Node).queue_free()
		agent_state.erase("hose")
	agent_state.boarded = true
	manager.call("dispose_responder", agent_state.agent)
	if agent_state.has("on_boarded"):
		(agent_state.on_boarded as Callable).call()


func _process(delta: float) -> void:
	_elapsed += delta
	for scene_value in scenes.values():
		var scene := scene_value as Dictionary
		for crew_value in (scene.crews as Dictionary).values():
			for agent_state_value in (crew_value as Dictionary).agents:
				_tick_agent(agent_state_value as Dictionary)
		for driver_value in (scene.drivers as Dictionary).values():
			_tick_agent(driver_value as Dictionary)
		_tick_winches(scene, delta)
	_tick_cargo()


func _tick_agent(agent_state: Dictionary) -> void:
	# Validate before casting: casting a freed instance is itself an error.
	var agent_value: Variant = agent_state.get("agent")
	if not is_instance_valid(agent_value) or bool(agent_state.get("boarded", false)):
		return
	if bool(agent_state.get("alighting", false)) or bool(agent_state.get("boarding_transit", false)):
		return
	var agent := agent_value as Node
	_tick_bin_handling(agent_state, agent)
	if agent_state.has("advance_at") and _elapsed >= float(agent_state.advance_at):
		agent_state.erase("advance_at")
		agent_state.erase("tipping")
		agent_state.step = int(agent_state.step) + 1
		_start_step(agent_state)
		return
	if agent_state.has("retry_at") and _elapsed >= float(agent_state.retry_at):
		agent_state.erase("retry_at")
		_start_step(agent_state)
	var program := agent_state.program as Array
	var index := int(agent_state.step)
	if index < program.size() and (program[index] as Dictionary).has("board") and _elapsed - float(agent_state.step_started) > BOARD_GIVE_UP_SECONDS:
		# Last resort (no legal path back for a minute): never strand a unit.
		var board_id := str((program[index] as Dictionary).board)
		var target := SceneLayout.vehicle_pose(life, board_id)
		var scene := _scene_of(agent_state)
		push_warning("Responder could not reach its vehicle; boarding forced (%s %s, scene %s, at %s, vehicle at %s, moving=%s, retry=%s)" % [board_id, str(agent_state.get("role", "")), str((scene.get("record", {}) as Dictionary).get("kind", "")), (agent.call("position") as Vector3).snapped(Vector3.ONE * 0.1), (target.get("centre", Vector3.ZERO) as Vector3).snapped(Vector3.ONE * 0.1), agent.call("is_moving"), agent_state.has("retry_at")])
		_finish_boarding(agent_state)
		return
	if index < program.size() and (program[index] as Dictionary).has("loop") and _elapsed >= float(agent_state.get("next_loop_at", INF)):
		agent_state.loop_index = int(agent_state.get("loop_index", 0)) + 1
		_play_loop(agent_state)
	if agent_state.has("hose") and bool(agent_state.get("at_post", false)):
		IncidentFx.aim_hose(agent_state.hose as Node3D, (agent.call("get_hand_world_transform") as Transform3D).origin + Vector3(0, 0.05, 0), agent_state.hose_target as Vector3)


func _bin(bin_id: String) -> Node3D:
	var bins := manager.get("garbage_bins") as Node
	return bins.call("bin_node", bin_id) as Node3D if bins != null else null


func _tick_bin_handling(agent_state: Dictionary, agent: Node) -> void:
	if agent_state.has("wheeling"):
		# The container rolls ahead of the loader's hands, turning from its
		# kerb orientation to face the truck's tail on the way out (and back).
		var job := agent_state.wheeling as Dictionary
		var bin_node := job.bin as Node3D
		var body := agent.get("body") as Node3D
		if not is_instance_valid(bin_node) or body == null:
			return
		var forward := body.global_transform.basis.z
		forward.y = 0.0
		forward = forward.normalized()
		var from := job.from as Vector3
		var to := job.to as Vector3
		var total := maxf(Vector2(to.x - from.x, to.z - from.z).length(), 0.01)
		var done := clampf(Vector2(body.global_position.x - from.x, body.global_position.z - from.z).length() / total, 0.0, 1.0)
		var facing_truck := atan2(forward.x, forward.z)
		var yaw := lerp_angle(float(job.from_yaw), facing_truck, smoothstep(0.0, 1.0, done)) if not bool(job.back) else lerp_angle(float(job.from_yaw), float(job.home_yaw), smoothstep(0.0, 1.0, done))
		var point := body.global_position + forward * WHEEL_REACH
		bin_node.global_transform = Transform3D(Basis(Vector3.UP, yaw), Vector3(point.x, GarbageBins.GROUND_Y, point.z))
	elif agent_state.has("tipping"):
		# Lifter cycle: raise and tip over the hopper rim, shake, lower.
		var job := agent_state.tipping as Dictionary
		var bin_node := job.bin as Node3D
		if not is_instance_valid(bin_node):
			return
		var t := clampf((_elapsed - float(job.t0)) / TIP_SECONDS, 0.0, 1.0)
		var amount := smoothstep(0.08, 0.38, t) * (1.0 - smoothstep(0.7, 0.95, t))
		var shake := sin(_elapsed * 38.0) * 0.012 * float(t > 0.45 and t < 0.65)
		var base := job.base as Transform3D
		var toward := base.basis.z
		var lift := GarbageBins.HOPPER_LIP - (GarbageBins.CASTER_HEIGHT + GarbageBins.BODY_HEIGHT)
		bin_node.global_transform = Transform3D(base.basis, base.origin + Vector3.UP * (lift * smoothstep(0.0, 0.5, amount) + shake) + toward * 0.1 * amount)
		GarbageBins.set_tip(bin_node, amount)
		# Over the hopper the load cell is tipped out: the container reads empty from then on.
		if amount > 0.9 and bin_node.has_meta("bin_weight_kg") and float(bin_node.get_meta("bin_weight_kg")) > 0.5:
			BinWeight.empty(bin_node)


func _board_crew(scene: Dictionary, unit_id: String) -> void:
	var crew := (scene.crews as Dictionary)[unit_id] as Dictionary
	crew.boarding = true
	for agent_state_value in crew.agents:
		var agent_state := agent_state_value as Dictionary
		if bool(agent_state.get("boarded", false)):
			continue
		if agent_state.has("hose"):
			(agent_state.hose as Node).call("set_intensity", 0.0)
		agent_state.program = [{"board": unit_id}]
		agent_state.step = 0
		agent_state.erase("at_post")
		_start_step(agent_state)


# --- Civilian drivers ------------------------------------------------------------------------

func _spawn_driver(scene: Dictionary, vehicle_id: String, node: Node3D) -> void:
	var pose := SceneLayout.vehicle_pose(life, vehicle_id)
	if pose.is_empty():
		return
	# The driver climbs out of the driver's door (+X side) of the wreck.
	var slot := DoorTransit.slot_for(node, 0, 1.0)
	var start := SceneLayout.door_point(pose, 1.0, 0.55) if slot.is_empty() else slot.outside as Vector3
	var index := (scene.drivers as Dictionary).size() + hash(vehicle_id)
	var model := str(CIVILIAN_MODELS[absi(index) % CIVILIAN_MODELS.size()])
	var agent := manager.call("spawn_civilian", "res://assets/rocketbox/%s/Export/%s.fbx" % [model, model], start, float(pose.yaw)) as Node
	if agent == null:
		return
	# Walk around the vehicle to the kerb band, then phone it in and wait.
	var record := scene.record as Dictionary
	var edge := SceneLayout.frame_of(str((record.get("location", {}) as Dictionary).get("edge", "")))
	var safe: Vector3
	if not edge.is_empty():
		var frame := SceneLayout.to_frame(edge, pose.centre as Vector3)
		safe = SceneLayout.from_frame(edge, frame.x - 1.0 - float((scene.drivers as Dictionary).size()) * 1.4, 5.0)
	else:
		safe = SceneLayout.junction_corner(str((record.get("location", {}) as Dictionary).get("junction", "")), pose.centre as Vector3)
	var agent_state := {"agent": agent, "role": "driver", "vehicle": vehicle_id, "step": 0, "step_started": _elapsed,
		"program": [{"walk": safe}, {"loop": ["PhoneText", "PhoneText", "TalkNeutral", "IdleLook"], "face": pose.centre, "period": 7.0}]}
	agent.connect("arrived", _on_agent_arrived.bind(agent_state))
	(scene.drivers as Dictionary)[vehicle_id] = agent_state
	if slot.is_empty():
		_start_step(agent_state)
	else:
		_alight_then_start(node, agent_state, slot, 0.6)


func _return_driver(scene: Dictionary, vehicle_id: String) -> void:
	(scene.acked as Dictionary)["driver_%s" % vehicle_id] = true
	var drivers := scene.drivers as Dictionary
	if not drivers.has(vehicle_id):
		manager.link.send({"action": "driver_ready", "incident": scene.id, "vehicle": vehicle_id})
		return
	var agent_state := drivers[vehicle_id] as Dictionary
	agent_state.program = [{"board": vehicle_id}]
	agent_state.step = 0
	agent_state.on_boarded = func() -> void: manager.link.send({"action": "driver_ready", "incident": scene.id, "vehicle": vehicle_id})
	_start_step(agent_state)


# --- Tow winch and cargo ----------------------------------------------------------------------

func _begin_winch(scene: Dictionary, vehicle_id: String, tow_id: String) -> void:
	var wreck_pose := SceneLayout.vehicle_pose(life, vehicle_id)
	if wreck_pose.is_empty() or not bool(life.call("has_keyed_vehicle", tow_id)):
		return
	life.call("set_vehicle_claimed", vehicle_id, true)
	(scene.loading as Dictionary)[vehicle_id] = {"tow": tow_id, "t": 0.0, "start": wreck_pose}
	# The driver (if out) rides with the tow truck.
	var drivers := scene.drivers as Dictionary
	if drivers.has(vehicle_id):
		var driver := drivers[vehicle_id] as Dictionary
		driver.program = [{"board": tow_id}]
		driver.step = 0
		_start_step(driver)


func _bed_pose(tow_id: String) -> Dictionary:
	## Pose of a wreck carried on a tow truck's flatbed (behind the cab).
	var tow := SceneLayout.vehicle_pose(life, tow_id)
	if tow.is_empty():
		return {}
	var yaw := float(tow.yaw)
	var forward := Vector3(sin(yaw), 0.0, cos(yaw))
	var centre := (tow.centre as Vector3) - forward * float(tow.length) * 0.17
	return {"centre": centre, "yaw": yaw, "lift": 1.05}


func _tick_winches(scene: Dictionary, delta: float) -> void:
	var loading := scene.loading as Dictionary
	for vehicle_id in loading.keys():
		var job := loading[vehicle_id] as Dictionary
		if bool(job.get("done", false)):
			continue
		var bed := _bed_pose(str(job.tow))
		var node := life.call("get_keyed_vehicle", vehicle_id) as Node3D
		if bed.is_empty() or node == null:
			continue
		job.t = minf(1.0, float(job.t) + delta / WINCH_SECONDS)
		var t := float(job.t)
		var start := job.start as Dictionary
		var tow_pose := SceneLayout.vehicle_pose(life, str(job.tow))
		var yaw_tow := float(bed.yaw)
		var forward := Vector3(sin(yaw_tow), 0.0, cos(yaw_tow))
		# Stage 1: the cable drags the wreck straight in line behind the tail
		# (turning it to face the truck). Stage 2: it is pulled up the tilted
		# bed onto the deck. It never passes through the truck's body.
		var ramp_foot := (tow_pose.centre as Vector3) - forward * (float(tow_pose.length) * 0.5 + float(start.length) * 0.5 + 0.4)
		var drag := smoothstep(0.0, 0.5, t)
		var climb := smoothstep(0.5, 1.0, t)
		var centre: Vector3
		if t < 0.5:
			centre = (start.centre as Vector3).lerp(ramp_foot, drag)
		else:
			centre = ramp_foot.lerp(bed.centre as Vector3, climb)
		var yaw := lerp_angle(float(start.yaw), yaw_tow, smoothstep(0.0, 0.35, t))
		life.call("place_keyed_vehicle", vehicle_id, centre, yaw)
		var height := float(bed.lift) * climb
		node.global_position.y = node.global_position.y + height
		# Nose up while it climbs the ramp, level once on the deck.
		node.rotation.x = -0.2 * sin(climb * PI)
		if t >= 1.0:
			job.done = true
			_cargo[vehicle_id] = {"tow": str(job.tow), "node": node}
			manager.link.send({"action": "wreck_loaded", "incident": scene.id, "vehicle": vehicle_id})


func _on_towed(handoff: Dictionary) -> void:
	var vehicle_id := str(handoff.get("id", ""))
	if not _cargo.has(vehicle_id) and bool(life.call("has_keyed_vehicle", vehicle_id)):
		# Headless/timeout path: the bridge removed it before a winch ran.
		_cargo[vehicle_id] = {"tow": str(handoff.get("unit", "")), "node": life.call("get_keyed_vehicle", vehicle_id)}
		life.call("set_vehicle_claimed", vehicle_id, true)


func _tick_cargo() -> void:
	for vehicle_id in _cargo.keys():
		var cargo := _cargo[vehicle_id] as Dictionary
		if bool(cargo.get("impounded", false)):
			continue
		var bed := _bed_pose(str(cargo.tow))
		var node := cargo.node as Node3D
		if bed.is_empty() or not is_instance_valid(node):
			continue
		life.call("place_keyed_vehicle", vehicle_id, bed.centre, float(bed.yaw))
		node.global_position.y += float(bed.lift)
		node.rotation.x = 0.0


func _cargo_arriving(tow_id: String, vehicle_id: String) -> void:
	## Wrecks are kept in the tow yard's impound row (oldest released when
	## more than three are stored).
	if not _cargo.has(vehicle_id):
		return
	var cargo := _cargo[vehicle_id] as Dictionary
	cargo.impound_pending = true
	var timer := get_tree().create_timer(14.0)
	timer.timeout.connect(_impound_cargo.bind(vehicle_id, tow_id))


func _cargo_left_city(vehicle_id: String) -> void:
	## A mutual-aid tow drove its load out through a perimeter gate.
	if not _cargo.has(vehicle_id):
		return
	var node := (_cargo[vehicle_id] as Dictionary).node as Node3D
	if is_instance_valid(node):
		VehicleDamage.restore(node)
	life.call("release_keyed_vehicle", vehicle_id)
	_cargo.erase(vehicle_id)


func _impound_cargo(vehicle_id: String, tow_id: String) -> void:
	if not _cargo.has(vehicle_id):
		return
	var cargo := _cargo[vehicle_id] as Dictionary
	var unit := (manager.fleet_presenter.call("get_unit_view", tow_id) as Dictionary)
	var depot := unit.get("depot", {}) as Dictionary
	var door := depot.get("door", [0.0, 0.0]) as Array
	var edge := RoadGeometry.parse_edge(str(depot.get("edge", "")))
	if edge.is_empty():
		return
	var slot := _impound.size() % 3
	var frame := SceneLayout.to_frame(edge, Vector3(float(door[0]), 0.0, float(door[1])))
	var spot := SceneLayout.from_frame(edge, frame.x + float(slot) * 6.0 - 6.0, frame.y + 4.0)
	var node := cargo.node as Node3D
	if manager.obstacles.oriented_box_blockers(spot, RoadGeometry.yaw_of(edge.direction as Vector3), 4.8, 2.2, 0.2).is_empty():
		life.call("place_keyed_vehicle", vehicle_id, spot, RoadGeometry.yaw_of(edge.direction as Vector3))
		node.rotation.x = 0.0
		cargo.impounded = true
		_impound.append(vehicle_id)
	else:
		# No clear impound space: the wreck is released into the yard's pool.
		VehicleDamage.restore(node)
		life.call("release_keyed_vehicle", vehicle_id)
		_cargo.erase(vehicle_id)
	while _impound.size() > 3:
		var oldest := str(_impound.pop_front())
		var old_node := life.call("get_keyed_vehicle", oldest) as Node3D
		if old_node != null:
			VehicleDamage.restore(old_node)
		life.call("release_keyed_vehicle", oldest)
		_cargo.erase(oldest)


func _scatter_cargo(scene: Dictionary, pose: Dictionary, seed_value: int) -> void:
	scene.debris_built = true
	var rng := RandomNumberGenerator.new()
	rng.seed = seed_value
	var yaw := float(pose.yaw)
	var forward := Vector3(sin(yaw), 0.0, cos(yaw))
	var left := Vector3(cos(yaw), 0.0, -sin(yaw))
	for index in 7:
		var box := CARGO_SCENE.instantiate() as Node3D
		(scene.root as Node3D).add_child(box)
		(scene.props as Array).append(box)
		# Spilled behind and beside the vehicle, within the 2 m behind its
		# rear that following traffic never enters (its minimum gap).
		var along := -float(pose.length) * 0.5 - rng.randf_range(0.2, 1.8)
		var side := -rng.randf_range(0.2, 1.9)
		var point := (pose.centre as Vector3) + forward * along + left * side
		box.scale = Vector3.ONE * rng.randf_range(0.9, 1.4)
		box.global_position = Vector3(point.x, 0.17, point.z)
		box.rotation = Vector3(0.0, rng.randf() * TAU, 0.0)
		for value in box.find_children("*", "VisualInstance3D", true, false):
			(value as VisualInstance3D).layers = IncidentFx.FX_LAYER


# --- Building fire -----------------------------------------------------------------------------

func _building_fire_points(scene: Dictionary) -> Array[Vector3]:
	if scene.has("fire_points"):
		return scene.fire_points as Array[Vector3]
	var points: Array[Vector3] = []
	var record := scene.record as Dictionary
	var building_info := (record.get("scene", {}) as Dictionary).get("building", {}) as Dictionary
	var building := get_tree().root.get_node_or_null(str(building_info.get("node", ""))) as Node3D
	var edge := SceneLayout.frame_of(str((record.get("location", {}) as Dictionary).get("edge", "")))
	if building == null or edge.is_empty():
		scene.fire_points = points
		return points
	var bounds := preload("res://scripts/city_spatial_safety.gd").world_bounds(building)
	# The facade facing the road: the bounds face nearest the carriageway.
	var right := edge.right as Vector3
	var centre := bounds.get_center()
	var facade_offset := absf(right.x) * bounds.size.x * 0.5 + absf(right.z) * bounds.size.z * 0.5
	var facade := centre - right * facade_offset
	var along := edge.direction as Vector3
	var width := absf(along.x) * bounds.size.x + absf(along.z) * bounds.size.z
	var floors := clampi(int(bounds.size.y / 3.2), 1, 6)
	for index in 3:
		var height := bounds.position.y + 2.2 + float(mini(index, floors - 1)) * 3.1
		var lateral := (float(index) - 1.0) * width * 0.26
		points.append(facade + along * lateral - right * 0.4 + Vector3(0, height - centre.y, 0))
	scene.fire_points = points
	scene.facade_normal = -right
	return points


func _update_building_fire(scene: Dictionary, fire: Dictionary) -> void:
	var fx := scene.fx as Dictionary
	var points := _building_fire_points(scene)
	if points.is_empty():
		return
	var intensity := float(fire.get("intensity", 0.0))
	for index in points.size():
		var key := "window_%d" % index
		if not fx.has(key):
			var flames := IncidentFx.fire(2.2, 0.8, 1.6, index == 1)
			(scene.root as Node3D).add_child(flames)
			flames.global_position = points[index]
			fx[key] = flames
		# Fire spreads from the first window; upper windows follow intensity.
		fx[key].call("set_intensity", clampf(intensity * 1.6 - float(index) * 0.3, 0.0, 1.0))
	if intensity <= 0.0 and not fx.has("steam"):
		var steam := IncidentFx.engine_smoke(true)
		(scene.root as Node3D).add_child(steam)
		steam.global_position = points[1]
		steam.set_intensity(1.0)
		fx["steam"] = steam


# --- Cones and closing -----------------------------------------------------------------------

func _build_cones(scene: Dictionary) -> void:
	if bool(scene.cones_built):
		return
	var record := scene.record as Dictionary
	var location := record.get("location", {}) as Dictionary
	var edge := SceneLayout.frame_of(str(location.get("edge", "")))
	var vehicles := scene.layout_vehicles as Array
	if edge.is_empty() or vehicles.size() < 2 or not str(location.get("junction", "")).is_empty():
		return
	scene.cones_built = true
	var start := INF
	var finish := -INF
	for vehicle in vehicles:
		var span := SceneLayout.vehicle_span(edge, vehicle)
		start = minf(start, span.x)
		finish = maxf(finish, span.y)
	# Cones line the scene on the centreline side only alongside scene
	# vehicles, where nothing can drive (never across the pull-out point).
	var s := start + 0.5
	while s <= finish - 0.5:
		var cone := CONE_SCENE.instantiate() as Node3D
		(scene.root as Node3D).add_child(cone)
		cone.global_position = SceneLayout.from_frame(edge, s, 0.35)
		cone.scale = Vector3.ONE * 1.1
		for value in cone.find_children("*", "VisualInstance3D", true, false):
			(value as VisualInstance3D).layers = IncidentFx.FX_LAYER
		(scene.props as Array).append(cone)
		s += 3.0


func _close_scene(scene: Dictionary) -> void:
	if bool(scene.closing):
		return
	scene.closing = true
	# Crews still out board their units; drivers that remain walk away.
	for unit_id in (scene.crews as Dictionary).keys():
		if not bool(((scene.crews as Dictionary)[unit_id] as Dictionary).boarding):
			_board_crew(scene, str(unit_id))
	for vehicle_id in (scene.drivers as Dictionary).keys():
		var driver := (scene.drivers as Dictionary)[vehicle_id] as Dictionary
		if not bool(driver.get("boarded", false)) and bool(life.call("has_keyed_vehicle", str(vehicle_id))):
			driver.program = [{"board": str(vehicle_id)}]
			driver.step = 0
			_start_step(driver)
	for effect in (scene.fx as Dictionary).values():
		if (effect as Object).has_method("set_intensity"):
			effect.call("set_intensity", 0.0)
	# Props are swept and the scene node removed once everyone has boarded.
	_finalize_when_empty(scene)


func _finalize_when_empty(scene: Dictionary) -> void:
	var waiting := false
	for crew_value in (scene.crews as Dictionary).values():
		for agent_state in (crew_value as Dictionary).agents:
			if not bool((agent_state as Dictionary).get("boarded", false)) and is_instance_valid((agent_state as Dictionary).agent):
				waiting = true
	for driver_value in (scene.drivers as Dictionary).values():
		if not bool((driver_value as Dictionary).get("boarded", false)) and is_instance_valid((driver_value as Dictionary).agent):
			waiting = true
	if waiting and _elapsed - float(scene.opened_at) < 3600.0:
		get_tree().create_timer(1.0).timeout.connect(_finalize_when_empty.bind(scene))
		return
	# Damage on released drivable vehicles is restored once they have left.
	for participant_value in (scene.record as Dictionary).get("participants", []):
		var vehicle_id := str((participant_value as Dictionary).get("id", ""))
		if not _cargo.has(vehicle_id):
			var node := life.call("get_keyed_vehicle", vehicle_id) as Node3D
			if node != null:
				get_tree().create_timer(40.0).timeout.connect(VehicleDamage.restore.bind(node))
	(scene.root as Node3D).queue_free()
	scenes.erase(scene.id)


func get_scene_view(incident_id: String) -> Dictionary:
	return scenes.get(incident_id, {}) as Dictionary
