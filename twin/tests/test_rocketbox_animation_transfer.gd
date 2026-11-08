extends SceneTree

const MODEL_PATH := "res://assets/rocketbox/Delivery_Male_01/Export/Delivery_Male_01.fbx"
const CLIPS := ["Walk", "Idle_Neutral", "Interact", "OfferParcel", "Farm_Harvest"]

var _failed := false

func _initialize() -> void:
	call_deferred("_run")

func _run() -> void:
	var model := (load(MODEL_PATH) as PackedScene).instantiate() as Node3D
	root.add_child(model)
	await process_frame
	var player := model.find_child("AnimationPlayer", true, false) as AnimationPlayer
	var skeleton := model.find_child("Skeleton3D", true, false) as Skeleton3D
	if player == null or skeleton == null:
		_fail("model must contain AnimationPlayer and Skeleton3D")
		quit(1)
		return
	var helper := load("res://scripts/rocketbox_animation_library.gd")
	if not helper.install_on(model, player):
		_fail("production Rocketbox animation transfer failed")
		quit(1)
		return
	var bounds := _bounds(model)
	print("MODEL_BOUNDS|", bounds.position, "|", bounds.size)
	# Rocketbox's FBX bind-space AABB is Z-up even though the imported skinned
	# skeleton is correctly Y-up. Its longest dimension is therefore the stable
	# authored stature measurement.
	var authored_stature := maxf(bounds.size.x, maxf(bounds.size.y, bounds.size.z))
	if authored_stature < 1.45 or authored_stature > 2.10:
		_fail("model stature outside realistic human range: %.3f" % authored_stature)
	for clip_name in CLIPS:
		player.play("rocketbox/%s" % clip_name)
		var animation := player.get_animation("rocketbox/%s" % clip_name)
		player.seek(animation.length * 0.5, true)
		player.advance(0.0)
		var head := skeleton.get_bone_global_pose(skeleton.find_bone("Bip01 Head")).origin
		var pelvis := skeleton.get_bone_global_pose(skeleton.find_bone("Bip01 Pelvis")).origin
		var left_hand := skeleton.get_bone_global_pose(skeleton.find_bone("Bip01 L Hand")).origin
		var right_hand := skeleton.get_bone_global_pose(skeleton.find_bone("Bip01 R Hand")).origin
		var left_foot := skeleton.get_bone_global_pose(skeleton.find_bone("Bip01 L Foot")).origin
		var right_foot := skeleton.get_bone_global_pose(skeleton.find_bone("Bip01 R Foot")).origin
		print("POSE|", clip_name, "|head-pelvis=", head.distance_to(pelvis), "|hands=", left_hand.distance_to(right_hand), "|feet=", left_foot.distance_to(right_foot))
		for point in [head, pelvis, left_hand, right_hand, left_foot, right_foot]:
			if is_nan(point.x) or is_nan(point.y) or is_nan(point.z) or is_inf(point.x) or is_inf(point.y) or is_inf(point.z):
				_fail("non-finite skeletal pose: %s" % clip_name)
		if head.distance_to(pelvis) < 0.35 or head.distance_to(pelvis) > 0.95 or left_hand.distance_to(right_hand) > 2.0 or left_foot.distance_to(right_foot) > 1.55:
			_fail("implausible skeletal pose envelope for %s" % clip_name)
	print("ROCKETBOX_TRANSFER|PASS" if not _failed else "ROCKETBOX_TRANSFER|FAIL")
	quit(1 if _failed else 0)

func _bounds(model: Node3D) -> AABB:
	var result := AABB()
	var found := false
	for mesh_value in model.find_children("*", "MeshInstance3D", true, false):
		var mesh := mesh_value as MeshInstance3D
		if mesh.mesh == null:
			continue
		var transformed := mesh.global_transform * mesh.mesh.get_aabb()
		result = transformed if not found else result.merge(transformed)
		found = true
	return result

func _finite_bounds(bounds: AABB) -> bool:
	for value in [bounds.position.x, bounds.position.y, bounds.position.z, bounds.size.x, bounds.size.y, bounds.size.z]:
		if is_nan(value) or is_inf(value):
			return false
	return true

func _fail(message: String) -> void:
	_failed = true
	push_error(message)
