extends Node3D

## Functional visual grid: two renewable source hubs feed a redundant outer
## transmission ring, then street-side distribution feeders supply all six
## water-reservoir parcels and the three existing power-switch actuators.

const POLE_SCENE := preload("res://assets/kenney/city_roads/electricity-pole-single.glb")
const WIDE_POLE_SCENE := preload("res://assets/kenney/city_roads/electricity-pole-single.glb")
const WIND_SCENE := preload("res://assets/kenney/city_industrial/windmill.glb")
const SOLAR_SCENE := preload("res://assets/kenney/city_industrial/solar-panel-landscape-group.glb")
const POWER_BOX_SCENE := preload("res://assets/poly_pizza/city_props/Power Box.glb")

const RING_NORTH := Vector3(0.0, 0.17, -412.0)
const RING_EAST := Vector3(412.0, 0.17, 0.0)
const RING_SOUTH := Vector3(0.0, 0.17, 412.0)
const RING_WEST := Vector3(-412.0, 0.17, 0.0)
const CABLE_HEIGHT := 10.8

var _nodes: Dictionary = {}
var _edges: Array[Dictionary] = []
var _poles_by_location: Dictionary = {}
var _line_material: StandardMaterial3D
var _source_ids: Array[String] = []


func _ready() -> void:
	_line_material = _make_line_material()
	_create_generation_hub("generation_north", Vector3(0.0, 0.17, -470.0), false)
	_create_generation_hub("generation_south", Vector3(0.0, 0.17, 470.0), true)
	_add_node("ring_north", RING_NORTH, "transmission")
	_add_node("ring_east", RING_EAST, "transmission")
	_add_node("ring_south", RING_SOUTH, "transmission")
	_add_node("ring_west", RING_WEST, "transmission")
	_add_node("feeder_north", Vector3(-87.0, 0.17, -332.5), "distribution")
	_add_node("feeder_south", Vector3(103.0, 0.17, 332.5), "distribution")
	_add_node("core_west", Vector3(-87.0, 0.17, 332.5), "distribution")
	_add_node("core_east", Vector3(103.0, 0.17, -332.5), "distribution")
	_build_transmission_ring()
	_connect_water_reservoirs()
	_connect_power_switches()
	_evaluate_grid()


func _create_generation_hub(node_id: String, location: Vector3, flipped: bool) -> void:
	_add_node(node_id, location, "source")
	_source_ids.append(node_id)
	var hub := Node3D.new()
	hub.name = "PowerHub_%s" % node_id
	hub.position = location
	hub.add_to_group("power_sources")
	hub.set_meta("kind", "renewable_generation")
	add_child(hub)
	var wind := WIND_SCENE.instantiate() as Node3D
	wind.name = "WindTurbine"
	wind.position = Vector3(-15.0 if not flipped else 15.0, 0.0, 0.0)
	hub.add_child(wind)
	_fit_asset_to_height(wind, 24.0)
	var solar := SOLAR_SCENE.instantiate() as Node3D
	solar.name = "SolarArray"
	solar.position = Vector3(15.0 if not flipped else -15.0, 0.0, 0.0)
	solar.rotation.y = PI if flipped else 0.0
	hub.add_child(solar)
	_fit_asset_to_width(solar, 18.0)
	var cabinet := POWER_BOX_SCENE.instantiate() as Node3D
	cabinet.name = "GenerationControlCabinet"
	cabinet.position = Vector3(0.0, 0.0, 10.0)
	hub.add_child(cabinet)
	_fit_asset_to_height(cabinet, 3.4)


