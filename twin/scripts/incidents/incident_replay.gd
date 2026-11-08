extends Node3D

## Incident recorder and replay theatre.
##
## Recording: a rolling 20 s buffer of every rendered vehicle pose (30 Hz) and
## the signal state of each controller, written every frame after CityLife.
## When an incident's participants are armed, undamaged ghost copies of them
## are cloned on the spot; 8 s after impact the window [−10 s, +8 s] around
## the contact is frozen with ghosts for every vehicle within 70 m.
##
## Playback: ghosts live on render layer 20, which live cameras never draw;
## the replay camera draws the static city plus layer 20 and excludes live
## traffic (2), people (4) and effects (8). The live simulation keeps running.

signal replay_finished

const VehicleDamage := preload("res://scripts/incidents/vehicle_damage.gd")
const IncidentFx := preload("res://scripts/incidents/incident_fx.gd")
const REPLAY_LAYER := 1 << 19
const LIVE_LAYERS := 2 | 4 | 8
const SAMPLE_SECONDS := 1.0 / 30.0
const BUFFER_SECONDS := 20.0
const PRE_IMPACT := 10.0
const POST_IMPACT := 8.0
const CAPTURE_RADIUS := 70.0

var life: Node
var _buffer: Array = []
var _sample_elapsed := 0.0
var _clock := 0.0
var _armed_ghosts: Dictionary = {}
var _pending: Dictionary = {}
var clips: Dictionary = {}
var _playing: Dictionary = {}
var _camera: Camera3D
var _previous_camera: Camera3D
var playback_time := 0.0
var playback_speed := 0.5
var paused := false
var camera_mode := "orbit"


func setup(city_life: Node) -> void:
	life = city_life
	name = "IncidentReplay"
	process_priority = 90
	_camera = Camera3D.new()
	_camera.name = "ReplayCamera"
	_camera.fov = 50.0
	_camera.far = 1600.0
	_camera.cull_mask = (0xFFFFF & ~LIVE_LAYERS) | REPLAY_LAYER
	add_child(_camera)


static func exclude_replay_layer(tree: SceneTree) -> void:
	## Every live camera must never draw replay ghosts.
	for value in tree.root.find_children("*", "Camera3D", true, false):
		var camera := value as Camera3D
		if camera.name != "ReplayCamera":
			camera.cull_mask &= ~REPLAY_LAYER


# --- Recording --------------------------------------------------------------------

func _process(delta: float) -> void:
	_clock += delta
	_sample_elapsed += delta
	if _sample_elapsed >= SAMPLE_SECONDS and life != null and bool(life.get("sumo_connected")):
		_sample_elapsed = 0.0
		_record_sample()
	for incident_id in _pending.keys():
		var pending := _pending[incident_id] as Dictionary
		if _clock >= float(pending.freeze_at):
			_freeze_clip(incident_id, pending)
			_pending.erase(incident_id)
	if not _playing.is_empty():
		_advance_playback(delta)


func _record_sample() -> void:
	var poses: Dictionary = {}
	for key in (life.get("_sumo_nodes_by_id") as Dictionary):
		var render_state := (life.get("_sumo_nodes_by_id") as Dictionary)[key] as Dictionary
		var node := render_state.node as Node3D
		if is_instance_valid(node) and node.visible:
			poses[str(key)] = node.global_transform
	_buffer.append({"t": _clock, "poses": poses, "signals": (life.get("_sumo_signal_approaches") as Dictionary).duplicate(true)})
	while not _buffer.is_empty() and _clock - float(_buffer[0].t) > BUFFER_SECONDS:
		_buffer.pop_front()


func on_event(event: Dictionary) -> void:
	var kind := str(event.get("kind", ""))
	var incident_id := str(event.get("incident", ""))
	if kind == "incident_armed":
		# Clone the participants now, before any damage is applied.
		var ghosts: Dictionary = {}
		for vehicle_id in event.get("participants", []):
			var node := life.call("get_keyed_vehicle", str(vehicle_id)) as Node3D
			if node != null:
				ghosts[str(vehicle_id)] = _make_ghost(node)
		_armed_ghosts[incident_id] = ghosts
	elif kind == "impact":
		var point := event.get("point", [0.0, 0.0]) as Array
		_pending[incident_id] = {
			"impact_clock": _clock, "freeze_at": _clock + POST_IMPACT, "point": Vector3(float(point[0]), 0.0, float(point[1])),
			"vehicles": event.get("vehicles", []), "delta_v": float(event.get("delta_v", 4.0)), "severity": str(event.get("severity", "")),
		}
		# Participants added by first contact (a third party) are cloned now;
		# they were undamaged until this instant.
		var ghosts := _armed_ghosts.get(incident_id, {}) as Dictionary
		for vehicle_id in event.get("vehicles", []):
			if not ghosts.has(str(vehicle_id)):
				var node := life.call("get_keyed_vehicle", str(vehicle_id)) as Node3D
				if node != null:
					ghosts[str(vehicle_id)] = _make_ghost(node)
		_armed_ghosts[incident_id] = ghosts


