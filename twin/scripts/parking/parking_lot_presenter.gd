extends Node3D

## Draws the city's smart car parks and everything that happens in them. The bridge owns the lots (see scripts/parking_lots.py):
## it decides who may enter and counts the cars; it publishes each lot's occupancy and, for every car inside, its phase
## (entering / parked / leaving) and when that phase began. This presenter only turns those facts into pictures: the barrier
## arm lifts while a car passes, the board shows "05/12", a red warning triangle flashes over the barrier while a car is
## turned away, and each car drives the short way between the kerb and its bay along a path fixed by its bay number.

const RoadGeometry := preload("res://scripts/incidents/road_geometry.gd")
const Zones := preload("res://scripts/city_zones.gd")
const DoorTransit := preload("res://scripts/incidents/vehicle_door_transit.gd")
const ResponderAgent := preload("res://scripts/incidents/responder_agent.gd")
const ProceduralVehicle := preload("res://scripts/vehicles/procedural_vehicle.gd")
const LOTS_PATH := "res://sumo/parking_lots.json"
const LOT_LENGTH := 22.0
const FRONT := 12.0            # street-side edge of the lot surface, metres from the road centreline
const DEPTH := 16.0
const KERB := 5.75
const AISLE_V := 20.0
const BAY_WIDTH := 2.6
const DRIVEWAY_HALF := 2.4
const GROUND_Y := 0.19
const ENTRY_APPROACH_METRES := 6.0

var lots: Dictionary = {}          # id -> {"data", "node", "frame", "arm", "sign_label", "sign_plate", "triangle", "arm_angle", "state"}
var _life: Node
var _packet_time := 0.0
var _packet_local := 0.0
var _paused := false
## Continuous simulation time used by the parking animations. Packet time is
## deliberately not returned directly: a newly received packet is necessarily
## older than the extrapolated frame that was just drawn, so resetting to it
## made every car step backwards several times a second.
var _motion_time := 0.0
var _motion_time_initialized := false
var _nodes: Dictionary = {}        # vehicle id -> car node held in a lot
var _car_phases: Dictionary = {}   # vehicle id -> authoritative parking phase
var _handoff_poses: Dictionary = {} # vehicle id -> exact last rendered SUMO centre/yaw
var _entry_elapsed: Dictionary = {} # vehicle id -> frame-advanced entry time; never packet-fast-forwarded
var _drivers: Dictionary = {}      # vehicle id -> embodied driver while a car is parked
var _claimed_citizens: Dictionary = {} # real SocietyKernel identities currently using a parked car
var _missing_since: Dictionary = {}
var _cleared := false
var _frames := 0

func _ready() -> void:
	name = "ParkingLots"
	var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(LOTS_PATH)) if FileAccess.file_exists(LOTS_PATH) else null
	if not parsed is Dictionary:
		return
	for entry_value in (parsed as Dictionary).get("lots", []) as Array:
		_build_lot(entry_value as Dictionary)
	_life = get_parent().get_node_or_null("CityLife")
	if _life != null:
		_life.connect("sumo_state_applied", _on_state)
		_life.connect("vehicle_handed_off", _on_handoff)

# --- geometry ---------------------------------------------------------------------------------

## World point at u metres along the street and v metres to the right of its centreline, at the lot's anchor.
func world(lot: Dictionary, u: float, v: float) -> Vector3:
	var edge := lot.edge_info as Dictionary
	var base := RoadGeometry.centreline_point(edge, float(lot.data.pos)) + (edge.direction as Vector3) * u + (edge.right as Vector3) * v
	base.y = 0.17
	return base

func bay_centre(slot: int, capacity: int) -> Vector2:
	if slot < 6:
		var row_one: Array[float] = [-3.7, -6.3, -8.9, 3.7, 6.3, 8.9]
		return Vector2(row_one[slot], FRONT + 0.6 + 2.5)
	var row_two: Array[float] = [-6.5, -3.9, -1.3, 1.3, 3.9, 6.5]
	return Vector2(row_two[clampi(slot - 6, 0, 5)], AISLE_V + 2.4 + 2.5)

func bay_yaw(lot: Dictionary, slot: int) -> float:
	var edge := lot.edge_info as Dictionary
	var toward := -(edge.right as Vector3) if slot < 6 else (edge.right as Vector3)
	return RoadGeometry.yaw_of(toward)

## Waypoints (position, yaw) between the kerb and a bay. Entering drives in nose first; leaving backs out of the bay, then drives forward.
func path_for(lot: Dictionary, slot: int, entering: bool) -> Array:
	var edge := lot.edge_info as Dictionary
	var inward := RoadGeometry.yaw_of(edge.right as Vector3)
	var outward := RoadGeometry.yaw_of(-(edge.right as Vector3))
	var along := RoadGeometry.yaw_of(edge.direction as Vector3)
	var bay := bay_centre(slot, int(lot.data.capacity))
	var bay_heading := bay_yaw(lot, slot)
	var aisle_heading := along if bay.x >= 0.0 else along + PI
	var gate := world(lot, 0.0, FRONT + 0.6)
	var live_lane := RoadGeometry.lane_point(edge, float(lot.data.pos) - ENTRY_APPROACH_METRES)
	if entering:
		# The first bend begins in the live lane and ends square in the
		# driveway.  This makes the SUMO -> parking handoff continuous instead
		# of sliding across the kerb with an unrelated yaw.
		var approach := _forward_right_turn(live_lane, edge.direction as Vector3, edge.right as Vector3, gate, 24)
		var interior := _rounded_polyline(PackedVector3Array([
			gate,
			world(lot, 0.0, AISLE_V),
			world(lot, bay.x - signf(bay.x) * 1.2, AISLE_V),
			world(lot, bay.x, AISLE_V + (-1.6 if slot < 6 else 1.6)),
			world(lot, bay.x, bay.y),
		]), 1.5, 6)
		approach.append_array(interior.slice(1))
		return approach
	return _rounded_polyline(PackedVector3Array([
		world(lot, bay.x, bay.y),
		world(lot, bay.x, AISLE_V),
		world(lot, bay.x - signf(bay.x) * 1.2, AISLE_V),
		world(lot, 0.0, AISLE_V),
		gate,
	]), 1.5, 6)

