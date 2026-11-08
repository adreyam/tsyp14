extends Node

## One embodied responder (police officer, paramedic, firefighter, tow or
## utility worker). Uses the same production contract as citizens:
## Microsoft Rocketbox avatar + shared Bip01 mocap library (no root motion,
## no runtime IK), animation evaluated first, root-only foot lock second,
## cameras last. Navigation only ever follows paths produced by the scene's
## planner, and every step is re-checked against a dynamic blocker so a
## responder yields instead of walking through a car, a citizen or a colleague.

signal arrived(agent: Node)
signal action_finished(agent: Node, clip: String)
signal path_blocked(agent: Node)
signal transit_finished(agent: Node)

const RocketboxAnimationLibrary := preload("res://scripts/rocketbox_animation_library.gd")
const ResponderOutfits := preload("res://scripts/incidents/responder_outfits.gd")
const WalkStride := preload("res://scripts/walk_stride.gd")
const GROUND_Y := 0.17
const FOOT_BONES := ["Bip01 L Foot", "Bip01 R Foot", "Bip01 L Toe0", "Bip01 R Toe0"]
const GRIP_FINGERS := ["Bip01 R Finger11", "Bip01 R Finger21", "Bip01 R Finger31", "Bip01 R Finger41"]
const ANIMATION_PRIORITY := -100
const GROUNDING_PRIORITY := -50
const BLOCK_REPLAN_SECONDS := 2.2
const BODY_RADIUS := 0.32

var body: Node3D
var player: AnimationPlayer
var skeleton: Skeleton3D
var role := ""
var agent_id := ""
var speed := 1.45
## Callable(agent, point) -> bool: true when a dynamic obstacle occupies point.
var dynamic_blocker: Callable
## Callable(from: Vector3, goal: Vector3) -> PackedVector3Array used to re-plan
## around whatever blocked the current path.
var planner: Callable
var _goal: Variant = null
var _replans := 0
var _foot_indices := PackedInt32Array()
var _foot_reference_y := 0.0
var _path := PackedVector3Array()
var _path_index := 0
var _yaw := 0.0
var _action_clip := ""
var _action_until := -1.0
var _action_loop := false
var _elapsed := 0.0
var _blocked_seconds := 0.0
var _block_point := Vector3.ZERO
var _face_point: Variant = null
var _hand_items: Dictionary = {}
var _grip_attachment: BoneAttachment3D
var _grip_transform := Transform3D.IDENTITY
var _ready_for_motion := false
# Scripted step between a vehicle seat and the kerb (see transit()).
var _transit: Dictionary = {}
# Root drop below standing height (a seated occupant), eased by transit().
var _sink := 0.0
# Inside a vehicle (between alighting/boarding transits): not drawn.
var _seated := false
# Ground speed of this avatar's walk clip (feet stay planted at any pace).
var _walk_natural := 1.3
## Clip used to walk ("Walk_Carry_Loop" while wheeling a container) and
## whether the agent walks backwards (pulling something it faces).
var walk_clip := "Walking"
var walk_backwards := false
var _walk_naturals: Dictionary = {}


