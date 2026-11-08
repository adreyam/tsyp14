extends RefCounted

## Plays baked Rocketbox motion-capture clips (assets/rocketbox/clips_{m,f}.res) on a citizen and applies the
## capture's own root motion (a sit-down's step back onto the seat, a stand-up's step forward, a 90 degree turn)
## to the character node, so the performance lands exactly where the actor's body went.

static var _libraries := {}

var person: Node3D
var player: AnimationPlayer
var current := ""
var _motion := PackedVector3Array()
var _yaw := PackedFloat32Array()
var _start_xf := Transform3D.IDENTITY
var _t0_motion := Vector3.ZERO
var _t0_yaw := 0.0
var _clip_time := 0.0
var _length := 0.0
var _apply_motion := false
var finished := true

static func library_for(person_node: Node3D) -> AnimationLibrary:
	var gender := "f" if person_node.scene_file_path.contains("Female_") else "m"
	if not _libraries.has(gender):
		_libraries[gender] = load("res://assets/rocketbox/clips_%s.res" % gender) as AnimationLibrary
	return _libraries[gender] as AnimationLibrary

func setup(person_node: Node3D, animation_player: AnimationPlayer) -> void:
	person = person_node
	player = animation_player
	if not player.has_animation_library("rb"):
		player.add_animation_library("rb", library_for(person_node))

func has_clip(clip: String) -> bool:
	return player.has_animation("rb/" + clip)

func length_of(clip: String) -> float:
	return player.get_animation("rb/" + clip).length if has_clip(clip) else 0.0

## Start a clip. `move` = apply its extracted root motion to the character node (transitions, turns).
func play(clip: String, blend := 0.2, move := true, speed := 1.0) -> void:
	if not has_clip(clip):
		push_warning("missing mocap clip " + clip)
		return
	var animation := player.get_animation("rb/" + clip)
	current = clip
	_motion = animation.get_meta("root_motion", PackedVector3Array()) as PackedVector3Array
	_yaw = animation.get_meta("root_yaw", PackedFloat32Array()) as PackedFloat32Array
	_apply_motion = move and _motion.size() > 1
	_start_xf = person.global_transform
	_clip_time = 0.0
	_length = animation.length
	finished = false
	player.play("rb/" + clip, blend, speed)

func stop_motion() -> void:
	_apply_motion = false

## Call every frame before the skeleton updates.
func update(delta: float) -> void:
	if finished or current == "":
		return
	_clip_time += delta * player.speed_scale
	var animation := player.get_animation("rb/" + current)
	if animation.loop_mode == Animation.LOOP_NONE and _clip_time >= _length:
		_clip_time = _length
		finished = true
	if _apply_motion:
		var index := clampf(_clip_time * 30.0, 0.0, float(_motion.size() - 1))
		var low := int(floor(index))
		var high := mini(low + 1, _motion.size() - 1)
		var fraction := index - float(low)
		var offset := _motion[low].lerp(_motion[high], fraction) - _motion[0]
		var turn := lerpf(_yaw[low], _yaw[high], fraction) - _yaw[0]
		var basis := _start_xf.basis * Basis(Vector3.UP, turn)
		person.global_transform = Transform3D(basis, _start_xf.origin + _start_xf.basis * Vector3(offset.x, 0.0, offset.z))

func clip_time() -> float:
	return _clip_time