func entry_path_for(vehicle_id: String, lot: Dictionary, slot: int) -> Array:
	## Start at the exact pose that was on screen when SUMO handed the car over.
	## Reconstructing the start from lane geometry caused a visible lateral snap
	## before the actual turn whenever render interpolation lagged the packet.
	var handoff := _handoff_poses.get(vehicle_id, {}) as Dictionary
	if handoff.is_empty():
		return path_for(lot, slot, true)
	var edge := lot.edge_info as Dictionary
	var start := handoff.get("centre", RoadGeometry.lane_point(edge, float(lot.data.pos) - ENTRY_APPROACH_METRES)) as Vector3
	start.y = 0.17
	var gate := world(lot, 0.0, FRONT + 0.6)
	var bay := bay_centre(slot, int(lot.data.capacity))
	var bay_heading := bay_yaw(lot, slot)
	var aisle_heading := RoadGeometry.yaw_of(edge.direction as Vector3) if bay.x >= 0.0 else RoadGeometry.yaw_of(edge.direction as Vector3) + PI
	# Use the authoritative road axes for the turn geometry. The captured visual
	# yaw may contain a small interpolation steering offset; building the path
	# from that offset leaves a diagonal end correction that looks exactly like
	# a sideways slide before the visible turn.
	var approach := _forward_right_turn(start, edge.direction as Vector3, edge.right as Vector3, gate, 24)
	var interior := _rounded_polyline(PackedVector3Array([
		gate,
		world(lot, 0.0, AISLE_V),
		world(lot, bay.x - signf(bay.x) * 1.2, AISLE_V),
		world(lot, bay.x, AISLE_V + (-1.6 if slot < 6 else 1.6)),
		world(lot, bay.x, bay.y),
	]), 1.5, 6)
	approach.append_array(interior.slice(1))
	return approach

func merge_path_for(lot: Dictionary) -> Array:
	## A car leaves the driveway only after a safe gap.  It reverses through a
	## broad curve into the actual live lane and arrives with SUMO's heading, so
	## handing it back cannot create the old instant ninety-degree spin.
	var edge := lot.edge_info as Dictionary
	var gate := world(lot, 0.0, FRONT + 0.6)
	var live_lane := RoadGeometry.lane_point(edge, float(lot.data.pos))
	return _bezier_points(gate, gate - (edge.right as Vector3) * 5.0, live_lane + (edge.direction as Vector3) * 5.0, live_lane, 9)

func _bezier_points(start: Vector3, control_a: Vector3, control_b: Vector3, finish: Vector3, steps: int) -> Array:
	var points: Array = []
	for index in range(steps + 1):
		var t := float(index) / float(maxi(steps, 1))
		var inverse := 1.0 - t
		var point := inverse * inverse * inverse * start + 3.0 * inverse * inverse * t * control_a + 3.0 * inverse * t * t * control_b + t * t * t * finish
		points.append({"p": point, "yaw": 0.0})
	return points

## A mechanically forward-only road-to-driveway turn. The car follows a
## constant-radius quarter circle from its live heading, so its yaw changes on
## the very first moving samples. Once it is pointing into the driveway, any
## remaining distance is travelled straight in that same direction. Unlike a
## free Bezier, this construction cannot translate laterally and turn later.
func _forward_right_turn(start: Vector3, forward_value: Vector3, right_value: Vector3, finish: Vector3, steps: int) -> Array:
	var forward := forward_value.normalized()
	var right := right_value.normalized()
	var to_finish := finish - start
	# Preserve the supplied road side even if the captured SUMO yaw differs by a
	# few interpolation degrees at the custody boundary.
	if to_finish.dot(right) < 0.0:
		right = -right
	var forward_distance := maxf(to_finish.dot(forward), 0.8)
	var lateral_distance := maxf(to_finish.dot(right), forward_distance)
	var radius := minf(forward_distance, lateral_distance)
	var points: Array = []
	for index in range(steps + 1):
		var theta := (PI * 0.5) * float(index) / float(maxi(steps, 1))
		var point := start + forward * (sin(theta) * radius) + right * ((1.0 - cos(theta)) * radius)
		points.append({"p": point, "yaw": RoadGeometry.yaw_of(forward * cos(theta) + right * sin(theta))})
	var arc_end := start + forward * radius + right * radius
	var remaining := maxf(0.0, lateral_distance - radius)
	var straight_end := arc_end + right * remaining
	if remaining > 0.01:
		points.append({"p": straight_end, "yaw": RoadGeometry.yaw_of(right)})
	# With orthogonal authoritative road axes this is only floating-point noise;
	# snapping the final sample keeps the exact physical gate coordinate.
	(points[points.size() - 1] as Dictionary)["p"] = finish
	return points

## Cuts each right-angle waypoint with a bounded quadratic arc. Unlike a
## Catmull-Rom spline this cannot overshoot outside the driveway or a bay, and
## unlike the old polyline it gives the car a tangent it can steer along before
## reaching the corner.
func _rounded_polyline(anchors: PackedVector3Array, radius: float, steps: int) -> Array:
	var points: Array = []
	if anchors.is_empty():
		return points
	points.append({"p": anchors[0], "yaw": 0.0})
	for index in range(1, anchors.size() - 1):
		var previous := anchors[index - 1]
		var corner := anchors[index]
		var following := anchors[index + 1]
		var incoming := corner - previous
		var outgoing := following - corner
		if incoming.length() < 0.01 or outgoing.length() < 0.01:
			continue
		var cut_in := minf(radius, incoming.length() * 0.42)
		var cut_out := minf(radius, outgoing.length() * 0.42)
		var before := corner - incoming.normalized() * cut_in
		var after := corner + outgoing.normalized() * cut_out
		if (points[-1].p as Vector3).distance_to(before) > 0.001:
			points.append({"p": before, "yaw": 0.0})
		for step in range(1, steps + 1):
			var t := float(step) / float(maxi(steps, 1))
			var inverse := 1.0 - t
			var point := inverse * inverse * before + 2.0 * inverse * t * corner + t * t * after
			points.append({"p": point, "yaw": 0.0})
	points.append({"p": anchors[anchors.size() - 1], "yaw": 0.0})
	return points

