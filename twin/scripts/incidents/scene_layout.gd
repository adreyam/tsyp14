extends RefCounted

## Geometry of an incident scene in Godot space, derived from the live poses
## of the scene's stationary vehicles (wrecks and units on scene). It is the
## single source for where responders may stand and walk:
##
## * kerb band (3.9–5.75 m from the centreline): no vehicle body in the lane
##   ever reaches it (widest truck spans 0.85–3.65 m);
## * door-access strips (0.45–3.9 m) only alongside stationary scene vehicles
##   (1.3 m past each end, inside the minimum gap of the next car)
##   and across gaps between them shorter than an overtaking car needs to
##   merge back (9.5 m), so nothing can drive there;
## * for junction scenes: a 1.3 m zone around each wreck (green paths keep
##   3.2 m from the wreck), and the junction box only while signals are held
##   all-red for crews to cross.

const RoadGeometry := preload("res://scripts/incidents/road_geometry.gd")
const KERB_INNER := 3.9
const KERB_OUTER := RoadGeometry.KERB_OFFSET
const STRIP_INNER := 0.45
const MERGE_GAP := 9.5
const STRIP_END_MARGIN := 1.3
const WRECK_ZONE := 1.3


static func vehicle_pose(life: Node, key: String) -> Dictionary:
	if not bool(life.call("has_keyed_vehicle", key)):
		return {}
	var pose := life.call("get_keyed_pose", key) as Dictionary
	var dimensions := life.call("get_vehicle_dimensions", key) as Vector2
	return {"key": key, "centre": pose.centre, "yaw": float(pose.yaw), "length": dimensions.x, "width": dimensions.y}


static func scene_vehicles(life: Node, record: Dictionary, units_on_scene: Array) -> Array:
	var output: Array = []
	# Only an established scene has a layout: before onset the participants
	# are still driving toward the collision.
	if str(record.get("phase", "")) in ["arming", "approach", "sliding", "coasting"]:
		return output
	for participant_value in record.get("participants", []):
		var participant := participant_value as Dictionary
		if str(participant.get("status", "")) in ["released", "towed", ""]:
			continue
		var pose := vehicle_pose(life, str(participant.get("id", "")))
		if not pose.is_empty():
			pose["kind"] = "wreck"
			output.append(pose)
	for unit_id in units_on_scene:
		var pose := vehicle_pose(life, str(unit_id))
		if not pose.is_empty():
			pose["kind"] = "unit"
			output.append(pose)
	return output


static func frame_of(edge_id: String) -> Dictionary:
	return RoadGeometry.parse_edge(edge_id)


static func to_frame(edge: Dictionary, point: Vector3) -> Vector2:
	## (lane position s, lateral offset from the centreline toward the kerb).
	var relative := point - (edge.start as Vector3)
	return Vector2(relative.dot(edge.direction as Vector3) - RoadGeometry.JUNCTION_HALF, relative.dot(edge.right as Vector3))


static func from_frame(edge: Dictionary, s: float, lateral: float) -> Vector3:
	var point := RoadGeometry.side_point(edge, s, lateral)
	return Vector3(point.x, 0.17, point.z)


static func frame_rect(edge: Dictionary, s_from: float, s_to: float, lateral_from: float, lateral_to: float) -> Rect2:
	var a := from_frame(edge, s_from, lateral_from)
	var b := from_frame(edge, s_to, lateral_to)
	return Rect2(Vector2(minf(a.x, b.x), minf(a.z, b.z)), Vector2(absf(a.x - b.x), absf(a.z - b.z)))


static func vehicle_span(edge: Dictionary, vehicle: Dictionary) -> Vector2:
	var centre := to_frame(edge, vehicle.centre as Vector3)
	var yaw := float(vehicle.yaw)
	var forward := Vector3(sin(yaw), 0.0, cos(yaw))
	var along := absf(forward.dot(edge.direction as Vector3))
	var across := absf(forward.dot(edge.right as Vector3))
	var half := float(vehicle.length) * 0.5 * along + float(vehicle.width) * 0.5 * across
	return Vector2(centre.x - half, centre.x + half)