func _build_transmission_ring() -> void:
	# The dense city grid has no safe continuous overhead route: every line that
	# crosses it would have to pass through an active road junction.  Therefore
	# visible high-voltage equipment stays in the landscaped perimeter verge,
	# while the city-centre distribution mesh is deliberately underground.
	_add_connection("generation_north", "ring_north", [Vector3(0.0, CABLE_HEIGHT, -470.0), Vector3(0.0, CABLE_HEIGHT, -412.0)])
	_add_connection("generation_south", "ring_south", [Vector3(0.0, CABLE_HEIGHT, 470.0), Vector3(0.0, CABLE_HEIGHT, 412.0)])
	_add_connection("ring_north", "ring_east", [Vector3(0.0, CABLE_HEIGHT, -412.0), Vector3(412.0, CABLE_HEIGHT, -412.0), Vector3(412.0, CABLE_HEIGHT, 0.0)])
	_add_connection("ring_east", "ring_south", [Vector3(412.0, CABLE_HEIGHT, 0.0), Vector3(412.0, CABLE_HEIGHT, 412.0), Vector3(0.0, CABLE_HEIGHT, 412.0)])
	_add_connection("ring_south", "ring_west", [Vector3(0.0, CABLE_HEIGHT, 412.0), Vector3(-412.0, CABLE_HEIGHT, 412.0), Vector3(-412.0, CABLE_HEIGHT, 0.0)])
	_add_connection("ring_west", "ring_north", [Vector3(-412.0, CABLE_HEIGHT, 0.0), Vector3(-412.0, CABLE_HEIGHT, -412.0), Vector3(0.0, CABLE_HEIGHT, -412.0)])
	_add_connection("ring_north", "feeder_north", [], false)
	_add_connection("feeder_north", "core_west", [], false)
	_add_connection("core_west", "ring_south", [], false)
	_add_connection("ring_north", "core_east", [], false)
	_add_connection("core_east", "feeder_south", [], false)
	_add_connection("feeder_south", "ring_south", [], false)
	_add_connection("ring_west", "core_east", [], false)
	_add_connection("ring_west", "core_west", [], false)
	_add_connection("core_west", "ring_east", [], false)
	_build_visible_urban_distribution()


func _build_visible_urban_distribution() -> void:
	# Every run is deliberately broken before each crossroad.  This puts poles
	# only in the block-edge sidewalk strip (8 m off a road centre) and never
	# inside the 15 m junction square.  The short underground joins through each
	# junction are represented by the connected graph above.
	for road_side in [-87.0, 103.0]:
		_add_safe_roadside_runs(road_side, true)
		_add_safe_roadside_runs(road_side, false)


func _add_safe_roadside_runs(corridor: float, horizontal: bool) -> void:
	var junction_centres := [-380.0, -285.0, -190.0, -95.0, 0.0, 95.0, 190.0, 285.0, 380.0]
	for index in range(junction_centres.size() - 1):
		var from_centre: float = float(junction_centres[index])
		var to_centre: float = float(junction_centres[index + 1])
		# The junction has a 7.5 m half-width. An extra 2.5 m clearance leaves
		# the pole base and cables completely outside vehicle space.
		var start_axis := from_centre + 10.0
		var end_axis := to_centre - 10.0
		var start := Vector3(start_axis, CABLE_HEIGHT, corridor) if horizontal else Vector3(corridor, CABLE_HEIGHT, start_axis)
		var end := Vector3(end_axis, CABLE_HEIGHT, corridor) if horizontal else Vector3(corridor, CABLE_HEIGHT, end_axis)
		_add_line_span(start, end, false)


func _connect_water_reservoirs() -> void:
	var routes := {
		"NorthCentralReservoir": {"to": "feeder_north"},
		"SouthCentralReservoir": {"to": "feeder_south"},
		"NorthWestReservoir": {"to": "ring_west"},
		"NorthEastReservoir": {"to": "ring_east"},
		"SouthWestReservoir": {"to": "ring_west"},
		"SouthEastReservoir": {"to": "ring_east"},
	}
	for site_value in get_tree().get_nodes_in_group("water_reservoir_sites"):
		var site := site_value as Node3D
		var route := routes.get(site.name, {}) as Dictionary
		if route.is_empty():
			continue
		var node_id := "reservoir_%s" % site.name
		_add_node(node_id, site.global_position, "consumer")
		_add_connection(node_id, str(route.to), [], false)


