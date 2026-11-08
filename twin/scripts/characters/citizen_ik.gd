extends SkeletonModifier3D

## Procedural layer that runs after the animation clip on a Rocketbox (Bip01) skeleton.
## The clip supplies the natural motion; this layer makes it physically honest:
##   * feet are pinned to the ground (or to a seat's floor) so nobody slides, floats or sinks
##   * the pelvis can be lowered (crouch / sit) with the legs re-solved onto the pinned feet
##   * hands reach a world-space grip frame (a lid handle, a cup, a keypad) with a two-bone solve
##   * fingers wrap around the object with a named grip pose
##   * the head turns to what the person is looking at
## All solving is analytic two-bone IK in skeleton space, so it has no state that can drift.

const FINGERS := {
	"index": ["Finger1", "Finger11", "Finger12"], "middle": ["Finger2", "Finger21", "Finger22"],
	"ring": ["Finger3", "Finger31", "Finger32"], "pinky": ["Finger4", "Finger41", "Finger42"],
	"thumb": ["Finger0", "Finger01", "Finger02"],
}

## Curl in degrees per joint (base, middle, tip) for every named hand pose.
const GRIP_POSES := {
	"relaxed": {"index": [12, 14, 8], "middle": [14, 16, 10], "ring": [16, 18, 12], "pinky": [18, 20, 14], "thumb": [4, 6, 4]},
	"open": {"index": [0, 0, 0], "middle": [0, 0, 0], "ring": [0, 0, 0], "pinky": [0, 0, 0], "thumb": [0, 0, 0]},
	"flat": {"index": [0, 2, 0], "middle": [0, 2, 0], "ring": [0, 2, 0], "pinky": [0, 2, 0], "thumb": [0, 0, 0]},
	"hold_handle": {"index": [62, 78, 52], "middle": [64, 80, 54], "ring": [66, 82, 56], "pinky": [68, 82, 56], "thumb": [20, 22, 24]},
	"hold_cup": {"index": [48, 56, 36], "middle": [52, 60, 38], "ring": [56, 62, 40], "pinky": [60, 64, 42], "thumb": [14, 18, 16]},
	"hold_box": {"index": [28, 22, 14], "middle": [30, 24, 14], "ring": [32, 26, 16], "pinky": [34, 28, 18], "thumb": [6, 8, 6]},
	"hold_flat": {"index": [14, 16, 10], "middle": [16, 18, 10], "ring": [18, 20, 12], "pinky": [20, 22, 12], "thumb": [10, 12, 8]},
	"hold_neck": {"index": [70, 86, 60], "middle": [72, 88, 62], "ring": [74, 88, 62], "pinky": [76, 88, 62], "thumb": [24, 26, 26]},
	"pinch": {"index": [34, 30, 20], "middle": [48, 62, 40], "ring": [58, 70, 46], "pinky": [64, 74, 50], "thumb": [22, 26, 22]},
	"press": {"index": [4, 4, 2], "middle": [64, 78, 50], "ring": [68, 80, 52], "pinky": [72, 82, 54], "thumb": [18, 20, 14]},
	"push_palm": {"index": [4, 4, 2], "middle": [4, 4, 2], "ring": [6, 4, 2], "pinky": [8, 4, 2], "thumb": [4, 2, 0]},
	"grab_edge": {"index": [40, 50, 30], "middle": [42, 52, 32], "ring": [44, 54, 34], "pinky": [46, 54, 34], "thumb": [12, 14, 10]},
}

var ground_y := 0.0                    # world height of the pavement under the feet
var foot_ground_clamp := true          # never let a sole go below the ground
var pelvis_drop := 0.0                 # metres the pelvis is lowered (world up)
var pelvis_shift := Vector3.ZERO       # metres of horizontal pelvis shift in world space
var spine_bend := 0.0                  # radians the torso folds forward
var spine_twist := 0.0                 # radians the torso turns about the vertical axis
var head_target_world := Vector3.ZERO
var head_weight := 0.0
var leg_pole_forward := 1.0            # knee pole direction (forward)

