extends RefCounted

## Objective "is this person mangled?" check on a solved pose (needs CitizenIK.record_pose_audit = true).
## Every rule is a limit of the human body; a frame passes only if none is broken.

const RULES := ["bone_stretch", "elbow_range", "knee_range", "knee_direction", "elbow_over_shoulder", "hand_in_torso", "hand_behind_back", "head_turn", "head_pitch", "spine_bend", "spine_twist", "facing_target", "legs_hanging", "feet_crossed"]

static func _angle(a: Vector3, b: Vector3, c: Vector3) -> float:
	## interior angle at b between a and c, in degrees
	return rad_to_deg((a - b).normalized().angle_to((c - b).normalized()))

static func _flat(v: Vector3) -> Vector3:
	return Vector3(v.x, 0.0, v.z)

static func _segment_distance(point: Vector3, a: Vector3, b: Vector3) -> float:
	var ab := b - a
	var t := clampf((point - a).dot(ab) / maxf(ab.length_squared(), 0.000001), 0.0, 1.0)
	return point.distance_to(a + ab * t)

## `forward` is the character's facing (world), `to_target` where the prop is (world, flat) or ZERO.
static func check(ik: Node, forward: Vector3, to_target: Vector3, allow_hand_to_head := false) -> Array:
	var violations: Array = []
	var p := ik.pose as Dictionary
	if p.is_empty():
		return ["no_pose"]
	var rest := ik.rest_lengths as Dictionary
	var fwd := _flat(forward).normalized()
	var right := fwd.cross(Vector3.UP).normalized()
	# bone lengths never change on a rigid skeleton: anything else means an IK bug
	for side in ["L", "R"]:
		var upper: float = (p["Bip01 %s UpperArm" % side] as Vector3).distance_to(p["Bip01 %s Forearm" % side] as Vector3)
		var fore: float = (p["Bip01 %s Forearm" % side] as Vector3).distance_to(p["Bip01 %s Hand" % side] as Vector3)
		var thigh: float = (p["Bip01 %s Thigh" % side] as Vector3).distance_to(p["Bip01 %s Calf" % side] as Vector3)
		var shin: float = (p["Bip01 %s Calf" % side] as Vector3).distance_to(p["Bip01 %s Foot" % side] as Vector3)
		for pair in [[upper, "%s_upper" % side], [fore, "%s_fore" % side], [thigh, "%s_thigh" % side], [shin, "%s_shin" % side]]:
			if absf(float(pair[0]) - float(rest[pair[1]])) > float(rest[pair[1]]) * 0.03:
				violations.append("bone_stretch")
	for side in ["L", "R"]:
		var shoulder := p["Bip01 %s UpperArm" % side] as Vector3
		var elbow := p["Bip01 %s Forearm" % side] as Vector3
		var wrist := p["Bip01 %s Hand" % side] as Vector3
		var hip := p["Bip01 %s Thigh" % side] as Vector3
		var knee := p["Bip01 %s Calf" % side] as Vector3
		var ankle := p["Bip01 %s Foot" % side] as Vector3
		var elbow_angle := _angle(shoulder, elbow, wrist)
		if elbow_angle < 22.0:
			violations.append("elbow_range")
		var knee_angle := _angle(hip, knee, ankle)
		if knee_angle < 20.0:
			violations.append("knee_range")
		# knees bend forward: the knee sits in front of the hip-ankle line
		var line_mid := (hip + ankle) * 0.5
		if (knee - line_mid).dot(fwd) < -0.03 and knee_angle < 165.0:
			violations.append("knee_direction")
		if elbow.y > shoulder.y + 0.17:
			violations.append("elbow_over_shoulder")
		var pelvis := p["Bip01 Pelvis"] as Vector3
		var neck := p["Bip01 Neck"] as Vector3
		if _segment_distance(wrist, pelvis, neck) < 0.085:
			violations.append("hand_in_torso")
		if (wrist - pelvis).dot(fwd) < -0.34:
			violations.append("hand_behind_back")
		# a leg hanging dead straight while the foot is off the ground is a floating pose
	var pelvis_p := p["Bip01 Pelvis"] as Vector3
	var head := p["Bip01 Head"] as Vector3
	var neck_p := p["Bip01 Neck"] as Vector3
	# head turn / pitch relative to the chest
	var chest_forward := _chest_forward(p, fwd)
	var head_dir := _flat(p["head_forward"] as Vector3).normalized()
	# Yaw is only meaningful while the face is not pointing straight down/up.
	var horizontal_share := _flat(p["head_forward"] as Vector3).length()
	var yaw := rad_to_deg(chest_forward.signed_angle_to(head_dir, Vector3.UP))
	if horizontal_share > 0.45 and absf(yaw) > 80.0:
		violations.append("head_turn")
	var pitch := rad_to_deg(asin(clampf((head - neck_p).normalized().dot(Vector3.UP), -1.0, 1.0)))
	# neck flexion: the head may nod/tilt about 55 degrees off the line of the spine, no more
	var flexion := rad_to_deg((head - neck_p).normalized().angle_to((neck_p - pelvis_p).normalized()))
	# Work at the ground bends the whole back, so the head may follow the eyes further down.
	var spine_lean := rad_to_deg((neck_p - pelvis_p).normalized().angle_to(Vector3.UP))
	if flexion > (58.0 if spine_lean < 35.0 else 80.0):
		violations.append("head_pitch")
	# spine
	var spine_vec := neck_p - pelvis_p
	var bend := rad_to_deg(spine_vec.normalized().angle_to(Vector3.UP))
	var forward_lean := spine_vec.dot(fwd)
	if (forward_lean >= 0.0 and bend > 85.0) or (forward_lean < 0.0 and bend > 28.0):
		violations.append("spine_bend")
	var twist := rad_to_deg(chest_forward.angle_to(fwd))
	if twist > 55.0:
		violations.append("spine_twist")
	if to_target != Vector3.ZERO and rad_to_deg(chest_forward.angle_to(_flat(to_target).normalized())) > 62.0:
		violations.append("facing_target")
	# feet crossed: the left foot must stay on the left of the right foot by a few cm
	var l_ankle := p["Bip01 L Foot"] as Vector3
	var r_ankle := p["Bip01 R Foot"] as Vector3
	if (l_ankle - r_ankle).dot(right) > 0.12:
		violations.append("feet_crossed")
	return violations

static func _chest_forward(p: Dictionary, fallback: Vector3) -> Vector3:
	var shoulders := (p["Bip01 L UpperArm"] as Vector3) - (p["Bip01 R UpperArm"] as Vector3)
	shoulders.y = 0.0
	if shoulders.length() < 0.05:
		return fallback
	# The character's left is on its right-hand side when facing +Z: forward = leftward x up for this rig (+Z facing, left = +X).
	var forward := shoulders.normalized().cross(Vector3.UP)
	return forward.normalized() if forward.dot(fallback) >= 0.0 else -forward.normalized()

