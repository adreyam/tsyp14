extends SceneTree

const OUTPUT_DIR := "res://screenshots/fix78_crouch_grounding_verified_final"


func _initialize() -> void:
	call_deferred("_render")


func _prop_for(station_id: String) -> Node3D:
	for value in get_nodes_in_group("npc_meter_work_props"):
		if value is Node3D and str((value as Node3D).get_meta("activity_station", "")) == station_id:
			return value as Node3D
	return null


func _frame_subject(camera: Camera3D, actor: Node3D, prop: Node3D) -> void:
	var toward := prop.global_position - actor.global_position
	toward.y = 0.0
	if toward.length_squared() < 0.01:
		toward = -actor.global_transform.basis.z
	toward = toward.normalized()
	var side := Vector3(-toward.z, 0.0, toward.x)
	var target := actor.global_position.lerp(prop.global_position, 0.38) + Vector3.UP * 0.62
	camera.look_at_from_position(target + side * 2.45 - toward * 0.65 + Vector3.UP * 0.18, target, Vector3.UP)


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
	for _unused in 40:
		await process_frame
	var director := city.get_node("NpcAi")
	var chosen_state := {}
	for state_value in director.call("get_agent_debug"):
		var state := state_value as Dictionary
		if str(state.job_prop_kind) == "water_meter":
			chosen_state = state
			break
	if chosen_state.is_empty():
		printerr("FIX78 CROUCH RENDER FAILED: no water-meter worker")
		quit(1)
		return
	director.process_mode = Node.PROCESS_MODE_DISABLED
	var actor := chosen_state.node as Node3D
	var prop := _prop_for(str(chosen_state.station_id))
	var chosen_agent := {}
	for agent_value in director.get("_agents") as Array:
		var agent := agent_value as Dictionary
		if agent.node == actor:
			chosen_agent = agent
			break
	if prop == null or chosen_agent.is_empty():
		printerr("FIX78 CROUCH RENDER FAILED: missing actor state or meter prop")
		quit(1)
		return
	var player := chosen_agent.animation as AnimationPlayer
	var animation := player.get_animation("rocketbox/InspectLow")
	for value in actor.find_children("*", "Node3D", true, false):
		var held := value as Node3D
		if held.is_in_group("npc_job_tools") or held.is_in_group("npc_handled_items"):
			held.visible = false
	_frame_subject(camera, actor, prop)
	var phases := [0.10, 0.30, 0.50, 0.70, 0.90]
	for index in phases.size():
		var phase := float(phases[index])
		player.play("rocketbox/InspectLow", 0.0)
		player.seek(animation.length * phase, true)
		player.advance(0.0)
		player.pause()
		actor.global_position.y = float(chosen_agent.ground_y)
		director.call("_apply_pose_grounding", chosen_agent, 1.0 / 60.0)
		for _unused in 3:
			await process_frame
		var path := "%s/crouch_%02d_phase_%02d.png" % [OUTPUT_DIR, index, int(round(phase * 100.0))]
		var error := root.get_texture().get_image().save_png(ProjectSettings.globalize_path(path))
		print("FIX78_CROUCH_GROUND_RENDER|phase=%.2f|root_offset=%.3f|save=%d" % [phase, float(chosen_agent.pose_ground_offset), error])
	print("FIX78_CROUCH_GROUND_RENDER_COMPLETE|frames=%d" % phases.size())
	quit(0)