func setup(container: Node3D, model_path: String, role_name: String, identifier: String, position_value: Vector3, yaw_value: float) -> bool:
	role = role_name
	agent_id = identifier
	name = "Responder_%s" % identifier.replace("-", "_").replace("#", "_")
	var packed := load(model_path) as PackedScene
	if packed == null:
		push_error("Responder model missing: %s" % model_path)
		return false
	body = packed.instantiate() as Node3D
	body.name = "ResponderBody_%s" % identifier.replace("-", "_").replace("#", "_")
	container.add_child(body)
	body.global_position = Vector3(position_value.x, GROUND_Y, position_value.z)
	_yaw = yaw_value
	body.rotation.y = _yaw
	body.add_to_group("incident_responders")
	body.set_meta("model_family", "rocketbox_realistic")
	body.set_meta("npc_visual_height", 1.82)
	body.set_meta("npc_surface_y", GROUND_Y)
	body.set_meta("responder_role", role)
	body.set_meta("responder_id", identifier)
	player = body.find_child("AnimationPlayer", true, false) as AnimationPlayer
	skeleton = body.find_child("Skeleton3D", true, false) as Skeleton3D
	var library := RocketboxAnimationLibrary.full_library_for(body) if player != null and skeleton != null else null
	if library == null:
		push_error("Responder %s could not build the Rocketbox library" % identifier)
		return false
	player.add_animation_library("rocketbox", library)
	_walk_natural = WalkStride.natural_speed(body, player, skeleton, "rocketbox/Walking")
	player.process_priority = ANIMATION_PRIORITY
	process_priority = GROUNDING_PRIORITY
	for bone_name in FOOT_BONES:
		var index := skeleton.find_bone(str(bone_name))
		if index >= 0:
			_foot_indices.append(index)
	# Calibrate at the neutral stance, then fix gear and the hand cradle to the
	# skeleton at that exact evaluated pose.
	player.play("rocketbox/Idle_Neutral")
	player.seek(player.get_animation("rocketbox/Idle_Neutral").length * 0.5, true)
	player.advance(0.0)
	_foot_reference_y = _lowest_foot_local_y()
	ResponderOutfits.apply(body, skeleton, role)
	_prepare_grip()
	_play("Idle_Neutral", true)
	_ready_for_motion = true
	return true


func _prepare_grip() -> void:
	var hand_index := skeleton.find_bone("Bip01 R Hand")
	if hand_index < 0:
		return
	var cradle := Vector3.ZERO
	var count := 0
	for finger in GRIP_FINGERS:
		var index := skeleton.find_bone(str(finger))
		if index >= 0:
			cradle += (skeleton.global_transform * skeleton.get_bone_global_pose(index)).origin
			count += 1
	if count == 0:
		return
	cradle /= float(count)
	_grip_attachment = BoneAttachment3D.new()
	_grip_attachment.name = "ResponderGrip"
	_grip_attachment.bone_name = "Bip01 R Hand"
	skeleton.add_child(_grip_attachment)
	_grip_attachment.global_transform = skeleton.global_transform * skeleton.get_bone_global_pose(hand_index)
	_grip_transform = _grip_attachment.global_transform.affine_inverse() * Transform3D(Basis(Vector3.UP, _yaw), cradle)


func hold_item(kind: String, item: Node3D) -> void:
	## Attaches a prop at the finger cradle; its own origin should be its grip.
	if _grip_attachment == null:
		item.queue_free()
		return
	release_item(kind)
	var holder := Node3D.new()
	holder.name = "Held_%s" % kind
	holder.transform = _grip_transform
	_grip_attachment.add_child(holder)
	holder.add_child(item)
	_hand_items[kind] = holder


func release_item(kind: String) -> void:
	if _hand_items.has(kind):
		var holder := _hand_items[kind] as Node3D
		if is_instance_valid(holder):
			holder.queue_free()
		_hand_items.erase(kind)


func get_hand_world_transform() -> Transform3D:
	if _grip_attachment == null:
		return body.global_transform
	return _grip_attachment.global_transform * _grip_transform


# --- Commands ---------------------------------------------------------------

func go_to(goal: Vector3) -> bool:
	## Plans and walks to goal; re-plans automatically when blocked.
	_goal = goal
	_replans = 0
	var path: PackedVector3Array = planner.call(position(), goal) if planner.is_valid() else PackedVector3Array([position(), goal])
	if path.is_empty():
		return false
	walk_path(path, true)
	return true


func walk_path(points: PackedVector3Array, keep_goal := false) -> void:
	if not keep_goal:
		_goal = null
	_path = points
	_path_index = 1 if points.size() > 1 else 0
	_face_point = null
	_action_clip = ""
	_blocked_seconds = 0.0


func perform(clip: String, seconds := -1.0, face: Variant = null) -> void:
	## seconds < 0 loops until the next command.
	_path = PackedVector3Array()
	_path_index = 0
	_face_point = face
	_action_clip = clip
	_action_loop = seconds < 0.0
	_action_until = _elapsed + seconds if seconds >= 0.0 else -1.0
	_play(clip, _action_loop)


