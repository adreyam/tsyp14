extends RefCounted

## Street furniture laid out by rules, not scattered. Every block is ringed by a
## footpath whose kerb is 40 m from the block centre. Furniture follows the kerb:
## seating comes in pairs with a bin at the end and planters flanking it, hydrants sit
## a few metres before the corners, cabinets and bike racks stand where people can
## reach them from the path. Each piece faces the footpath, is aligned to the edge,
## stands on the pavement and is rejected if it touches a building, the road or
## another piece.

const PropFactory := preload("res://scripts/props/prop_factory.gd")
const CitySpatialSafety := preload("res://scripts/city_spatial_safety.gd")

const PAVEMENT_Y := 0.16
const BLOCK_PITCH := 95.0
const BLOCK_ORIGIN := -142.5
const KERB := 40.0
const PIECE_GAP := 0.25

## (kind, distance of the piece's centre from the block centre line, along-edge offset,
## front direction: "in" = toward the footpath/building, "out" = toward the road)
const AMENITY_SIDE := [
	["bench", 39.45, -11.0, "in"], ["trash", 39.55, -9.1, "in"], ["planter", 39.35, -14.2, "in"],
	["bench", 39.45, 11.0, "in"], ["trash", 39.55, 13.0, "in"],
	["planter", 39.35, 19.5, "in"],
	["drinking_fountain", 39.5, 2.0, "in"], ["vending_machine", 39.4, -21.0, "in"],
	["coffee_cart", 36.2, -3.0, "out"], ["picnic_table", 35.2, 25.0, "out"],
]
const UTILITY_SIDE := [
	["hydrant", 39.6, -31.0, "in"], ["power", 39.45, 23.0, "in"], ["trash", 39.55, 6.0, "in"],
	["bicycle", 39.3, -9.0, "in"], ["bicycle", 39.3, -3.6, "in"],
	["parking_meter", 39.75, -22.0, "in"], ["parking_meter", 39.75, -16.0, "in"],
	["payphone", 39.45, 14.0, "in"], ["sandwich_board", 36.4, 10.0, "out"], ["market_counter", 36.2, -1.5, "out"],
]

static func populate(tree: SceneTree, parent: Node3D, buildings: Array[AABB]) -> Dictionary:
	for old in tree.get_nodes_in_group("urban_props"):
		if old is Node:
			(old as Node).queue_free()
	var placed: Array[AABB] = []
	var report := {"placed": 0, "rejected": 0, "by_kind": {}}
	for row in 4:
		for column in 4:
			var centre := Vector3(BLOCK_ORIGIN + BLOCK_PITCH * column, PAVEMENT_Y, BLOCK_ORIGIN + BLOCK_PITCH * row)
			var block_seed := row * 4 + column
			for side in 4:
				var outward := [Vector3(0, 0, -1), Vector3(1, 0, 0), Vector3(0, 0, 1), Vector3(-1, 0, 0)][side] as Vector3
				var tangent := outward.cross(Vector3.UP).normalized()
				# Sides alternate between a seating side and a utility side, and the pattern
				# rotates block by block so neighbouring blocks are not copies of each other.
				var amenity := (side + block_seed) % 2 == 0
				var layout := AMENITY_SIDE if amenity else UTILITY_SIDE
				var mirror := 1.0 if ((block_seed + side) / 2) % 2 == 0 else -1.0
				for entry in layout:
					var kind := str(entry[0])
					var piece := PropFactory.build(kind)
					if piece == null:
						continue
					var position := centre + outward * float(entry[1]) + tangent * float(entry[2]) * mirror
					var front := outward if str(entry[3]) == "out" else -outward
					piece.name = "Street_%s_%d_%s" % [kind, block_seed * 4 + side, str(int(entry[2]))]
					piece.add_to_group("urban_props")
					piece.set_meta("urban_prop_kind", kind)
					parent.add_child(piece)
					piece.global_position = Vector3(position.x, PAVEMENT_Y, position.z)
					piece.rotation.y = atan2(front.x, front.z)
					var bounds := CitySpatialSafety.world_bounds(piece)
					if bounds.size == Vector3.ZERO or _blocked(bounds, buildings, placed):
						piece.queue_free()
						report.rejected = int(report.rejected) + 1
						continue
					placed.append(bounds)
					piece.set_meta("collision_safe_placement", true)
					report.placed = int(report.placed) + 1
					var by_kind := report.by_kind as Dictionary
					by_kind[kind] = int(by_kind.get(kind, 0)) + 1
	return report

static func _blocked(bounds: AABB, buildings: Array[AABB], placed: Array[AABB]) -> bool:
	if CitySpatialSafety.overlaps_road(bounds, 0.05):
		return true
	for building in buildings:
		if CitySpatialSafety.horizontal_intersects(bounds, building, 0.15):
			return true
	for other in placed:
		if CitySpatialSafety.horizontal_intersects(bounds, other, PIECE_GAP):
			return true
	return false

## Smart-city devices (street lights, sensors) register after the furniture exists, so a second
## pass removes any piece that ended up inside one.
static func prune_device_overlaps(tree: SceneTree) -> int:
	var removed := 0
	var devices: Array[AABB] = []
	for value in tree.get_nodes_in_group("smart_city_devices"):
		if value is Node3D and (value as Node3D).visible:
			var box := CitySpatialSafety.world_bounds(value as Node3D)
			if box.size != Vector3.ZERO:
				devices.append(box)
	for piece in tree.get_nodes_in_group("urban_props"):
		if not piece is Node3D or (piece as Node3D).is_queued_for_deletion():
			continue
		var box := CitySpatialSafety.world_bounds(piece as Node3D)
		for device in devices:
			if CitySpatialSafety.horizontal_intersects(box, device, 0.2):
				(piece as Node3D).queue_free()
				removed += 1
				break
	return removed