func pose_on(path: Array, progress: float) -> Dictionary:
	var eased := smoothstep(0.0, 1.0, clampf(progress, 0.0, 1.0))
	var lengths: Array[float] = []
	var total := 0.0
	for index in path.size() - 1:
		var length := ((path[index].p as Vector3) - (path[index + 1].p as Vector3)).length()
		lengths.append(length)
		total += length
	var target := total * eased
	for index in lengths.size():
		if target <= lengths[index] or index == lengths.size() - 1:
			var local := clampf(target / maxf(lengths[index], 0.001), 0.0, 1.0)
			var p1 := (path[index] as Dictionary).p as Vector3
			var p2 := (path[index + 1] as Dictionary).p as Vector3
			# The approach and merge portions are already densely sampled Beziers.
			# Interpolating those samples directly keeps the car on the intended
			# road-to-driveway curve; Catmull-Rom overshot their lateral control
			# points and was the source of the visible side-slide/jitter.
			var position := p1.lerp(p2, local)
			# Look a short, equal distance behind and ahead on the complete path.
			# Using only this polyline segment made yaw jump at every sampled curve
			# boundary even though position itself was continuous.
			var tangent_span := minf(0.24, maxf(total * 0.01, 0.08))
			var before := _point_at_path_distance(path, lengths, maxf(0.0, total * eased - tangent_span))
			var after := _point_at_path_distance(path, lengths, minf(total, total * eased + tangent_span))
			var tangent := after - before
			if tangent.length_squared() < 0.000001:
				tangent = p2 - p1
			return {"p": position, "yaw": RoadGeometry.yaw_of(tangent), "segment": index, "local": local}
		target -= lengths[index]
	return {"p": path[path.size() - 1].p, "yaw": path[path.size() - 1].yaw, "segment": path.size() - 2, "local": 1.0}

func _point_at_path_distance(path: Array, lengths: Array[float], distance: float) -> Vector3:
	var remaining := maxf(distance, 0.0)
	for index in lengths.size():
		var segment_length := maxf(lengths[index], 0.001)
		if remaining <= segment_length or index == lengths.size() - 1:
			var start := (path[index] as Dictionary).p as Vector3
			var finish := (path[index + 1] as Dictionary).p as Vector3
			return start.lerp(finish, clampf(remaining / segment_length, 0.0, 1.0))
		remaining -= segment_length
	return (path[path.size() - 1] as Dictionary).p as Vector3

# --- building the lots -------------------------------------------------------------------------

func _build_lot(data: Dictionary) -> void:
	var edge := RoadGeometry.parse_edge(str(data.edge))
	if edge.is_empty():
		return
	var lot := {"data": data, "edge_info": edge, "arm_angle": 0.0, "state": {}, "blink": 0.0}
	var root := Node3D.new()
	root.name = "ParkingLot_%s" % str(data.id)
	var direction := edge.direction as Vector3
	root.transform = Transform3D(Basis(direction, Vector3.UP, direction.cross(Vector3.UP)), world(lot, 0.0, 0.0))
	root.set_meta("lot_id", str(data.id))
	root.set_meta("zone", str(data.zone))
	root.add_to_group("parking_lots")
	add_child(root)
	lot["node"] = root
	_asphalt(root, lot)
	_bay_lines(root, lot)
	_barrier(root, lot)
	_board(root, lot)
	_warning_triangle(root, lot)
	lots[str(data.id)] = lot

## Local coordinates: x along the street, z towards the road (so a point v metres to the right of the centreline has z = -v).
func _local(u: float, y: float, v: float) -> Vector3:
	return Vector3(u, y, -v)

func _material(colour: Color, roughness := 0.9, emission := 0.0) -> StandardMaterial3D:
	var material := StandardMaterial3D.new()
	material.albedo_color = colour
	material.roughness = roughness
	if emission > 0.0:
		material.emission_enabled = true
		material.emission = colour
		material.emission_energy_multiplier = emission
	return material

func _box(parent: Node3D, label: String, size: Vector3, local: Vector3, material: Material) -> MeshInstance3D:
	var mesh := MeshInstance3D.new()
	mesh.name = label
	var box := BoxMesh.new()
	box.size = size
	mesh.mesh = box
	mesh.material_override = material
	mesh.position = local
	parent.add_child(mesh)
	return mesh

func _asphalt(root: Node3D, _lot: Dictionary) -> void:
	var asphalt := _material(Color(0.12, 0.125, 0.135), 0.95)
	_box(root, "Surface", Vector3(LOT_LENGTH, 0.06, DEPTH), _local(0.0, GROUND_Y - 0.03, FRONT + DEPTH * 0.5), asphalt)
	_box(root, "Driveway", Vector3(DRIVEWAY_HALF * 2.0 + 0.4, 0.06, FRONT - KERB + 0.6), _local(0.0, GROUND_Y - 0.03, (FRONT + KERB) * 0.5 - 0.3), asphalt)
	var kerb := _material(Color(0.62, 0.62, 0.6), 0.8)
	_box(root, "KerbLeft", Vector3(LOT_LENGTH, 0.14, 0.25), _local(0.0, 0.2, FRONT - 0.12), kerb).visible = false

func _bay_lines(root: Node3D, lot: Dictionary) -> void:
	var paint := _material(Color(0.93, 0.93, 0.9), 0.7)
	var surface := SurfaceTool.new()
	surface.begin(Mesh.PRIMITIVE_TRIANGLES)
	var capacity := int(lot.data.capacity)
	for slot in capacity:
		var centre := bay_centre(slot, capacity)
		var half_width := BAY_WIDTH * 0.5 - 0.06
		var near := centre.y - (2.4 if slot < 6 else 2.4)
		var far := centre.y + (2.4 if slot < 6 else 2.4)
		for side in [-1.0, 1.0]:
			_strip(surface, centre.x + side * half_width, near, centre.x + side * half_width, far, 0.09)
		var back := far if slot >= 6 else near
		_strip(surface, centre.x - half_width, back, centre.x + half_width, back, 0.09)
	var mesh := MeshInstance3D.new()
	mesh.name = "BayLines"
	mesh.mesh = surface.commit()
	mesh.material_override = paint
	root.add_child(mesh)