func stand(face: Variant = null) -> void:
	perform("Idle_Neutral", -1.0, face)


func transit(goal: Vector3, seconds: float, sink_to: float, face_yaw: Variant = null) -> void:
	## Moves straight to goal over seconds: climbing out of / into a seat
	## through an open door (sink_to > 0 ends seated inside). The person
	## ducks through the opening (feet stay on the ground) and is hidden
	## once inside the body, never sunk through the floor. The path is the
	## door opening itself, so the vehicle's own body is not an obstacle
	## here; everything else stays with the planner.
	_path = PackedVector3Array()
	_path_index = 0
	_action_clip = ""
	_transit = {"from": body.global_position, "to": goal, "t": 0.0, "seconds": maxf(seconds, 0.05), "into": sink_to > 0.0, "out_of": _seated, "face": face_yaw}


func set_seated_drop(drop: float) -> void:
	## drop > 0: seated inside a vehicle (hidden behind its body and glass).
	_seated = drop > 0.0
	_sink = 0.0
	if is_instance_valid(body):
		body.visible = not _seated


func is_transiting() -> bool:
	return not _transit.is_empty()


func is_moving() -> bool:
	return _path_index < _path.size() and not _path.is_empty()


func position() -> Vector3:
	return body.global_position if is_instance_valid(body) else Vector3.ZERO


func place(position_value: Vector3, yaw_value: float) -> void:
	body.global_position = Vector3(position_value.x, body.global_position.y, position_value.z)
	_yaw = yaw_value
	body.rotation.y = _yaw


func dispose() -> void:
	if is_instance_valid(body):
		body.queue_free()
	queue_free()


# --- Simulation ---------------------------------------------------------------

func _physics_process(delta: float) -> void:
	if not _ready_for_motion or not is_instance_valid(body):
		return
	_elapsed += delta
	if not _transit.is_empty():
		_step_transit(delta)
		return
	if is_moving():
		_step_along_path(delta)
		return
	if _face_point is Vector3:
		var to_face := (_face_point as Vector3) - body.global_position
		to_face.y = 0.0
		if to_face.length() > 0.05:
			_yaw = lerp_angle(_yaw, atan2(to_face.x, to_face.z), minf(delta * 5.0, 1.0))
			body.rotation.y = _yaw
	if not _action_clip.is_empty() and _action_until >= 0.0 and _elapsed >= _action_until:
		var finished := _action_clip
		_action_clip = ""
		_action_until = -1.0
		_play("Idle_Neutral", true)
		action_finished.emit(self, finished)


func _step_along_path(delta: float) -> void:
	var target := _path[_path_index]
	var offset := target - body.global_position
	offset.y = 0.0
	if offset.length() <= 0.12:
		_path_index += 1
		if _path_index >= _path.size():
			_path = PackedVector3Array()
			_path_index = 0
			_play("Idle_Neutral", true)
			arrived.emit(self)
		return
	var direction := offset.normalized()
	var step := minf(speed * delta, offset.length())
	var candidate := body.global_position + direction * step
	if dynamic_blocker.is_valid() and bool(dynamic_blocker.call(self, candidate)):
		_blocked_seconds += delta
		_block_point = body.global_position + direction * 0.8
		_play("Idle_Neutral", true)
		if _blocked_seconds >= BLOCK_REPLAN_SECONDS:
			_blocked_seconds = 0.0
			_replan_around_block()
		return
	_blocked_seconds = 0.0
	body.global_position = Vector3(candidate.x, body.global_position.y, candidate.z)
	var heading := atan2(direction.x, direction.z) + (PI if walk_backwards else 0.0)
	_yaw = lerp_angle(_yaw, heading, minf(delta * 7.0, 1.0))
	body.rotation.y = _yaw
	_play(walk_clip, true)
	# Backwards: the same stride played in reverse keeps planted feet still.
	player.speed_scale = WalkStride.playback_scale(speed, _natural_for(walk_clip)) * (-1.0 if walk_backwards else 1.0)


