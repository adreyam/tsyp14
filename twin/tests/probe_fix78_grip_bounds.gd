extends SceneTree


func _initialize() -> void:
	call_deferred("_run")


func _bounds_in_space(model: Node3D, space: Node3D) -> AABB:
	var result := AABB()
	var found := false
	var inverse_space := space.global_transform.affine_inverse()
	for value in model.find_children("*", "MeshInstance3D", true, false):
		var mesh := value as MeshInstance3D
		if mesh.mesh == null:
			continue
		var candidate := (inverse_space * mesh.global_transform) * mesh.mesh.get_aabb()
		result = candidate if not found else result.merge(candidate)
		found = true
	return result if found else AABB()


func _run() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	root.add_child(city)
	await process_frame
	var seen := {}
	for value in get_nodes_in_group("npc_job_tools") + get_nodes_in_group("npc_handled_items"):
		var model := value as Node3D
		var kind := str(model.get_meta("job_tool", model.get_meta("handled_item_kind", "")))
		if seen.has(kind):
			continue
		seen[kind] = true
		var anchor := model.get_parent() as Node3D
		var bounds := _bounds_in_space(model, anchor)
		print("FIX78_GRIP_BOUNDS|kind=%s|position=%s|rotation=%s|bounds_position=%s|bounds_size=%s|ratio=%s" % [kind, str(model.position), str(model.rotation_degrees), str(bounds.position), str(bounds.size), str(model.get_meta("npc_grip_point_ratio", Vector3.ZERO))])
	quit(0)
