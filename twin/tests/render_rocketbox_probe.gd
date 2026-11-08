extends SceneTree

const MODELS := [
	"res://assets/rocketbox/Delivery_Male_01/Export/Delivery_Male_01.fbx",
	"res://assets/rocketbox/Medical_Female_01/Export/Medical_Female_01.fbx",
	"res://assets/rocketbox/Construction_Male_01/Export/Construction_Male_01.fbx",
]
const CLIPS := ["Walk", "Chest_Open", "PickUp_Table"]

func _init() -> void:
	call_deferred("_run")

func _run() -> void:
	root.size = Vector2i(900, 900)
	var world := Node3D.new()
	root.add_child(world)
	var environment_node := WorldEnvironment.new()
	var environment := Environment.new()
	environment.background_mode = Environment.BG_COLOR
	environment.background_color = Color(0.12, 0.15, 0.19)
	environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	environment.ambient_light_color = Color(0.75, 0.82, 0.94)
	environment.ambient_light_energy = 0.75
	environment_node.environment = environment
	world.add_child(environment_node)
	var light := DirectionalLight3D.new()
	light.rotation_degrees = Vector3(-42.0, -32.0, 0.0)
	light.light_energy = 2.2
	light.shadow_enabled = true
	world.add_child(light)
	for index in 3:
		var model := (load(str(MODELS[index])) as PackedScene).instantiate() as Node3D
		model.position.x = float(index - 1) * 1.55
		world.add_child(model)
		var player := model.find_child("AnimationPlayer", true, false) as AnimationPlayer
		load("res://scripts/rocketbox_animation_library.gd").install_on(model, player)
		player.play("rocketbox/%s" % str(CLIPS[index]))
		var clip := player.get_animation("rocketbox/%s" % str(CLIPS[index]))
		player.seek(clip.length * 0.5, true)
		player.advance(0.0)
	var ground := MeshInstance3D.new()
	var box := BoxMesh.new()
	box.size = Vector3(6.0, 0.05, 4.0)
	ground.mesh = box
	ground.position.y = -0.025
	world.add_child(ground)
	var camera := Camera3D.new()
	camera.fov = 38.0
	world.add_child(camera)
	camera.look_at_from_position(Vector3(4.5, 2.0, 6.8), Vector3(0.0, 0.9, 0.0), Vector3.UP)
	camera.current = true
	for unused in 12:
		await process_frame
	var error := root.get_texture().get_image().save_png("/tmp/rocketbox_probe.png")
	print("ROCKETBOX_RENDER|", error)
	quit(0 if error == OK else 1)
