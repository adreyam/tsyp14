extends SceneTree

const OUTPUT_DIR := "res://screenshots/fix74_prop_jobs"

func _initialize() -> void:
	call_deferred("_render")

func _render() -> void:
	root.size = Vector2i(1280, 720)
	DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path(OUTPUT_DIR))
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	root.add_child(city)
	var environment := city.get_node("WorldEnvironment") as WorldEnvironment
	environment.environment.background_mode = Environment.BG_COLOR
	environment.environment.background_color = Color(0.48, 0.62, 0.78)
	environment.environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	environment.environment.ambient_light_color = Color(0.9, 0.94, 1.0)
	environment.environment.ambient_light_energy = 0.85
	environment.environment.fog_enabled = false
	(city.get_node("Sun") as DirectionalLight3D).light_energy = 2.1
	for layer in city.find_children("*", "CanvasLayer", true, false):
		(layer as CanvasLayer).visible = false
	var camera := Camera3D.new()
	city.add_child(camera)
	camera.current = true
	camera.fov = 43.0
	for unused in 40:
		await process_frame
	var director := city.get_node("NpcAi")
	var frame_index := 0
	for round_index in 3:
		var guaranteed: Array[Dictionary] = []
		for state_value in director.call("get_agent_debug"):
			var state := state_value as Dictionary
			if bool(state.guaranteed_prop_job) and bool(state.prop_interaction_active):
				guaranteed.append(state)
		for state in guaranteed:
			var person := state.node as Node3D
			var prop: Node3D = null
			for prop_value in get_nodes_in_group("npc_activity_props"):
				var candidate := prop_value as Node3D
				if str(candidate.get_meta("activity_station", "")) == str(state.station_id):
					prop = candidate
					break
			if prop == null:
				continue
			var target := (person.global_position + prop.global_position) * 0.5 + Vector3.UP * 0.92
			var line := prop.global_position - person.global_position
			line.y = 0.0
			if line.length_squared() < 0.01:
				line = Vector3.FORWARD
			var view_side := Vector3(-line.z, 0.0, line.x).normalized()
			camera.look_at_from_position(target + view_side * 4.25 + Vector3.UP * 1.05, target, Vector3.UP)
			for unused in 4:
				await process_frame
			var path := "%s/job_%02d_r%d_%s_%s.png" % [OUTPUT_DIR, frame_index, round_index, str(state.role).replace(" ", "_"), str(state.animation_clip)]
			var error := root.get_texture().get_image().save_png(ProjectSettings.globalize_path(path))
			print("FIX74_PROP_JOB_RENDER|", frame_index, "|", error, "|", state.role, "|", state.job_prop_kind, "|", state.animation_clip)
			frame_index += 1
		Engine.time_scale = 5.0
		await create_timer(0.65, true, false, true).timeout
		Engine.time_scale = 1.0
	print("FIX74_PROP_JOB_RENDER_COMPLETE|", frame_index)
	quit(0 if frame_index >= 15 else 1)
