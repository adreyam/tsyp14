extends Camera3D

signal target_selected(target: Node3D, direct_city_selection: bool)
signal selection_cleared
signal street_camera_released(camera_id: String)

@export var move_speed := 75.0
@export var fast_multiplier := 3.0
@export var mouse_sensitivity := 0.0011
@export var zoom_step := 11.0
@export var minimum_height := 2.0
@export var maximum_height := 720.0
@export var minimum_orbit_distance := 2.5
@export var maximum_orbit_distance := 1200.0
@export var orbit_sensitivity := 0.0042
@export var orbit_pan_sensitivity := 0.0016
@export var orbit_dolly_sensitivity := 0.012
## The camera never leaves the city area.
@export var city_half_extent := 1500.0

const GROUND_Y := 0.17
const DRAG_THRESHOLD := 6.0

const SELECTABLE_GROUPS := [
	"vehicles", "backdrop_vehicles", "pedestrians", "backdrop_pedestrians", "traffic_cameras", "incident_responders",
	"smart_city_devices", "actuators", "power_service_meters", "water_service_meters",
	"power_poles", "power_lines", "power_sources", "water_reservoirs", "water_reservoir_meters",
	"npc_meter_work_props", "garbage_bins", "emergency_stations",
]

var _looking := false
var _drag_origin := Vector2.ZERO
var _drag_distance := 0.0
var _middle_drag_mode := ""
# Left drag pans along the ground; right drag orbits around the point under
# the cursor at press.
var _right_orbiting := false
var _orbit_pivot := Vector3.ZERO
var _orbit_initialized := false
var _start_transform: Transform3D
var _selected_target: Node3D
var _framed_follow := false
var _free_follow := false
var _return_to_free_follow := false
var _frame_anchor := Vector3.ZERO
var _frame_offset := Vector3.ZERO
var _frame_world_offset := Vector3.ZERO
var _frame_world_anchor_offset := Vector3.ZERO
var _free_follow_last_centre := Vector3.ZERO
var _first_person := false
var _first_person_offset := Vector3.ZERO
var _first_person_yaw := PI
var _first_person_pitch := deg_to_rad(-4.0)
## When set, the next left click picks a building (incident targeting)
## instead of selecting a model: Callable(building: Node3D).
var building_pick_callback: Callable


func _ready() -> void:
	# Run after CityLife: the camera reads the car's final transform for this
	# exact frame instead of following one render step behind it.
	process_priority = 100
	_start_transform = global_transform
	var city_life := get_parent().get_node_or_null("CityLife")
	if city_life != null and city_life.has_signal("vehicle_visual_departed"):
		city_life.connect("vehicle_visual_departed", _on_vehicle_visual_departed)
	_update_selection_status()


func _on_vehicle_visual_departed(target: Node3D, vehicle_id: String) -> void:
	if target != _selected_target:
		return
	# Keep the last useful street framing and detach before the pooled model is
	# hidden or reused. Normal displayed cars are rerouted before the boundary;
	# this is a defensive fallback for an externally removed SUMO vehicle.
	_exit_to_close_third_person()
	_update_selection_status("%s completed its trip  •  free camera retained at the exit" % vehicle_id)