static func midblock_safe_rects(edge: Dictionary, vehicles: Array) -> Array[Rect2]:
	var rects: Array[Rect2] = []
	if vehicles.is_empty():
		return rects
	var spans: Array = []
	for vehicle in vehicles:
		spans.append(vehicle_span(edge, vehicle))
	spans.sort_custom(func(a: Vector2, b: Vector2) -> bool: return a.x < b.x)
	var scene_start := (spans[0] as Vector2).x
	var scene_end := (spans[-1] as Vector2).y
	rects.append(frame_rect(edge, scene_start - 5.0, scene_end + 2.0, KERB_INNER, KERB_OUTER + 0.2))
	for index in spans.size():
		var span := spans[index] as Vector2
		# Past each end far enough to walk around the stopped body (a moving
		# car never comes within its minimum gap of a stationary one).
		rects.append(frame_rect(edge, span.x - STRIP_END_MARGIN, span.y + STRIP_END_MARGIN, STRIP_INNER, KERB_INNER + 0.1))
		if index + 1 < spans.size():
			var next := spans[index + 1] as Vector2
			if next.x - span.y < MERGE_GAP:
				rects.append(frame_rect(edge, span.y, next.x, STRIP_INNER, KERB_INNER + 0.1))
	return rects


static func kerb_post(edge: Dictionary, vehicles: Array, upstream := true) -> Vector3:
	## Traffic-control post on the kerb band, facing approaching traffic.
	var start := INF
	var finish := -INF
	for vehicle in vehicles:
		var span := vehicle_span(edge, vehicle)
		start = minf(start, span.x)
		finish = maxf(finish, span.y)
	var s := start - 3.5 if upstream else finish + 1.0
	return from_frame(edge, s, 4.9)


static func door_point(vehicle: Dictionary, side: float, along := 0.35) -> Vector3:
	## side +1 = vehicle's left (driver side in right-hand traffic).
	var yaw := float(vehicle.yaw)
	var forward := Vector3(sin(yaw), 0.0, cos(yaw))
	var left := Vector3(cos(yaw), 0.0, -sin(yaw))
	var point := (vehicle.centre as Vector3) + left * side * (float(vehicle.width) * 0.5 + 0.5) + forward * float(vehicle.length) * (along - 0.5) * 0.9
	return Vector3(point.x, 0.17, point.z)


static func end_point(vehicle: Dictionary, front: bool, gap := 0.9) -> Vector3:
	var yaw := float(vehicle.yaw)
	var forward := Vector3(sin(yaw), 0.0, cos(yaw))
	var point := (vehicle.centre as Vector3) + forward * (float(vehicle.length) * 0.5 + gap) * (1.0 if front else -1.0)
	return Vector3(point.x, 0.17, point.z)


static func wreck_zone_box(vehicle: Dictionary) -> Dictionary:
	return {"centre": vehicle.centre, "yaw": float(vehicle.yaw), "length": float(vehicle.length) + WRECK_ZONE * 2.0, "width": float(vehicle.width) + WRECK_ZONE * 2.0}


static func junction_rect(junction_id: String) -> Rect2:
	var parts := junction_id.trim_prefix("Junction_r").split("_c")
	if parts.size() != 2:
		return Rect2()
	var centre := RoadGeometry.junction_position(int(parts[0]), int(parts[1]))
	return Rect2(Vector2(centre.x - 9.0, centre.z - 9.0), Vector2(18.0, 18.0))


static func junction_corner(junction_id: String, near: Vector3) -> Vector3:
	## The kerb corner of a junction closest to near (an officer's post).
	var parts := junction_id.trim_prefix("Junction_r").split("_c")
	if parts.size() != 2:
		return near
	var centre := RoadGeometry.junction_position(int(parts[0]), int(parts[1]))
	var best := centre
	var best_distance := INF
	for corner in [Vector3(7.2, 0, 7.2), Vector3(-7.2, 0, 7.2), Vector3(7.2, 0, -7.2), Vector3(-7.2, 0, -7.2)]:
		var candidate := centre + (corner as Vector3)
		var distance := candidate.distance_to(near)
		if distance < best_distance:
			best_distance = distance
			best = candidate
	return Vector3(best.x, 0.17, best.z)
