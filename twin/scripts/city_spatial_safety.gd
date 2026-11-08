extends RefCounted

## Shared geometry rules for pedestrians, activity props and vegetation.
## These operate on rendered world bounds so imported model pivots cannot make
## placement decisions disagree with what the player actually sees.

const ROAD_CENTERS := [-380.0,-285.0,-190.0,-95.0,0.0,95.0,190.0,285.0,380.0]
const ROAD_HALF_WIDTH := 7.5

static func world_bounds(owner: Node3D) -> AABB:
	var found := false
	var result := AABB()
	for value in owner.find_children("*", "MeshInstance3D", true, false):
		var mesh_node := value as MeshInstance3D
		if mesh_node.mesh == null:
			continue
		var bounds := mesh_node.global_transform * mesh_node.mesh.get_aabb()
		result = bounds if not found else result.merge(bounds)
		found = true
	return result if found else AABB()

static func horizontal_intersects(a: AABB, b: AABB, margin := 0.0) -> bool:
	if a.size == Vector3.ZERO or b.size == Vector3.ZERO:
		return false
	return a.position.x < b.end.x + margin and a.end.x > b.position.x - margin and a.position.z < b.end.z + margin and a.end.z > b.position.z - margin

static func point_inside_horizontal(point: Vector3, bounds: AABB, margin := 0.0) -> bool:
	if bounds.size == Vector3.ZERO:
		return false
	return point.x > bounds.position.x - margin and point.x < bounds.end.x + margin and point.z > bounds.position.z - margin and point.z < bounds.end.z + margin

static func overlaps_road(bounds: AABB, margin := 0.0) -> bool:
	if bounds.size == Vector3.ZERO:
		return false
	for center in ROAD_CENTERS:
		if bounds.position.x < center + ROAD_HALF_WIDTH + margin and bounds.end.x > center - ROAD_HALF_WIDTH - margin:
			return true
		if bounds.position.z < center + ROAD_HALF_WIDTH + margin and bounds.end.z > center - ROAD_HALF_WIDTH - margin:
			return true
	return false

static func building_bounds(tree: SceneTree) -> Array[AABB]:
	var output: Array[AABB] = []
	var seen := {}
	for value in tree.get_nodes_in_group("buildings") + tree.get_nodes_in_group("backdrop_buildings"):
		if not value is Node3D:
			continue
		var node := value as Node3D
		if seen.has(node.get_instance_id()):
			continue
		seen[node.get_instance_id()] = true
		var bounds := world_bounds(node)
		if bounds.size != Vector3.ZERO:
			output.append(bounds)
	return output

static func ground_rendered_node(node: Node3D, surface_y := 0.16) -> float:
	node.force_update_transform()
	var bounds := world_bounds(node)
	if bounds.size == Vector3.ZERO:
		return 0.0
	var correction := surface_y - bounds.position.y
	if absf(correction) > 0.0005:
		node.global_position.y += correction
		node.force_update_transform()
	node.set_meta("spatial_safety_grounded", true)
	node.set_meta("spatial_safety_surface_y", surface_y)
	node.set_meta("spatial_safety_ground_correction", correction)
	return correction

static func deconflict_trees(tree: SceneTree, buildings: Array[AABB]) -> Dictionary:
	var original_intersections := 0
	var relocated := 0
	var hidden := 0
	for value in tree.get_nodes_in_group("city_trees"):
		if not value is Node3D:
			continue
		var tree_node := value as Node3D
		if not tree_node.visible:
			continue
		var bounds := world_bounds(tree_node)
		var blocker := _first_intersection(bounds, buildings, 0.20)
		if blocker.size == Vector3.ZERO:
			continue
		original_intersections += 1
		var original := tree_node.global_position
		var away := original - blocker.get_center()
		away.y = 0.0
		if away.length_squared() < 0.01:
			away = Vector3.RIGHT
		away = away.normalized()
		var side := Vector3(-away.z, 0.0, away.x)
		var directions := [away, side, -side, (away + side).normalized(), (away - side).normalized(), -away]
		var resolved := false
		for radius in [3.0, 5.0, 7.0, 9.0, 12.0, 15.0, 18.0]:
			for direction in directions:
				tree_node.global_position = original + (direction as Vector3) * float(radius)
				tree_node.force_update_transform()
				var candidate_bounds := world_bounds(tree_node)
				if not overlaps_road(candidate_bounds, 0.15) and _first_intersection(candidate_bounds, buildings, 0.20).size == Vector3.ZERO:
					tree_node.set_meta("spatial_safety_relocated", true)
					tree_node.set_meta("spatial_safety_original_position", original)
					relocated += 1
					resolved = true
					break
			if resolved:
				break
		if not resolved:
			tree_node.global_position = original
			tree_node.visible = false
			tree_node.set_meta("spatial_safety_hidden", "building_or_road_conflict")
			hidden += 1
	return {"original_intersections": original_intersections, "relocated": relocated, "hidden": hidden}

