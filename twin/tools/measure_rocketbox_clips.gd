extends SceneTree

## Measures, for every baked Rocketbox clip, where the hands, head, pelvis and feet are during the working
## part of the capture (character-node space, root motion removed) so props can be placed to meet it.
## Output: res://assets/rocketbox/clip_stats.json

const ClipPlayer := preload("res://scripts/characters/clip_player.gd")

func _initialize() -> void:
	call_deferred("_run")

func _vec(v: Vector3) -> Array:
	return [snappedf(v.x, 0.001), snappedf(v.y, 0.001), snappedf(v.z, 0.001)]

func _run() -> void:
	var stats := {}
	for model in ["Male_Adult_01", "Female_Adult_01"]:
		var person := (load("res://assets/rocketbox/%s/Export/%s.fbx" % [model, model]) as PackedScene).instantiate() as Node3D
		root.add_child(person)
		var player := person.find_child("AnimationPlayer", true, false) as AnimationPlayer
		var clips := ClipPlayer.new()
		clips.setup(person, player)
		var skeleton := person.find_child("Skeleton3D", true, false) as Skeleton3D
		var library := ClipPlayer.library_for(person)
		var gender := "m" if model.begins_with("Male") else "f"
		for clip in library.get_animation_list():
			var animation := library.get_animation(clip)
			player.play("rb/" + clip)
			player.speed_scale = 0.0
			var sums := {"rh": Vector3.ZERO, "lh": Vector3.ZERO, "head": Vector3.ZERO, "pelvis": Vector3.ZERO}
			var lows := {"rh_y": INF, "lh_y": INF}
			var n := 0
			var steps := 12
			for i in steps:
				var t := animation.length * (0.15 + 0.7 * float(i) / float(steps - 1))
				player.seek(t, true)
				await process_frame
				var sk := skeleton.global_transform
				var rh := sk * skeleton.get_bone_global_pose(skeleton.find_bone("Bip01 R Hand")).origin
				var lh := sk * skeleton.get_bone_global_pose(skeleton.find_bone("Bip01 L Hand")).origin
				var hd := sk * skeleton.get_bone_global_pose(skeleton.find_bone("Bip01 Head")).origin
				var pv := sk * skeleton.get_bone_global_pose(skeleton.find_bone("Bip01 Pelvis")).origin
				sums.rh += rh; sums.lh += lh; sums.head += hd; sums.pelvis += pv
				n += 1
			var entry := {"length": snappedf(animation.length, 0.01), "loop": animation.loop_mode == Animation.LOOP_LINEAR,
				"rh": _vec(sums.rh / n), "lh": _vec(sums.lh / n), "head": _vec(sums.head / n), "pelvis": _vec(sums.pelvis / n)}
			var motion := animation.get_meta("root_motion", PackedVector3Array()) as PackedVector3Array
			if motion.size() > 1:
				entry["root_end"] = _vec(motion[motion.size() - 1] - motion[0])
			var yaw := animation.get_meta("root_yaw", PackedFloat32Array()) as PackedFloat32Array
			if yaw.size() > 1:
				entry["yaw_end"] = snappedf(yaw[yaw.size() - 1] - yaw[0], 0.001)
			(stats.get_or_add(clip, {}) as Dictionary)[gender] = entry
		person.queue_free()
	var file := FileAccess.open("res://assets/rocketbox/clip_stats.json", FileAccess.WRITE)
	file.store_string(JSON.stringify(stats, "\t"))
	file.close()
	print("MEASURED ", stats.size(), " clips")
	quit()
