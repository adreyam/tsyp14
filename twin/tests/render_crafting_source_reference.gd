extends SceneTree

func _player(node: Node) -> AnimationPlayer:
	return node.find_children("*", "AnimationPlayer", true, false)[0] as AnimationPlayer

func _init() -> void:
	call_deferred("_run")

func _run() -> void:
	root.size = Vector2i(800, 800) if OS.has_environment("CRAFT_CLOSE") else Vector2i(1200, 700)
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
	var animation_scene := (load("res://assets/crafting_animations/source/HumanM@Gathering02.fbx") as PackedScene).instantiate()
	world.add_child(animation_scene)
	var source_player := _player(animation_scene)
	var source_name := str(source_player.get_animation_list()[0])
	var animation := source_player.get_animation(source_name).duplicate(true) as Animation
	for track in range(animation.get_track_count() - 1, -1, -1):
		if animation.track_get_type(track) == Animation.TYPE_SCALE_3D:
			animation.remove_track(track)
			continue
		var path_text := str(animation.track_get_path(track))
		if not OS.has_environment("CRAFT_SOURCE_MODEL") and path_text.begins_with("%CraftingSkeleton:"):
			animation.track_set_path(track, NodePath(path_text.replace("%CraftingSkeleton:", "%GeneralSkeleton:")))
	animation_scene.queue_free()
	await process_frame
	var model_count := 1 if OS.has_environment("CRAFT_CLOSE") else 4
	for index in model_count:
		var model_path := "res://assets/crafting_animations/reference_retarget/HumanM_Model.fbx" if OS.has_environment("CRAFT_SOURCE_MODEL") else "res://assets/crafting_animations/retarget_targets/Worker.glb"
		var model := (load(model_path) as PackedScene).instantiate() as Node3D
		world.add_child(model)
		model.position.x = 0.0 if OS.has_environment("CRAFT_CLOSE") else -4.2 + float(index) * 2.8
		var found_players := model.find_children("*", "AnimationPlayer", true, false)
		var player: AnimationPlayer
		if found_players.is_empty():
			player = AnimationPlayer.new()
			model.add_child(player)
			player.root_node = NodePath("..")
		else:
			player = found_players[0] as AnimationPlayer
		if OS.has_environment("CRAFT_NATIVE_WALK"):
			var walk_name := ""
			for animation_name in player.get_animation_list():
				if str(animation_name).ends_with("|Walk"):
					walk_name = str(animation_name)
					break
			player.play(walk_name)
			player.seek(player.get_animation(walk_name).length * (0.18 + float(index) * 0.22), true)
			player.pause()
			continue
		var library := AnimationLibrary.new()
		library.add_animation("Gathering", animation)
		player.add_animation_library("preview", library)
		if not OS.has_environment("CRAFT_NEUTRAL"):
			player.play("preview/Gathering")
			player.seek(animation.length * (0.55 if OS.has_environment("CRAFT_CLOSE") else 0.18 + float(index) * 0.22), true)
			player.pause()
			if OS.has_environment("CRAFT_CLOSE"):
				var skeleton := model.find_children("*", "Skeleton3D", true, false)[0] as Skeleton3D
				var hips := skeleton.find_bone("Hips")
				print("CRAFT_POSE|source_model=", OS.has_environment("CRAFT_SOURCE_MODEL"), "|rest=", skeleton.get_bone_rest(hips).origin, "|pose=", skeleton.get_bone_pose_position(hips), "|motion_scale=", skeleton.motion_scale)
	var camera := Camera3D.new()
	world.add_child(camera)
	camera.current = true
	camera.fov = 38
	camera.look_at_from_position(Vector3(3.2, 2.5, 5.0), Vector3(0, 1.0, 0)) if OS.has_environment("CRAFT_CLOSE") else camera.look_at_from_position(Vector3(8.0, 4.0, 10.5), Vector3(0, 1.2, 0))
	for unused in 10:
		await process_frame
	var output := "/tmp/crafting_source_gathering.png"
	root.get_texture().get_image().save_png(output)
	print("CRAFT_SOURCE_REFERENCE|", output)
	quit()
