extends SceneTree

const OUTPUT_DIR := "res://screenshots/fix78_finger_edge_grips_verified_final"
const REQUIRED_KINDS := 8


func _initialize() -> void:
	call_deferred("_render")


func _active_held_model(person: Node3D) -> Node3D:
	for value in person.find_children("*", "Node3D", true, false):
		var candidate := value as Node3D
		if candidate.visible and (candidate.is_in_group("npc_job_tools") or candidate.is_in_group("npc_handled_items")):
			return candidate
	return null


func _frame_grip(camera: Camera3D, person: Node3D, held_model: Node3D) -> void:
	var skeleton := person.find_child("Skeleton3D", true, false) as Skeleton3D
	var hand_index := skeleton.find_bone("Bip01 R Hand")
	var hand := skeleton.global_transform * skeleton.get_bone_global_pose(hand_index).origin
	var chest := person.global_position + Vector3.UP * 1.32
	var target := hand.lerp(chest, 0.28)
	var facing := held_model.global_position - person.global_position
	facing.y = 0.0
	if facing.length_squared() < 0.01:
		facing = -person.global_transform.basis.z
	facing = facing.normalized()
	var side := Vector3(-facing.z, 0.0, facing.x)
	camera.look_at_from_position(target + side * 1.28 - facing * 0.52 + Vector3.UP * 0.20, target, Vector3.UP)


func _render() -> void:
	root.size = Vector2i(1280, 720)
	DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path(OUTPUT_DIR))
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	root.add_child(city)
	var environment := city.get_node("WorldEnvironment") as WorldEnvironment
	environment.environment.background_mode = Environment.BG_COLOR
	environment.environment.background_color = Color(0.48, 0.62, 0.78)
	environment.environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	environment.environment.ambient_light_color = Color(0.92, 0.95, 1.0)
	environment.environment.ambient_light_energy = 0.84
	environment.environment.fog_enabled = false
	(city.get_node("Sun") as DirectionalLight3D).light_energy = 1.65
	for layer in city.find_children("*", "CanvasLayer", true, false):
		(layer as CanvasLayer).visible = false
	var camera := Camera3D.new()
	city.add_child(camera)
	camera.current = true
	camera.fov = 31.0
	for _unused in 40:
		await process_frame
	var director := city.get_node("NpcAi")
	var captured := {}
	for _sample in 900:
		await process_frame
		for state_value in director.call("get_agent_debug"):
			var state := state_value as Dictionary
			if float(state.speed_mps) > 0.01:
				continue
			var kind := str(state.active_hand_item_kind)
			if kind.is_empty():
				kind = str(state.active_tool_kind)
			if kind.is_empty() or captured.has(kind):
				continue
			var person := state.node as Node3D
			var held_model := _active_held_model(person)
			if held_model == null:
				continue
			_frame_grip(camera, person, held_model)
			for _unused in 3:
				await process_frame
			var path := "%s/grip_%02d_%s_%s.png" % [OUTPUT_DIR, captured.size(), kind, str(state.animation_clip)]
			var error := root.get_texture().get_image().save_png(ProjectSettings.globalize_path(path))
			print("FIX78_HAND_GRIP_RENDER|kind=%s|role=%s|clip=%s|save=%d" % [kind, str(state.role), str(state.animation_clip), error])
			captured[kind] = true
			if captured.size() >= REQUIRED_KINDS and captured.has("meter_reader") and captured.has("watering_can"):
				break
		if captured.size() >= REQUIRED_KINDS and captured.has("meter_reader") and captured.has("watering_can"):
			break
	if captured.size() < REQUIRED_KINDS or not captured.has("meter_reader") or not captured.has("watering_can"):
		printerr("FIX78 HAND GRIP RENDER FAILED: only %d kinds; meter_reader=%s watering_can=%s" % [captured.size(), str(captured.has("meter_reader")), str(captured.has("watering_can"))])
		quit(1)
		return
	print("FIX78_HAND_GRIP_RENDER_COMPLETE|frames=%d|kinds=%s" % [captured.size(), str(captured.keys())])
	quit(0)