func _strip(surface: SurfaceTool, u0: float, v0: float, u1: float, v1: float, width: float) -> void:
	var a := _local(u0, GROUND_Y + 0.005, v0)
	var b := _local(u1, GROUND_Y + 0.005, v1)
	var side := Vector3(0.0, 0.0, 1.0) if absf(u1 - u0) > absf(v1 - v0) else Vector3(1.0, 0.0, 0.0)
	var o := side * width * 0.5
	for point in [a - o, b - o, b + o, a - o, b + o, a + o]:
		surface.set_normal(Vector3.UP)
		surface.add_vertex(point)

func _barrier(root: Node3D, lot: Dictionary) -> void:
	var post_material := _material(Color(0.2, 0.22, 0.25), 0.5)
	var v := FRONT - 0.2
	_box(root, "PostLeft", Vector3(0.3, 1.25, 0.3), _local(-DRIVEWAY_HALF - 0.3, 0.8, v), post_material)
	_box(root, "PostRight", Vector3(0.3, 1.0, 0.3), _local(DRIVEWAY_HALF + 0.3, 0.7, v), post_material)
	var hinge := Node3D.new()
	hinge.name = "ArmHinge"
	hinge.position = _local(-DRIVEWAY_HALF - 0.12, 1.05, v)
	root.add_child(hinge)
	var arm := MeshInstance3D.new()
	arm.name = "Arm"
	var stripes := ImageTexture.create_from_image(_stripe_image())
	var arm_box := BoxMesh.new()
	arm_box.size = Vector3(DRIVEWAY_HALF * 2.0 + 0.06, 0.13, 0.11)
	arm.mesh = arm_box
	var arm_material := StandardMaterial3D.new()
	arm_material.albedo_texture = stripes
	arm_material.texture_filter = BaseMaterial3D.TEXTURE_FILTER_LINEAR
	arm_material.uv1_scale = Vector3(6.0, 1.0, 1.0)
	arm_material.roughness = 0.6
	arm.material_override = arm_material
	arm.position = Vector3(DRIVEWAY_HALF + 0.03, 0.0, 0.0)
	hinge.add_child(arm)
	var lamp := _box(root, "BarrierLamp", Vector3(0.18, 0.18, 0.18), _local(DRIVEWAY_HALF + 0.3, 1.32, v), _material(Color(0.1, 1.0, 0.3), 0.4, 3.0))
	lot["hinge"] = hinge
	lot["lamp"] = lamp

func _stripe_image() -> Image:
	var image := Image.create(32, 4, false, Image.FORMAT_RGB8)
	for x in 32:
		var red := (x / 4) % 2 == 0
		for y in 4:
			image.set_pixel(x, y, Color(0.9, 0.08, 0.08) if red else Color(0.96, 0.96, 0.96))
	return image

func _board(root: Node3D, lot: Dictionary) -> void:
	var post_material := _material(Color(0.2, 0.22, 0.25), 0.5)
	var u := DRIVEWAY_HALF + 1.7
	_box(root, "BoardPost", Vector3(0.2, 3.1, 0.2), _local(u, 1.55, FRONT - 0.5), post_material)
	var plate := _box(root, "BoardPlate", Vector3(3.0, 1.45, 0.12), _local(u, 3.3, FRONT - 0.5), _material(Color(0.04, 0.07, 0.1), 0.4))
	var title := Label3D.new()
	title.name = "BoardTitle"
	title.text = "PARKING"
	title.font_size = 64
	title.pixel_size = 0.0095
	title.modulate = Color(0.72, 0.84, 0.96)
	title.position = Vector3(0.0, 0.46, 0.07)
	title.double_sided = false
	plate.add_child(title)
	var count := Label3D.new()
	count.name = "BoardCount"
	count.text = "00/12"
	count.font_size = 150
	count.pixel_size = 0.0098
	count.modulate = Color(0.2, 1.0, 0.35)
	count.outline_size = 12
	count.position = Vector3(0.0, -0.12, 0.07)
	count.double_sided = false
	plate.add_child(count)
	# The same board seen from behind the lot, so the count reads from both sides.
	var back := count.duplicate() as Label3D
	back.name = "BoardCountBack"
	back.position = Vector3(0.0, -0.12, -0.07)
	back.rotation.y = PI
	plate.add_child(back)
	lot["count_label"] = count
	lot["count_back"] = back
	lot["title_label"] = title

func _warning_triangle(root: Node3D, lot: Dictionary) -> void:
	var holder := Node3D.new()
	holder.name = "WarningTriangle"
	holder.position = _local(0.0, 3.5, FRONT - 0.4)
	holder.visible = false
	root.add_child(holder)
	holder.add_child(_triangle("TriangleRed", 1.0, 0.0, _material(Color(0.95, 0.08, 0.06), 0.4, 2.4)))
	holder.add_child(_triangle("TriangleWhite", 0.66, 0.03, _material(Color(1.0, 0.98, 0.95), 0.5, 1.4)))
	var mark := Label3D.new()
	mark.text = "!"
	mark.font_size = 220
	mark.pixel_size = 0.0075
	mark.modulate = Color(0.08, 0.08, 0.08)
	mark.position = Vector3(0.0, -0.12, 0.1)
	mark.double_sided = false
	holder.add_child(mark)
	var mark_back := mark.duplicate() as Label3D
	mark_back.position = Vector3(0.0, -0.12, -0.1)
	mark_back.rotation.y = PI
	holder.add_child(mark_back)
	lot["triangle"] = holder

## An equilateral warning triangle (apex up), `scale` times the full size, thickened to 0.1 m so it reads from both sides.
func _triangle(label: String, scale: float, lift: float, material: Material) -> MeshInstance3D:
	var height := 1.75 * scale
	var half := 1.0 * scale
	var apex := Vector3(0.0, height * 0.6, 0.0)
	var left := Vector3(-half, -height * 0.4, 0.0)
	var right := Vector3(half, -height * 0.4, 0.0)
	var surface := SurfaceTool.new()
	surface.begin(Mesh.PRIMITIVE_TRIANGLES)
	for side in [1.0, -1.0]:
		var z := (0.05 + lift) * float(side)
		var points := [apex + Vector3(0, 0, z), left + Vector3(0, 0, z), right + Vector3(0, 0, z)]
		if side < 0.0:
			points = [points[0], points[2], points[1]]
		for point in points:
			surface.set_normal(Vector3(0.0, 0.0, side))
			surface.add_vertex(point)
	var mesh := MeshInstance3D.new()
	mesh.name = label
	mesh.mesh = surface.commit()
	mesh.material_override = material
	return mesh