var _sk: Skeleton3D
var _idx: Dictionary = {}
var _fwd := Vector3.FORWARD            # skeleton-space forward / up / right of the standing rig
var _up := Vector3.UP
var _right := Vector3.RIGHT
var _pelvis_height := 0.89
var _ankle_height := 0.085
var _toe_sole := 0.02
var _calibrated := false
var _hands := {"L": {}, "R": {}}
var _feet := {"L": {}, "R": {}}
var _grip_blend := {"L": 0.0, "R": 0.0}
var _grip_pose := {"L": "relaxed", "R": "relaxed"}
var _grip_amount := {"L": 1.0, "R": 1.0}
var _palm_local := {}                  # side -> palm normal in hand-local space (calibrated)
var _curl_hand_local := {}
var _head_forward_local := Vector3.BACK
var _sole_normal_local := {}
var _contact_local := {}              # side -> {foot: [heel], toe: [ball, tip]} calibrated sole contact points
var auto_ground := true               # keep the planted foot exactly on the pavement while walking/idling
var _auto_drop := 0.0
var _curl_axis := {}                   # bone name -> curl axis in bone-local space
var _hand_weight := {"L": 0.0, "R": 0.0}
var _foot_weight := {"L": 0.0, "R": 0.0}
var _pin_targets := {"L": null, "R": null}

signal calibrated_ready

func setup(skeleton: Skeleton3D) -> void:
	_sk = skeleton
	for bone_name in ["Bip01", "Bip01 Pelvis", "Bip01 Spine", "Bip01 Spine1", "Bip01 Spine2", "Bip01 Neck", "Bip01 Head",
			"Bip01 L Clavicle", "Bip01 L UpperArm", "Bip01 L Forearm", "Bip01 L Hand",
			"Bip01 R Clavicle", "Bip01 R UpperArm", "Bip01 R Forearm", "Bip01 R Hand",
			"Bip01 L Thigh", "Bip01 L Calf", "Bip01 L Foot", "Bip01 L Toe0",
			"Bip01 R Thigh", "Bip01 R Calf", "Bip01 R Foot", "Bip01 R Toe0"]:
		_idx[bone_name] = skeleton.find_bone(bone_name)
	for side in ["L", "R"]:
		for finger in FINGERS.values():
			for joint in finger:
				var full := "Bip01 %s %s" % [side, joint]
				_idx[full] = skeleton.find_bone(full)

# --- calibration -----------------------------------------------------------------

## Call once with the skeleton in a standing pose (idle clip evaluated) and the
## character standing on the ground at the node origin of the skeleton's owner.
func calibrate(standing_sole_world_y: float) -> void:
	if _sk == null:
		return
	var to_sk := _sk.global_transform.affine_inverse()
	var l_foot := _sk.get_bone_global_pose(int(_idx["Bip01 L Foot"])).origin
	var r_foot := _sk.get_bone_global_pose(int(_idx["Bip01 R Foot"])).origin
	var l_toe := _sk.get_bone_global_pose(int(_idx["Bip01 L Toe0"])).origin
	var r_toe := _sk.get_bone_global_pose(int(_idx["Bip01 R Toe0"])).origin
	_up = Vector3.UP
	var forward := ((l_toe - l_foot) + (r_toe - r_foot))
	forward.y = 0.0
	_fwd = forward.normalized() if forward.length() > 0.001 else Vector3.BACK
	_right = _fwd.cross(_up).normalized()
	var sole_in_sk := (to_sk * Vector3(0, standing_sole_world_y, 0)).y
	_ankle_height = ((l_foot.y + r_foot.y) * 0.5) - sole_in_sk
	_pelvis_height = _sk.get_bone_global_pose(int(_idx["Bip01 Pelvis"])).origin.y - sole_in_sk
	_toe_sole = ((l_toe.y + r_toe.y) * 0.5) - sole_in_sk
	for side in ["L", "R"]:
		_calibrate_hand(side)
		var foot_basis := _sk.get_bone_global_pose(int(_idx["Bip01 %s Foot" % side])).basis.orthonormalized()
		_sole_normal_local[side] = foot_basis.inverse() * _up
		var fp := _sk.get_bone_global_pose(int(_idx["Bip01 %s Foot" % side]))
		var tp := _sk.get_bone_global_pose(int(_idx["Bip01 %s Toe0" % side]))
		var ground_sk := (to_sk * Vector3(0, standing_sole_world_y, 0)).y
		var heel_point := Vector3(fp.origin.x, ground_sk, fp.origin.z)
		var ball_point := Vector3(tp.origin.x, ground_sk, tp.origin.z)
		var toe_dir := Vector3(tp.origin.x - fp.origin.x, 0.0, tp.origin.z - fp.origin.z).normalized()
		_contact_local[side] = {"heel": fp.affine_inverse() * heel_point, "ball": tp.affine_inverse() * ball_point,
			"tip": tp.affine_inverse() * (ball_point + toe_dir * 0.075)}
	_head_forward_local = _sk.get_bone_global_pose(int(_idx["Bip01 Head"])).basis.orthonormalized().inverse() * _fwd
	_calibrated = true
	calibrated_ready.emit()

