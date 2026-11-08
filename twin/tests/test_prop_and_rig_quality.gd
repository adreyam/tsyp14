extends SceneTree

## Real-world prop sizing/grounding, anchor/part coverage of every interaction, and the foot-planting
## guarantees of the character IK layer (idle, walk, crouch).

const PropFactory := preload("res://scripts/props/prop_factory.gd")
const PropCatalog := preload("res://scripts/props/prop_catalog.gd")
const Activity := preload("res://scripts/characters/activity_library.gd")
const RocketboxAnimationLibrary := preload("res://scripts/rocketbox_animation_library.gd")
const CitizenIK := preload("res://scripts/characters/citizen_ik.gd")

var failures: Array[String] = []

func _initialize() -> void:
	call_deferred("_run")

func _fail(message: String) -> void:
	failures.append(message)
	print("FAIL: ", message)

func _run() -> void:
	_test_props()
	_test_interaction_references()
	await _test_rig()
	print("TEST_RESULT ", "PASS" if failures.is_empty() else "FAIL (%d)" % failures.size())
	quit(0 if failures.is_empty() else 1)

func _test_props() -> void:
	var kinds := PropCatalog.ENTRIES.keys()
	for kind in kinds:
		var prop := PropFactory.build(str(kind))
		if prop == null:
			_fail("prop %s did not build" % kind)
			continue
		root.add_child(prop)
		var entry := PropCatalog.entry(str(kind))
		var size: Vector3 = prop.get_meta("prop_size")
		var expected := float(entry.get("height", 0.0))
		if expected > 0.0 and not bool(entry.get("flush", false)) and absf(size.y - expected) > maxf(0.12, expected * 0.18):
			_fail("%s is %.2f m tall, catalogued %.2f m" % [kind, size.y, expected])
		if size.x > 4.0 or size.z > 4.0 or size.y > 4.0:
			_fail("%s is implausibly large %s" % [kind, str(size)])
		if not bool(entry.get("flush", false)) and absf(PropFactory.ground_gap(prop, 0.0)) > 0.001:
			_fail("%s floats/sinks by %.3f m" % [kind, PropFactory.ground_gap(prop, 0.0)])
		prop.queue_free()

func _test_interaction_references() -> void:
	## Every clip an activity plays must exist, for both genders, in the baked Rocketbox libraries; every prop
	## kind in the catalogue gets a scene whose stand position is outside the prop's own footprint.
	var libraries := {"m": load("res://assets/rocketbox/clips_m.res") as AnimationLibrary, "f": load("res://assets/rocketbox/clips_f.res") as AnimationLibrary}
	var played := {}
	for kind in PropCatalog.ENTRIES.keys():
		for stage_index in 4:
			for beat_value in Activity.beats_for(str(kind), stage_index, 7):
				played[str((beat_value as Dictionary).clip)] = true
	for clip in played:
		for gender in libraries:
			if not (libraries[gender] as AnimationLibrary).has_animation(str(clip)):
				_fail("clip %s is missing from the %s library" % [clip, gender])
		if Activity.stats(str(clip)).is_empty():
			_fail("clip %s has no measured hand statistics" % clip)
	for kind in PropCatalog.ENTRIES.keys():
		var prop := PropFactory.build(str(kind))
		if prop == null:
			continue
		root.add_child(prop)
		var scene := Activity.scene_class(str(kind))
		var stand := Activity.stand_transform(prop, str(kind), scene)
		var position: Vector3 = stand.position
		var size: Vector3 = prop.get_meta("prop_size")
		var local := prop.global_transform.affine_inverse() * position
		if absf(local.x) < size.x * 0.5 - 0.02 and absf(local.z) < size.z * 0.5 - 0.02 and not (str(kind) in ["manhole", "water_meter"]):
			_fail("%s (%s): the person would stand inside the prop at %s" % [kind, scene, str(local)])
		prop.queue_free()

func _test_rig() -> void:
	for model in ["Male_Adult_01", "Female_Adult_05", "Construction_Male_02"]:
		var person := (load("res://assets/rocketbox/%s/Export/%s.fbx" % [model, model]) as PackedScene).instantiate() as Node3D
		root.add_child(person)
		var player := person.find_child("AnimationPlayer", true, false) as AnimationPlayer
		RocketboxAnimationLibrary.install_on(person, player)
		var skeleton := person.find_child("Skeleton3D", true, false) as Skeleton3D
		player.play("rocketbox/Idle_Neutral")
		player.seek(0.5, true)
		player.advance(0.0)
		var ik := CitizenIK.new()
		skeleton.add_child(ik)
		ik.setup(skeleton)
		ik.calibrate(0.0)
		for clip in ["Idle_Neutral", "Walking"]:
			var animation := player.get_animation("rocketbox/" + clip)
			player.play("rocketbox/" + clip)
			player.speed_scale = 0.0
			var worst := 0.0
			for step in 24:
				player.seek(animation.length * float(step) / 24.0, true)
				await process_frame
				await process_frame
				worst = maxf(worst, absf(float(ik.final_state["support"])))
			if worst > 0.004:
				_fail("%s %s: lowest foot contact off the pavement by %.1f mm" % [model, clip, worst * 1000.0])
		# Crouch with pinned feet: the soles may not slide and the pelvis must drop by the requested amount.
		player.play("rocketbox/Idle_Neutral")
		player.speed_scale = 1.0
		await process_frame
		await process_frame
		var pins := {}
		for side in ["L", "R"]:
			var sole: Vector3 = ik.sole_world(side)
			sole.y = 0.0
			pins[side] = sole
			ik.pin_foot(side, sole, 0.0, 1.0)
		var standing_pelvis: float = ik.final_state["pelvis"].y
		ik.pelvis_drop = 0.4
		ik.spine_bend = 0.5
		var max_slip := 0.0
		for step in 30:
			await process_frame
			for side in ["L", "R"]:
				max_slip = maxf(max_slip, (ik.final_state["sole_" + side] as Vector3).distance_to(pins[side] as Vector3))
		if max_slip > 0.006:
			_fail("%s crouch: sole slid %.1f mm" % [model, max_slip * 1000.0])
		var dropped: float = standing_pelvis - float(ik.final_state["pelvis"].y)
		if absf(dropped - 0.4) > 0.03:
			_fail("%s crouch: pelvis dropped %.2f m, expected 0.40 m" % [model, dropped])
		person.queue_free()
