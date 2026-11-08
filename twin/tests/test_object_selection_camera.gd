extends SceneTree


func _init() -> void:
	call_deferred("_test")


func _fail(message: String) -> void:
	printerr("OBJECT SELECTION CAMERA TEST FAILED: " + message)
	quit(1)


func _test() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate()
	root.add_child(city)
	await process_frame
	var camera := city.get_node("EstablishingCamera") as Camera3D
	var vehicle := get_nodes_in_group("vehicles")[0] as Node3D
	var life := city.get_node("CityLife")
	if camera == null or vehicle == null:
		_fail("city did not create camera and vehicle targets")
		return
	if int(city.get_node("CityLife").process_priority) >= camera.process_priority:
		_fail("CityLife must update before the third-person follow camera")
		return
	# Isolate camera following from fallback traffic while directly moving the
	# target in this test.
	life.set_process(false)
	var vehicle_meshes := vehicle.find_children("*", "MeshInstance3D", true, false)
	if vehicle_meshes.is_empty():
		_fail("vehicle has no render mesh to click")
		return
	var selectable_meshes: Array[MeshInstance3D] = camera.call("_selectable_meshes")
	if selectable_meshes.is_empty():
		_fail("selection has no person/vehicle meshes")
		return
	for mesh in selectable_meshes:
		var clicked_target := camera.call("_selectable_ancestor", mesh) as Node3D
		var normal_follow_target := clicked_target != null and (clicked_target.is_in_group("vehicles") or clicked_target.is_in_group("backdrop_vehicles") or clicked_target.is_in_group("pedestrians") or clicked_target.is_in_group("backdrop_pedestrians") or clicked_target.is_in_group("incident_responders"))
		var registered_object := clicked_target != null and bool(clicked_target.get_meta("highlightable", false))
		if clicked_target == null or (not normal_follow_target and not registered_object) or clicked_target.is_in_group("buildings") or clicked_target.is_in_group("backdrop_buildings"):
			_fail("selection exposed unregistered scenery/building geometry: %s groups=%s highlightable=%s" % [clicked_target.name if clicked_target != null else "null", str(clicked_target.get_groups()) if clicked_target != null else "[]", str(registered_object)])
			return
	var traffic_cameras := get_nodes_in_group("traffic_cameras")
	if traffic_cameras.size() != 16:
		_fail("expected sixteen physical traffic cameras, got %d" % traffic_cameras.size())
		return
	for traffic_camera_value in traffic_cameras:
		var traffic_camera := traffic_camera_value as Node3D
		if traffic_camera.get_node_or_null("FeedCamera") == null or traffic_camera.find_children("Lens", "MeshInstance3D", true, false).is_empty():
			_fail("%s does not contain a physical lens and live feed camera" % traffic_camera.name)
			return
	# Selecting this moving vehicle directly verifies that the same framing and
	# first-person path works for a SUMO-driven model as well as static objects.
	camera.call("_select_target", vehicle)
	if not bool(camera.get("_framed_follow")) or bool(camera.get("_first_person")):
		_fail("selected model did not enter persistent framed-follow mode")
		return
	var framed_position := camera.global_position
	vehicle.global_position += Vector3(3.0, 0.0, 0.0)
	await process_frame
	if camera.global_position.distance_to(framed_position) < 2.0:
		_fail("framed camera did not follow the selected moving model")
		return
	camera.call("_toggle_first_person")
	await process_frame
	if not bool(camera.get("_first_person")):
		_fail("V toggle did not enter first-person mode for selected object")
		return
	var vehicle_world_bounds: AABB = camera.call("_world_bounds", vehicle)
	if vehicle_world_bounds.has_point(camera.global_position):
		_fail("vehicle first-person camera is still inside the model geometry")
		return
	var view_before_mouse := -camera.global_transform.basis.z
	var first_person_mouse := InputEventMouseMotion.new()
	first_person_mouse.relative = Vector2(160.0, -45.0)
	camera.call("_unhandled_input", first_person_mouse)
	if (-camera.global_transform.basis.z).dot(view_before_mouse) > 0.995:
		_fail("first-person did not react to mouse movement without holding another button")
		return
	if vehicle_world_bounds.has_point(camera.global_position):
		_fail("first-person mouse-look moved the camera origin inside the vehicle")
		return
	var first_person_position := camera.global_position
	vehicle.global_position += Vector3(3.0, 0.0, 0.0)
	await process_frame
	if camera.global_position.distance_to(first_person_position) < 2.0:
		_fail("first-person camera did not follow the selected moving model")
		return
	camera.call("_toggle_first_person")
	if bool(camera.get("_first_person")):
		_fail("second V toggle did not leave first-person mode")
		return
	var frame_anchor: Vector3 = camera.get("_frame_anchor")
	var frame_world_offset: Vector3 = camera.get("_frame_world_offset")
	var frame_world_anchor_offset: Vector3 = camera.get("_frame_world_anchor_offset")
	var expected_framed_position := vehicle.global_position + frame_world_anchor_offset + frame_world_offset
	if camera.global_position.distance_to(expected_framed_position) > 0.01:
		_fail("second V toggle did not restore the same framed-follow view")
		return
	# SUMO may make small heading corrections through intersections. They must
	# never orbit the exterior camera around the vehicle or look like movement
	# jitter; only the selected model's world displacement may move this view.
	var third_person_before_turn := camera.global_position
	vehicle.rotation.y += 0.7
	await process_frame
	if camera.global_position.distance_to(third_person_before_turn) > 0.01:
		_fail("third-person camera moved when only the selected vehicle heading changed")
		return
	# Escape from first-person must detach to a wider, movable free camera near
	# the selected model, not follow the target or return to the distant overview.
	camera.call("_toggle_first_person")
	await process_frame
	camera.call("_exit_to_close_third_person")
	if bool(camera.get("_first_person")) or bool(camera.get("_framed_follow")) or bool(camera.get("_free_follow")) or camera.get("_selected_target") != null:
		_fail("escape did not detach into the free third-person camera")
		return
	var camera_rotation_before_input := camera.rotation
	camera.set("_looking", true)
	var look_event := InputEventMouseMotion.new()
	look_event.relative = Vector2(20.0, -8.0)
	camera.call("_unhandled_input", look_event)
	camera.set("_looking", false)
	if camera.rotation.is_equal_approx(camera_rotation_before_input):
		_fail("escape third-person view ignored free camera mouse-look input")
		return
	var close_follow_position := camera.global_position
	vehicle.global_position += Vector3(2.0, 0.0, 0.0)
	await process_frame
	if camera.global_position.distance_to(close_follow_position) > 0.01:
		_fail("escape free camera remained attached to the selected target")
		return
	# Blender-style general camera gestures use a stable pivot. Verify orbit,
	# pan, and dolly independently instead of accepting input flags as proof.
	camera.call("_reset_orbit_from_view")
	var orbit_pivot: Vector3 = camera.get("_orbit_pivot")
	var orbit_distance := camera.global_position.distance_to(orbit_pivot)
	var before_orbit := camera.global_position
	camera.set("_middle_drag_mode", "orbit")
	camera.call("_apply_middle_drag", Vector2(70.0, -22.0))
	if camera.global_position.distance_to(before_orbit) < 0.5 or absf(camera.global_position.distance_to(orbit_pivot) - orbit_distance) > 0.02:
		_fail("MMB orbit did not rotate around a stable pivot")
		return
	var before_pan_position := camera.global_position
	var before_pan_pivot: Vector3 = camera.get("_orbit_pivot")
	camera.set("_middle_drag_mode", "pan")
	camera.call("_apply_middle_drag", Vector2(30.0, 15.0))
	var pan_camera_delta := camera.global_position - before_pan_position
	var pan_pivot_delta: Vector3 = camera.get("_orbit_pivot") - before_pan_pivot
	if pan_camera_delta.length() < 0.1 or pan_camera_delta.distance_to(pan_pivot_delta) > 0.01:
		_fail("Shift+MMB pan did not translate camera and pivot together")
		return
	var before_dolly_distance := camera.global_position.distance_to(camera.get("_orbit_pivot") as Vector3)
	camera.set("_middle_drag_mode", "dolly")
	camera.call("_apply_middle_drag", Vector2(0.0, -24.0))
	var after_dolly_distance := camera.global_position.distance_to(camera.get("_orbit_pivot") as Vector3)
	if after_dolly_distance >= before_dolly_distance or after_dolly_distance < float(camera.minimum_orbit_distance) - 0.01:
		_fail("Ctrl+MMB dolly did not zoom toward the pivot within its distance bound")
		return
	# A realistic citizen has a much narrower depth than a car. Explicitly audit
	# that the same exterior support-distance calculation keeps their eye camera
	# outside the animated body envelope too.
	var person := get_nodes_in_group("pedestrians")[0] as Node3D
	camera.call("_select_target", person)
	camera.call("_toggle_first_person")
	await process_frame
	var person_bounds: AABB = camera.call("_world_bounds", person)
	if person_bounds.has_point(camera.global_position):
		_fail("person first-person camera is inside the animated body envelope")
		return
	print("OBJECT SELECTION CAMERA TEST PASSED: bounded mouse-look works for people/cars; MMB orbit, Shift+MMB pan, Ctrl+MMB dolly, and Escape detach are functional")
	quit(0)