func _calibrate_hand(side: String) -> void:
	var hand := int(_idx["Bip01 %s Hand" % side])
	var hand_pose := _sk.get_bone_global_pose(hand)
	var knuckles := Vector3.ZERO
	for finger in ["Finger1", "Finger2", "Finger3", "Finger4"]:
		knuckles += _sk.get_bone_global_pose(int(_idx["Bip01 %s %s" % [side, finger]])).origin
	knuckles /= 4.0
	var finger_dir := (knuckles - hand_pose.origin).normalized()
	var thumb := _sk.get_bone_global_pose(int(_idx["Bip01 %s Finger0" % side])).origin
	var to_thumb := thumb - knuckles
	to_thumb -= finger_dir * to_thumb.dot(finger_dir)
	to_thumb = to_thumb.normalized()
	# Right hand, palm down, fingers forward: the thumb is on the left, so palm = -(F x T);
	# the left hand mirrors it.
	var palm := finger_dir.cross(to_thumb)
	if side == "R":
		palm = -palm
	palm = palm.normalized()
	_palm_local[side] = hand_pose.basis.inverse() * palm
	var curl_axis_global := finger_dir.cross(palm).normalized()
	_curl_hand_local[side] = hand_pose.basis.orthonormalized().inverse() * curl_axis_global
	for finger in FINGERS.values():
		for joint in finger:
			var bone := int(_idx["Bip01 %s %s" % [side, joint]])
			if bone >= 0:
				_curl_axis["Bip01 %s %s" % [side, joint]] = _sk.get_bone_global_pose(bone).basis.orthonormalized().inverse() * curl_axis_global
	# The thumb opposes the palm rather than curling in its plane.
	var thumb_axis := finger_dir.cross(palm).normalized()
	for joint in FINGERS.thumb:
		var bone := int(_idx["Bip01 %s %s" % [side, joint]])
		if bone >= 0:
			_curl_axis["Bip01 %s %s" % [side, joint]] = _sk.get_bone_global_pose(bone).basis.orthonormalized().inverse() * thumb_axis

## Hand-bone-local frame whose X is the palm normal (toward what the palm holds),
## Y the axis the fingers curl around and Z along the fingers; origin at the palm centre.
func hold_frame_local(side: String) -> Transform3D:
	var p := (_palm_local[side] as Vector3).normalized()
	var a := (_curl_hand_local[side] as Vector3).normalized()
	var z := p.cross(a).normalized()
	return Transform3D(Basis(p, a, z), Vector3(0.045, 0.0, 0.0))

func bone_name_of(side: String) -> String:
	return "Bip01 %s Hand" % side

func is_calibrated() -> bool:
	return _calibrated

func pelvis_height() -> float:
	return _pelvis_height

func ankle_height() -> float:
	return _ankle_height

# --- public control surface ----------------------------------------------------------

## Reach the hand to a world-space grip frame. `frame.basis`: X = finger direction,
## Y = palm normal pointing at the object (the palm faces along +Y... see hand_frame()).
func set_hand_target(side: String, world_frame: Transform3D, weight: float, grip_pose := "relaxed", grip_amount := 1.0) -> void:
	_hands[side] = {"frame": world_frame, "grip_pose": grip_pose}
	_hand_weight[side] = clampf(weight, 0.0, 1.0)
	_grip_pose[side] = grip_pose
	_grip_amount[side] = grip_amount

func set_hand_weight(side: String, weight: float) -> void:
	_hand_weight[side] = clampf(weight, 0.0, 1.0)

func set_grip(side: String, grip_pose: String, amount := 1.0) -> void:
	_grip_pose[side] = grip_pose
	_grip_amount[side] = amount

## 0..1 how closed the hand is on its pose (animated by the interaction director).
func set_grip_blend(side: String, blend: float) -> void:
	_grip_blend[side] = clampf(blend, 0.0, 1.0)

func clear_hand(side: String) -> void:
	_hands[side] = {}
	_hand_weight[side] = 0.0

func hand_weight(side: String) -> float:
	return float(_hand_weight[side])

## Pin a foot at a world position (the ankle target is derived from the sole).
## `sole_basis` is the foot's heading in world space; only its yaw is used.
func pin_foot(side: String, sole_world: Vector3, yaw: float, weight := 1.0) -> void:
	_feet[side] = {"sole": sole_world, "yaw": yaw}
	_foot_weight[side] = clampf(weight, 0.0, 1.0)