func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and event.keycode == KEY_ESCAPE:
		_release_mouse_gestures()
		var street_feed := _active_street_feed_camera()
		if street_feed != null:
			_exit_street_camera_view(street_feed)
		elif _selected_target != null and is_instance_valid(_selected_target):
			_exit_to_close_third_person()
		else:
			_clear_target_follow()
			global_transform = _start_transform
			_reset_orbit_from_view()
		get_viewport().set_input_as_handled()
		return
	# The establishing camera still receives input while a physical street feed
	# owns this viewport. Only Escape is meaningful until that feed is released.
	if not is_current():
		return
	if event is InputEventMouseButton:
		if event.button_index == MOUSE_BUTTON_RIGHT:
			if _first_person:
				get_viewport().set_input_as_handled()
				return
			if event.pressed and (Input.is_key_pressed(KEY_ALT) or Input.is_key_pressed(KEY_CTRL)):
				# Alt/Ctrl + right drag: free look from where the camera stands.
				_looking = true
				Input.set_mouse_mode(Input.MOUSE_MODE_CAPTURED)
			elif event.pressed:
				_begin_right_orbit(event.position)
			else:
				_looking = false
				_right_orbiting = false
				Input.set_mouse_mode(Input.MOUSE_MODE_VISIBLE)
			get_viewport().set_input_as_handled()
		elif event.pressed and event.button_index in [MOUSE_BUTTON_WHEEL_UP, MOUSE_BUTTON_WHEEL_DOWN]:
			if not _first_person:
				# Touchpads report fractional smooth-scroll factors.
				var notch: float = (1.0 if event.button_index == MOUSE_BUTTON_WHEEL_UP else -1.0) * (event.factor if event.factor > 0.0 else 1.0)
				if _framed_follow:
					# Following a model: zoom stays centred on it.
					_zoom(notch)
				else:
					_zoom_toward_cursor(notch, event.position)
			get_viewport().set_input_as_handled()
		elif event.button_index == MOUSE_BUTTON_LEFT:
			if event.pressed:
				_drag_origin = event.position
				_drag_distance = 0.0
			elif _drag_distance < DRAG_THRESHOLD:
				_select_at_screen_point(event.position)
			get_viewport().set_input_as_handled()
		elif event.button_index == MOUSE_BUTTON_MIDDLE and not _first_person:
			if event.pressed:
				_middle_drag_mode = "dolly" if Input.is_key_pressed(KEY_CTRL) else ("pan" if Input.is_key_pressed(KEY_SHIFT) else "orbit")
				_begin_middle_drag()
			else:
				_middle_drag_mode = ""
			get_viewport().set_input_as_handled()
	elif event is InputEventMouseMotion:
		if event.button_mask & MOUSE_BUTTON_MASK_LEFT:
			_drag_distance = maxf(_drag_distance, event.position.distance_to(_drag_origin))
			if not _first_person and not _looking and _drag_distance >= DRAG_THRESHOLD:
				_ground_pan(event.relative)
				get_viewport().set_input_as_handled()
				return
		if _first_person:
			# First-person is genuine mouse-look: entering it captures the pointer,
			# and no extra mouse button has to be held. Yaw remains unrestricted so
			# the citizen/driver can look fully behind them.
			_first_person_yaw = wrapf(_first_person_yaw - event.relative.x * mouse_sensitivity, -PI, PI)
			_first_person_pitch = clamp(_first_person_pitch - event.relative.y * mouse_sensitivity, deg_to_rad(-82.0), deg_to_rad(78.0))
			_apply_first_person_view()
			get_viewport().set_input_as_handled()
		elif _looking:
			rotation.y -= event.relative.x * mouse_sensitivity
			rotation.x -= event.relative.y * mouse_sensitivity
			rotation.x = clamp(rotation.x, deg_to_rad(-85.0), deg_to_rad(82.0))
			rotation.z = 0.0
			_orbit_initialized = false
			get_viewport().set_input_as_handled()
		elif _right_orbiting:
			_orbit_camera(event.relative)
			get_viewport().set_input_as_handled()
		elif not _middle_drag_mode.is_empty():
			_apply_middle_drag(event.relative)
			get_viewport().set_input_as_handled()
	elif event is InputEventKey and event.pressed and event.keycode == KEY_V:
		_toggle_first_person()
		get_viewport().set_input_as_handled()
	elif event is InputEventKey and event.pressed and event.keycode == KEY_R:
		_clear_target_follow()
		global_transform = _start_transform
		_reset_orbit_from_view()
	elif event is InputEventKey and event.pressed and event.keycode == KEY_H:
		_clear_target_follow()
		global_position = Vector3(720.0, 520.0, 720.0)
		rotation_degrees = Vector3(-28.8, 45.0, 0.0)
		_reset_orbit_from_view()
	elif event is InputEventKey and event.pressed and not event.ctrl_pressed and event.keycode == KEY_F:
		_clear_target_follow()
		global_position = Vector3(105.0, 32.0, 154.0)
		rotation_degrees = Vector3(-13.0, 34.0, 0.0)
		_reset_orbit_from_view()


func _process(delta: float) -> void:
	if _framed_follow:
		if _first_person:
			_apply_first_person_view()
		else:
			_apply_framed_follow_view()
		return
	if _free_follow:
		_apply_free_follow_translation()
	var direction := Vector3.ZERO
	var forward := -global_transform.basis.z
	var right := global_transform.basis.x
	forward.y = 0.0
	right.y = 0.0
	forward = forward.normalized()
	right = right.normalized()
	if Input.is_key_pressed(KEY_W) or Input.is_key_pressed(KEY_UP):
		direction += forward
	if Input.is_key_pressed(KEY_S) or Input.is_key_pressed(KEY_DOWN):
		direction -= forward
	if Input.is_key_pressed(KEY_A) or Input.is_key_pressed(KEY_LEFT):
		direction -= right
	if Input.is_key_pressed(KEY_D) or Input.is_key_pressed(KEY_RIGHT):
		direction += right
	if Input.is_key_pressed(KEY_Q):
		direction += Vector3.DOWN
	if Input.is_key_pressed(KEY_E):
		direction += Vector3.UP
	if direction.length_squared() > 0.0:
		var speed := move_speed * (fast_multiplier if Input.is_key_pressed(KEY_SHIFT) else 1.0)
		var translation := direction.normalized() * speed * delta
		var next_y := clampf(global_position.y + translation.y, minimum_height, maximum_height)
		translation.y = next_y - global_position.y
		global_position += translation
		if _orbit_initialized:
			_orbit_pivot += translation


# --- Map-style gestures --------------------------------------------------------

func _ground_under(screen_point: Vector2, plane_y := GROUND_Y) -> Variant:
	## World point on the ground plane under a screen position (null at sky).
	var origin := project_ray_origin(screen_point)
	var direction := project_ray_normal(screen_point)
	if direction.y > -0.01:
		return null
	var distance := (plane_y - origin.y) / direction.y
	if distance <= 0.0 or distance > 6000.0:
		return null
	return origin + direction * distance


func _ground_pan(relative: Vector2) -> void:
	## Same calm, distance-scaled feel as Shift+MMB pan, kept level with the
	## ground so dragging never changes the camera height.
	if not _orbit_initialized:
		_reset_orbit_from_view()
	var distance := clampf(global_position.distance_to(_orbit_pivot), minimum_orbit_distance, maximum_orbit_distance)
	var right := global_transform.basis.x
	right.y = 0.0
	var forward := -global_transform.basis.z
	forward.y = 0.0
	if right.length_squared() < 0.0001 or forward.length_squared() < 0.0001:
		return
	_translate_view((-right.normalized() * relative.x + forward.normalized() * relative.y) * distance * orbit_pan_sensitivity)


