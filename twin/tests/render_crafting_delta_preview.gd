extends SceneTree

const TARGET_MAP := {
	"Root":"Root", "Body":"Hips", "Abdomen":"Spine", "Torso":"Chest",
	"Chest":"UpperChest", "Neck":"Neck", "Head":"Head",
	"Shoulder.L":"LeftShoulder", "UpperArm.L":"LeftUpperArm",
	"LowerArm.L":"LeftLowerArm", "Wrist.L":"LeftHand",
	"UpperLeg.L":"LeftUpperLeg", "LowerLeg.L":"LeftLowerLeg",
	"Foot.L":"LeftFoot", "PT.L":"LeftToes",
	"Shoulder.R":"RightShoulder", "UpperArm.R":"RightUpperArm",
	"LowerArm.R":"RightLowerArm", "Wrist.R":"RightHand",
	"UpperLeg.R":"RightUpperLeg", "LowerLeg.R":"RightLowerLeg",
	"Foot.R":"RightFoot", "PT.R":"RightToes",
}

func _init() -> void:
	call_deferred("_run")

func _find_skeleton(node: Node) -> Skeleton3D:
	return node.find_children("*", "Skeleton3D", true, false)[0] as Skeleton3D

func _find_player(node: Node) -> AnimationPlayer:
	return node.find_children("*", "AnimationPlayer", true, false)[0] as AnimationPlayer

func _target_paths(player: AnimationPlayer) -> Dictionary:
	var result := {}
	for animation_name in player.get_animation_list():
		var animation := player.get_animation(animation_name)
		for track in animation.get_track_count():
			if animation.track_get_type(track) != Animation.TYPE_ROTATION_3D:
				continue
			var path := animation.track_get_path(track)
			var bone := str(path).get_slice(":", 1)
			if not bone.is_empty():
				result[bone] = path
		if not result.is_empty():
			break
	return result

func _bake(source_skeleton: Skeleton3D, target_skeleton: Skeleton3D, source_player: AnimationPlayer, target_player: AnimationPlayer) -> Animation:
	var source_name := str(source_player.get_animation_list()[0])
	var source_animation := source_player.get_animation(source_name)
	var paths := _target_paths(target_player)
	var baked := Animation.new()
	baked.length = source_animation.length
	baked.loop_mode = source_animation.loop_mode
	var track_by_target_index := {}
	var hips_position_track := -1
	var source_by_target_index := {}
	for target_bone_name in TARGET_MAP:
		var standard_name := str(TARGET_MAP[target_bone_name])
		var target_index := target_skeleton.find_bone(target_bone_name)
		var source_index := source_skeleton.find_bone(standard_name)
		if target_index < 0 or source_index < 0 or not paths.has(target_bone_name):
			continue
		var track := baked.add_track(Animation.TYPE_ROTATION_3D)
		baked.track_set_path(track, paths[target_bone_name])
		track_by_target_index[target_index] = track
		source_by_target_index[target_index] = source_index
		if standard_name == "Hips":
			hips_position_track = baked.add_track(Animation.TYPE_POSITION_3D)
			baked.track_set_path(hips_position_track, NodePath(str(paths[target_bone_name])))
	var sample_count := ceili(source_animation.length * 30.0) + 1
	for sample in sample_count:
		var time := minf(float(sample) / 30.0, source_animation.length)
		target_skeleton.reset_bone_poses()
		source_player.play(source_name)
		source_player.seek(time, true)
		source_player.pause()
		await process_frame
		for target_index in range(target_skeleton.get_bone_count()):
			if not source_by_target_index.has(target_index):
				continue
			var source_index := int(source_by_target_index[target_index])
			var source_rest_global := source_skeleton.get_bone_global_rest(source_index)
			var source_pose_global := source_skeleton.get_bone_global_pose(source_index)
			var target_rest_global := target_skeleton.get_bone_global_rest(target_index)
			var world_motion := source_pose_global * source_rest_global.affine_inverse()
			var desired_global := world_motion * target_rest_global
			var parent_index := target_skeleton.get_bone_parent(target_index)
			var parent_global := target_skeleton.get_bone_global_pose(parent_index) if parent_index >= 0 else Transform3D.IDENTITY
			var desired_local := parent_global.affine_inverse() * desired_global
			var desired_local_basis := desired_local.basis.orthonormalized()
			target_skeleton.set_bone_pose_rotation(target_index, desired_local_basis.get_rotation_quaternion())
			baked.rotation_track_insert_key(int(track_by_target_index[target_index]), time, desired_local_basis.get_rotation_quaternion())
			if str(TARGET_MAP[target_skeleton.get_bone_name(target_index)]) == "Hips" and hips_position_track >= 0:
				var source_local_offset := source_skeleton.get_bone_pose_position(source_index) - source_skeleton.get_bone_rest(source_index).origin
				var target_height := target_skeleton.get_bone_global_rest(target_index).origin.length()
				var source_height := source_skeleton.get_bone_global_rest(source_index).origin.length()
				var motion_ratio := target_height / maxf(source_height, 0.00001)
				var target_hips_position := target_skeleton.get_bone_rest(target_index).origin + source_local_offset * motion_ratio
				if sample == 0 or sample == sample_count / 2 or sample == sample_count - 1:
					print("DELTA_HIPS|", sample, "|source_rest=", source_skeleton.get_bone_rest(source_index).origin, "|source_pose=", source_skeleton.get_bone_pose_position(source_index), "|target=", target_hips_position, "|ratio=", motion_ratio)
				target_skeleton.set_bone_pose_position(target_index, target_hips_position)
				baked.position_track_insert_key(hips_position_track, time, target_hips_position)
	return baked