func unpin_foot(side: String) -> void:
	_feet[side] = {}
	_foot_weight[side] = 0.0

func is_foot_pinned(side: String) -> bool:
	return not (_feet[side] as Dictionary).is_empty()

func foot_world(side: String) -> Vector3:
	return _sk.global_transform * _sk.get_bone_global_pose(int(_idx["Bip01 %s Foot" % side])).origin

## World position of the sole under the ankle (current pose, before / after IK depending on caller).
func sole_world(side: String) -> Vector3:
	var ankle := foot_world(side)
	return ankle - Vector3.UP * _ankle_height

func hand_world(side: String) -> Vector3:
	return _sk.global_transform * _sk.get_bone_global_pose(int(_idx["Bip01 %s Hand" % side])).origin

## Palm centre in world space (hand bone origin pushed along the fingers).
func palm_world(side: String) -> Vector3:
	var hand := _sk.get_bone_global_pose(int(_idx["Bip01 %s Hand" % side]))
	return _sk.global_transform * (hand.origin + hand.basis.orthonormalized() * Vector3(0.045, 0.0, 0.0))

func palm_normal_world(side: String) -> Vector3:
	var hand := _sk.get_bone_global_pose(int(_idx["Bip01 %s Hand" % side]))
	return (_sk.global_transform.basis * hand.basis.orthonormalized() * (_palm_local[side] as Vector3)).normalized()

func finger_tip_world(side: String, finger: String) -> Vector3:
	var chain := FINGERS[finger] as Array
	var bone := int(_idx["Bip01 %s %s" % [side, chain[2]]])
	var pose := _sk.get_bone_global_pose(bone)
	var tip_length := 0.022
	return _sk.global_transform * (pose.origin + pose.basis.orthonormalized() * Vector3(tip_length, 0, 0))

## A hand frame (X = fingers, Y = back of hand... see calibration) for a world grip:
## `finger_dir` is where the fingers point, `palm_dir` where the palm faces.
func hand_frame(side: String, finger_dir: Vector3, palm_dir: Vector3, palm_position: Vector3) -> Transform3D:
	var f := finger_dir.normalized()
	var p := (palm_dir - f * palm_dir.dot(f)).normalized()
	# Express the wanted world frame as the hand bone's own global basis: we know the
	# calibrated palm direction and finger axis in the bone frame, so solve the rotation
	# that maps (bone finger axis, bone palm axis) onto (f, p).
	var palm_local := _palm_local[side] as Vector3
	var finger_local := Vector3.RIGHT
	var basis_local := _frame_from(finger_local, palm_local)
	var basis_world := _frame_from(f, p)
	var hand_basis := basis_world * basis_local.inverse()
	var palm_offset := hand_basis * Vector3(0.045, 0, 0)
	return Transform3D(hand_basis, palm_position - palm_offset)

static func _frame_from(forward_axis: Vector3, up_axis: Vector3) -> Basis:
	var x := forward_axis.normalized()
	var z := x.cross(up_axis).normalized()
	var y := z.cross(x).normalized()
	return Basis(x, y, z)

# --- the solver ---------------------------------------------------------------------------

var debug_calls := 0

static var profile_usec := 0
static var profile_calls := 0

func _process_modification() -> void:
	var started := Time.get_ticks_usec()
	_process_modification_inner()
	profile_usec += Time.get_ticks_usec() - started
	profile_calls += 1

func _process_modification_inner() -> void:
	debug_calls += 1
	if _sk == null or not _calibrated:
		return
	var to_sk := _sk.global_transform.affine_inverse()
	# Everything below is measured on the pure animation pose, before this layer changes anything.
	_anim_ankles = {}
	_anim_low = {}
	for side in ["L", "R"]:
		_anim_ankles[side] = _sk.get_bone_global_pose(int(_idx["Bip01 %s Foot" % side])).origin
		_anim_low[side] = _lowest_contact(side)
	var any_pin := is_foot_pinned("L") or is_foot_pinned("R")
	_auto_drop = 0.0
	if auto_ground and not any_pin:
		var support := _support_height()
		# The whole body is moved so the lower foot rests exactly on the pavement: a floating
		# gait is lowered by its gap, a sinking toe is raised by its depth. Both come from the
		# calibrated heel/ball/toe-tip contact points, so the correction is smooth, not noise.
		_auto_drop = clampf(support, -0.6, 0.7)
	_apply_pelvis(to_sk)
	_apply_spine()
	# The clavicles are children of the neck in this skeleton, so the head turns first and the
	# arms are solved on the final shoulder position.
	_apply_head(to_sk)
	_apply_legs(to_sk)
	_apply_arms(to_sk)
	_apply_fingers()
	_record_final()