func _make_ghost(node: Node3D) -> Node3D:
	var ghost := node.duplicate(0) as Node3D
	for child_name in ["VehicleLightRig", "BlackExhaustEmitter"]:
		var child := ghost.get_node_or_null(child_name)
		if child != null:
			child.free()
	VehicleDamage.restore(ghost)
	ghost.visible = false
	for value in ghost.find_children("*", "VisualInstance3D", true, false):
		(value as VisualInstance3D).layers = REPLAY_LAYER
	for group in ghost.get_groups():
		ghost.remove_from_group(group)
	add_child(ghost)
	return ghost


func _freeze_clip(incident_id: String, pending: Dictionary) -> void:
	var impact_clock := float(pending.impact_clock)
	var start := impact_clock - PRE_IMPACT
	var finish := impact_clock + POST_IMPACT
	var point := pending.point as Vector3
	var frames: Array = []
	var near: Dictionary = {}
	for sample in _buffer:
		var t := float(sample.t)
		if t < start or t > finish:
			continue
		frames.append(sample)
		for key in (sample.poses as Dictionary):
			var transform := (sample.poses as Dictionary)[key] as Transform3D
			if Vector2(transform.origin.x - point.x, transform.origin.z - point.z).length() <= CAPTURE_RADIUS:
				near[key] = true
	var ghosts := _armed_ghosts.get(incident_id, {}) as Dictionary
	for key in near:
		if not ghosts.has(key):
			var node := life.call("get_keyed_vehicle", str(key)) as Node3D
			if node != null:
				var ghost := _make_ghost(node)
				ghosts[key] = ghost
	clips[incident_id] = {
		"frames": frames, "ghosts": ghosts, "start": start, "impact": impact_clock, "end": finish,
		"point": point, "participants": pending.vehicles, "delta_v": pending.delta_v, "severity": pending.severity,
	}
	_armed_ghosts.erase(incident_id)


func clip_stats(incident_id: String) -> Dictionary:
	## Diagnostics: per-vehicle count of recorded poses in a frozen clip.
	if not clips.has(incident_id):
		return {}
	var clip := clips[incident_id] as Dictionary
	var coverage := {}
	for frame in clip.frames:
		for key in (frame.poses as Dictionary):
			coverage[key] = int(coverage.get(key, 0)) + 1
	var participant_coverage := {}
	for vehicle_id in clip.participants:
		participant_coverage[str(vehicle_id)] = int(coverage.get(str(vehicle_id), 0))
	return {"frames": (clip.frames as Array).size(), "ghosts": (clip.ghosts as Dictionary).size(), "participants": participant_coverage, "ghost_keys": (clip.ghosts as Dictionary).keys()}


func has_clip(incident_id: String) -> bool:
	return clips.has(incident_id)


# --- Playback ------------------------------------------------------------------------

func play(incident_id: String, viewport_camera: Camera3D) -> bool:
	if not clips.has(incident_id) or (clips[incident_id].frames as Array).is_empty():
		return false
	stop()
	_playing = clips[incident_id]
	_playing["id"] = incident_id
	_previous_camera = viewport_camera
	for ghost in (_playing.ghosts as Dictionary).values():
		(ghost as Node3D).visible = false
		VehicleDamage.restore(ghost)
	_playing["impact_shown"] = false
	playback_time = 0.0
	paused = false
	_camera.current = true
	_apply_frame()
	return true


func stop() -> void:
	if _playing.is_empty():
		return
	for ghost in (_playing.ghosts as Dictionary).values():
		(ghost as Node3D).visible = false
	_playing = {}
	if _previous_camera != null and is_instance_valid(_previous_camera):
		_previous_camera.current = true
	replay_finished.emit()


func is_playing() -> bool:
	return not _playing.is_empty()


func duration() -> float:
	return float(_playing.end) - float(_playing.start) if not _playing.is_empty() else 0.0


func seek(seconds: float) -> void:
	if _playing.is_empty():
		return
	playback_time = clampf(seconds, 0.0, duration())
	if playback_time < float(_playing.impact) - float(_playing.start):
		for vehicle_id in _playing.participants:
			var ghost := (_playing.ghosts as Dictionary).get(str(vehicle_id)) as Node3D
			if ghost != null:
				VehicleDamage.restore(ghost)
		_playing["impact_shown"] = false
	_apply_frame()


func relative_to_impact() -> float:
	return playback_time - (float(_playing.impact) - float(_playing.start)) if not _playing.is_empty() else 0.0


func _advance_playback(delta: float) -> void:
	if not paused:
		playback_time += delta * playback_speed
		if playback_time >= duration():
			playback_time = duration()
			paused = true
	_apply_frame()


