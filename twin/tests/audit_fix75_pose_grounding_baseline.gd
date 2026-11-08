extends SceneTree

func _initialize() -> void:
	call_deferred("_run")

func _foot_height(person: Node3D, skeleton: Skeleton3D) -> float:
	var inverse := person.global_transform.affine_inverse()
	var lowest := INF
	for bone_name in ["Bip01 L Foot", "Bip01 R Foot", "Bip01 L Toe0", "Bip01 R Toe0"]:
		var index := skeleton.find_bone(bone_name)
		if index < 0:
			continue
		var world_position := skeleton.global_transform * skeleton.get_bone_global_pose(index).origin
		lowest = minf(lowest, (inverse * world_position).y)
	return lowest

func _run() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	root.add_child(city)
	await process_frame
	await create_timer(0.45).timeout
	var director := city.get_node("NpcAi")
	var target: Node3D = null
	for state_value in director.call("get_agent_debug"):
		var state := state_value as Dictionary
		if bool(state.guaranteed_prop_job) and str(state.animation_clip) == "InspectLow":
			target = state.node as Node3D
			break
	if target == null:
		printerr("POSE_GROUNDING_BASELINE_FAILED|no crouched guaranteed worker")
		quit(1)
		return
	director.process_mode = Node.PROCESS_MODE_DISABLED
	var player := target.find_child("AnimationPlayer", true, false) as AnimationPlayer
	var skeleton := target.find_child("Skeleton3D", true, false) as Skeleton3D
	target.global_position.y = 0.17
	player.play("rocketbox/Idle_Neutral")
	player.seek(player.get_animation("rocketbox/Idle_Neutral").length * 0.5, true)
	player.advance(0.0)
	var reference := _foot_height(target, skeleton)
	var maximum_gap := 0.0
	for fraction in [0.10, 0.30, 0.50, 0.70, 0.90]:
		player.play("rocketbox/InspectLow")
		player.seek(player.get_animation("rocketbox/InspectLow").length * float(fraction), true)
		player.advance(0.0)
		var foot_height := _foot_height(target, skeleton)
		var gap := foot_height - reference
		maximum_gap = maxf(maximum_gap, gap)
		print("POSE_GROUNDING_BASELINE|fraction=%.2f|foot_reference=%.4f|foot_pose=%.4f|floating_gap=%.4f" % [fraction, reference, foot_height, gap])
	print("POSE_GROUNDING_BASELINE_SUMMARY|max_floating_gap=%.4f" % maximum_gap)
	quit(0)