var _anim_ankles := {}
var _anim_low := {}

## Height (world y) of the lowest sole contact point (heel, ball, toe tip of both feet) above the ground.
func _support_height() -> float:
	var lowest := INF
	for side in ["L", "R"]:
		lowest = minf(lowest, _lowest_contact(side))
	return lowest - ground_y

func _lowest_contact(side: String) -> float:
	var contact := _contact_local[side] as Dictionary
	var fp := _sk.get_bone_global_pose(int(_idx["Bip01 %s Foot" % side]))
	var tp := _sk.get_bone_global_pose(int(_idx["Bip01 %s Toe0" % side]))
	var to_world := _sk.global_transform
	var heel := (to_world * (fp * (contact.heel as Vector3))).y
	var ball := (to_world * (tp * (contact.ball as Vector3))).y
	var tip := (to_world * (tp * (contact.tip as Vector3))).y
	return minf(heel, minf(ball, tip))

func _apply_pelvis(to_sk: Transform3D) -> void:
	var drop := pelvis_drop + _auto_drop
	if drop == 0.0 and pelvis_shift == Vector3.ZERO:
		return
	var root := int(_idx["Bip01"])
	var delta_world := Vector3(pelvis_shift.x, -drop, pelvis_shift.z)
	var delta_sk := to_sk.basis * delta_world
	var pose := _sk.get_bone_global_pose(root)
	_set_global_pose(root, Transform3D(pose.basis, pose.origin + delta_sk))

func _apply_spine() -> void:
	if spine_bend == 0.0 and spine_twist == 0.0:
		return
	# Spread the fold over the three spine joints so the back curves instead of kinking.
	var weights := {"Bip01 Spine": 0.35, "Bip01 Spine1": 0.35, "Bip01 Spine2": 0.30}
	for bone_name in weights:
		var bone := int(_idx[bone_name])
		var share := float(weights[bone_name])
		var pose := _sk.get_bone_global_pose(bone)
		var bend := Basis(_right, -spine_bend * share)
		var twist := Basis(_up, spine_twist * share)
		_set_global_pose(bone, Transform3D(twist * bend * pose.basis, pose.origin))

func _apply_legs(to_sk: Transform3D) -> void:
	var moved := pelvis_drop != 0.0 or pelvis_shift != Vector3.ZERO
	for side in ["L", "R"]:
		var thigh := int(_idx["Bip01 %s Thigh" % side])
		var calf := int(_idx["Bip01 %s Calf" % side])
		var foot := int(_idx["Bip01 %s Foot" % side])
		var weight := float(_foot_weight[side]) if is_foot_pinned(side) else 0.0
		var anim_world: Vector3 = _sk.global_transform * (_anim_ankles[side] as Vector3)
		var target_world := anim_world
		var solve := moved
		if weight > 0.0:
			var sole := (_feet[side] as Dictionary).sole as Vector3
			target_world = anim_world.lerp(sole + Vector3.UP * _ankle_height, weight)
			solve = true
		if not solve:
			continue
		_two_bone(thigh, calf, foot, to_sk * target_world, _knee_pole(thigh, calf, foot), 1.0)
		if weight > 0.0:
			_flatten_foot(side, foot, true)

## Knee pole: keep the animation's own bend direction when the leg is visibly bent, otherwise forward.
func _knee_pole(thigh: int, calf: int, foot: int) -> Vector3:
	var pa := _sk.get_bone_global_pose(thigh).origin
	var pb := _sk.get_bone_global_pose(calf).origin
	var pc := _sk.get_bone_global_pose(foot).origin
	var axis := (pc - pa).normalized()
	var bend := (pb - pa) - axis * (pb - pa).dot(axis)
	if bend.length() > 0.015:
		return (bend.normalized() + _fwd * 0.6).normalized()
	return _fwd * leg_pole_forward

## Keep the sole level with the ground (rotate the foot so its calibrated sole normal points up),
## preserving the yaw the animation gave it.
func _flatten_foot(side: String, foot: int, pinned: bool) -> void:
	if not _sole_normal_local.has(side):
		return
	var pose := _sk.get_bone_global_pose(foot)
	var basis := pose.basis.orthonormalized()
	var normal_now: Vector3 = basis * (_sole_normal_local[side] as Vector3)
	var level := _arc(normal_now, _up)
	var full := Basis(level) * basis
	var amount := 1.0 if pinned else 0.6
	_set_global_pose(foot, Transform3D(basis.slerp(full, amount), pose.origin))

