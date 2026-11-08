extends SceneTree

const ROAD_CENTERS := [-380.0, -285.0, -190.0, -95.0, 0.0, 95.0, 190.0, 285.0, 380.0]
const ROAD_HALF_WIDTH := 7.5

func _initialize() -> void:
	var overlaps := 0
	for scene_path in ["res://scenes/Buildings.tscn", "res://scenes/CityBackdrop.tscn"]:
		var packed := load(scene_path) as PackedScene
		var root := packed.instantiate()
		get_root().add_child(root)
		await process_frame
		for value in get_nodes_in_group("buildings") + get_nodes_in_group("backdrop_buildings"):
			if value is Node3D and root.is_ancestor_of(value) == false and value != root:
				continue
			if value is Node3D:
				var bounds := _world_bounds(value as Node3D)
				if bounds.size != Vector3.ZERO and _overlaps_road(bounds):
					overlaps += 1
					print("ROAD_OVERLAP|%s|%s|pos=%s|size=%s" % [scene_path, (value as Node3D).get_path(), bounds.position, bounds.size])
		root.queue_free()
		await process_frame
	if overlaps>0:
		printerr("BUILDING ROAD AUDIT FAILED: %d building bounds overlap a traffic corridor"%overlaps)
		quit(1)
	else:
		print("BUILDING ROAD AUDIT PASSED: no building bounds overlap any of the 18 traffic corridors")
		quit(0)

func _world_bounds(owner: Node3D) -> AABB:
	var found := false
	var result := AABB()
	for child in owner.find_children("*", "MeshInstance3D", true, false):
		var mesh_instance := child as MeshInstance3D
		if mesh_instance.mesh == null:
			continue
		var transformed := mesh_instance.global_transform * mesh_instance.get_aabb()
		result = transformed if not found else result.merge(transformed)
		found = true
	return result if found else AABB()

func _overlaps_road(bounds: AABB) -> bool:
	var min_x := bounds.position.x
	var max_x := bounds.end.x
	var min_z := bounds.position.z
	var max_z := bounds.end.z
	for center in ROAD_CENTERS:
		if min_x < center + ROAD_HALF_WIDTH and max_x > center - ROAD_HALF_WIDTH:
			return true
		if min_z < center + ROAD_HALF_WIDTH and max_z > center - ROAD_HALF_WIDTH:
			return true
	return false
