extends RefCounted

## Exact mapping between SUMO's generated 9x9 grid and Godot world space.
## Mirrors scripts/build_sumo_city.py: junction (row, column) sits at SUMO
## ((column-1)*95, (row-1)*95); Godot = SUMO - 380 on both axes (SUMO +y is
## Godot +z). Every one-lane carriageway is 4.5 m wide, its centre 2.25 m to the
## right of the road centreline, and its lane position 0 starts 8.5 m after the
## upstream junction centre (half of the 17 m junction box).

const BLOCK := 95.0
const HALF_EXTENT := 380.0
const LANE_OFFSET := 2.25
const LANE_WIDTH := 4.5
const KERB_OFFSET := 5.75
const JUNCTION_HALF := 8.5
const LANE_LENGTH := 78.0
## Citizens walk a ring 9.5 m from the centreline (±1.7 m) in the core blocks.
const WALK_BAND_MIN := 7.6
const WALK_BAND_MAX := 11.4
const EDGE_PATTERN := "^E_r(\\d+)c(\\d+)_r(\\d+)c(\\d+)$"

static var _edge_regex: RegEx


static func junction_position(row: int, column: int) -> Vector3:
	return Vector3(float(column - 1) * BLOCK - HALF_EXTENT, 0.0, float(row - 1) * BLOCK - HALF_EXTENT)


static func parse_edge(edge_id: String) -> Dictionary:
	if _edge_regex == null:
		_edge_regex = RegEx.new()
		_edge_regex.compile(EDGE_PATTERN)
	var match_result := _edge_regex.search(edge_id)
	if match_result == null:
		return {}
	var from_row := int(match_result.get_string(1))
	var from_column := int(match_result.get_string(2))
	var to_row := int(match_result.get_string(3))
	var to_column := int(match_result.get_string(4))
	var start := junction_position(from_row, from_column)
	var finish := junction_position(to_row, to_column)
	var direction := (finish - start).normalized()
	return {
		"id": edge_id, "start": start, "end": finish, "direction": direction,
		"right": right_of(direction), "from": Vector2i(from_row, from_column), "to": Vector2i(to_row, to_column),
		"reverse": "E_r%dc%d_r%dc%d" % [to_row, to_column, from_row, from_column],
	}


static func right_of(direction: Vector3) -> Vector3:
	## Right-hand side of travel in this grid (verified against the lane shapes).
	return Vector3(direction.z, 0.0, -direction.x)


static func centreline_point(edge: Dictionary, lane_position: float) -> Vector3:
	return (edge.start as Vector3) + (edge.direction as Vector3) * (JUNCTION_HALF + lane_position)


static func lane_point(edge: Dictionary, lane_position: float, extra_lateral := 0.0) -> Vector3:
	## Centre of the edge's driving lane, optionally shifted to the right.
	return centreline_point(edge, lane_position) + (edge.right as Vector3) * (LANE_OFFSET + extra_lateral)


static func side_point(edge: Dictionary, lane_position: float, offset_from_centreline: float) -> Vector3:
	return centreline_point(edge, lane_position) + (edge.right as Vector3) * offset_from_centreline


static func yaw_of(direction: Vector3) -> float:
	return atan2(direction.x, direction.z)


static func lane_position_of(edge: Dictionary, point: Vector3) -> float:
	return (point - (edge.start as Vector3)).dot(edge.direction as Vector3) - JUNCTION_HALF


static func is_core_block_road(edge: Dictionary) -> bool:
	## Edges bordering the central 4x4 blocks, where citizens walk.
	var mid := ((edge.start as Vector3) + (edge.end as Vector3)) * 0.5
	return absf(mid.x) <= 190.0 + 0.1 and absf(mid.z) <= 190.0 + 0.1
