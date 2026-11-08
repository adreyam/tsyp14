extends SceneTree

const ACTIONS := ["Gather_Reach", "Gather_Low", "Gather_Pick", "Hammer_Ground", "Mine_Ground", "Mine_Wall", "Plow"]

func _init() -> void:
	call_deferred("_run")

func _run() -> void:
	root.size = Vector2i(1200, 700)
	var library := load("res://assets/crafting_animations/crafting_city_actions.tres") as AnimationLibrary
	for action in ACTIONS:
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
		var animation := library.get_animation(action)
		for index in 4:
			var model := (load("res://assets/crafting_animations/retarget_targets/worker.glb") as PackedScene).instantiate() as Node3D
			world.add_child(model)
			model.position.x = -4.2 + float(index) * 2.8
			var player := model.find_children("*", "AnimationPlayer", true, false)[0] as AnimationPlayer
			player.add_animation_library("craft", library)
			player.play("craft/%s" % action)
			player.seek(animation.length * (0.12 + float(index) * 0.25), true)
			player.pause()
		var camera := Camera3D.new()
		world.add_child(camera)
		camera.current = true
		camera.fov = 38
		camera.look_at_from_position(Vector3(8.0, 4.0, 10.5), Vector3(0, 1.0, 0))
		for unused in 8:
			await process_frame
		var output := "/tmp/crafting_library_%s.png" % action
		root.get_texture().get_image().save_png(output)
		print("CRAFT_LIBRARY_RENDER|", action, "|", output)
		world.queue_free()
		await process_frame
	quit()
