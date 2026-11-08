extends RefCounted

## Runs one citizen through a scene of motion-capture clips (see ActivityLibrary). The actor's performance is
## never altered: the director only starts each clip, lets its extracted root motion carry the body (the step back
## onto a seat, the rise out of it), swaps clips when they finish or their hold time ends, and puts carried
## objects into the hand at the right moment. Feet are kept on the pavement by the IK layer's ground contact only.

const ClipPlayer := preload("res://scripts/characters/clip_player.gd")
const ItemFactory := preload("res://scripts/props/item_factory.gd")
const ActivityLibrary := preload("res://scripts/characters/activity_library.gd")
const BinWeight := preload("res://scripts/props/bin_weight.gd")

const THROW_SECONDS := 0.42

signal finished

var person: Node3D
var skeleton: Skeleton3D
var ik: Node
var clips: RefCounted
var prop: Node3D
var beats: Array = []
var beat_index := -1
var beat_time := 0.0
var running := false
var items: Dictionary = {}
var walk_natural := 1.3
var steps_taken := 0
var report := {"max_hand_error": 0.0, "samples": 0, "max_foot_slip": 0.0, "hand_errors": [], "by_beat": {}, "goal_by_beat": {}}
var ground_y := 0.0

var _sockets := {}
var _flights: Array = []
var _fired := {}
var _beat_length := 1.0

func setup(person_node: Node3D, skeleton_node: Skeleton3D, ik_node: Node) -> void:
	person = person_node
	skeleton = skeleton_node
	ik = ik_node
	var player := person.find_child("AnimationPlayer", true, false) as AnimationPlayer
	clips = ClipPlayer.new()
	clips.setup(person, player)
	for side in ["L", "R"]:
		var attachment := BoneAttachment3D.new()
		attachment.name = "HoldSocket_%s" % side
		attachment.bone_name = ik.bone_name_of(side)
		skeleton.add_child(attachment)
		var frame := Node3D.new()
		frame.name = "HoldFrame"
		frame.transform = ik.hold_frame_local(side)
		attachment.add_child(frame)
		_sockets[side] = frame

func set_seat(_height: float, _back_dir: Vector3, _back_dist: float) -> void:
	pass

func is_running() -> bool:
	return running

func start(beat_list: Array, prop_node: Node3D, ground := 0.0) -> void:
	beats = beat_list
	prop = prop_node
	ground_y = ground
	beat_index = -1
	running = true
	_next_beat()

func stop() -> void:
	running = false
	_land_all_flights()
	clips.stop_motion()
	for kind in items.keys():
		hide_item(str(kind))

func _next_beat() -> void:
	beat_index += 1
	beat_time = 0.0
	_fired.clear()
	if beat_index >= beats.size():
		running = false
		finished.emit()
		return
	var beat := beats[beat_index] as Dictionary
	var clip := str(beat.clip)
	var moves := ActivityLibrary.stats(clip).has("root_end") and (absf(float((ActivityLibrary.stats(clip).get("root_end", [0, 0, 0]) as Array)[2])) > 0.05 or absf(float(ActivityLibrary.stats(clip).get("yaw_end", 0.0))) > 0.05)
	clips.play(clip, 0.35 if beat_index > 0 else 0.2, moves)
	var natural := ActivityLibrary.clip_length(clip)
	var hold := float(beat.get("hold", 0.0))
	_beat_length = hold if hold > 0.0 else natural

func update(delta: float) -> void:
	if not running:
		_update_flights(delta)
	if not running or beat_index < 0 or beat_index >= beats.size():
		return
	_update_flights(delta)
	clips.update(delta)
	beat_time += delta
	var beat := beats[beat_index] as Dictionary
	var fraction := clampf(beat_time / maxf(_beat_length, 0.05), 0.0, 1.0)
	for index in (beat.get("items", []) as Array).size():
		var spec := (beat.items as Array)[index] as Dictionary
		var key := "%d:%d" % [beat_index, index]
		if _fired.has(key) or fraction < float(spec.get("at", 0.0)):
			continue
		_fired[key] = true
		match str(spec.get("do", "")):
			"attach":
				attach_item(str(spec.kind), str(spec.get("hand", "R")))
			"hide":
				hide_item(str(spec.kind))
			"throw":
				_throw_item(str(spec.kind))
	# A held loop that is shorter than its hold time restarts; a one-shot ends with its clip.
	if beat_time >= _beat_length:
		_next_beat()

