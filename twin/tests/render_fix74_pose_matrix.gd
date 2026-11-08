extends SceneTree

const OUTPUT_DIR := "res://screenshots/fix74_pose_matrix"
const ROLE_POSES := {
	"road maintenance technician": ["InspectLow", "PickUp_Table", "RepairMid"],
	"parks worker": ["Farm_Watering", "Farm_PlantSeed", "Farm_Harvest"],
	"market stocker": ["Chest_Open", "PickUp_Table", "Interact"],
	"public information technician": ["Interact", "PickUp_Table"],
	"horticulture specialist": ["InspectLow", "Farm_PlantSeed", "Farm_Harvest"],
}

func _initialize() -> void:
	call_deferred("_render")

func _render() -> void:
	root.size = Vector2i(1280, 720)
	DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path(OUTPUT_DIR))
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	root.add_child(city)
	var environment := city.get_node("WorldEnvironment") as WorldEnvironment
	environment.environment.background_mode = Environment.BG_COLOR
	environment.environment.background_color = Color(0.44, 0.57, 0.72)
	environment.environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	environment.environment.ambient_light_color = Color(0.82, 0.86, 0.94)
	environment.environment.ambient_light_energy = 0.45
	environment.environment.fog_enabled = false
	(city.get_node("Sun") as DirectionalLight3D).light_energy = 1.35
	for layer in city.find_children("*", "CanvasLayer", true, false):
		(layer as CanvasLayer).visible = false
	var camera := Camera3D.new()
	city.add_child(camera)
	camera.current = true
	camera.fov = 41.0
	for unused in 45:
		await process_frame
	var director := city.get_node("NpcAi")
	var guaranteed := {}
	for state_value in director.call("get_agent_debug"):
		var state := state_value as Dictionary
		if bool(state.guaranteed_prop_job):
			guaranteed[str(state.role)] = state
	director.process_mode = Node.PROCESS_MODE_DISABLED
	var frame_index := 0
	for role_value in ROLE_POSES:
		var role := str(role_value)
		if not guaranteed.has(role):
			continue
		var state := guaranteed[role] as Dictionary
		var person := state.node as Node3D
		var prop: Node3D = null
		for prop_value in get_nodes_in_group("npc_activity_props"):
			var candidate := prop_value as Node3D
			if str(candidate.get_meta("activity_station", "")) == str(state.station_id):
				prop = candidate
				break
		if prop == null:
			continue
		var player := person.find_child("AnimationPlayer", true, false) as AnimationPlayer
		for clip_value in ROLE_POSES[role]:
			var clip := str(clip_value)
			var animation_name := "rocketbox/%s" % clip
			if not player.has_animation(animation_name):
				continue
			for tool_value in person.find_children("JobTool_*", "Node3D", true, false):
				var tool := tool_value as Node3D
				tool.visible = clip == "Farm_Watering" and str(tool.get_meta("job_tool", "")) == "watering_can"
			player.play(animation_name)
			player.speed_scale = 0.0
			var animation := player.get_animation(animation_name)
			player.seek(animation.length * 0.55, true)
			player.advance(0.0)
			var target := (person.global_position + prop.global_position) * 0.5 + Vector3.UP * 0.9
			var line := prop.global_position - person.global_position
			line.y = 0.0
			if line.length_squared() < 0.01:
				line = Vector3.FORWARD
			var view_side := Vector3(-line.z, 0.0, line.x).normalized()
			camera.look_at_from_position(target + view_side * 4.55 + Vector3.UP * 1.15, target, Vector3.UP)
			for unused in 5:
				await process_frame
			var path := "%s/pose_%02d_%s_%s.png" % [OUTPUT_DIR, frame_index, role.replace(" ", "_"), clip]
			var error := root.get_texture().get_image().save_png(ProjectSettings.globalize_path(path))
			print("FIX74_POSE_MATRIX_RENDER|", frame_index, "|", error, "|", role, "|", state.job_prop_kind, "|", clip)
			frame_index += 1
	print("FIX74_POSE_MATRIX_COMPLETE|", frame_index)
	quit(0 if frame_index == 14 else 1)