func _apply_arms(to_sk: Transform3D) -> void:
	for side in ["L", "R"]:
		var weight := float(_hand_weight[side])
		var spec := _hands[side] as Dictionary
		if weight <= 0.001 or spec.is_empty():
			continue
		var upper := int(_idx["Bip01 %s UpperArm" % side])
		var fore := int(_idx["Bip01 %s Forearm" % side])
		var hand := int(_idx["Bip01 %s Hand" % side])
		var frame_world := spec.frame as Transform3D
		var frame_sk := to_sk * frame_world
		# Elbow toward the floor and slightly out for work at chest height; out and back for raised hands,
		# so the elbow never swings up over the shoulder.
		var shoulder_y: float = _sk.get_bone_global_pose(upper).origin.y
		var raised := clampf((frame_sk.origin.y - (shoulder_y - 0.30)) / 0.30, 0.0, 1.0)
		var outward := _right * (1.0 if side == "R" else -1.0)
		var low_pole := outward * 0.6 - _up * 0.8 - _fwd * 0.2
		var high_pole := outward * 0.7 - _fwd * 0.2 - _up * 0.7
		var elbow_pole := low_pole.lerp(high_pole, raised).normalized()
		_two_bone(upper, fore, hand, frame_sk.origin, elbow_pole, weight)
		if OS.get_environment("IK_DEBUG") != "" and side == "R" and Engine.get_process_frames() % 20 == 0:
			var wrist_now := _sk.get_bone_global_pose(hand).origin
			var sh := _sk.get_bone_global_pose(upper).origin
			print("   ikdbg wrist_target=%s wrist_got=%s shoulder=%s need=%.3f lens=%.3f/%.3f w=%.2f" % [str(frame_sk.origin.snapped(Vector3(.01,.01,.01))), str(wrist_now.snapped(Vector3(.01,.01,.01))), str(sh.snapped(Vector3(.01,.01,.01))), sh.distance_to(frame_sk.origin), sh.distance_to(_sk.get_bone_global_pose(fore).origin), _sk.get_bone_global_pose(fore).origin.distance_to(wrist_now), weight])
		# Orient the hand onto the grip frame.
		var hand_pose := _sk.get_bone_global_pose(hand)
		var target_basis := frame_sk.basis.orthonormalized()
		var blended := hand_pose.basis.orthonormalized().slerp(target_basis, weight)
		_set_global_pose(hand, Transform3D(blended, hand_pose.origin))
		if OS.get_environment("IK_DEBUG") != "" and side == "R" and Engine.get_process_frames() % 20 == 0:
			var palm_got: Vector3 = _sk.global_transform * (hand_pose.origin + blended * Vector3(0.045, 0, 0))
			var palm_want: Vector3 = frame_world.origin + frame_world.basis * Vector3(0.045, 0, 0)
			print("   palmdbg got=%s want=%s basis_dot=%.3f w=%.2f" % [str(palm_got.snapped(Vector3(.01,.01,.01))), str(palm_want.snapped(Vector3(.01,.01,.01))), (blended * Vector3.RIGHT).dot(target_basis * Vector3.RIGHT), weight])

func _apply_head(to_sk: Transform3D) -> void:
	if head_weight <= 0.001:
		return
	var neck := int(_idx["Bip01 Neck"])
	var head := int(_idx["Bip01 Head"])
	var target_sk := to_sk * head_target_world
	var head_pose := _sk.get_bone_global_pose(head)
	var eye := head_pose.origin + _up * 0.07 + (head_pose.basis.orthonormalized() * _head_forward_local) * 0.05
	var want := (target_sk - eye).normalized()
	# Where the head faces now (the clip, the spine bend and the shoulders' twist already included).
	var current := head_pose.basis.orthonormalized() * _head_forward_local
	var delta := _arc(current, want)
	var angle := delta.get_angle()
	var limit := deg_to_rad(28.0)
	if angle > limit:
		delta = Quaternion(delta.get_axis(), limit)
	var amount := head_weight
	var neck_rotation := Quaternion.IDENTITY.slerp(delta, 0.4 * amount)
	var neck_pose := _sk.get_bone_global_pose(neck)
	_set_global_pose(neck, Transform3D(Basis(neck_rotation) * neck_pose.basis, neck_pose.origin))
	# The head takes whatever is still needed, never more than the cap in total.
	var head_now := _sk.get_bone_global_pose(head)
	var now_forward := head_now.basis.orthonormalized() * _head_forward_local
	var remaining := _arc(now_forward, want)
	var remaining_angle := minf(remaining.get_angle(), maxf(limit * amount - neck_rotation.get_angle(), 0.0) + 0.0)
	if remaining.get_angle() > 0.0001:
		remaining = Quaternion(remaining.get_axis(), minf(remaining.get_angle(), maxf(remaining_angle, 0.0)))
	_set_global_pose(head, Transform3D(Basis(remaining) * head_now.basis, head_now.origin))