# --- carried items ----------------------------------------------------------------------------

func ensure_item(kind: String) -> Node3D:
	if items.has(kind) and is_instance_valid(items[kind]):
		return items[kind] as Node3D
	var item := ItemFactory.build(kind)
	if item == null:
		return null
	person.add_child(item)
	item.visible = false
	items[kind] = item
	return item

func attach_item(kind: String, side: String) -> void:
	var item := ensure_item(kind)
	if item == null:
		return
	if item.get_parent() != null:
		item.get_parent().remove_child(item)
	(_sockets[side] as Node3D).add_child(item)
	item.transform = _hold_transform(item, side)
	item.visible = true

func _hold_transform(item: Node3D, side: String) -> Transform3D:
	var axis := (item.get_meta("grip_axis") as Vector3).normalized()
	var grip_point := item.get_meta("grip_point") as Vector3
	var radius := float(item.get_meta("grip_radius"))
	var roll := deg_to_rad(float(item.get_meta("grip_roll")))
	var b := Basis(Quaternion(axis, Vector3.UP))
	b = Basis(Vector3.UP, roll) * b
	if side == "L":
		b = Basis(Vector3.RIGHT, PI) * b
	return Transform3D(b, Vector3(radius, 0.0, 0.0) - b * grip_point)

func hide_item(kind: String) -> void:
	var item := items.get(kind) as Node3D
	if item == null:
		return
	if item.get_parent() != null:
		item.get_parent().remove_child(item)
	person.add_child(item)
	item.visible = false

func held_items() -> Array:
	var kinds: Array = []
	for side in ["L", "R"]:
		var frame := _sockets.get(side) as Node3D
		if frame == null:
			continue
		for child in frame.get_children():
			if child is Node3D and (child as Node3D).visible and (child as Node3D).has_meta("item_kind"):
				kinds.append(str((child as Node3D).get_meta("item_kind")))
	return kinds

# --- throwing waste into a bin --------------------------------------------------------------

## Released from the hand over the bin opening: the item falls in an arc, then the bin's load cell takes its weight.
func _throw_item(kind: String) -> void:
	var item := items.get(kind) as Node3D
	if item == null or not item.visible or not is_instance_valid(prop):
		return
	var start := item.global_position
	var parent := person.get_parent()
	if item.get_parent() != null:
		item.get_parent().remove_child(item)
	parent.add_child(item)
	item.global_position = start
	_flights.append({"item": item, "kind": kind, "from": start, "to": BinWeight.drop_point(prop), "t": 0.0, "bin": prop})

func _update_flights(delta: float) -> void:
	for flight in _flights.duplicate():
		var fl := flight as Dictionary
		fl.t = float(fl.t) + delta
		var u := clampf(float(fl.t) / THROW_SECONDS, 0.0, 1.0)
		var item := fl.item as Node3D
		if is_instance_valid(item):
			var position := (fl.from as Vector3).lerp(fl.to as Vector3, u)
			position.y += sin(u * PI) * 0.22
			item.global_position = position
			item.rotate_x(delta * 9.0)
		if u >= 1.0:
			_land(fl)

func _land(flight: Dictionary) -> void:
	_flights.erase(flight)
	var bin := flight.bin as Node3D
	if is_instance_valid(bin):
		BinWeight.deposit(bin, BinWeight.weight_for(str(flight.kind)), Time.get_ticks_msec() / 1000.0)
	hide_item(str(flight.kind))

func _land_all_flights() -> void:
	for flight in _flights.duplicate():
		_land(flight as Dictionary)