func _translate_view(shift: Vector3) -> Vector3:
	## Moves camera and pivot together inside the city area; returns the move.
	var target := global_position + shift
	target.x = clampf(target.x, -city_half_extent, city_half_extent)
	target.z = clampf(target.z, -city_half_extent, city_half_extent)
	var moved := target - global_position
	global_position = target
	_orbit_pivot += moved
	if _framed_follow and _selected_target != null and is_instance_valid(_selected_target):
		_frame_world_anchor_offset += moved
	return moved


func _begin_right_orbit(screen_point: Vector2) -> void:
	## Orbit around whatever is under the cursor (the city floor there).
	if _framed_follow and _selected_target != null and is_instance_valid(_selected_target):
		_begin_middle_drag()
	else:
		var hit: Variant = _ground_under(screen_point)
		if hit != null and global_position.distance_to(hit) <= maximum_orbit_distance:
			_orbit_pivot = hit
			_orbit_initialized = true
		elif not _orbit_initialized:
			_reset_orbit_from_view()
	_right_orbiting = true


func _zoom_toward_cursor(notches: float, screen_point: Vector2) -> void:
	## The original fixed zoom step, aimed at the point under the cursor
	## instead of the screen centre.
	var hit: Variant = _ground_under(screen_point)
	if hit == null:
		_zoom(notches)
		return
	var focus: Vector3 = hit
	var offset := global_position - focus
	var distance := offset.length()
	if distance < 0.001:
		return
	var new_distance := clampf(distance - zoom_step * notches, minimum_orbit_distance, maximum_orbit_distance)
	var target := focus + offset / distance * new_distance
	if target.y < minimum_height or target.y > maximum_height:
		return
	target.x = clampf(target.x, -city_half_extent, city_half_extent)
	target.z = clampf(target.z, -city_half_extent, city_half_extent)
	global_position = target
	_orbit_pivot = focus
	_orbit_initialized = true


func _select_at_screen_point(screen_point: Vector2) -> void:
	var ray_origin := project_ray_origin(screen_point)
	var ray_direction := project_ray_normal(screen_point)
	if building_pick_callback.is_valid():
		var picked := _building_at_ray(ray_origin, ray_direction)
		if picked != null:
			var callback := building_pick_callback
			building_pick_callback = Callable()
			callback.call(picked)
		return
	var closest_distance := INF
	var closest_target: Node3D
	# Imported GLB scenes have no collision bodies. This is a click-time mesh
	# AABB ray pass, so it adds no per-frame physics or simulation overhead.
	for mesh in _selectable_meshes():
		var hit := _ray_hit_for_mesh(mesh, ray_origin, ray_direction)
		if hit.is_empty() or float(hit.distance) >= closest_distance:
			continue
		closest_distance = float(hit.distance)
		closest_target = hit.target as Node3D
	if closest_target != null:
		_select_target(closest_target)
	else:
		# A plain click on empty city space releases an object focus. Camera
		# dragging never reaches this branch because of DRAG_THRESHOLD, so orbit,
		# pan and pivot gestures preserve the active outline and tag.
		if _selected_target != null and is_instance_valid(_selected_target) and bool(_selected_target.get_meta("highlightable", false)):
			_clear_target_follow()
		else:
			selection_cleared.emit()


func _selectable_meshes() -> Array[MeshInstance3D]:
	var meshes: Array[MeshInstance3D] = []
	var candidate_nodes: Array = []
	var seen_meshes := {}
	for group_name in SELECTABLE_GROUPS:
		candidate_nodes.append_array(get_tree().get_nodes_in_group(group_name))
	for candidate in candidate_nodes:
		if candidate is Node3D:
			for descendant in (candidate as Node3D).find_children("*", "MeshInstance3D", true, false):
				var mesh := descendant as MeshInstance3D
				if _selectable_ancestor(mesh) != null and not seen_meshes.has(mesh.get_instance_id()):
					seen_meshes[mesh.get_instance_id()] = true
					meshes.append(mesh)
	return meshes


func _ray_hit_for_mesh(mesh: MeshInstance3D, ray_origin: Vector3, ray_direction: Vector3) -> Dictionary:
	if mesh.mesh == null or not mesh.visible:
		return {}
	var target := _selectable_ancestor(mesh)
	if target == null:
		return {}
	if str(target.get_meta("model_family", "")) == "rocketbox_realistic":
		# Skinned Rocketbox meshes retain a horizontal bind-pose AABB even while
		# their rendered Bip01 skeleton is upright. Use the audited adult-human
		# selection envelope so clicking follows the visible body, not the bind pose.
		var human_bounds := _rocketbox_world_bounds(target)
		var human_hit: Variant = human_bounds.intersects_ray(ray_origin, ray_direction)
		if human_hit is Vector3:
			return {"distance": ray_origin.distance_to(human_hit as Vector3), "target": target}
		return {}
	# GLB imports can have baked vertex offsets and scaled intermediate nodes,
	# so use a world AABB only as a cheap broad-phase filter.
	var world_bounds := _mesh_world_bounds(mesh)
	if not (world_bounds.intersects_ray(ray_origin, ray_direction) is Vector3):
		return {}
	var closest_distance := INF
	var faces := mesh.mesh.get_faces()
	for vertex_index in range(0, faces.size(), 3):
		var triangle_hit: Variant = Geometry3D.ray_intersects_triangle(
			ray_origin,
			ray_direction,
			mesh.to_global(faces[vertex_index]),
			mesh.to_global(faces[vertex_index + 1]),
			mesh.to_global(faces[vertex_index + 2])
		)
		if triangle_hit is Vector3:
			closest_distance = minf(closest_distance, ray_origin.distance_to(triangle_hit as Vector3))
	if closest_distance == INF:
		return {}
	return {"distance": closest_distance, "target": target}