static func deconflict_urban_props(tree: SceneTree, buildings: Array[AABB]) -> Dictionary:
	var original_building_intersections := 0
	var original_road_intersections := 0
	var original_prop_intersections := 0
	var relocated := 0
	var hidden := 0
	var grounded := 0
	var maximum_ground_correction := 0.0
	var accepted_bounds: Array[AABB] = []
	for value in tree.get_nodes_in_group("urban_props"):
		if not value is Node3D:
			continue
		var prop := value as Node3D
		if not prop.visible:
			continue
		var ground_correction := ground_rendered_node(prop)
		grounded += 1
		maximum_ground_correction = maxf(maximum_ground_correction, absf(ground_correction))
		var bounds := world_bounds(prop)
		var building_conflict := _first_intersection(bounds, buildings, 0.12).size != Vector3.ZERO
		var road_conflict := overlaps_road(bounds, 0.05)
		var prop_conflict := _first_intersection(bounds, accepted_bounds, 0.12).size != Vector3.ZERO
		if building_conflict:
			original_building_intersections += 1
		if road_conflict:
			original_road_intersections += 1
		if prop_conflict:
			original_prop_intersections += 1
		if not building_conflict and not road_conflict and not prop_conflict:
			accepted_bounds.append(bounds)
			continue
		var original := prop.global_position
		var nearest_blocker := _first_intersection(bounds, buildings, 0.12)
		var away := original - nearest_blocker.get_center() if nearest_blocker.size != Vector3.ZERO else Vector3.RIGHT
		away.y = 0.0
		if away.length_squared() < 0.01:
			away = Vector3.RIGHT
		away = away.normalized()
		var side := Vector3(-away.z, 0.0, away.x)
		var directions := [away, side, -side, (away + side).normalized(), (away - side).normalized(), -away]
		var resolved := false
		# Small street furniture usually needs only a short nudge. Larger radii
		# cover the few backdrop props whose source positions landed under a
		# differently sized procedural building.
		for radius in [2.0, 3.5, 5.0, 7.0, 9.0, 12.0, 16.0, 20.0, 24.0]:
			for direction in directions:
				prop.global_position = original + (direction as Vector3) * float(radius)
				prop.force_update_transform()
				var candidate := world_bounds(prop)
				if candidate.size != Vector3.ZERO and not overlaps_road(candidate, 0.05) and _first_intersection(candidate, buildings, 0.12).size == Vector3.ZERO and _first_intersection(candidate, accepted_bounds, 0.12).size == Vector3.ZERO:
					prop.set_meta("spatial_safety_relocated", true)
					prop.set_meta("spatial_safety_original_position", original)
					accepted_bounds.append(candidate)
					relocated += 1
					resolved = true
					break
			if resolved:
				break
		if not resolved:
			prop.global_position = original
			prop.visible = false
			prop.set_meta("spatial_safety_hidden", "building_road_or_prop_conflict")
			hidden += 1
	return {
		"original_building_intersections": original_building_intersections,
		"original_road_intersections": original_road_intersections,
		"original_prop_intersections": original_prop_intersections,
		"relocated": relocated,
		"hidden": hidden,
		"grounded": grounded,
		"maximum_ground_correction": maximum_ground_correction
	}

static func _first_intersection(bounds: AABB, buildings: Array[AABB], margin: float) -> AABB:
	for building in buildings:
		if horizontal_intersects(bounds, building, margin):
			return building
	return AABB()