# --- clearing the sites -------------------------------------------------------------------------

func _clear_sites() -> void:
	var wanted := {}
	for lot_value in lots.values():
		for label in (lot_value.data as Dictionary).get("clear", []) as Array:
			wanted[str(label)] = true
	if wanted.is_empty():
		return
	for group_name in ["backdrop_buildings", "urban_props", "city_trees", "buildings"]:
		for value in get_tree().get_nodes_in_group(group_name):
			var node := value as Node3D
			if node != null and wanted.has(str(node.name)):
				node.visible = false
				node.set_meta("cleared_for_parking", true)
	for found in get_tree().root.find_children("Street*", "Node3D", true, false):
		if wanted.has(str(found.name)):
			(found as Node3D).visible = false
			found.set_meta("cleared_for_parking", true)

# --- live state ---------------------------------------------------------------------------------

func _on_state(state: Dictionary) -> void:
	_packet_time = float(state.get("time", _packet_time))
	_packet_local = Time.get_ticks_msec() / 1000.0
	_paused = bool((state.get("operator", {}) as Dictionary).get("paused", false))
	if not _motion_time_initialized:
		_motion_time = _packet_time - 0.12
		_motion_time_initialized = true
	elif _paused:
		# Pausing may deliver the final authoritative tick after the last frame.
		# It is safe to move forward to it, but never rewind an already drawn pose.
		_motion_time = maxf(_motion_time, _packet_time)
	else:
		# Rendering can briefly run below real time while the full city finishes
		# loading. Delta-only advancement then falls seconds behind the bridge:
		# the backend completes `entering` before the visible car has moved and
		# the next phase places it directly in the bay. Keep a monotonic floor one
		# small interpolation interval behind the newest authoritative packet.
		_motion_time = maxf(_motion_time, _packet_time - 0.12)
	var parking := state.get("parking", {}) as Dictionary
	for lot_state_value in parking.get("lots", []) as Array:
		var lot_state := lot_state_value as Dictionary
		if lots.has(str(lot_state.id)):
			(lots[str(lot_state.id)] as Dictionary).state = lot_state
	_parking_meta = parking
	var present := {}
	for vehicle_value in state.get("vehicles", []) as Array:
		present[str((vehicle_value as Dictionary).get("id", ""))] = true
	# A car that is driving in SUMO again leaves the presenter's custody.
	for key in _nodes.keys():
		if present.has(key):
			var released_node := _nodes.get(key) as Node3D
			if released_node != null and is_instance_valid(released_node) and released_node.is_in_group("npc_dynamic_obstacles"):
				released_node.remove_from_group("npc_dynamic_obstacles")
			if _life != null:
				_life.call("set_vehicle_claimed", key, false)
			_nodes.erase(key)
			_handoff_poses.erase(key)
			_entry_elapsed.erase(key)
	var parked_ids := {}
	_car_phases.clear()
	for lot_value in lots.values():
		for car_value in ((lot_value as Dictionary).state.get("cars", []) as Array):
			var parked_car := car_value as Dictionary
			parked_ids[str(parked_car.id)] = true
			_car_phases[str(parked_car.id)] = str(parked_car.phase)
	for key in _drivers.keys():
		var record := _drivers[key] as Dictionary
		# An evacuated car park (zone closed): the car pulls out without the walk back and boarding, its driver is
		# taken as already inside, so the citizen is removed as the car leaves its bay.
		var evacuated := not bool(record.get("boarded", false)) and not bool(record.get("boarding", false)) and str(_car_phases.get(key, "gone")) in ["departure_wait", "leaving", "merge_wait", "merge_turn", "gone"]
		if (not parked_ids.has(key) and bool(record.get("boarded", false))) or evacuated:
			var agent := record.get("agent") as Node
			if is_instance_valid(agent):
				agent.call("dispose")
			_claimed_citizens.erase(str(record.get("citizen_id", "")))
			_drivers.erase(key)
	_adopt_unseen_cars()

var _parking_meta: Dictionary = {}

func _adopt_unseen_cars() -> void:
	if _life == null:
		return
	for lot_value in lots.values():
		for car_value in ((lot_value as Dictionary).state.get("cars", []) as Array):
			var car := car_value as Dictionary
			var key := str(car.id)
			if _nodes.has(key):
				continue
			var node := _life.call("acquire_keyed_vehicle", key, str(car.get("cls", "car"))) as Node3D
			if node != null:
				_nodes[key] = node

func _on_handoff(key: String, node: Node3D, handoff: Dictionary) -> void:
	if str(handoff.get("reason", "")) == "park" and node != null:
		_nodes[key] = node
		var visible_pose := _life.call("get_keyed_pose", key) as Dictionary if _life != null else {}
		_handoff_poses[key] = visible_pose if not visible_pose.is_empty() else {"centre": node.global_position, "yaw": node.rotation.y}

func sim_now() -> float:
	return _motion_time if _motion_time_initialized else _packet_time

func _advance_motion_clock(delta: float) -> void:
	if not _motion_time_initialized or _paused:
		return
	# `delta` already contains Engine.time_scale, so it advances at exactly the
	# selected simulation speed. Packet correction is forward-only and capped;
	# network arrival jitter can never pull a rendered car back down its path.
	_motion_time += maxf(delta, 0.0)
	var rate := clampf(float(_life.get("sim_rate")), 0.0, 8.0) if _life != null else 1.0
	var packet_target := _packet_time + maxf(0.0, Time.get_ticks_msec() / 1000.0 - _packet_local) * rate
	var forward_error := packet_target - _motion_time
	if forward_error > 0.0:
		_motion_time += minf(forward_error, maxf(delta * 0.18, 0.001))