func _selectable_ancestor(node: Node) -> Node3D:
	var current: Node = node
	while current != null:
		if current is Node3D:
			for group_name in SELECTABLE_GROUPS:
				if current.is_in_group(group_name):
					var candidate := current as Node3D
					var normal_follow_target: bool = group_name in ["vehicles", "backdrop_vehicles", "pedestrians", "backdrop_pedestrians", "incident_responders"]
					if normal_follow_target or bool(candidate.get_meta("highlightable", false)):
						return candidate
		current = current.get_parent()
	return null


func _select_target(target: Node3D, direct_city_selection := true) -> void:
	_selected_target = target
	_first_person = false
	_free_follow = false
	_return_to_free_follow = false
	var local_bounds := _local_bounds(target)
	_frame_anchor = local_bounds.get_center()
	var centre := target.to_global(_frame_anchor)
	var bounds := _world_bounds(target)
	var radius := maxf(bounds.size.length() * 0.5, 1.25)
	var view_direction := global_position - centre
	if view_direction.length_squared() < 0.001:
		view_direction = Vector3(1.0, 0.75, 1.0)
	var focusable_device := bool(target.get_meta("highlightable", false)) and not _target_kind(target) in ["vehicle", "person"]
	var frame_distance := maxf(radius * (4.0 if focusable_device else 2.7), 9.0 if focusable_device else 5.5)
	var framed_position := centre + view_direction.normalized() * frame_distance
	_frame_offset = target.to_local(framed_position) - _frame_anchor
	# A third-person camera must follow the already-smoothed world movement of
	# the selected model, not orbit around its local axes on every tiny SUMO yaw
	# correction. Keeping this offset in world space removes the exterior-view
	# shake while preserving the selected camera distance.
	_frame_world_offset = framed_position - centre
	_frame_world_anchor_offset = centre - target.get_global_transform_interpolated().origin
	_first_person_offset = Vector3(
		_frame_anchor.x,
		local_bounds.position.y + local_bounds.size.y * 0.72,
		_frame_anchor.z + local_bounds.size.z * 0.16
	)
	_first_person_yaw = PI # Imported cars and people face local +Z; cameras face -Z.
	_first_person_pitch = deg_to_rad(-4.0)
	_framed_follow = true
	_apply_framed_follow_view()
	_orbit_pivot = centre
	_orbit_initialized = true
	_update_selection_status()
	target_selected.emit(target, direct_city_selection)


func _building_at_ray(ray_origin: Vector3, ray_direction: Vector3) -> Node3D:
	var closest: Node3D = null
	var closest_distance := INF
	for group_name in ["buildings", "backdrop_buildings"]:
		for value in get_tree().get_nodes_in_group(group_name):
			var building := value as Node3D
			if building == null or not building.is_visible_in_tree():
				continue
			var hit: Variant = _world_bounds(building).intersects_ray(ray_origin, ray_direction)
			if hit is Vector3 and ray_origin.distance_to(hit as Vector3) < closest_distance:
				closest_distance = ray_origin.distance_to(hit as Vector3)
				closest = building
	return closest


func watch_point(point: Vector3, radius := 20.0, subject: Node3D = null, facing := Vector3.ZERO) -> void:
	## Frames a world point (an incident scene) from a raised three-quarter
	## view and makes it the orbit pivot; MMB orbit/pan/zoom keep working.
	## subject (e.g. a burning building) is what is being watched, never an
	## occluder; facing is the side it must be seen from (its street front).
	_clear_target_follow()
	var preferred := facing if facing.length_squared() > 0.01 else global_position - point
	preferred.y = 0.0
	var facing_weight := 30.0 if facing.length_squared() > 0.01 else 2.0
	if preferred.length_squared() < 1.0:
		preferred = Vector3(1.0, 0.0, 1.0)
	preferred = preferred.normalized()
	var occluders := _occluder_bounds(subject)
	# Several rays across the scene, not just its centre: a wall beside the
	# line of sight fills half the frame even when the centre is visible.
	var side := preferred.cross(Vector3.UP).normalized()
	var best_position := point + preferred * radius + Vector3(0.0, radius * 0.62, 0.0)
	var best_score := -INF
	for distance_scale in [1.0, 0.72]:
		for height_scale in [0.62, 0.95]:
			for yaw_degrees in [0.0, 45.0, -45.0, 90.0, -90.0, 135.0, -135.0, 180.0]:
				var horizontal := preferred.rotated(Vector3.UP, deg_to_rad(float(yaw_degrees)))
				var candidate := point + horizontal * radius * float(distance_scale) + Vector3(0.0, radius * float(height_scale), 0.0)
				if _inside_any(candidate, occluders, 1.5):
					continue
				var across := horizontal.cross(Vector3.UP).normalized()
				var blocked := 0
				for offset in [Vector3(0, 1.0, 0), across * 4.0 + Vector3(0, 1.0, 0), -across * 4.0 + Vector3(0, 1.0, 0), Vector3(0, 4.0, 0), horizontal * -3.0 + Vector3(0, 1.0, 0)]:
					blocked += _segment_blocked_count(candidate, point + (offset as Vector3), occluders)
				var score := -float(blocked) * 100.0 + horizontal.dot(preferred) * facing_weight + float(distance_scale) - float(height_scale) * 0.5
				if score > best_score:
					best_score = score
					best_position = candidate
	global_position = best_position
	look_at(point, Vector3.UP)
	_orbit_pivot = point
	_orbit_initialized = true
	_update_selection_status("WATCHING INCIDENT  •  MMB orbit  •  wheel zoom  •  Esc release")


