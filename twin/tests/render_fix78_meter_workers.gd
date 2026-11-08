extends SceneTree

const OUTPUT_DIR := "res://screenshots/fix78_meter_workers_verified_final"
const FOOT_BONES := ["Bip01 L Foot", "Bip01 R Foot", "Bip01 L Toe0", "Bip01 R Toe0"]


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
	var target := (actor.global_position + prop.global_position) * 0.5 + Vector3.UP * 0.78
	camera.look_at_from_position(target + side * 2.75 - toward * 1.15 + Vector3.UP * 0.62, target, Vector3.UP)


func _save_frame(path: String) -> int:
	for _unused in 3:
		await process_frame
	return root.get_texture().get_image().save_png(ProjectSettings.globalize_path(path))


func _prepare_exact_pose(person: Node3D, clip: String, visible_tool_kind: String) -> void:
	var player := person.find_child("AnimationPlayer", true, false) as AnimationPlayer
	var animation_name := "rocketbox/%s" % clip
	if player == null or not player.has_animation(animation_name):
		return
	player.stop()
	player.play(animation_name, 0.0)
	var animation := player.get_animation(animation_name)
	player.seek(animation.length * 0.55, true)
	player.advance(0.0)
	player.pause()
	for value in person.find_children("*", "Node3D", true, false):
		var held := value as Node3D
		if held.is_in_group("npc_job_tools"):
			held.visible = str(held.get_meta("job_tool", "")) == visible_tool_kind
		elif held.is_in_group("npc_handled_items"):
			held.visible = false
	var skeleton := person.find_child("Skeleton3D", true, false) as Skeleton3D
	var lowest := INF
	for bone_name in FOOT_BONES:
		var bone_index := skeleton.find_bone(str(bone_name))
		if bone_index >= 0:
			lowest = minf(lowest, (skeleton.global_transform * skeleton.get_bone_global_pose(bone_index).origin).y)
	if lowest < INF:
		var expected := float(person.get_meta("npc_surface_y", person.global_position.y)) + float(person.get_meta("npc_foot_reference_y", 0.0))
		person.global_position.y += expected - lowest


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
	camera.fov = 42.0
	for _unused in 40:
		await process_frame
	var director := city.get_node("NpcAi")
	var meter_states := []
	for state_value in director.call("get_agent_debug"):
		var state := state_value as Dictionary
		if bool(state.prop_interaction_active) and str(state.job_prop_kind) in ["power_meter", "water_meter"]:
			meter_states.append(state)
	director.process_mode = Node.PROCESS_MODE_DISABLED
	var saved := 0
	var kinds := {}
	var kind_indices := {"water_meter":0, "power_meter":0}
	var exact_clips := {
		"water_meter":["InspectLow", "PickUp_Table"],
		"power_meter":["PanelKnock", "InspectLabel", "RepairMid"],
	}
	for state_value in meter_states:
		var state := state_value as Dictionary
		var actor := state.node as Node3D
		var prop := _prop_for(str(state.station_id))
		if prop == null:
			continue
		var kind := str(state.job_prop_kind)
		var candidates := exact_clips[kind] as Array
		var exact_clip := str(candidates[int(kind_indices[kind]) % candidates.size()])
		kind_indices[kind] = int(kind_indices[kind]) + 1
		_prepare_exact_pose(actor, exact_clip, "meter_reader" if exact_clip == "PickUp_Table" else "")
		await process_frame
		_frame_subject(camera, actor, prop)
		var path := "%s/meter_%02d_%s_%s.png" % [OUTPUT_DIR, saved, kind, exact_clip]
		var error := await _save_frame(path)
		print("FIX78_METER_RENDER|kind=%s|role=%s|exact_clip=%s|save=%d" % [kind, str(state.role), exact_clip, error])
		kinds[kind] = true
		saved += 1
	if saved < 3 or not kinds.has("power_meter") or not kinds.has("water_meter"):
		printerr("FIX78 METER RENDER FAILED: only %d active meter workers across %s" % [saved, str(kinds.keys())])
		quit(1)
		return
	print("FIX78_METER_RENDER_COMPLETE|frames=%d|kinds=%d" % [saved, kinds.size()])
	quit(0)
