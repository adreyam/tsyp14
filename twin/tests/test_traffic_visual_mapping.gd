extends SceneTree


func _init() -> void:
	call_deferred("_test")


func _fail(message: String) -> void:
	printerr("TRAFFIC VISUAL MAPPING TEST FAILED: " + message)
	quit(1)


func _test() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate()
	root.add_child(city)
	await process_frame
	var life := city.get_node("CityLife")
	life.call("_apply_sumo_state", {
		"source": "SUMO 1.25.0 TraCI",
		"simulated_vehicle_count": 1,
		"stopped_vehicle_count": 0,
		"turning_vehicle_count": 1,
		"vehicles": [{"id": "heading_check", "x": 12.0, "z": -8.0, "angle": 90.0, "speed": 9.0}],
		"signals": [{
			"id": "Junction_r3_c3",
			"approaches": {"north": "G", "south": "G", "east": "r", "west": "r"},
		}],
	})
	var mapped: Dictionary = life.get("_sumo_nodes_by_id")
	if not mapped.has("heading_check"):
		_fail("SUMO vehicle state was not mapped to a visual car")
		return
	var car_state := mapped["heading_check"] as Dictionary
	var car := car_state.node as Node3D
	# The imported vehicle nose is local +Z (Vector3.BACK). SUMO heading 90° is east.
	var nose := car.global_transform.basis * Vector3.BACK
	if nose.normalized().dot(Vector3.RIGHT) < 0.98:
		_fail("eastbound SUMO car is not facing east: %s" % nose)
		return
	# Packet delivery is discrete; a malformed long jump must be rejected rather
	# than rendered as a teleport or a backwards snap in third-person view.
	var before_smoothing := car.global_position
	life.call("_apply_sumo_state", {
		"source": "SUMO 1.25.0 TraCI",
		"simulated_vehicle_count": 1,
		"vehicles": [{"id": "heading_check", "x": 32.0, "z": -8.0, "angle": 90.0, "speed": 9.0}],
		"signals": [],
	})
	life.call("_update_sumo_traffic", 1.0 / 60.0)
	var smoothing_step := car.global_position.distance_to(before_smoothing)
	if smoothing_step > 0.01:
		_fail("visual renderer accepted an invalid 20 m packet jump: %.3f m" % smoothing_step)
		return

	for traffic_signal_value in get_nodes_in_group("traffic_signals"):
		var traffic_signal := traffic_signal_value as Node3D
		for signal_head_name in ["SignalModel", "SignalModelOpposite"]:
			if traffic_signal.get_node_or_null(signal_head_name) == null:
				_fail("%s is missing its authored opposite-corner right-angle traffic-light assembly" % traffic_signal.name)
				return
		if traffic_signal.get_node_or_null("SignalModelPerpendicular") != null or traffic_signal.get_node_or_null("SignalModelPerpendicularOpposite") != null:
			_fail("%s contains extra poles instead of exactly two opposite-corner right-angle assemblies" % traffic_signal.name)
			return
		if not traffic_signal.find_children("IntegratedLens_*", "MeshInstance3D", true, false).is_empty():
			_fail("traffic signal still contains generated marker geometry")
			return
		var authored_meshes := (traffic_signal.get_node("SignalModel") as Node3D).find_children("*", "MeshInstance3D", true, false)
		if authored_meshes.is_empty() or not (authored_meshes[0] as MeshInstance3D).visible:
			_fail("authored external traffic-light model is not visible")
			return
		if (authored_meshes[0] as MeshInstance3D).material_override != null:
			_fail("authored traffic-light mesh was replaced by a material override")
			return
	if get_nodes_in_group("traffic_signals").size() != 49:
		_fail("full-city traffic signal expansion did not create 49 visible controlled intersections")
		return
	var native_lenses: Dictionary = (life.get("_signal_lamps") as Dictionary).get("Junction_r3_c3", {})
	for approach in ["north", "south", "east", "west"]:
		var approach_lenses: Dictionary = native_lenses.get(approach, {})
		if (approach_lenses.get("red", []) as Array).is_empty() or (approach_lenses.get("amber", []) as Array).is_empty() or (approach_lenses.get("green", []) as Array).is_empty():
			_fail("%s does not expose separate native red, amber, and green lens faces" % approach)
			return
	_assert_phase(native_lenses, "north", "G")
	_assert_phase(native_lenses, "south", "G")
	_assert_phase(native_lenses, "east", "r")
	_assert_phase(native_lenses, "west", "r")
	# Yellow must illuminate the real middle amber lens, while the perpendicular
	# road stays red. This is the exact live SUMO axis transition.
	life.call("_apply_sumo_state", {
		"source": "SUMO 1.25.0 TraCI",
		"simulated_vehicle_count": 1,
		"vehicles": [{"id": "heading_check", "x": 12.1, "z": -8.0, "angle": 90.0, "speed": 9.0}],
		"signals": [{"id": "Junction_r3_c3", "approaches": {"north": "y", "south": "y", "east": "r", "west": "r"}}],
	})
	_assert_phase(native_lenses, "north", "y")
	_assert_phase(native_lenses, "south", "y")
	_assert_phase(native_lenses, "east", "r")
	_assert_phase(native_lenses, "west", "r")
	life.call("_apply_sumo_state", {
		"source": "SUMO 1.25.0 TraCI",
		"simulated_vehicle_count": 1,
		"vehicles": [{"id": "heading_check", "x": 12.2, "z": -8.0, "angle": 90.0, "speed": 9.0}],
		"signals": [{"id": "Junction_r3_c3", "approaches": {"north": "r", "south": "r", "east": "r", "west": "r"}}],
	})
	for approach in ["north", "south", "east", "west"]:
		_assert_phase(native_lenses, approach, "r")

	var wheel_before := 0.0
	if not car_state.wheels.is_empty():
		wheel_before = (car_state.wheels[0] as Node3D).rotation.x
	await create_timer(0.15).timeout
	if not car_state.wheels.is_empty() and is_equal_approx(wheel_before, (car_state.wheels[0] as Node3D).rotation.x):
		_fail("a moving SUMO vehicle did not rotate its wheel node")
		return
	print("TRAFFIC VISUAL MAPPING TEST PASSED: forward heading, rolling wheels, two opposite-corner right-angle signal assemblies, and SUMO-aligned red/amber/green phases are mapped correctly")
	quit(0)


func _assert_phase(native_lenses: Dictionary, approach: String, expected_state: String) -> void:
	var lenses := native_lenses[approach] as Dictionary
	var red := (lenses.red[0] as StandardMaterial3D).emission_enabled
	var amber := (lenses.amber[0] as StandardMaterial3D).emission_enabled
	var green := (lenses.green[0] as StandardMaterial3D).emission_enabled
	var expected := {"r": [true, false, false], "y": [false, true, false], "G": [false, false, true]}
	var state: Array = expected[expected_state]
	if red != bool(state[0]) or amber != bool(state[1]) or green != bool(state[2]):
		_fail("%s approach is not %s (red=%s amber=%s green=%s)" % [approach, expected_state, red, amber, green])