func _occluder_bounds(subject: Node3D) -> Array[AABB]:
	var output: Array[AABB] = []
	var seen := {}
	for group_name in ["buildings", "city_trees", "urban_props", "power_poles"]:
		var values := get_tree().get_nodes_in_group(group_name)
		if group_name == "city_trees":
			var central_buildings := get_parent().get_node_or_null("Buildings")
			if central_buildings != null:
				values.append_array(central_buildings.find_children("Tree_*", "Node3D", true, false))
		for value in values:
			if not value is Node3D or seen.has(value.get_instance_id()):
				continue
			var obstacle := value as Node3D
			seen[obstacle.get_instance_id()] = true
			if not obstacle.visible or (subject != null and (obstacle == subject or subject.is_ancestor_of(obstacle) or obstacle.is_ancestor_of(subject))):
				continue
			var bounds := _world_bounds(obstacle)
			if bounds.size.length_squared() >= 0.001:
				output.append(bounds.grow(0.18))
	return output


func _inside_any(point: Vector3, bounds_list: Array[AABB], margin: float) -> bool:
	for bounds in bounds_list:
		if bounds.grow(margin).has_point(point):
			return true
	return false


func _segment_blocked_count(from: Vector3, to: Vector3, bounds_list: Array[AABB]) -> int:
	for bounds in bounds_list:
		if bounds.intersects_segment(from, to) is Vector3:
			return 1
	return 0


func follow_target(target: Node3D) -> bool:
	# Public control-room entry point.  It intentionally uses exactly the same
	# framing and smoothing as clicking the model in the normal city view.
	if target == null or not is_instance_valid(target):
		return false
	_select_target(target, false)
	return true


func _toggle_first_person() -> void:
	if _selected_target == null or not is_instance_valid(_selected_target):
		_update_selection_status("Click a model first  •  V: first-person")
		return
	_first_person = not _first_person
	if _first_person:
		# A free-follow camera must be temporarily converted to first-person so
		# the selected target continues to drive its camera transform each frame.
		_return_to_free_follow = _free_follow
		_free_follow = false
		_framed_follow = true
		_apply_first_person_view()
		Input.set_mouse_mode(Input.MOUSE_MODE_CAPTURED)
	else:
		Input.set_mouse_mode(Input.MOUSE_MODE_VISIBLE)
		if _return_to_free_follow:
			_framed_follow = false
			_free_follow = true
			_free_follow_last_centre = _selected_target.get_global_transform_interpolated().origin + _frame_world_anchor_offset
		else:
			_apply_framed_follow_view()
	_update_selection_status()


func _exit_to_close_third_person() -> void:
	# Escape detaches completely at a slightly wider third-person position.
	# This deliberately does not follow the selected model afterward: it is the
	# same free camera behavior as the original view, just placed near the object
	# the player was examining.
	if _selected_target == null or not is_instance_valid(_selected_target):
		_clear_target_follow()
		global_transform = _start_transform
		return
	var bounds := _world_bounds(_selected_target)
	var centre := bounds.get_center()
	var view_direction := global_position - centre
	if view_direction.length_squared() < 0.001:
		view_direction = Vector3(1.0, 0.55, 1.0)
	view_direction.y = maxf(view_direction.y, 0.32)
	var radius := maxf(bounds.size.length() * 0.5, 1.25)
	# Leave a visibly wider third-person gap than first-person while remaining
	# close enough to read the selected model and nearby street activity.
	_frame_world_offset = view_direction.normalized() * maxf(radius * 2.7, 6.5)
	_frame_world_anchor_offset = centre - _selected_target.get_global_transform_interpolated().origin
	global_position = _selected_target.get_global_transform_interpolated().origin + _frame_world_anchor_offset + _frame_world_offset
	look_at(_selected_target.get_global_transform_interpolated().origin + _frame_world_anchor_offset, Vector3.UP)
	_orbit_pivot = centre
	_orbit_initialized = true
	_clear_target_follow()
	_update_selection_status("FREE CAMERA  •  detached near the selected model")


func _clear_target_follow() -> void:
	if _first_person:
		Input.set_mouse_mode(Input.MOUSE_MODE_VISIBLE)
	_first_person = false
	_framed_follow = false
	_free_follow = false
	_return_to_free_follow = false
	_selected_target = null
	_update_selection_status()
	selection_cleared.emit()


func _apply_framed_follow_view() -> void:
	if _selected_target == null or not is_instance_valid(_selected_target):
		_clear_target_follow()
		return
	# Keep both the camera position and look target in world space. Imported GLB
	# mesh centres are often offset from their Node3D root; transforming that
	# centre by a changing vehicle yaw caused the last visible exterior jitter.
	var centre := _selected_target.get_global_transform_interpolated().origin + _frame_world_anchor_offset
	global_position = centre + _frame_world_offset
	look_at(centre, Vector3.UP)
	_orbit_pivot = centre
	_orbit_initialized = true


