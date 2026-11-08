extends SceneTree

const RocketboxAnimationLibrary := preload("res://scripts/rocketbox_animation_library.gd")

func _initialize() -> void:
	call_deferred("_run")

func _run() -> void:
	var person := (load("res://assets/rocketbox/Construction_Female_01/Export/Construction_Female_01.fbx") as PackedScene).instantiate() as Node3D
	root.add_child(person)
	await process_frame
	var skeleton := person.find_child("Skeleton3D", true, false) as Skeleton3D
	var player := person.find_child("AnimationPlayer", true, false) as AnimationPlayer
	print("FIX78_ANIMATION_PROCESS|callback_mode=%d|process_priority=%d|physics_priority=%d" % [player.callback_mode_process, player.process_priority, player.process_physics_priority])
	RocketboxAnimationLibrary.install_on(person, player)
	for bone_index in skeleton.get_bone_count():
		var bone_name := str(skeleton.get_bone_name(bone_index))
		if bone_name.contains("Hand") or bone_name.contains("Finger") or bone_name.contains("Foot") or bone_name.contains("Toe") or bone_name.contains("Pelvis") or bone_name.contains("Thigh") or bone_name.contains("Calf") or bone_name.contains("Spine") or bone_name.contains("Neck") or bone_name.contains("Head"):
			var rest_global := skeleton.get_bone_global_rest(bone_index)
			print("FIX78_BONE|%s|parent=%s|origin=%s|basis_x=%s|basis_y=%s|basis_z=%s" % [bone_name, str(skeleton.get_bone_name(skeleton.get_bone_parent(bone_index))) if skeleton.get_bone_parent(bone_index) >= 0 else "", str(rest_global.origin), str(rest_global.basis.x), str(rest_global.basis.y), str(rest_global.basis.z)])
	var sample_bones := ["Bip01 Pelvis", "Bip01 Spine", "Bip01 Spine1", "Bip01 Spine2", "Bip01 L Thigh", "Bip01 L Calf", "Bip01 L Foot", "Bip01 R Thigh", "Bip01 R Calf", "Bip01 R Foot", "Bip01 Neck", "Bip01 Head"]
	var local_samples := {}
	for clip in ["Idle_Neutral", "PickUp_Table"]:
		var animation_name := "rocketbox/%s" % clip
		var animation := player.get_animation(animation_name)
		player.play(animation_name)
		player.seek(animation.length * 0.55, true)
		player.advance(0.0)
		local_samples[clip] = {}
		for bone_name in sample_bones:
			var bone_index := skeleton.find_bone(bone_name)
			(local_samples[clip] as Dictionary)[bone_name] = skeleton.get_bone_pose(bone_index)
	for bone_name in sample_bones:
		var idle_pose := (local_samples["Idle_Neutral"] as Dictionary)[bone_name] as Transform3D
		var work_pose := (local_samples["PickUp_Table"] as Dictionary)[bone_name] as Transform3D
		print("FIX78_POSE_DELTA|%s|rotation_deg=%.3f|position_m=%.4f" % [bone_name, rad_to_deg(idle_pose.basis.get_rotation_quaternion().angle_to(work_pose.basis.get_rotation_quaternion())), idle_pose.origin.distance_to(work_pose.origin)])
	quit(0)
