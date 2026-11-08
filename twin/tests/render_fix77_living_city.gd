extends SceneTree

const OUTPUT_DIR := "res://screenshots/fix77_living_city"


func _initialize() -> void:
	call_deferred("_render")


func _prop_for(station_id: String) -> Node3D:
	for value in get_nodes_in_group("npc_activity_props"):
		if value is Node3D and str((value as Node3D).get_meta("activity_station", "")) == station_id:
			return value as Node3D
	return null


func _frame_subject(camera: Camera3D, actor: Node3D, prop: Node3D) -> void:
	var target := actor.global_position + Vector3.UP * 1.05
	var toward := -actor.global_transform.basis.z.normalized()
	if is_instance_valid(prop):
		target = (actor.global_position + prop.global_position) * 0.5 + Vector3.UP * 0.90
		toward = prop.global_position - actor.global_position
		toward.y = 0.0
		if toward.length_squared() > 0.01: toward = toward.normalized()
	var side := Vector3(-toward.z, 0.0, toward.x)
	camera.look_at_from_position(target + side * 3.15 - toward * 1.35 + Vector3.UP * 0.72, target, Vector3.UP)


func _save_frame(path: String) -> int:
	for _unused in 3: await process_frame
	return root.get_texture().get_image().save_png(ProjectSettings.globalize_path(path))


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
	environment.environment.ambient_light_energy = 0.82
	environment.environment.tonemap_exposure = 1.0
	environment.environment.fog_enabled = false
	(city.get_node("Sun") as DirectionalLight3D).light_energy = 1.65
	for layer in city.find_children("*", "CanvasLayer", true, false):
		(layer as CanvasLayer).visible = false
	var camera := Camera3D.new()
	city.add_child(camera)
	camera.current = true
	camera.fov = 43.0
	for _unused in 35: await process_frame
	var director := city.get_node("NpcAi") as Node
	var saved := 0

	# First audit the purposeful, object-supported exchanges at human scale.
	var grouped := {}
	for state_value in director.call("get_agent_debug"):
		var state := state_value as Dictionary
		var interaction_id := str(state.interaction_id)
		if interaction_id.is_empty(): continue
		if not grouped.has(interaction_id): grouped[interaction_id] = []
		(grouped[interaction_id] as Array).append(state)
	for interaction_id in grouped.keys():
		var pair := grouped[interaction_id] as Array
		var a := (pair[0] as Dictionary).node as Node3D
		var b := (pair[1] as Dictionary).node as Node3D
		var midpoint := (a.global_position + b.global_position) * 0.5 + Vector3.UP * 1.02
		var line := (b.global_position - a.global_position).normalized()
		var side := Vector3(-line.z, 0.0, line.x)
		camera.look_at_from_position(midpoint - side * 4.15 + Vector3.UP * 1.05, midpoint, Vector3.UP)
		var path := "%s/exchange_%02d_%s.png" % [OUTPUT_DIR, saved, str(interaction_id)]
		var error := await _save_frame(path)
		print("FIX77_LIVING_CITY_RENDER|exchange|%s|turns=%s/%s|item=%s|save=%d" % [str(interaction_id), str((pair[0] as Dictionary).interaction_turn), str((pair[1] as Dictionary).interaction_turn), str((pair[0] as Dictionary).active_hand_item_kind) + str((pair[1] as Dictionary).active_hand_item_kind), error])
		saved += 1

	# Observe normal runtime until six distinct held-item/tool work scenes and six
	# role-distinct physical work scenes have appeared. No pose is forced here.
	var captured_item_kinds := {}
	var captured_physical_roles := {}
	Engine.time_scale = 3.0
	for frame in 480:
		await physics_frame
		if frame % 6 != 0: continue
		for state_value in director.call("get_agent_debug"):
			var state := state_value as Dictionary
			if not str(state.motion_state).begins_with("performing_"): continue
			var item_kind := str(state.active_hand_item_kind)
			if item_kind.is_empty(): item_kind = str(state.active_tool_kind)
			var role := str(state.role)
			var category := ""
			var category_key := ""
			if not item_kind.is_empty() and not captured_item_kinds.has(item_kind) and captured_item_kinds.size() < 6:
				category = "handled_item"
				category_key = item_kind
				captured_item_kinds[item_kind] = true
			elif bool(state.prop_interaction_active) and not captured_physical_roles.has(role) and captured_physical_roles.size() < 6:
				category = "physical_work"
				category_key = role
				captured_physical_roles[role] = true
			if category.is_empty(): continue
			Engine.time_scale = 1.0
			var actor := state.node as Node3D
			_frame_subject(camera, actor, _prop_for(str(state.station_id)))
			var path := "%s/%s_%02d_%s_%s.png" % [OUTPUT_DIR, category, saved, category_key.replace(" ", "_"), str(state.animation_clip)]
			var error := await _save_frame(path)
			print("FIX77_LIVING_CITY_RENDER|%s|role=%s|stage=%s|clip=%s|item=%s|save=%d" % [category, role, str(state.work_stage), str(state.animation_clip), item_kind, error])
			saved += 1
			Engine.time_scale = 3.0
		if captured_item_kinds.size() >= 6 and captured_physical_roles.size() >= 6:
			break
	Engine.time_scale = 1.0
	print("FIX77_LIVING_CITY_RENDER_COMPLETE|frames=%d|item_kinds=%d|physical_roles=%d" % [saved, captured_item_kinds.size(), captured_physical_roles.size()])
	quit(0 if saved >= 18 and captured_item_kinds.size() >= 6 and captured_physical_roles.size() >= 6 else 1)