func _apply_free_follow_translation() -> void:
	if _selected_target == null or not is_instance_valid(_selected_target):
		_clear_target_follow()
		return
	# Only translate by the target's world displacement. Never overwrite the
	# user's own rotation, pan, WASD move or wheel zoom while following it.
	var centre := _selected_target.get_global_transform_interpolated().origin + _frame_world_anchor_offset
	global_position += centre - _free_follow_last_centre
	_free_follow_last_centre = centre


func _apply_first_person_view() -> void:
	if _selected_target == null or not is_instance_valid(_selected_target):
		_first_person = false
		_update_selection_status("Selection lost")
		return
	var bounds := _world_bounds(_selected_target)
	var centre := bounds.get_center()
	var target_basis := _selected_target.get_global_transform_interpolated().basis.orthonormalized()
	var forward := target_basis * Vector3.BACK
	forward.y = 0.0
	if forward.length_squared() < 0.001:
		forward = Vector3.BACK
	forward = forward.normalized()
	var height := maxf(bounds.size.y, 0.8)
	# Project the world AABB onto the target's horizontal forward axis. Adding a
	# hard near-plane clearance guarantees the camera origin remains outside the
	# rendered model at every heading instead of relying on an approximate model
	# length (which failed for wide cars and crouched citizens).
	var projected_half_extent := 0.5 * (absf(forward.x) * bounds.size.x + absf(forward.z) * bounds.size.z)
	var kind := _target_kind(_selected_target)
	var exterior_clearance := 0.38 if kind == "vehicle" else (0.18 if kind == "person" else 0.24)
	var eye_fraction := 0.76 if kind == "vehicle" else (0.84 if kind == "person" else 0.72)
	var first_person_position := Vector3(
		centre.x + forward.x * (projected_half_extent + exterior_clearance),
		bounds.position.y + height * eye_fraction,
		centre.z + forward.z * (projected_half_extent + exterior_clearance)
	)
	var look_basis := Basis.from_euler(Vector3(_first_person_pitch, _first_person_yaw, 0.0))
	global_transform = Transform3D(target_basis * look_basis, first_person_position)
	_orbit_initialized = false


func _release_mouse_gestures() -> void:
	_looking = false
	_right_orbiting = false
	_middle_drag_mode = ""
	Input.set_mouse_mode(Input.MOUSE_MODE_VISIBLE)


func _active_street_feed_camera() -> Camera3D:
	var active := get_viewport().get_camera_3d()
	if active == null or active == self:
		return null
	var device := active.get_parent()
	if device is Node3D and device.is_in_group("traffic_cameras"):
		return active
	return null


func _exit_street_camera_view(feed_camera: Camera3D) -> void:
	var device := feed_camera.get_parent() as Node3D
	if device == null:
		current = true
		global_transform = _start_transform
		_clear_target_follow()
		_reset_orbit_from_view()
		return
	var bounds := _world_bounds(device)
	var centre := bounds.get_center()
	var radius := maxf(bounds.size.length() * 0.5, 2.2)
	# Pull back along the physical lens's rear axis and rise slightly. This makes
	# Escape visibly leave the feed instead of snapping to an unrelated overview,
	# while keeping the whole roadside camera and its junction context in frame.
	var retreat := feed_camera.global_transform.basis.z.normalized()
	retreat.y = 0.0
	if retreat.length_squared() < 0.001:
		retreat = Vector3(1.0, 0.0, 1.0)
	retreat = retreat.normalized()
	var distance := clampf(maxf(radius * 3.8, 13.5), minimum_orbit_distance, maximum_orbit_distance)
	_clear_target_follow()
	current = true
	global_position = _unobstructed_street_exit_position(device, centre, retreat, distance)
	global_position.y = clampf(global_position.y, minimum_height, maximum_height)
	look_at(centre, Vector3.UP)
	_orbit_pivot = centre
	_orbit_initialized = true
	var camera_id := str(device.get_meta("camera_id", device.name))
	_update_selection_status("FREE CAMERA  •  released from %s  •  MMB orbit restored" % camera_id)
	street_camera_released.emit(camera_id)


func _unobstructed_street_exit_position(device: Node3D, centre: Vector3, preferred: Vector3, distance: float) -> Vector3:
	var best_position := centre + (preferred + Vector3.UP * 0.30).normalized() * distance
	var best_score := -INF
	# Start with the view direction users were just watching, then fan around the
	# device. A one-time AABB visibility check prevents trees/buildings from
	# filling the released view without adding any per-frame camera cost.
	for yaw_degrees in [0.0, 45.0, -45.0, 90.0, -90.0, 135.0, -135.0, 180.0]:
		var horizontal := preferred.rotated(Vector3.UP, deg_to_rad(float(yaw_degrees)))
		var candidate := centre + (horizontal + Vector3.UP * 0.30).normalized() * distance
		candidate.y = clampf(candidate.y, minimum_height, maximum_height)
		var occluders := _street_exit_occluder_count(candidate, centre, device)
		# A slight three-quarter-view preference makes the pole and camera housing
		# readable as a physical asset instead of collapsing into one dark line.
		var preference := horizontal.dot(preferred) + (0.40 if absf(float(yaw_degrees)) == 45.0 else 0.0)
		var score := -float(occluders) * 100.0 + preference
		if score > best_score:
			best_score = score
			best_position = candidate
	return best_position