func _natural_for(clip: String) -> float:
	if clip == "Walking":
		return _walk_natural
	if not _walk_naturals.has(clip):
		_walk_naturals[clip] = WalkStride.natural_speed(body, player, skeleton, "rocketbox/%s" % clip, _walk_natural)
	return float(_walk_naturals[clip])


func _step_transit(delta: float) -> void:
	_transit.t = float(_transit.t) + delta / float(_transit.seconds)
	var t := minf(float(_transit.t), 1.0)
	var eased := t * t * (3.0 - 2.0 * t)
	var from := _transit.from as Vector3
	var to := _transit.to as Vector3
	var point := from.lerp(to, eased)
	body.global_position = Vector3(point.x, body.global_position.y, point.z)
	var heading: float = float(_transit.face) if _transit.face != null else atan2(to.x - from.x, to.z - from.z)
	_yaw = lerp_angle(_yaw, heading, minf(delta * 9.0, 1.0))
	body.rotation.y = _yaw
	# Ducking under the roof line while passing through the door opening;
	# otherwise a short, deliberate step at the pace of the move itself.
	var ducking := (bool(_transit.into) and t >= 0.4) or (bool(_transit.out_of) and t < 0.45)
	if bool(_transit.out_of) and _seated:
		_seated = false
		body.visible = true
	if ducking:
		_play("CrouchWork", true)
	else:
		_play("Walking" if from.distance_to(to) > 0.25 else "Idle_Neutral", true)
		player.speed_scale = WalkStride.playback_scale(from.distance_to(to) / float(_transit.seconds), _walk_natural)
	if bool(_transit.into) and t >= 0.8 and not _seated:
		# Inside the cab: from here on hidden behind the body and glass.
		_seated = true
		body.visible = false
	if t >= 1.0:
		_transit = {}
		player.speed_scale = 1.0
		_play("Idle_Neutral", true)
		transit_finished.emit(self)


func _replan_around_block() -> void:
	_replans += 1
	if _goal is Vector3 and planner.is_valid():
		# Route around whatever holds the spot just ahead (a citizen standing
		# still never moves out of a path planned through it).
		var path: PackedVector3Array = planner.call(position(), _goal as Vector3, [_block_point])
		if not path.is_empty():
			walk_path(path, true)
			return
	# No alternative found: stay put (never clip through anything) and report.
	path_blocked.emit(self)


func _process(_delta: float) -> void:
	# Runs after the AnimationPlayer (priority -100) evaluated this frame's pose:
	# lock the lowest foot to the surface by moving only the root vertically.
	if not _ready_for_motion or not is_instance_valid(body):
		return
	var live := _lowest_foot_local_y()
	body.global_position.y = GROUND_Y + clampf(_foot_reference_y - live, -0.68, 0.18) - _sink


func _lowest_foot_local_y() -> float:
	var inverse := body.global_transform.affine_inverse()
	var lowest := INF
	for index in _foot_indices:
		lowest = minf(lowest, (inverse * (skeleton.global_transform * skeleton.get_bone_global_pose(index).origin)).y)
	return 0.0 if lowest == INF else lowest


func _play(clip: String, loop: bool) -> void:
	var animation_name := "rocketbox/%s" % clip
	if not player.has_animation(animation_name):
		animation_name = "rocketbox/Idle_Neutral"
	var animation := player.get_animation(animation_name)
	var loop_mode := Animation.LOOP_LINEAR if loop else Animation.LOOP_NONE
	# Writing loop_mode emits changed on the shared clip and makes every player
	# using the library rebuild its caches, so write only on a real change.
	if animation != null and animation.loop_mode != loop_mode:
		animation.loop_mode = loop_mode
	if player.current_animation != animation_name or not player.is_playing():
		player.play(animation_name, 0.18)
	if clip != "Walking" and clip != walk_clip:
		# Only the walk is retimed to ground speed; every other clip plays
		# at its captured pace.
		player.speed_scale = 1.0
