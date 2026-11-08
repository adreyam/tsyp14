extends SceneTree

const ROAD_CENTERS := [-380.0,-285.0,-190.0,-95.0,0.0,95.0,190.0,285.0,380.0]
const ROAD_HALF_WIDTH := 7.5

func _init() -> void:
	call_deferred("_run")

func _run() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	root.add_child(city)
	await process_frame
	await create_timer(0.5).timeout
	var buildings := _unique_nodes(get_nodes_in_group("buildings") + get_nodes_in_group("backdrop_buildings"))
	var props := _unique_nodes(get_nodes_in_group("npc_activity_props") + get_nodes_in_group("npc_interaction_props"))
	var trees := _unique_nodes(get_nodes_in_group("city_trees"))
	var urban_props := _unique_nodes(get_nodes_in_group("urban_props"))
	var prop_building := 0
	var prop_road := 0
	var tree_building := 0
	var urban_prop_building := 0
	var urban_prop_road := 0
	for prop in props:
		var bounds := _world_bounds(prop)
		if _overlaps_road(bounds):
			prop_road += 1
			print("BASELINE_PROP_ROAD|", prop.name, "|", bounds)
		for building in buildings:
			if _xz_intersects(bounds, _world_bounds(building), 0.05):
				prop_building += 1
				print("BASELINE_PROP_BUILDING|", prop.name, "|", building.name)
				break
	for tree in trees:
		if not tree.visible:
			continue
		var tree_bounds := _world_bounds(tree)
		for building in buildings:
			if _xz_intersects(tree_bounds, _world_bounds(building), 0.05):
				tree_building += 1
				print("BASELINE_TREE_BUILDING|", tree.name, "|", building.name)
				break
	for prop in urban_props:
		if not prop.visible:
			continue
		var bounds := _world_bounds(prop)
		if _overlaps_road(bounds):
			urban_prop_road += 1
			print("BASELINE_URBAN_PROP_ROAD|", prop.name, "|", bounds)
		for building in buildings:
			if _xz_intersects(bounds, _world_bounds(building), 0.05):
				urban_prop_building += 1
				print("BASELINE_URBAN_PROP_BUILDING|", prop.name, "|", building.name)
				break
	var npc_building_samples := 0
	var director := city.get_node("NpcAi")
	for sample in 100:
		for state_value in director.call("get_agent_debug"):
			var person := (state_value as Dictionary).node as Node3D
			for building in buildings:
				if _point_inside_xz(person.global_position, _world_bounds(building), 0.42):
					npc_building_samples += 1
					print("BASELINE_NPC_BUILDING|", sample, "|", person.name, "|", building.name, "|", person.global_position)
					break
		await create_timer(0.1).timeout
	print("FIX74_SPATIAL_BASELINE|props=%d|prop_building=%d|prop_road=%d|urban_props=%d|urban_prop_building=%d|urban_prop_road=%d|trees=%d|tree_building=%d|npc_building_samples=%d" % [props.size(), prop_building, prop_road, urban_props.size(), urban_prop_building, urban_prop_road, trees.size(), tree_building, npc_building_samples])
	quit(0)

func _unique_nodes(values: Array) -> Array[Node3D]:
	var output: Array[Node3D] = []
	var seen := {}
	for value in values:
		if value is Node3D and not seen.has(value):
			seen[value] = true
			output.append(value as Node3D)
	return output

func _world_bounds(owner: Node3D) -> AABB:
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

func _xz_intersects(a: AABB, b: AABB, margin: float) -> bool:
	if a.size == Vector3.ZERO or b.size == Vector3.ZERO:
		return false
	return a.position.x < b.end.x + margin and a.end.x > b.position.x - margin and a.position.z < b.end.z + margin and a.end.z > b.position.z - margin

func _point_inside_xz(point: Vector3, bounds: AABB, margin: float) -> bool:
	if bounds.size == Vector3.ZERO:
		return false
	return point.x > bounds.position.x - margin and point.x < bounds.end.x + margin and point.z > bounds.position.z - margin and point.z < bounds.end.z + margin

func _overlaps_road(bounds: AABB) -> bool:
	for center in ROAD_CENTERS:
		if bounds.position.x < center + ROAD_HALF_WIDTH and bounds.end.x > center - ROAD_HALF_WIDTH:
			return true
		if bounds.position.z < center + ROAD_HALF_WIDTH and bounds.end.z > center - ROAD_HALF_WIDTH:
			return true
	return false