func _apply_fingers() -> void:
	for side in ["L", "R"]:
		if float(_hand_weight[side]) <= 0.001:
			continue
		var pose_name := str(_grip_pose[side])
		var pose := (GRIP_POSES.get(pose_name, GRIP_POSES["relaxed"])) as Dictionary
		var amount := float(_grip_amount[side]) * float(_grip_blend[side])
		for finger_name in FINGERS:
			var chain := FINGERS[finger_name] as Array
			var angles := pose[finger_name] as Array
			for joint_index in 3:
				var bone_name := "Bip01 %s %s" % [side, chain[joint_index]]
				var bone := int(_idx.get(bone_name, -1))
				if bone < 0 or not _curl_axis.has(bone_name):
					continue
				var theta := deg_to_rad(float(angles[joint_index])) * amount
				if theta == 0.0:
					continue
				_sk.set_bone_pose_rotation(bone, _sk.get_bone_pose_rotation(bone) * Quaternion(_curl_axis[bone_name] as Vector3, theta))

## Final solved positions (world), read by tests: Skeleton3D.get_bone_global_pose() from
## outside the modifier returns the pre-modifier pose, so metrics are captured here.
var final_state := {}
var record_pose_audit := false      # tests only: also record the full limb set the pose audit needs
var pose := {}
var rest_lengths := {}

func _record_final() -> void:
	var to_world := _sk.global_transform
	var out := {}
	for side in ["L", "R"]:
		var foot := _sk.get_bone_global_pose(int(_idx["Bip01 %s Foot" % side]))
		var toe := _sk.get_bone_global_pose(int(_idx["Bip01 %s Toe0" % side]))
		out["ankle_" + side] = to_world * foot.origin
		out["sole_" + side] = to_world * (foot.origin - _up * _ankle_height)
		out["toe_" + side] = to_world * toe.origin
		var hand := _sk.get_bone_global_pose(int(_idx["Bip01 %s Hand" % side]))
		out["hand_" + side] = to_world * hand.origin
		out["palm_" + side] = to_world * (hand.origin + hand.basis.orthonormalized() * Vector3(0.045, 0, 0))
		out["shoulder_" + side] = to_world * _sk.get_bone_global_pose(int(_idx["Bip01 %s UpperArm" % side])).origin
		out["tip_index_" + side] = to_world * (_sk.get_bone_global_pose(int(_idx["Bip01 %s Finger12" % side])).origin)
	out["support"] = _support_height()
	out["neck"] = to_world * _sk.get_bone_global_pose(int(_idx["Bip01 Neck"])).origin
	out["pelvis"] = to_world * _sk.get_bone_global_pose(int(_idx["Bip01 Pelvis"])).origin
	out["head"] = to_world * _sk.get_bone_global_pose(int(_idx["Bip01 Head"])).origin
	final_state = out
	if record_pose_audit:
		_record_pose()

# --- IK math --------------------------------------------------------------------------------

func _two_bone(root_bone: int, mid_bone: int, end_bone: int, target: Vector3, pole: Vector3, weight: float) -> void:
	var a := _sk.get_bone_global_pose(root_bone)
	var b := _sk.get_bone_global_pose(mid_bone)
	var c := _sk.get_bone_global_pose(end_bone)
	var pa := a.origin
	var pb := b.origin
	var pc := c.origin
	var l1 := pa.distance_to(pb)
	var l2 := pb.distance_to(pc)
	var to_target := target - pa
	var d := clampf(to_target.length(), 0.02, (l1 + l2) * 0.999)
	var dir := to_target.normalized()
	var x := (d * d + l1 * l1 - l2 * l2) / (2.0 * d)
	var h := sqrt(maxf(l1 * l1 - x * x, 0.0))
	var perp := pole - dir * pole.dot(dir)
	if perp.length() < 0.001:
		perp = (pb - pa) - dir * (pb - pa).dot(dir)
	perp = perp.normalized()
	var new_b := pa + dir * x + perp * h
	var new_c := pa + dir * d
	# Rotate the upper bone so its child lands on new_b, then the lower so the end lands on the target.
	var q1 := _arc((pb - pa).normalized(), (new_b - pa).normalized())
	var a_basis := Basis(q1) * a.basis
	var q2 := _arc(Basis(q1) * (pc - pb).normalized(), (new_c - new_b).normalized())
	var b_basis := Basis(q2) * Basis(q1) * b.basis
	if weight < 0.999:
		a_basis = a.basis.orthonormalized().slerp(a_basis.orthonormalized(), weight)
		b_basis = b.basis.orthonormalized().slerp(b_basis.orthonormalized(), weight)
	_set_global_pose(root_bone, Transform3D(a_basis, pa))
	var b_now := _sk.get_bone_global_pose(mid_bone)
	_set_global_pose(mid_bone, Transform3D(b_basis, b_now.origin))
	var end_now := _sk.get_bone_global_pose(end_bone)
	# Keep the end bone's world orientation: only its parent moved.
	_set_global_pose(end_bone, Transform3D(c.basis, end_now.origin))