func _street_exit_occluder_count(from: Vector3, to: Vector3, device: Node3D) -> int:
	var count := 0
	for group_name in ["buildings", "city_trees", "urban_props", "power_poles"]:
		var obstacle_values := get_tree().get_nodes_in_group(group_name)
		# The original central blocks predate the spatial-safety groups; their
		# authored Tree_01/Tree_02 instances must still count as camera occluders.
		if group_name == "city_trees":
			var central_buildings := get_parent().get_node_or_null("Buildings")
			if central_buildings != null:
				obstacle_values.append_array(central_buildings.find_children("Tree_*", "Node3D", true, false))
		var seen := {}
		for value in obstacle_values:
			if not value is Node3D or value == device:
				continue
			var obstacle := value as Node3D
			if seen.has(obstacle.get_instance_id()):
				continue
			seen[obstacle.get_instance_id()] = true
			if not obstacle.visible:
				continue
			var bounds := _world_bounds(obstacle)
			if bounds.size.length_squared() < 0.001:
				continue
			# Leave the final metre around the camera device out of the occlusion
			# segment so the selected pole/housing cannot reject its own view.
			var segment_end := to.lerp(from, minf(1.0 / maxf(from.distance_to(to), 1.0), 0.2))
			if bounds.grow(0.18).intersects_segment(from, segment_end) is Vector3:
				count += 1
	return count


func _begin_middle_drag() -> void:
	if _framed_follow and _selected_target != null and is_instance_valid(_selected_target):
		_orbit_pivot = _selected_target.get_global_transform_interpolated().origin + _frame_world_anchor_offset
		_orbit_initialized = true
	else:
		# Always orbit about what is in the middle of the view right now: a
		# pivot left over from before a pan, zoom or keyboard move would make
		# the first drag snap the camera round to look at that old point.
		_reset_orbit_from_view()


func _reset_orbit_from_view() -> void:
	var forward := -global_transform.basis.z.normalized()
	var distance := clampf(maxf(global_position.y * 0.85, 24.0), 24.0, 180.0)
	# Prefer the point under the cursor direction on the city surface. For a
	# near-horizontal view, use a stable point ahead instead of an extreme plane
	# intersection thousands of metres away.
	if forward.y < -0.04:
		var surface_distance := (minimum_height - global_position.y) / forward.y
		if surface_distance > minimum_orbit_distance and surface_distance < maximum_orbit_distance:
			distance = surface_distance
	_orbit_pivot = global_position + forward * distance
	_orbit_initialized = true


func _apply_middle_drag(relative: Vector2) -> void:
	if not _orbit_initialized:
		_reset_orbit_from_view()
	match _middle_drag_mode:
		"orbit":
			_orbit_camera(relative)
		"pan":
			_pan_camera(relative)
		"dolly":
			_dolly_camera(relative.y * orbit_dolly_sensitivity)


func _orbit_camera(relative: Vector2) -> void:
	var offset := global_position - _orbit_pivot
	if offset.length() < minimum_orbit_distance:
		offset = global_transform.basis.z.normalized() * minimum_orbit_distance
	offset = offset.rotated(Vector3.UP, -relative.x * orbit_sensitivity)
	var right := Vector3.UP.cross(offset).normalized()
	if right.length_squared() < 0.001:
		right = global_transform.basis.x.normalized()
	var candidate := offset.rotated(right, -relative.y * orbit_sensitivity)
	var vertical_ratio := candidate.normalized().dot(Vector3.UP)
	# Avoid the singularity directly above/below the pivot and never orbit below
	# the city surface. Horizontal and full 360-degree azimuth remain available.
	var pitch := 0.0
	if absf(vertical_ratio) < 0.985 and (_orbit_pivot.y + candidate.y) >= minimum_height:
		offset = candidate
		pitch = -relative.y * orbit_sensitivity
	# The view turns by exactly the rotation applied to the position, so the
	# pivot stays where it is on screen (under the cursor for a right-drag)
	# instead of the camera snapping round to look straight at it.
	var forward := (Basis(right, pitch) * Basis(Vector3.UP, -relative.x * orbit_sensitivity)) * -global_transform.basis.z
	global_position = _orbit_pivot + offset
	global_position.y = minf(global_position.y, maximum_height)
	if absf(forward.normalized().dot(Vector3.UP)) < 0.995:
		global_transform.basis = Basis.looking_at(forward.normalized(), Vector3.UP)
	else:
		look_at(_orbit_pivot, Vector3.UP)
	_commit_orbit_to_follow()


func _pan_camera(relative: Vector2) -> void:
	var distance := clampf(global_position.distance_to(_orbit_pivot), minimum_orbit_distance, maximum_orbit_distance)
	var scale_value := distance * orbit_pan_sensitivity
	var translation := (-global_transform.basis.x * relative.x + global_transform.basis.y * relative.y) * scale_value
	var next_y := clampf(global_position.y + translation.y, minimum_height, maximum_height)
	translation.y = next_y - global_position.y
	global_position += translation
	_orbit_pivot += translation
	if _framed_follow and _selected_target != null and is_instance_valid(_selected_target):
		_frame_world_anchor_offset += translation
	look_at(_orbit_pivot, Vector3.UP)


func _dolly_camera(logarithmic_change: float) -> void:
	var offset := global_position - _orbit_pivot
	var old_distance := maxf(offset.length(), minimum_orbit_distance)
	var new_distance := clampf(old_distance * exp(logarithmic_change), minimum_orbit_distance, maximum_orbit_distance)
	if offset.length_squared() < 0.001:
		offset = global_transform.basis.z.normalized()
	global_position = _orbit_pivot + offset.normalized() * new_distance
	global_position.y = clampf(global_position.y, minimum_height, maximum_height)
	look_at(_orbit_pivot, Vector3.UP)
	_commit_orbit_to_follow()


