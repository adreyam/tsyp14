extends SceneTree

const OUTPUT_DIR := "res://screenshots/fix78_live_crouch_grounding_verified_v2"
const FOOT_BONES := ["Bip01 L Foot", "Bip01 R Foot", "Bip01 L Toe0", "Bip01 R Toe0"]


func _initialize() -> void:
	call_deferred("_render")


func _prop_for(station_id: String) -> Node3D:
	for value in get_nodes_in_group("npc_activity_props"):
		if value is Node3D and str((value as Node3D).get_meta("activity_station", "")) == station_id:
			return value as Node3D
	return null


func _lowest_foot_world_y(person: Node3D) -> float:
	var skeleton := person.find_child("Skeleton3D", true, false) as Skeleton3D
	var lowest := INF
	for bone_name in FOOT_BONES:
		var bone_index := skeleton.find_bone(str(bone_name))
		if bone_index >= 0:
			lowest = minf(lowest, (skeleton.global_transform * skeleton.get_bone_global_pose(bone_index).origin).y)
	return lowest


func _frame_subject(camera: Camera3D, actor: Node3D, prop: Node3D) -> void:
	var toward := prop.global_position - actor.global_position
	toward.y = 0.0
	if toward.length_squared() < 0.01:
		toward = -actor.global_transform.basis.z
	toward = toward.normalized()
	var side := Vector3(-toward.z, 0.0, toward.x)
	var target := actor.global_position.lerp(prop.global_position, 0.35) + Vector3.UP * 0.60
	camera.look_at_from_position(target + side * 2.35 - toward * 0.62 + Vector3.UP * 0.20, target, Vector3.UP)


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
	camera.fov = 38.0
	var director := city.get_node("NpcAi")
	var saved := 0
	var cooldown := 0
	for _frame in 420:
		await process_frame
		await RenderingServer.frame_post_draw
		cooldown = maxi(0, cooldown - 1)
		if cooldown > 0:
			continue
		for state_value in director.call("get_agent_debug"):
			var state := state_value as Dictionary
			if str(state.animation_clip) != "InspectLow" or float(state.speed_mps) > 0.01:
				continue
			var actor := state.node as Node3D
			var prop := _prop_for(str(state.station_id))
			if prop == null:
				continue
			_frame_subject(camera, actor, prop)
			for _unused in 3:
				await process_frame
				await RenderingServer.frame_post_draw
			var expected := float(state.surface_y) + float(actor.get_meta("npc_foot_reference_y", 0.0))
			var error_m := absf(_lowest_foot_world_y(actor) - expected)
			var path := "%s/live_crouch_%02d_%s.png" % [OUTPUT_DIR, saved, str(state.job_prop_kind)]
			var error := root.get_texture().get_image().save_png(ProjectSettings.globalize_path(path))
			print("FIX78_LIVE_CROUCH_RENDER|prop=%s|foot_error=%.4f|save=%d" % [str(state.job_prop_kind), error_m, error])
			saved += 1
			cooldown = 15
			break
		if saved >= 5:
			break
	if saved < 5:
		printerr("FIX78 LIVE CROUCH RENDER FAILED: only %d frames" % saved)
		quit(1)
		return
	print("FIX78_LIVE_CROUCH_RENDER_COMPLETE|frames=%d" % saved)
	quit(0)