func _run() -> void:
	root.size = Vector2i(1200, 700)
	var world := Node3D.new()
	root.add_child(world)
	var environment_node := WorldEnvironment.new()
	var environment := Environment.new()
	environment.background_mode = Environment.BG_COLOR
	environment.background_color = Color("7d8996")
	environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	environment.ambient_light_energy = 1.1
	environment_node.environment = environment
	world.add_child(environment_node)
	var light := DirectionalLight3D.new()
	light.rotation_degrees = Vector3(-48, -35, 0)
	light.light_energy = 1.5
	light.shadow_enabled = true
	world.add_child(light)
	var floor := MeshInstance3D.new()
	var floor_mesh := BoxMesh.new()
	floor_mesh.size = Vector3(13, 0.1, 7)
	floor.mesh = floor_mesh
	floor.position.y = -0.05
	world.add_child(floor)
	var source := (load("res://assets/crafting_animations/source/HumanM@Gathering02.fbx") as PackedScene).instantiate() as Node3D
	world.add_child(source)
	for mesh in source.find_children("*", "MeshInstance3D", true, false):
		(mesh as MeshInstance3D).visible = false
	var source_skeleton := _find_skeleton(source)
	var source_player := _find_player(source)
	var first_target := (load("res://assets/poly_pizza/city_pack/Worker.glb") as PackedScene).instantiate() as Node3D
	world.add_child(first_target)
	var target_skeleton := _find_skeleton(first_target)
	var target_player := _find_player(first_target)
	var baked := await _bake(source_skeleton, target_skeleton, source_player, target_player)
	first_target.queue_free()
	await process_frame
	for index in 4:
		var target := (load("res://assets/poly_pizza/city_pack/Worker.glb") as PackedScene).instantiate() as Node3D
		world.add_child(target)
		target.position.x = -4.2 + float(index) * 2.8
		var player := _find_player(target)
		var library := AnimationLibrary.new()
		library.add_animation("Gathering", baked)
		player.add_animation_library("preview", library)
		player.play("preview/Gathering")
		player.seek(baked.length * (0.18 + float(index) * 0.22), true)
		player.pause()
	var camera := Camera3D.new()
	world.add_child(camera)
	camera.current = true
	camera.fov = 38
	camera.look_at_from_position(Vector3(8.0, 4.0, 10.5), Vector3(0, 1.2, 0))
	for unused in 10:
		await process_frame
	var output := "/tmp/crafting_delta_gathering.png"
	root.get_texture().get_image().save_png(output)
	print("CRAFT_DELTA_RENDER|", output, "|", baked.length)
	quit()