func _commit_orbit_to_follow() -> void:
	if _framed_follow and _selected_target != null and is_instance_valid(_selected_target):
		_frame_world_offset = global_position - (_selected_target.get_global_transform_interpolated().origin + _frame_world_anchor_offset)


func _world_bounds(target: Node3D) -> AABB:
	if str(target.get_meta("model_family", "")) == "rocketbox_realistic":
		return _rocketbox_world_bounds(target)
	var result := AABB()
	var has_bounds := false
	var mesh_values: Array = [target] if target is MeshInstance3D else []
	mesh_values.append_array(target.find_children("*", "MeshInstance3D", true, false))
	for mesh_value in mesh_values:
		var mesh := mesh_value as MeshInstance3D
		if mesh.mesh == null:
			continue
		for corner in _aabb_corners(mesh.mesh.get_aabb()):
			var point := mesh.to_global(corner)
			result = AABB(point, Vector3.ZERO) if not has_bounds else result.expand(point)
			has_bounds = true
	return result


func _mesh_world_bounds(mesh: MeshInstance3D) -> AABB:
	var result := AABB()
	var has_bounds := false
	for corner in _aabb_corners(mesh.mesh.get_aabb()):
		var point := mesh.to_global(corner)
		result = AABB(point, Vector3.ZERO) if not has_bounds else result.expand(point)
		has_bounds = true
	return result


func _local_bounds(target: Node3D) -> AABB:
	if str(target.get_meta("model_family", "")) == "rocketbox_realistic":
		return AABB(Vector3(-0.46, 0.0, -0.34), Vector3(0.92, float(target.get_meta("npc_visual_height", 1.82)), 0.68))
	var result := AABB()
	var has_bounds := false
	var mesh_values: Array = [target] if target is MeshInstance3D else []
	mesh_values.append_array(target.find_children("*", "MeshInstance3D", true, false))
	for mesh_value in mesh_values:
		var mesh := mesh_value as MeshInstance3D
		if mesh.mesh == null:
			continue
		for corner in _aabb_corners(mesh.mesh.get_aabb()):
			var point := target.to_local(mesh.to_global(corner))
			result = AABB(point, Vector3.ZERO) if not has_bounds else result.expand(point)
			has_bounds = true
	return result


func _rocketbox_world_bounds(target: Node3D) -> AABB:
	var height := float(target.get_meta("npc_visual_height", 1.82))
	# Pose grounding moves the character root below the pavement for crouched
	# mocap while the visible feet remain on the surface. Selection and camera
	# framing must therefore anchor to the authored surface, not the shifted root.
	var surface_y := float(target.get_meta("npc_surface_y", target.global_position.y))
	var centre := Vector3(target.global_position.x, surface_y + height * 0.5, target.global_position.z)
	return AABB(centre - Vector3(0.46, height * 0.5, 0.34), Vector3(0.92, height, 0.68))


func _aabb_corners(bounds: AABB) -> Array[Vector3]:
	var start := bounds.position
	var end := bounds.end
	return [
		Vector3(start.x, start.y, start.z), Vector3(end.x, start.y, start.z),
		Vector3(start.x, end.y, start.z), Vector3(end.x, end.y, start.z),
		Vector3(start.x, start.y, end.z), Vector3(end.x, start.y, end.z),
		Vector3(start.x, end.y, end.z), Vector3(end.x, end.y, end.z),
	]


func _update_selection_status(override_text := "") -> void:
	var status := get_parent().get_node_or_null("CameraControls/SelectionStatus") as Label
	if status == null:
		return
	if not override_text.is_empty():
		status.text = override_text
	elif _selected_target == null or not is_instance_valid(_selected_target):
		status.text = "SELECT: click a model to frame it  •  MMB: orbit  •  Shift+MMB: pan  •  Ctrl+MMB: dolly"
	else:
		var view_name := "first-person" if _first_person else ("framed follow" if _framed_follow else "movable third-person")
		status.text = "%s  •  %s  •  %s  •  V: toggle mouse-look  •  Esc: release" % [_selected_target.name, _target_kind(_selected_target), view_name]


func _target_kind(target: Node3D) -> String:
	for group_name in ["vehicles", "backdrop_vehicles"]:
		if target.is_in_group(group_name):
			return "vehicle"
	for group_name in ["pedestrians", "backdrop_pedestrians", "incident_responders"]:
		if target.is_in_group(group_name):
			return "person"
	if target.is_in_group("traffic_cameras"):
		return "traffic camera"
	return "object"


func get_selected_target() -> Node3D:
	return _selected_target if _selected_target != null and is_instance_valid(_selected_target) else null


func _zoom(amount: float) -> void:
	if not _orbit_initialized:
		_reset_orbit_from_view()
	var offset := global_position - _orbit_pivot
	var old_distance := maxf(offset.length(), minimum_orbit_distance)
	var new_distance := clampf(old_distance - zoom_step * amount, minimum_orbit_distance, maximum_orbit_distance)
	if offset.length_squared() < 0.001:
		offset = global_transform.basis.z.normalized()
	global_position = _orbit_pivot + offset.normalized() * new_distance
	global_position.y = clampf(global_position.y, minimum_height, maximum_height)
	look_at(_orbit_pivot, Vector3.UP)
	_commit_orbit_to_follow()
