extends RefCounted

## Foot-locked walking: an in-place walk clip has a natural ground speed (how
## far a planted foot travels back under the body per second). Playing the
## clip at moving_speed / natural_speed keeps every planted foot still on the
## pavement instead of skating. The natural speed is measured once per clip
## and skeleton from the clip itself.

# Toes: the ankle sits lowest at heel strike while still swinging forward,
# whereas the toe stays down for exactly the planted phase.
const FOOT_BONES := ["Bip01 L Toe0", "Bip01 R Toe0"]
const SAMPLES := 48
const MIN_SCALE := 0.55
const MAX_SCALE := 1.75

static var _cache: Dictionary = {}


static func natural_speed(character: Node3D, player: AnimationPlayer, skeleton: Skeleton3D, clip_name: String, fallback := 1.1) -> float:
	## character: the root whose +Z is the walking direction.
	var animation := player.get_animation(clip_name) if player != null and player.has_animation(clip_name) else null
	if animation == null or skeleton == null:
		return fallback
	var key := "%d|%d" % [animation.get_instance_id(), skeleton.get_bone_count()]
	if _cache.has(key):
		return float(_cache[key])
	var speed := _measure(character, player, skeleton, clip_name, animation)
	_cache[key] = speed if speed > 0.2 else fallback
	return float(_cache[key])


static func playback_scale(moving_speed: float, natural: float) -> float:
	return clampf(moving_speed / maxf(natural, 0.1), MIN_SCALE, MAX_SCALE)


static func _measure(character: Node3D, player: AnimationPlayer, skeleton: Skeleton3D, clip_name: String, animation: Animation) -> float:
	## Mean backward speed of each foot relative to the body while it is
	## planted (toe within 1.5 cm of its lowest height): exactly the ground
	## speed at which the planted foot stands still.
	var previous_clip := player.current_animation
	var previous_position := player.current_animation_position if not previous_clip.is_empty() else 0.0
	var previous_speed := player.speed_scale
	var inverse := character.global_transform.affine_inverse()
	var step := animation.length / float(SAMPLES)
	var speed_sum := 0.0
	var count := 0
	player.play(clip_name)
	for bone_name in FOOT_BONES:
		var bone := skeleton.find_bone(bone_name)
		if bone < 0:
			return 0.0
		var points: Array[Vector3] = []
		var lowest := INF
		for k in SAMPLES + 1:
			player.seek(step * float(k), true)
			var point := inverse * (skeleton.global_transform * skeleton.get_bone_global_pose(bone).origin)
			points.append(point)
			lowest = minf(lowest, point.y)
		for k in SAMPLES:
			if points[k].y < lowest + 0.015 and points[k + 1].y < lowest + 0.015:
				speed_sum += (points[k].z - points[k + 1].z) / step
				count += 1
	if not previous_clip.is_empty():
		player.play(previous_clip)
		player.seek(previous_position, true)
	player.speed_scale = previous_speed
	return speed_sum / float(maxi(count, 1))
