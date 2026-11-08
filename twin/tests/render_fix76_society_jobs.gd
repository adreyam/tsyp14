extends SceneTree

const OUTPUT_DIR := "res://screenshots/fix76_society_jobs"
const FOOT_BONES := ["Bip01 L Foot", "Bip01 R Foot", "Bip01 L Toe0", "Bip01 R Toe0"]

func _initialize() -> void:
	call_deferred("_render")

func _lowest_foot_y(person: Node3D) -> float:
	var skeleton := person.find_child("Skeleton3D", true, false) as Skeleton3D
	var lowest := INF
	for bone_name in FOOT_BONES:
		var index := skeleton.find_bone(bone_name)
		if index >= 0:
			lowest = minf(lowest, (skeleton.global_transform * skeleton.get_bone_global_pose(index).origin).y)
	return lowest

func _prop_for(station_id: String) -> Node3D:
	for value in get_nodes_in_group("npc_activity_props"):
		if value is Node3D and str((value as Node3D).get_meta("activity_station", "")) == station_id:
			return value as Node3D
	return null

func _render() -> void:
	root.size = Vector2i(1280, 720)
	DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path(OUTPUT_DIR))
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	root.add_child(city)
	var environment := city.get_node("WorldEnvironment") as WorldEnvironment
	environment.environment.background_mode = Environment.BG_COLOR
	environment.environment.background_color = Color(0.46, 0.60, 0.76)
	environment.environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	environment.environment.ambient_light_color = Color(0.88, 0.92, 1.0)
	environment.environment.ambient_light_energy = 0.48
	environment.environment.tonemap_exposure = 0.90
	environment.environment.adjustment_brightness = 0.96
	environment.environment.fog_enabled = false
	(city.get_node("Sun") as DirectionalLight3D).light_energy = 1.25
	for layer in city.find_children("*", "CanvasLayer", true, false):
		(layer as CanvasLayer).visible = false
	var camera := Camera3D.new()
	city.add_child(camera)
	camera.current = true
	camera.fov = 39.0
	for _unused in 45:
		await process_frame
	# Capture inside the motion rather than during its initial neutral/blend-in
	# keys; this makes visual review judge actual hand use and crouch depth.
	await create_timer(0.6).timeout
	var director := city.get_node("NpcAi")
	var frame_index := 0
	var maximum_foot_error := 0.0
	var observed_clips := {}
	var requested_rounds_text := OS.get_environment("FIX76_RENDER_ROUNDS")
	var render_rounds := clampi(int(requested_rounds_text), 1, 4) if requested_rounds_text.is_valid_int() else 4
	var freeze_midpose := OS.get_environment("FIX76_RENDER_MIDPOSE") == "1"
	if freeze_midpose:
		director.process_mode = Node.PROCESS_MODE_DISABLED
	for round_index in render_rounds:
		var states: Array[Dictionary] = []
		for state_value in director.call("get_agent_debug"):
			var state := state_value as Dictionary
			if bool(state.guaranteed_prop_job) and bool(state.prop_interaction_active):
				states.append(state)
		for state in states:
			var person := state.node as Node3D
			var prop := _prop_for(str(state.station_id))
			if prop == null:
				continue
			if str(state.get("work_order_id", "")).is_empty() or str(state.get("decision_reason", "")).is_empty():
				printerr("FIX76_SOCIETY_RENDER_REJECTED|missing causal work state|%s" % str(state.get("citizen_id", state.npc_id)))
				quit(1)
				return
			if freeze_midpose:
				var player := person.find_child("AnimationPlayer", true, false) as AnimationPlayer
				var animation_name := "rocketbox/%s" % str(state.animation_clip)
				if player.has_animation(animation_name):
					player.play(animation_name)
					player.speed_scale = 0.0
					player.seek(player.get_animation(animation_name).length * 0.55, true)
					player.advance(0.0)
					await process_frame
					var desired_foot_y := float(person.get_meta("npc_surface_y", state.surface_y)) + float(person.get_meta("npc_foot_reference_y", 0.0))
					person.global_position.y += desired_foot_y - _lowest_foot_y(person)
			observed_clips[str(state.animation_clip)] = true
			var expected_foot_y := float(person.get_meta("npc_surface_y", state.surface_y)) + float(person.get_meta("npc_foot_reference_y", 0.0))
			var foot_error := absf(_lowest_foot_y(person) - expected_foot_y)
			maximum_foot_error = maxf(maximum_foot_error, foot_error)
			var target := (person.global_position + prop.global_position) * 0.5 + Vector3.UP * 0.83
			var line := prop.global_position - person.global_position
			line.y = 0.0
			if line.length_squared() < 0.01:
				line = Vector3.FORWARD
			var view_side := Vector3(-line.z, 0.0, line.x).normalized()
			camera.look_at_from_position(target + view_side * 3.75 + Vector3.UP * 0.92, target, Vector3.UP)
			for _unused in 5:
				await process_frame
			var path := "%s/job_%02d_r%d_%s_%s.png" % [OUTPUT_DIR, frame_index, round_index, str(state.role).replace(" ", "_"), str(state.animation_clip)]
			var error := root.get_texture().get_image().save_png(ProjectSettings.globalize_path(path))
			print("FIX76_SOCIETY_JOB_RENDER|%d|%s|%s|%s|work_order=%s|foot_error=%.3f|save=%d" % [frame_index, str(state.role), str(state.job_prop_kind), str(state.animation_clip), str(state.work_order_id), foot_error, error])
			frame_index += 1
		Engine.time_scale = 5.0
		await create_timer(3.00, true, false, true).timeout
		Engine.time_scale = 1.0
	print("FIX76_SOCIETY_JOB_RENDER_COMPLETE|frames=%d|clips=%d|max_foot_error=%.3f" % [frame_index, observed_clips.size(), maximum_foot_error])
	var required_clip_variety := 4 if render_rounds == 1 else 8
	quit(0 if frame_index == render_rounds * 5 and observed_clips.size() >= required_clip_variety and maximum_foot_error <= 0.035 else 1)