func _connect_power_switches() -> void:
	var routes := {
		"PowerSwitch_01": {"to": "feeder_north"},
		"PowerSwitch_02": {"to": "feeder_south"},
		"PowerSwitch_03": {"to": "feeder_south"},
	}
	for switch_value in get_tree().get_nodes_in_group("power_switches"):
		var power_switch := switch_value as Node3D
		var route := routes.get(power_switch.name, {}) as Dictionary
		if route.is_empty():
			continue
		var node_id := "switch_%s" % power_switch.name
		_add_node(node_id, power_switch.global_position, "consumer")
		_add_connection(node_id, str(route.to), [], false)


func _add_node(node_id: String, location: Vector3, kind: String) -> void:
	_nodes[node_id] = {"position": location, "kind": kind, "powered": false}


func _add_connection(from_id: String, to_id: String, points: Array, visible := true) -> void:
	_edges.append({"from": from_id, "to": to_id, "enabled": true})
	if not visible:
		return
	for index in range(points.size() - 1):
		_add_line_span(points[index] as Vector3, points[index + 1] as Vector3, index == 0 or index == points.size() - 2)


func _add_line_span(start: Vector3, end: Vector3, is_anchor: bool) -> void:
	var distance := start.distance_to(end)
	var steps := maxi(1, ceili(distance / 47.5))
	for index in range(steps + 1):
		var point := start.lerp(end, float(index) / float(steps))
		_ensure_pole(Vector3(point.x, 0.17, point.z), is_anchor and (index == 0 or index == steps))
	for index in range(steps):
		var a := start.lerp(end, float(index) / float(steps))
		var b := start.lerp(end, float(index + 1) / float(steps))
		_add_three_cables(a, b)


func _ensure_pole(location: Vector3, transmission: bool) -> void:
	var key := "%d:%d" % [roundi(location.x * 10.0), roundi(location.z * 10.0)]
	if _poles_by_location.has(key):
		return
	var pole := (WIDE_POLE_SCENE if transmission else POLE_SCENE).instantiate() as Node3D
	pole.name = "PowerPole_%s" % key.replace(":", "_")
	pole.position = location
	pole.add_to_group("power_poles")
	pole.set_meta("network", "connected_power_grid")
	add_child(pole)
	_fit_asset_to_height(pole, 12.5 if transmission else 11.0)
	_poles_by_location[key] = pole


func _add_three_cables(start: Vector3, end: Vector3) -> void:
	var direction := (end - start).normalized()
	var sideways := direction.cross(Vector3.UP).normalized()
	for offset_value in [-0.16, 0.16]:
		var offset: float = float(offset_value)
		var a: Vector3 = start + sideways * offset
		var b: Vector3 = end + sideways * offset
		var middle: Vector3 = a.lerp(b, 0.5) + Vector3.DOWN * minf(0.7, a.distance_to(b) * 0.014)
		_add_cable_segment(a, middle)
		_add_cable_segment(middle, b)


func _add_cable_segment(start: Vector3, end: Vector3) -> void:
	var cable := MeshInstance3D.new()
	cable.name = "PowerLine"
	cable.add_to_group("power_lines")
	var mesh := CylinderMesh.new()
	mesh.top_radius = 0.024
	mesh.bottom_radius = 0.024
	mesh.height = start.distance_to(end)
	mesh.radial_segments = 8
	cable.mesh = mesh
	cable.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	cable.material_override = _line_material
	cable.position = start.lerp(end, 0.5)
	cable.quaternion = Quaternion(Vector3.UP, (end - start).normalized())
	add_child(cable)