func _apply_frame() -> void:
	var frames := _playing.frames as Array
	var absolute := float(_playing.start) + playback_time
	var index := 0
	while index < frames.size() - 2 and float(frames[index + 1].t) < absolute:
		index += 1
	var a := frames[index] as Dictionary
	var b := frames[mini(index + 1, frames.size() - 1)] as Dictionary
	var span := maxf(float(b.t) - float(a.t), 0.0001)
	var weight := clampf((absolute - float(a.t)) / span, 0.0, 1.0)
	for key in (_playing.ghosts as Dictionary):
		var ghost := (_playing.ghosts as Dictionary)[key] as Node3D
		var pose_a: Variant = (a.poses as Dictionary).get(key)
		var pose_b: Variant = (b.poses as Dictionary).get(key, pose_a)
		if pose_a == null:
			ghost.visible = false
			continue
		ghost.visible = true
		ghost.global_transform = (pose_a as Transform3D).interpolate_with(pose_b as Transform3D, weight)
	if not bool(_playing.impact_shown) and absolute >= float(_playing.impact):
		_playing["impact_shown"] = true
		_show_impact()
	_update_camera()


func _show_impact() -> void:
	var burst := IncidentFx.impact_burst(Vector3(1, 0, 0), float(_playing.delta_v))
	add_child(burst)
	burst.global_position = (_playing.point as Vector3) + Vector3(0, 0.9, 0)
	for value in burst.find_children("*", "VisualInstance3D", true, false):
		(value as VisualInstance3D).layers = REPLAY_LAYER
	get_tree().create_timer(6.0).timeout.connect(burst.queue_free)
	var record := life.get_parent().get_node("Incidents").get("last_state") as Dictionary
	for vehicle_id in _playing.participants:
		var ghost := (_playing.ghosts as Dictionary).get(str(vehicle_id)) as Node3D
		var live := life.call("get_keyed_vehicle", str(vehicle_id)) as Node3D
		if ghost != null and live != null and live.has_meta("damage_zone"):
			VehicleDamage.apply_crumple(ghost, str(live.get_meta("damage_zone")), float(live.get_meta("damage_level")), hash(str(vehicle_id)))


func _update_camera() -> void:
	var point := _playing.point as Vector3
	var striker_key := str((_playing.participants as Array)[0]) if not (_playing.participants as Array).is_empty() else ""
	var striker := (_playing.ghosts as Dictionary).get(striker_key) as Node3D
	match camera_mode:
		"chase":
			if striker != null and striker.visible:
				var forward := striker.global_transform.basis.z.normalized()
				forward.y = 0.0
				forward = forward.normalized()
				_camera.global_position = striker.global_position - forward * 10.5 + Vector3(0, 3.6, 0)
				_camera.look_at(striker.global_position + forward * 6.0 + Vector3(0, 0.9, 0), Vector3.UP)
		"overhead":
			_camera.global_position = point + Vector3(0.01, 34.0, 18.0)
			_camera.look_at(point, Vector3.UP)
		"street":
			# Low kerb-side view across the impact point, like a CCTV mast.
			var side := Vector3(7.5, 0.0, 7.5)
			_camera.global_position = point + side + Vector3(0, 3.2, 0)
			_camera.look_at(point + Vector3(0, 0.8, 0), Vector3.UP)
		"driver":
			if striker != null and striker.visible:
				var forward := striker.global_transform.basis.z.normalized()
				forward.y = 0.0
				forward = forward.normalized()
				_camera.global_position = striker.global_position + forward * 2.9 + Vector3(0, 1.45, 0)
				_camera.look_at(_camera.global_position + forward * 10.0 - Vector3(0, 0.6, 0), Vector3.UP)
		_:
			var angle := playback_time * 0.22
			_camera.global_position = point + Vector3(cos(angle) * 15.0, 7.5, sin(angle) * 15.0)
			_camera.look_at(point + Vector3(0, 0.6, 0), Vector3.UP)


func overlay_text() -> String:
	## HUD: time to impact, participant speeds and the signal they faced.
	if _playing.is_empty():
		return ""
	var lines: Array[String] = ["REPLAY %s   T%+.1f s   ×%.2f%s" % [str(_playing.id), relative_to_impact(), playback_speed, "   (paused)" if paused else ""]]
	var frames := _playing.frames as Array
	var absolute := float(_playing.start) + playback_time
	var index := 0
	while index < frames.size() - 2 and float(frames[index + 1].t) < absolute:
		index += 1
	for vehicle_id in _playing.participants:
		var key := str(vehicle_id)
		var a: Variant = (frames[index].poses as Dictionary).get(key)
		var b: Variant = (frames[mini(index + 1, frames.size() - 1)].poses as Dictionary).get(key)
		if a != null and b != null:
			var dt := maxf(float(frames[mini(index + 1, frames.size() - 1)].t) - float(frames[index].t), 0.001)
			var speed := (b as Transform3D).origin.distance_to((a as Transform3D).origin) / dt * 3.6
			lines.append("%s  %3.0f km/h" % [key, speed])
	return "\n".join(lines)
