extends SceneTree

func _init() -> void:
	call_deferred("_run")

func _run() -> void:
	var target_path := "res://assets/poly_pizza/city_pack/Worker.glb" if OS.has_environment("ORIGINAL_TARGET") else "res://assets/crafting_animations/retarget_targets/Worker.glb"
	var scene := (load(target_path) as PackedScene).instantiate()
	root.add_child(scene)
	await process_frame
	var skeleton := scene.find_children("*", "Skeleton3D", true, false)[0] as Skeleton3D
	print("TARGET_SKELETON|", scene.get_path_to(skeleton), "|", skeleton.name, "|motion_scale=", skeleton.motion_scale, "|hips_rest=", skeleton.get_bone_global_rest(skeleton.find_bone("Hips")).origin)
	for bone_name in ["Root", "Hips"]: print("TARGET_REST|", bone_name, "|local=", skeleton.get_bone_rest(skeleton.find_bone(bone_name)).origin, "|global=", skeleton.get_bone_global_rest(skeleton.find_bone(bone_name)).origin, "|parent=", skeleton.get_bone_parent(skeleton.find_bone(bone_name)))
	for index in mini(12, skeleton.get_bone_count()): print("TARGET_BONE|", index, "|", skeleton.get_bone_name(index), "|parent=", skeleton.get_bone_parent(index), "|rest=", skeleton.get_bone_rest(index).origin)
	var target_players := scene.find_children("*", "AnimationPlayer", true, false)
	if not target_players.is_empty(): print("TARGET_ANIMS|", (target_players[0] as AnimationPlayer).get_animation_list())
	var source_scene := (load("res://assets/crafting_animations/reference_retarget/HumanM_Model.fbx") as PackedScene).instantiate()
	root.add_child(source_scene)
	var source_skeleton := source_scene.find_children("*", "Skeleton3D", true, false)[0] as Skeleton3D
	print("SOURCE_MODEL_SKELETON|", source_skeleton.name, "|motion_scale=", source_skeleton.motion_scale, "|hips_rest=", source_skeleton.get_bone_global_rest(source_skeleton.find_bone("Hips")).origin)
	for bone_name in ["Root", "Hips"]: print("SOURCE_REST|", bone_name, "|local=", source_skeleton.get_bone_rest(source_skeleton.find_bone(bone_name)).origin, "|global=", source_skeleton.get_bone_global_rest(source_skeleton.find_bone(bone_name)).origin, "|parent=", source_skeleton.get_bone_parent(source_skeleton.find_bone(bone_name)))
	quit()