func _process(delta: float) -> void:
	_frames += 1
	if not _cleared and _frames == 4:
		_cleared = true
		_clear_sites()
	if _frames == 120:
		_clear_sites()
	_advance_motion_clock(delta)
	var now := sim_now()
	var enter_s := float(_parking_meta.get("enter_s", 6.5))
	var leave_s := float(_parking_meta.get("leave_s", 6.5))
	var merge_s := float(_parking_meta.get("merge_s", 2.8))
	for lot_value in lots.values():
		var lot := lot_value as Dictionary
		var state := lot.state as Dictionary
		_update_board(lot, state)
		_update_barrier(lot, state, delta)
		_update_warning(lot, state, delta)
		for car_value in state.get("cars", []) as Array:
			var car := car_value as Dictionary
			var car_id := str(car.id)
			var node := _nodes.get(car_id) as Node3D
			if node == null or not is_instance_valid(node):
				continue
			var slot := int(car.slot)
			var phase := str(car.phase)
			if phase == "entry_wait":
				_entry_elapsed[car_id] = 0.0
			elif phase == "entering" and not _entry_elapsed.has(car_id):
				# The first parking-owned frame must remain at the exact SUMO
				# handoff pose. Packet time may already be seconds ahead after a
				# heavy city frame, so entry motion is advanced only by frame delta.
				_entry_elapsed[car_id] = 0.0
			var entry_elapsed := float(_entry_elapsed.get(car_id, enter_s))
			var entry_visual_active := phase == "entering" or (_entry_elapsed.has(car_id) and entry_elapsed < enter_s and phase in ["alighting", "parked"])
			var visual_phase := "entering" if entry_visual_active else phase
			var pose: Dictionary
			if entry_visual_active:
				pose = pose_on(entry_path_for(car_id, lot, slot), entry_elapsed / enter_s)
			elif phase == "leaving":
				pose = pose_on(path_for(lot, slot, false), (now - float(car.t0)) / leave_s)
				# The parked car reverses as one continuous manoeuvre.  Its body keeps
				# its parking heading; there is no mid-driveway 180-degree pose flip.
				pose["yaw"] = float(pose.yaw) + PI
			elif phase == "merge_wait":
				pose = pose_on(path_for(lot, slot, false), 1.0)
				pose["yaw"] = float(pose.yaw) + PI
			elif phase == "merge_turn":
				pose = pose_on(merge_path_for(lot), (now - float(car.t0)) / merge_s)
				pose["yaw"] = float(pose.yaw) + PI
			elif phase == "entry_wait":
				pose = pose_on(entry_path_for(car_id, lot, slot), 0.0)
			else:
				pose = pose_on(path_for(lot, slot, true), 1.0)
			var position := pose.p as Vector3
			var driveway_moving := visual_phase in ["entering", "leaving", "merge_wait", "merge_turn"]
			if driveway_moving:
				if not node.is_in_group("npc_dynamic_obstacles"):
					node.add_to_group("npc_dynamic_obstacles")
				node.set_meta("npc_dynamic_obstacle_radius", 1.75)
			elif node.is_in_group("npc_dynamic_obstacles"):
				node.remove_from_group("npc_dynamic_obstacles")
			if _life != null:
				_life.call("place_keyed_vehicle", car_id, position, float(pose.yaw))
			else:
				position.y = node.global_position.y
				node.global_position = position
				node.rotation.y = float(pose.yaw)
			node.visible = true
			_update_car_lamps(node, visual_phase, pose, entry_elapsed if entry_visual_active else now - float(car.t0), leave_s)
			# If the backend crossed into parked while rendering was catching up,
			# still materialize the real driver once the car physically reaches its
			# bay; never skip the alighting lifecycle because of packet lead.
			var driver_phase := "alighting" if (not entry_visual_active and phase == "parked" and not _drivers.has(car_id)) else phase
			_update_driver(car_id, node, lot, slot, driver_phase)
			if entry_visual_active:
				_entry_elapsed[car_id] = minf(enter_s, entry_elapsed + maxf(delta, 0.0))
			elif phase != "entry_wait":
				_entry_elapsed.erase(car_id)

func _update_car_lamps(node: Node3D, phase: String, pose: Dictionary, elapsed: float, leave_s: float) -> void:
	var signals := 0
	if phase == "entry_wait":
		signals = ProceduralVehicle.SIGNAL_BLINKER_EMERGENCY | ProceduralVehicle.SIGNAL_BRAKELIGHT
	elif phase == "entering":
		# The approach turns right through the barrier, then left/right into a bay.
		signals = ProceduralVehicle.SIGNAL_BLINKER_RIGHT if int(pose.get("segment", 0)) < 3 else ProceduralVehicle.SIGNAL_BLINKER_LEFT
	elif phase == "leaving":
		signals = ProceduralVehicle.SIGNAL_BACKDRIVE | ProceduralVehicle.SIGNAL_BLINKER_EMERGENCY
	elif phase == "merge_wait":
		signals = ProceduralVehicle.SIGNAL_BACKDRIVE | ProceduralVehicle.SIGNAL_BRAKELIGHT | ProceduralVehicle.SIGNAL_BLINKER_EMERGENCY
	elif phase == "merge_turn":
		signals = ProceduralVehicle.SIGNAL_BACKDRIVE | ProceduralVehicle.SIGNAL_BLINKER_LEFT
	elif phase in ["alighting", "boarding"]:
		signals = ProceduralVehicle.SIGNAL_BRAKELIGHT | ProceduralVehicle.SIGNAL_BLINKER_EMERGENCY
	ProceduralVehicle.set_lamps(node, 0, signals)

