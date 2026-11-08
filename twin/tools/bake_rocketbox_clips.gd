extends SceneTree

## Bakes Microsoft Rocketbox motion-capture FBX clips (MIT) into one compact AnimationLibrary per gender.
## Run: godot --headless --script tools/bake_rocketbox_clips.gd -- <dir with m_*.max.fbx / f_*.max.fbx>
## Per clip: rotations of every avatar bone, the pelvis and root height, no scale, no limb translations. The
## root's horizontal motion and turn (MotionExtractionHelper) are stored as metadata "root_motion" (positions)
## and "root_yaw" (radians), sampled at 30 Hz, so a director can move the character node exactly as the capture did.

const RATE := 30.0
const LOOPING_PREFIXES := ["idle_", "sit_chair_idle", "sit_chair_breathe", "sit_table_idle", "sit_table_breathe", "drink_idle", "crouch_idle",
	"documents_idle", "documentfile_idle", "newspaper_", "umbrella_", "headphones_", "trolley_idle", "cell_phone_talk", "gestic_", "walk_", "run_", "dancing_", "waiting_walk", "work_", "cheer_", "claphands_"]

func _initialize() -> void:
	var args := OS.get_cmdline_user_args()
	var dir_path := str(args[0]) if args.size() > 0 else "/tmp/claude-1000/rb/fbx"
	for gender in ["m", "f"]:
		var reference := (load("res://assets/rocketbox/%s/Export/%s.fbx" % ["Male_Adult_01" if gender == "m" else "Female_Adult_01", "Male_Adult_01" if gender == "m" else "Female_Adult_01"]) as PackedScene).instantiate()
		var skeleton := reference.find_child("Skeleton3D", true, false) as Skeleton3D
		var library := AnimationLibrary.new()
		var files := DirAccess.get_files_at(dir_path)
		files.sort()
		for file in files:
			if not file.begins_with(gender + "_") or not file.ends_with(".max.fbx"):
				continue
			var clip_name := file.trim_prefix(gender + "_").trim_suffix(".max.fbx")
			var animation := _bake(dir_path.path_join(file), skeleton, clip_name)
			if animation != null:
				library.add_animation(clip_name, animation)
		var out := "res://assets/rocketbox/clips_%s.res" % gender
		var err := ResourceSaver.save(library, out, ResourceSaver.FLAG_COMPRESS)
		print("BAKE ", gender, " clips=", library.get_animation_list().size(), " saved=", err == OK, " -> ", out)
		reference.free()
	quit()

func _load(path: String) -> Animation:
	var doc = ClassDB.instantiate("FBXDocument")
	var state = ClassDB.instantiate("FBXState")
	if int(doc.append_from_file(path, state, 0)) != OK:
		return null
	var scene: Node = doc.generate_scene(state)
	var player := scene.find_child("AnimationPlayer", true, false) as AnimationPlayer
	var animation := player.get_animation(player.get_animation_list()[0]).duplicate(true) as Animation
	scene.free()
	return animation

func _bake(path: String, skeleton: Skeleton3D, clip_name: String) -> Animation:
	var source := _load(path)
	if source == null:
		push_error("cannot load " + path)
		return null
	var helper_pos := -1
	var helper_rot := -1
	var root_pos := -1
	var root_rot := -1
	for t in source.get_track_count():
		var p := str(source.track_get_path(t))
		var ty := source.track_get_type(t)
		if p == "MotionExtractionHelper":
			if ty == Animation.TYPE_POSITION_3D: helper_pos = t
			if ty == Animation.TYPE_ROTATION_3D: helper_rot = t
		elif p == "Skeleton3D:Bip01":
			if ty == Animation.TYPE_POSITION_3D: root_pos = t
			if ty == Animation.TYPE_ROTATION_3D: root_rot = t
	var result := Animation.new()
	result.length = source.length
	result.step = 1.0 / RATE
	var loop := false
	for prefix in LOOPING_PREFIXES:
		if clip_name.begins_with(prefix):
			loop = true
	result.loop_mode = Animation.LOOP_LINEAR if loop else Animation.LOOP_NONE
	var samples := int(ceil(source.length * RATE)) + 1
	var motion := PackedVector3Array()
	var yaw := PackedFloat32Array()
	for i in samples:
		var time := minf(float(i) / RATE, source.length)
		var hp := source.position_track_interpolate(helper_pos, time) if helper_pos >= 0 else Vector3.ZERO
		var hr := source.rotation_track_interpolate(helper_rot, time) if helper_rot >= 0 else Quaternion.IDENTITY
		motion.append(hp)
		yaw.append(hr.get_euler(EULER_ORDER_YXZ).y)
	result.set_meta("root_motion", motion)
	result.set_meta("root_yaw", yaw)
	for t in source.get_track_count():
		var track_path := source.track_get_path(t)
		var text := str(track_path)
		if not text.begins_with("Skeleton3D:"):
			continue
		var bone := str(track_path.get_subname(0))
		if skeleton.find_bone(bone) < 0:
			continue
		var ty := source.track_get_type(t)
		var is_root := bone == "Bip01"
		if ty == Animation.TYPE_SCALE_3D or (is_root and ty == Animation.TYPE_ROTATION_3D):
			# The root's rotation is the exporter's axis convention; the turn lives in the extracted root_yaw.
			continue
		if ty == Animation.TYPE_POSITION_3D and not is_root and bone != "Bip01 Pelvis":
			continue
		var track := result.add_track(ty)
		result.track_set_path(track, track_path)
		var key_count := source.track_get_key_count(t)
		for k in key_count:
			var time := source.track_get_key_time(t, k)
			var value: Variant = source.track_get_key_value(t, k)
			if is_root and ty == Animation.TYPE_POSITION_3D:
				# Height stays in the pose; the capture's walk/step-back is the extracted root motion.
				var extracted := source.position_track_interpolate(helper_pos, time) if helper_pos >= 0 else Vector3.ZERO
				value = Vector3((value as Vector3).x - extracted.x, (value as Vector3).y, (value as Vector3).z - extracted.z)
			elif is_root and ty == Animation.TYPE_ROTATION_3D and helper_rot >= 0:
				var turn := source.rotation_track_interpolate(helper_rot, time)
				value = turn.inverse() * (value as Quaternion)
			result.track_insert_key(track, time, value)
	# Resample to 30 Hz and pack: the capture is 60+ Hz, far more than a walking citizen needs.
	result.compress(8192, 30.0, 4.0)
	return result