func _evaluate_grid() -> void:
	var connected: Dictionary = {}
	var frontier: Array[String] = _source_ids.duplicate()
	for source_id in _source_ids:
		connected[source_id] = true
	while not frontier.is_empty():
		var current: String = frontier.pop_front()
		for edge_value in _edges:
			var edge := edge_value as Dictionary
			if not bool(edge.enabled):
				continue
			var next_id := ""
			if str(edge.from) == current:
				next_id = str(edge.to)
			elif str(edge.to) == current:
				next_id = str(edge.from)
			if not next_id.is_empty() and not connected.has(next_id):
				connected[next_id] = true
				frontier.append(next_id)
	for node_id in _nodes.keys():
		var node := _nodes[node_id] as Dictionary
		node.powered = connected.has(node_id)
		_nodes[node_id] = node
	_sync_consumer_state()
	var line_color := Color(0.018, 0.06, 0.16) if connected.size() == _nodes.size() else Color(0.10, 0.012, 0.008)
	if _line_material != null:
		_line_material.emission = line_color


func _make_line_material() -> StandardMaterial3D:
	var material := StandardMaterial3D.new()
	material.albedo_color = Color(0.035, 0.045, 0.06)
	material.metallic = 0.85
	material.roughness = 0.32
	material.emission_enabled = true
	material.emission = Color(0.018, 0.05, 0.12)
	material.emission_energy_multiplier = 0.35
	return material


func _sync_consumer_state() -> void:
	for site_value in get_tree().get_nodes_in_group("water_reservoir_sites"):
		var site := site_value as Node3D
		site.set_meta("power_online", bool((_nodes.get("reservoir_%s" % site.name, {}) as Dictionary).get("powered", false)))
	for switch_value in get_tree().get_nodes_in_group("power_switches"):
		var power_switch := switch_value as Node3D
		power_switch.set_meta("power_online", bool((_nodes.get("switch_%s" % power_switch.name, {}) as Dictionary).get("powered", false)))


func set_switch_enabled(switch_name: String, enabled: bool) -> void:
	var node_id := "switch_%s" % switch_name
	for edge_value in _edges:
		var edge := edge_value as Dictionary
		if str(edge.from) == node_id or str(edge.to) == node_id:
			edge.enabled = enabled
	_evaluate_grid()


func get_grid_status() -> Dictionary:
	var powered_consumers := 0
	for node_value in _nodes.values():
		var node := node_value as Dictionary
		if str(node.kind) == "consumer" and bool(node.powered):
			powered_consumers += 1
	return {"sources": _source_ids.size(), "nodes": _nodes.size(), "edges": _edges.size(), "powered_consumers": powered_consumers, "total_consumers": 9}


func _fit_asset_to_height(asset: Node3D, height_value: float) -> void:
	var bounds := _local_bounds(asset)
	if bounds.size.y > 0.001:
		asset.scale *= height_value / bounds.size.y
		asset.position.y = 0.17 - bounds.position.y * asset.scale.y


func _fit_asset_to_width(asset: Node3D, width_value: float) -> void:
	var bounds := _local_bounds(asset)
	var width := maxf(bounds.size.x, bounds.size.z)
	if width > 0.001:
		asset.scale *= width_value / width
		asset.position.y = 0.17 - bounds.position.y * asset.scale.y


func _local_bounds(target: Node3D) -> AABB:
	var result := AABB()
	var has_bounds := false
	for mesh_value in target.find_children("*", "MeshInstance3D", true, false):
		var mesh := mesh_value as MeshInstance3D
		if mesh.mesh == null:
			continue
		for corner in _aabb_corners(mesh.mesh.get_aabb()):
			var point := target.to_local(mesh.to_global(corner))
			result = AABB(point, Vector3.ZERO) if not has_bounds else result.expand(point)
			has_bounds = true
	return result


func _aabb_corners(bounds: AABB) -> Array[Vector3]:
	var start := bounds.position
	var end := bounds.end
	return [Vector3(start.x, start.y, start.z), Vector3(end.x, start.y, start.z), Vector3(start.x, end.y, start.z), Vector3(end.x, end.y, start.z), Vector3(start.x, start.y, end.z), Vector3(end.x, start.y, end.z), Vector3(start.x, end.y, end.z), Vector3(end.x, end.y, end.z)]