static func _arc(from: Vector3, to: Vector3) -> Quaternion:
	var f := from.normalized()
	var t := to.normalized()
	var dot := clampf(f.dot(t), -1.0, 1.0)
	if dot > 0.99999:
		return Quaternion.IDENTITY
	if dot < -0.99999:
		var axis := f.cross(Vector3.UP)
		if axis.length() < 0.001:
			axis = f.cross(Vector3.RIGHT)
		return Quaternion(axis.normalized(), PI)
	return Quaternion(f.cross(t).normalized(), acos(dot))

## Write a bone's pose from a skeleton-space (global) transform, converting to the
## local pose the Skeleton3D stores. Position is only written for the root bone.
func _set_global_pose(bone: int, global: Transform3D) -> void:
	var parent := _sk.get_bone_parent(bone)
	var local := global
	if parent >= 0:
		local = _sk.get_bone_global_pose(parent).affine_inverse() * global
	var rotation := local.basis.orthonormalized().get_rotation_quaternion()
	_sk.set_bone_pose_rotation(bone, rotation)
	if parent < 0:
		_sk.set_bone_pose_position(bone, local.origin)


func _record_pose() -> void:
	var w := _sk.global_transform
	var p := {}
	for name in ["Bip01 Pelvis", "Bip01 Spine", "Bip01 Spine2", "Bip01 Neck", "Bip01 Head",
			"Bip01 L Clavicle", "Bip01 R Clavicle", "Bip01 L UpperArm", "Bip01 R UpperArm", "Bip01 L Forearm", "Bip01 R Forearm", "Bip01 L Hand", "Bip01 R Hand",
			"Bip01 L Thigh", "Bip01 R Thigh", "Bip01 L Calf", "Bip01 R Calf", "Bip01 L Foot", "Bip01 R Foot", "Bip01 L Toe0", "Bip01 R Toe0"]:
		var bone := int(_idx.get(name, -1))
		if bone < 0:
			bone = _sk.find_bone(name)
			_idx[name] = bone
		var pose_bone := _sk.get_bone_global_pose(bone)
		p[name] = w * pose_bone.origin
		p[name + ":basis"] = w.basis * pose_bone.basis.orthonormalized()
	p["head_forward"] = w.basis * ((_sk.get_bone_global_pose(int(_idx["Bip01 Head"])).basis.orthonormalized()) * _head_forward_local)
	pose = p
	if rest_lengths.is_empty():
		rest_lengths = {
			"L_upper": (p["Bip01 L UpperArm"] as Vector3).distance_to(p["Bip01 L Forearm"] as Vector3), "L_fore": (p["Bip01 L Forearm"] as Vector3).distance_to(p["Bip01 L Hand"] as Vector3),
			"R_upper": (p["Bip01 R UpperArm"] as Vector3).distance_to(p["Bip01 R Forearm"] as Vector3), "R_fore": (p["Bip01 R Forearm"] as Vector3).distance_to(p["Bip01 R Hand"] as Vector3),
			"L_thigh": (p["Bip01 L Thigh"] as Vector3).distance_to(p["Bip01 L Calf"] as Vector3), "L_shin": (p["Bip01 L Calf"] as Vector3).distance_to(p["Bip01 L Foot"] as Vector3),
			"R_thigh": (p["Bip01 R Thigh"] as Vector3).distance_to(p["Bip01 R Calf"] as Vector3), "R_shin": (p["Bip01 R Calf"] as Vector3).distance_to(p["Bip01 R Foot"] as Vector3)}