func _update_driver(vehicle_id: String, vehicle: Node3D, lot: Dictionary, slot_index: int, phase: String) -> void:
	if phase == "alighting" and not _drivers.has(vehicle_id):
		_spawn_driver(vehicle_id, vehicle, lot, slot_index)
	var record := _drivers.get(vehicle_id, {}) as Dictionary
	if record.is_empty():
		return
	var agent := record.agent as Node
	if bool(record.get("alighted", false)) and not bool(record.get("inside", false)) and not agent.call("is_moving") and not agent.call("is_transiting"):
		# The driver has reached their actual agenda destination.  Keep this
		# embodied SocietyKernel citizen in the world (rather than hiding a prop
		# at the edge of the lot) until their car trip back begins.
		record["inside"] = true
		agent.call("perform", str(record.get("activity_clip", "Interact")), -1.0)
		_complete_society_trip(record)
	if phase == "returning" and not bool(record.get("returning", false)):
		record["returning"] = true
		_begin_society_return_trip(record)
		var return_path := _return_path_from(record, agent.call("position") as Vector3)
		var door_point := DoorTransit.nearest_slot(vehicle, agent.call("position") as Vector3)
		if not door_point.is_empty():
			return_path.append(door_point.outside as Vector3)
		agent.call("walk_path", return_path)
	if phase == "returning" and bool(record.get("returning", false)) and not bool(record.get("at_car_sent", false)) and not agent.call("is_moving") and not agent.call("is_transiting"):
		record["at_car_sent"] = true
		_send_parking_driver_event(vehicle_id, "at_car")
	if phase == "boarding" and bool(record.get("returning", false)) and not bool(record.get("boarding", false)) and not bool(record.get("boarded", false)) and not agent.call("is_moving") and not agent.call("is_transiting"):
		record["boarding"] = true
		var door := DoorTransit.nearest_slot(vehicle, agent.call("position") as Vector3)
		DoorTransit.board(vehicle, agent, door, func() -> void:
			record["boarded"] = true
			record.erase("boarding")
			_complete_society_trip(record)
			_send_parking_driver_event(vehicle_id, "boarded")
		)

func _send_parking_driver_event(vehicle_id: String, event: String) -> void:
	var control_room := get_tree().current_scene as Node
	if control_room != null and control_room.has_method("_send_bridge"):
		control_room.call("_send_bridge", {"command": "parking_driver", "vehicle": vehicle_id, "event": event})

func _return_path_from(record: Dictionary, current: Vector3) -> PackedVector3Array:
	var outbound := record.get("outbound_path", PackedVector3Array()) as PackedVector3Array
	if outbound.is_empty():
		return PackedVector3Array([current])
	var nearest := 0
	var nearest_distance := INF
	for index in outbound.size():
		var distance := current.distance_squared_to(outbound[index])
		if distance < nearest_distance:
			nearest = index
			nearest_distance = distance
	var returning := PackedVector3Array([current])
	for index in range(nearest, -1, -1):
		returning.append(outbound[index])
	return returning

func _spawn_driver(vehicle_id: String, vehicle: Node3D, lot: Dictionary, slot_index: int) -> void:
	var model_names := ["Male_Adult_01", "Female_Adult_01", "Male_Adult_05", "Female_Adult_05"]
	var model := str(model_names[absi(hash(vehicle_id)) % model_names.size()])
	var agent := ResponderAgent.new()
	add_child(agent)
	var start_pose := pose_on(path_for(lot, slot_index, true), 1.0)
	if not agent.setup(self, "res://assets/rocketbox/%s/Export/%s.fbx" % [model, model], "civilian", "parking_%s" % vehicle_id, start_pose.p as Vector3, float(start_pose.yaw)):
		agent.queue_free()
		return
	var slot := DoorTransit.slot_for(vehicle, 0, 1.0)
	if slot.is_empty():
		# Never leave an orphaned person in a bay if this particular vehicle
		# model has no usable door marker.
		agent.call("dispose")
		return
	agent.set("dynamic_blocker", func(_ignored: Node, point: Vector3) -> bool:
		# The vehicle trajectory is authoritative and continuous; people yield to
		# a moving parking car. Previously both sides tried to yield, the car was
		# frozen for its whole timed phase, then teleported to the bay when that
		# phase expired.
		for parking_key in _nodes.keys():
			if str(_car_phases.get(parking_key, "")) in ["entering", "leaving", "merge_turn"]:
				var moving_car := _nodes.get(parking_key) as Node3D
				if moving_car != null and is_instance_valid(moving_car) and moving_car.global_position.distance_to(point) < 1.9:
					return true
		for citizen_value in get_tree().get_nodes_in_group("pedestrians"):
			var citizen := citizen_value as Node3D
			if citizen != null and citizen != agent.get("body") and citizen.visible and citizen.global_position.distance_to(point) < 0.75:
				return true
		return false
	)
	var identity := _claim_society_driver(vehicle_id, str(lot.data.id))
	var npc := get_parent().get_node_or_null("NpcAi") as Node
	var bay := bay_centre(slot_index, int(lot.data.capacity))
	var side_sign := signf(bay.x) if absf(bay.x) > 0.1 else (-1.0 if posmod(absi(hash(vehicle_id)), 2) == 0 else 1.0)
	var side_u := side_sign * (LOT_LENGTH * 0.5 + 1.4)
	var pedestrian_exit := world(lot, side_u, 9.4)
	var plan: Dictionary = {}
	if npc != null:
		plan = npc.call("parking_citizen_plan", str(identity.get("citizen_id", "")), pedestrian_exit) as Dictionary
	if plan.is_empty():
		var abandoned := {"citizen_id": str(identity.get("citizen_id", "")), "trip_id": str(identity.get("trip_id", ""))}
		_complete_society_trip(abandoned)
		_claimed_citizens.erase(str(identity.get("citizen_id", "")))
		agent.call("dispose")
		return
	var city_path := plan.outbound_path as PackedVector3Array
	var outbound_path := PackedVector3Array([
		start_pose.p as Vector3,
		world(lot, bay.x, AISLE_V),
		world(lot, side_u, AISLE_V),
		world(lot, side_u, FRONT + 1.2),
		pedestrian_exit,
	])
	for city_index in range(1, city_path.size()):
		outbound_path.append(city_path[city_index])
	var destination := plan.destination as Vector3
	var record := {"agent": agent, "lot": str(lot.data.id), "slot": slot_index, "alighted": false, "boarded": false, "inside": false, "returning": false, "destination": destination, "outbound_path": outbound_path, "activity_clip": str(plan.get("activity_clip", "Interact")), "return_yaw": float(start_pose.yaw), "citizen_id": str(identity.get("citizen_id", "")), "activity": plan.get("activity", identity.get("activity", {})), "trip_id": str(identity.get("trip_id", ""))}
	_drivers[vehicle_id] = record
	var body := agent.get("body") as Node3D
	body.set_meta("citizen_id", str(record.citizen_id))
	body.set_meta("citizen_activity", (record.activity as Dictionary).duplicate(true))
	body.set_meta("citizen_decision_reason", "the daily agenda requires this journey from the parked car to a real city activity")
	DoorTransit.alight(vehicle, agent, slot, 0.25, func() -> void:
		record["alighted"] = true
		var path := outbound_path.duplicate()
		path[0] = agent.call("position") as Vector3
		agent.call("walk_path", path)
	)

