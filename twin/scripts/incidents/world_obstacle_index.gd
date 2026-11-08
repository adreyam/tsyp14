extends RefCounted

## Spatial index of every static object a vehicle or responder must not occupy:
## buildings, trees, street furniture, smart-city devices, poles, signal posts,
## utility hardware and citizen job/interaction props. Bounds are the rendered
## world AABBs (CitySpatialSafety.world_bounds), so decisions match the pixels.
## Poles and signal posts use their ground support footprint only, matching the
## pedestrian planner, because their arms overhang the road above head height.

const CitySpatialSafety := preload("res://scripts/city_spatial_safety.gd")
const CELL := 16.0
const BOUNDS_GROUPS := [
	"buildings", "backdrop_buildings", "urban_props", "smart_city_devices", "water_valves",
	"power_switches", "water_reservoirs", "water_reservoir_sites", "traffic_cameras", "sensors", "power_sources",
	"water_service_meters", "power_service_meters", "npc_activity_props", "npc_interaction_props",
]
const POST_GROUPS := {"city_trees": 0.75, "power_poles": 0.32, "traffic_signals": 0.30, "street_lights": 0.30, "smart_street_lights": 0.30}

var _entries: Array[Dictionary] = []
var _grid: Dictionary = {}


func build(tree: SceneTree, extra_tree_nodes: Array = []) -> void:
	_entries.clear()
	_grid.clear()
	var seen := {}
	for group_name in BOUNDS_GROUPS:
		for value in tree.get_nodes_in_group(group_name):
			_add_node(value, "bounds", 0.0, seen)
	for group_name in POST_GROUPS:
		for value in tree.get_nodes_in_group(group_name):
			_add_node(value, "post", float(POST_GROUPS[group_name]), seen)
	for value in extra_tree_nodes:
		_add_node(value, "post", 0.75, seen)


func _add_node(value: Variant, kind: String, radius: float, seen: Dictionary) -> void:
	if not value is Node3D:
		return
	var node := value as Node3D
	if not node.is_visible_in_tree() or seen.has(node.get_instance_id()):
		return
	seen[node.get_instance_id()] = true
	var bounds: AABB
	if kind == "post":
		var base := node.global_position
		bounds = AABB(Vector3(base.x - radius, 0.0, base.z - radius), Vector3(radius * 2.0, 4.0, radius * 2.0))
	else:
		bounds = CitySpatialSafety.world_bounds(node)
		if bounds.size == Vector3.ZERO:
			return
	add_bounds(bounds, node.name, node)


func add_bounds(bounds: AABB, label: String, node: Node3D = null) -> void:
	var index := _entries.size()
	_entries.append({"bounds": bounds, "label": label, "node": node})
	for cell_x in range(floori(bounds.position.x / CELL), floori(bounds.end.x / CELL) + 1):
		for cell_z in range(floori(bounds.position.z / CELL), floori(bounds.end.z / CELL) + 1):
			var key := Vector2i(cell_x, cell_z)
			if not _grid.has(key):
				_grid[key] = []
			(_grid[key] as Array).append(index)


func entry_count() -> int:
	return _entries.size()


func _candidates(area: Rect2) -> Array:
	var found := {}
	for cell_x in range(floori(area.position.x / CELL), floori(area.end.x / CELL) + 1):
		for cell_z in range(floori(area.position.y / CELL), floori(area.end.y / CELL) + 1):
			for index in _grid.get(Vector2i(cell_x, cell_z), []):
				found[index] = true
	return found.keys()


func rect_blockers(area: Rect2, margin := 0.0, ignored: Array = []) -> Array[String]:
	## Labels of obstacles whose horizontal footprint overlaps area (+margin).
	var grown := area.grow(margin)
	var blockers: Array[String] = []
	for index in _candidates(grown):
		var entry := _entries[int(index)] as Dictionary
		if ignored.has(entry.node):
			continue
		var bounds := entry.bounds as AABB
		var footprint := Rect2(bounds.position.x, bounds.position.z, bounds.size.x, bounds.size.z)
		if footprint.intersects(grown):
			blockers.append(str(entry.label))
	return blockers


func point_blocked(point: Vector3, radius: float, ignored: Array = []) -> bool:
	return not rect_blockers(Rect2(point.x - radius, point.z - radius, radius * 2.0, radius * 2.0), 0.0, ignored).is_empty()


func oriented_box_blockers(centre: Vector3, yaw: float, length: float, width: float, margin := 0.0, ignored: Array = []) -> Array[String]:
	## Exact test of an oriented rectangle (a vehicle footprint) against the
	## axis-aligned obstacle footprints via separating axes.
	var half_length := length * 0.5 + margin
	var half_width := width * 0.5 + margin
	var forward := Vector2(sin(yaw), cos(yaw))
	var side := Vector2(forward.y, -forward.x)
	var extent := Vector2(absf(forward.x) * half_length + absf(side.x) * half_width, absf(forward.y) * half_length + absf(side.y) * half_width)
	var area := Rect2(centre.x - extent.x, centre.z - extent.y, extent.x * 2.0, extent.y * 2.0)
	var blockers: Array[String] = []
	for index in _candidates(area):
		var entry := _entries[int(index)] as Dictionary
		if ignored.has(entry.node):
			continue
		var bounds := entry.bounds as AABB
		if _obb_overlaps_aabb(Vector2(centre.x, centre.z), forward, side, half_length, half_width, bounds):
			blockers.append(str(entry.label))
	return blockers


static func _obb_overlaps_aabb(centre: Vector2, forward: Vector2, side: Vector2, half_length: float, half_width: float, bounds: AABB) -> bool:
	var box_min := Vector2(bounds.position.x, bounds.position.z)
	var box_max := Vector2(bounds.end.x, bounds.end.z)
	var box_centre := (box_min + box_max) * 0.5
	var box_half := (box_max - box_min) * 0.5
	var delta := box_centre - centre
	for axis in [Vector2.RIGHT, Vector2.DOWN, forward, side]:
		var a := axis as Vector2
		var obb_radius := absf(forward.dot(a)) * half_length + absf(side.dot(a)) * half_width
		var aabb_radius := absf(a.x) * box_half.x + absf(a.y) * box_half.y
		if absf(delta.dot(a)) > obb_radius + aabb_radius:
			return false
	return true


func segment_blockers(start: Vector3, finish: Vector3, half_width: float, ignored: Array = []) -> Array[String]:
	## Obstacles touching a straight corridor of the given half width.
	var delta := finish - start
	delta.y = 0.0
	var length := delta.length()
	if length < 0.01:
		return rect_blockers(Rect2(start.x - half_width, start.z - half_width, half_width * 2.0, half_width * 2.0), 0.0, ignored)
	var centre := (start + finish) * 0.5
	return oriented_box_blockers(centre, atan2(delta.x, delta.z), length, half_width * 2.0, 0.0, ignored)