func _society() -> Node:
	var npc := get_parent().get_node_or_null("NpcAi") as Node
	return npc.get("_society") as Node if npc != null else null

func _claim_society_driver(vehicle_id: String, lot_id: String) -> Dictionary:
	var society := _society()
	if society == null:
		return {}
	var first := 33 + posmod(absi(hash(vehicle_id)), 168)
	for offset in 168:
		var citizen_id := "citizen_%04d" % (33 + posmod(first - 33 + offset, 168))
		if _claimed_citizens.has(citizen_id):
			continue
		var state := society.call("get_citizen_public_state", citizen_id) as Dictionary
		if state.is_empty() or not str(state.get("active_trip_id", "")).is_empty():
			continue
		var activity := state.get("current_activity", {}) as Dictionary
		var destination_id := str(activity.get("location_id", "neighbourhood"))
		var trip_id := str(society.call("begin_trip", citizen_id, "parking_%s" % lot_id, destination_id, "car", 3.0))
		if not trip_id.is_empty():
			_claimed_citizens[citizen_id] = vehicle_id
			return {"citizen_id": citizen_id, "activity": activity, "trip_id": trip_id}
	return {}

func _begin_society_return_trip(record: Dictionary) -> void:
	var citizen_id := str(record.get("citizen_id", ""))
	var society := _society()
	if citizen_id.is_empty() or society == null:
		return
	var activity := record.get("activity", {}) as Dictionary
	record["trip_id"] = str(society.call("begin_trip", citizen_id, str(activity.get("location_id", "neighbourhood")), "vehicle_return", "walk", 1.0))

func _complete_society_trip(record: Dictionary) -> void:
	var citizen_id := str(record.get("citizen_id", ""))
	var trip_id := str(record.get("trip_id", ""))
	var society := _society()
	if citizen_id.is_empty() or trip_id.is_empty() or society == null:
		return
	society.call("complete_trip", citizen_id, trip_id, "arrived")
	record["trip_id"] = ""

func _update_board(lot: Dictionary, state: Dictionary) -> void:
	if not state.has("occupied"):
		return
	var occupied := int(state.occupied)
	var capacity := int(state.capacity)
	var full := occupied >= capacity
	var closed := str(state.get("barrier_mode", "auto")) == "closed"
	var text := "%02d/%02d" % [occupied, capacity]
	var colour := Color(0.2, 1.0, 0.35)
	if closed:
		colour = Color(1.0, 0.25, 0.2)
		text = "CLOSED"
	elif full:
		colour = Color(1.0, 0.2, 0.15)
	elif float(occupied) / float(maxi(capacity, 1)) >= 0.75:
		colour = Color(1.0, 0.78, 0.2)
	for key in ["count_label", "count_back"]:
		var label := lot[key] as Label3D
		label.text = text
		label.modulate = colour
	(lot.title_label as Label3D).text = "FULL" if (full and not closed) else "PARKING"
	((lot.lamp as MeshInstance3D).material_override as StandardMaterial3D).albedo_color = colour
	((lot.lamp as MeshInstance3D).material_override as StandardMaterial3D).emission = colour

func _update_barrier(lot: Dictionary, state: Dictionary, delta: float) -> void:
	var target := deg_to_rad(82.0) if bool(state.get("gate_open", false)) else 0.0
	lot.arm_angle = lerpf(float(lot.arm_angle), target, 1.0 - exp(-7.0 * delta))
	(lot.hinge as Node3D).rotation.z = float(lot.arm_angle)

func _update_warning(lot: Dictionary, state: Dictionary, delta: float) -> void:
	var holder := lot.triangle as Node3D
	var show := bool(state.get("warning", false))
	lot.blink = float(lot.blink) + delta
	holder.visible = show and (int(float(lot.blink) * 3.0) % 2 == 0)

# --- reports for the device interface ----------------------------------------------------------------

func lot_report(lot_id: String) -> Dictionary:
	if not lots.has(lot_id):
		return {}
	var lot := lots[lot_id] as Dictionary
	var state := lot.state as Dictionary
	var data := lot.data as Dictionary
	var gate := Vector3(float(data.x), 0.17, float(data.z))
	return {
		"id": lot_id, "zone": str(data.zone), "edge": str(data.edge), "kerb_position_m": float(data.pos), "location": {"x": gate.x, "y": gate.y, "z": gate.z},
		"capacity": int(state.get("capacity", data.capacity)), "occupied": int(state.get("occupied", 0)), "free": int(state.get("capacity", data.capacity)) - int(state.get("occupied", 0)),
		"incoming": int(state.get("incoming", 0)), "barrier_mode": str(state.get("barrier_mode", "auto")), "gate_open": bool(state.get("gate_open", false)),
		"warning": bool(state.get("warning", false)), "denied_entries": int(state.get("denied_total", 0)), "entered_total": int(state.get("entered_total", 0)),
		"left_total": int(state.get("left_total", 0)), "redirected_total": int(state.get("redirected_total", 0)),
		"cars": _cars_json(state.get("cars", []) as Array),
	}

func all_reports() -> Array:
	var out: Array = []
	for lot_id in lots:
		out.append(lot_report(str(lot_id)))
	return out

func note_mode(lot_id: String, mode: String) -> void:
	if lots.has(lot_id):
		((lots[lot_id] as Dictionary).state as Dictionary)["barrier_mode"] = mode

func _cars_json(cars: Array) -> Array:
	var out: Array = []
	for value in cars:
		var car := (value as Dictionary).duplicate()
		car["slot"] = int(car.get("slot", 0))
		out.append(car)
	return out
