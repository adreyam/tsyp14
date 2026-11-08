extends SceneTree

## The smart car parks as the city shows them: every sited lot exists, the board reads "occupied/capacity", a full or closed lot
## says so, the barrier arm lifts while a car passes, the red warning triangle shows while a car is turned away, and the device
## interface reports every lot. The bridge side is tested against real SUMO in scripts/test_parking_lots.py.

const Zones := preload("res://scripts/city_zones.gd")
const LOT_SIDE_CLEARANCE_FOR_TEST := 11.5

func _init() -> void:
	call_deferred("_run")

func _fail(message: String) -> void:
	push_error("PARKING LOT TEST FAILED: " + message)
	print("PARKING LOT TEST FAILED: " + message)
	quit(1)

func _run() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	root.add_child(city)
	for i in 10:
		await process_frame
	var presenter := city.get_node("ParkingLots")
	var lots := presenter.get("lots") as Dictionary
	var sited := (JSON.parse_string(FileAccess.get_file_as_string("res://sumo/parking_lots.json")) as Dictionary).lots as Array
	if lots.size() != sited.size() or lots.size() < 6:
		_fail("%d lots built for %d sited" % [lots.size(), sited.size()])
		return
	var zones := {}
	for lot_id in lots:
		var lot := lots[lot_id] as Dictionary
		var node := lot.node as Node3D
		var gate := Vector3(float(lot.data.x), 0.17, float(lot.data.z))
		if Zones.zone_of_position(gate) != str(lot.data.zone):
			_fail("%s is not in its own zone %s" % [lot_id, lot.data.zone])
			return
		zones[str(lot.data.zone)] = true
		for part in ["Surface", "Driveway", "BayLines", "PostLeft", "ArmHinge", "BoardPlate", "WarningTriangle"]:
			if node.find_child(part, true, false) == null:
				_fail("%s lacks %s" % [lot_id, part])
				return
		if node.global_position.distance_to(gate) > 40.0:
			_fail("%s is not next to its barrier" % lot_id)
			return
	if zones.size() < 6:
		_fail("lots cover only %d zones" % zones.size())
		return
	# SUMO ownership transfer must be position/heading continuous.  The entry
	# curve starts from the exact rendered handoff pose, not a reconstructed
	# lane point that can create a sideways correction before turning.
	var continuity_lot := lots[str(lots.keys()[0])] as Dictionary
	var continuity_edge := continuity_lot.edge_info as Dictionary
	var continuity_start := presenter.call("world", continuity_lot, -6.0, 2.25) as Vector3
	continuity_start.y = 0.17
	var continuity_yaw := atan2((continuity_edge.direction as Vector3).x, (continuity_edge.direction as Vector3).z)
	var handoff_poses := presenter.get("_handoff_poses") as Dictionary
	handoff_poses["continuity_probe"] = {"centre": continuity_start, "yaw": continuity_yaw}
	var continuity_path := presenter.call("entry_path_for", "continuity_probe", continuity_lot, 2) as Array
	var continuity_pose := presenter.call("pose_on", continuity_path, 0.0) as Dictionary
	var continuity_heading := Vector3(sin(float(continuity_pose.yaw)), 0.0, cos(float(continuity_pose.yaw)))
	if (continuity_pose.p as Vector3).distance_to(continuity_start) > 0.01 or continuity_heading.dot(continuity_edge.direction as Vector3) < 0.94:
		_fail("parking handoff is not continuous: %s" % continuity_pose)
		return
	var gate_point := presenter.call("world", continuity_lot, 0.0, 12.6) as Vector3
	var gate_index := 0
	var gate_distance := INF
	for point_index in continuity_path.size():
		var distance_to_gate := ((continuity_path[point_index] as Dictionary).p as Vector3).distance_to(gate_point)
		if distance_to_gate < gate_distance:
			gate_distance = distance_to_gate
			gate_index = point_index
	if gate_distance > 0.05 or gate_index < 3:
		_fail("parking entry does not approach and steer through its real gate: %.2f m, index %d" % [gate_distance, gate_index])
		return
	# Steering must be visibly underway during the early approach. A long first
	# Bezier handle can remain tangent mathematically while making the body look
	# as if it translates and only turns afterward.
	var early_turn_pose := presenter.call("pose_on", continuity_path, 0.15) as Dictionary
	var early_heading := Vector3(sin(float(early_turn_pose.yaw)), 0.0, cos(float(early_turn_pose.yaw)))
	if early_heading.dot(continuity_edge.direction as Vector3) > 0.96:
		_fail("parking entry postpones visible steering until after horizontal translation: %s" % early_turn_pose)
		return
	# The body must steer along the path throughout the manoeuvre. A lateral
	# translation followed by a late yaw (the former visible slide/snap) makes
	# the body heading disagree with its next frame's displacement.
	var previous_entry_pose := continuity_pose
	for sample in range(1, 241):
		var entry_pose := presenter.call("pose_on", continuity_path, float(sample) / 240.0) as Dictionary
		var displacement := (entry_pose.p as Vector3) - (previous_entry_pose.p as Vector3)
		if displacement.length() > 0.0001:
			var body_heading := Vector3(sin(float(entry_pose.yaw)), 0.0, cos(float(entry_pose.yaw)))
			if body_heading.dot(displacement.normalized()) < 0.94:
				_fail("parking car slides sideways before steering at sample %d: %s" % [sample, entry_pose])
				return
		previous_entry_pose = entry_pose
	# A late network packet must never rewind the presenter clock. Rewinding was
	# responsible for the repeated forward/back jitter along an otherwise valid
	# curve.
	presenter.set("_motion_time_initialized", true)
	presenter.set("_motion_time", 100.20)
	presenter.call("_on_state", {"time": 100.10, "operator": {"paused": false}, "vehicles": [], "parking": {"lots": []}})
	if float(presenter.call("sim_now")) < 100.20:
		_fail("a late parking packet rewound the rendered car")
		return
	presenter.set("_motion_time_initialized", false)
	presenter.set("_motion_time", 0.0)
	# Exercise the actual presenter transform pipeline across the complete timed
	# entry. The car must advance on intermediate frames and the backend phase
	# change to `alighting` must not be the frame that teleports it into its bay.
	var motion_key := "parking_motion_probe"
	var motion_handoffs := presenter.get("_handoff_poses") as Dictionary
	motion_handoffs[motion_key] = {"centre": continuity_start, "yaw": continuity_yaw}
	var motion_packet := {"time": 200.0, "operator": {"paused": true}, "vehicles": [], "parking": {"time": 200.0, "enter_s": 6.5, "leave_s": 6.5, "lots": [
		{"id": str(continuity_lot.data.id), "capacity": 12, "occupied": 1, "barrier_mode": "auto", "gate_open": true, "warning": false, "cars": [{"id": motion_key, "slot": 2, "phase": "entering", "t0": 200.0, "cls": "car"}]},
	]}}
	presenter.call("_on_state", motion_packet)
	var motion_nodes := presenter.get("_nodes") as Dictionary
	var motion_node := motion_nodes.get(motion_key) as Node3D
	if motion_node == null:
		_fail("timed parking entry did not acquire a visible vehicle")
		return
	var motion_positions := PackedVector3Array()
	for motion_sample in range(66):
		presenter.set("_motion_time", 200.0 + float(motion_sample) * 0.1)
		presenter.call("_process", 0.1)
		motion_positions.append((presenter.get("_nodes") as Dictionary)[motion_key].global_position)
	if not motion_node.is_in_group("npc_dynamic_obstacles"):
		_fail("a car crossing the parking entrance is invisible to pedestrian avoidance")
		return
	var moving_frames := 0
	var largest_motion_step := 0.0
	for motion_index in range(1, motion_positions.size()):
		var motion_step := motion_positions[motion_index].distance_to(motion_positions[motion_index - 1])
		largest_motion_step = maxf(largest_motion_step, motion_step)
		moving_frames += int(motion_step > 0.01)
	if moving_frames < 45 or largest_motion_step > 1.2:
		_fail("parking entry did not move continuously (moving frames %d/65, largest step %.2f m)" % [moving_frames, largest_motion_step])
		return
	(motion_packet.parking.lots[0].cars[0] as Dictionary).phase = "alighting"
	presenter.call("_on_state", motion_packet)
	presenter.call("_process", 0.0)
	if motion_node.global_position.distance_to(motion_positions[-1]) > 0.05:
		_fail("parking car teleported when entering changed to alighting")
		return
	if motion_node.is_in_group("npc_dynamic_obstacles"):
		_fail("a stationary parked car kept blocking the pedestrian network")
		return
	(city.get_node("CityLife") as Node).call("release_keyed_vehicle", motion_key)
	motion_nodes.erase(motion_key)
	motion_handoffs.erase(motion_key)
	presenter.set("_motion_time_initialized", false)
	presenter.set("_motion_time", 0.0)
	# Parking citizens use a real SocietyKernel identity and the same city
	# sidewalk graph as ordinary citizens; their destination cannot be a fake
	# waiting point beside the lot.
	var npc := city.get_node("NpcAi") as Node
	var first_lot := lots[str(lots.keys()[0])] as Dictionary
	var driver_origin := presenter.call("world", first_lot, 0.0, 18.0) as Vector3
	var citizen_plan := npc.call("parking_citizen_plan", "citizen_0033", driver_origin) as Dictionary
	var citizen_path := citizen_plan.get("outbound_path", PackedVector3Array()) as PackedVector3Array
	if citizen_plan.is_empty() or citizen_path.size() < 3 or (citizen_plan.destination as Vector3).distance_to(driver_origin) < 15.0:
		_fail("parking citizen was not routed to a real city activity: %s" % citizen_plan)
		return
	var outer_connector := npc.call("_sidewalk_connector", Vector3(-300.0, 0.17, -370.6), Vector3(-180.6, 0.17, -180.6)) as PackedVector3Array
	for connector_index in outer_connector.size() - 1:
		var connector_delta := outer_connector[connector_index + 1] - outer_connector[connector_index]
		if absf(connector_delta.x) > 0.05 and absf(connector_delta.z) > 0.05:
			_fail("outer parking connector cuts diagonally through a road: %s" % outer_connector)
			return
	# Feed the presenter states exactly as the bridge publishes them.
	var first := str(lots.keys()[0])
	var second := str(lots.keys()[1])
	var cars: Array = []
	for slot in 5:
		cars.append({"id": "civ_%d" % slot, "slot": slot, "phase": "alighting" if slot == 0 else "parked", "t0": 49.9 if slot == 0 else 10.0, "cls": "car"})
	var packet := {"time": 50.0, "operator": {"paused": true}, "vehicles": [], "parking": {"time": 50.0, "enter_s": 6.5, "leave_s": 6.5, "lots": [
		{"id": first, "capacity": 12, "occupied": 5, "barrier_mode": "auto", "gate_open": false, "warning": false, "denied_total": 0, "entered_total": 5, "left_total": 0, "redirected_total": 0, "incoming": 1, "cars": cars},
		{"id": second, "capacity": 12, "occupied": 12, "barrier_mode": "auto", "gate_open": true, "warning": true, "denied_total": 2, "entered_total": 12, "left_total": 0, "redirected_total": 1, "incoming": 0, "cars": []},
	]}}
	presenter.call("_on_state", packet)
	for i in 40:
		await process_frame
	# The alighting citizen must clear the door/bay and enter the aisle route;
	# this specifically guards against parked cars blocking their own driver.
	var drivers := presenter.get("_drivers") as Dictionary
	if not drivers.has("civ_0"):
		_fail("alighting car did not create its SocietyKernel driver")
		return
	var driver_record := drivers["civ_0"] as Dictionary
	var driver_agent := driver_record.agent as Node
	var driver_start := driver_agent.call("position") as Vector3
	for i in 120:
		await create_timer(0.02).timeout
	var driver_after := driver_agent.call("position") as Vector3
	if driver_after.distance_to(driver_start) < 1.0 or not bool(driver_record.get("alighted", false)):
		_fail("parking citizen remained stuck beside the car (moved %.2f m)" % driver_after.distance_to(driver_start))
		return
	var outbound := driver_record.get("outbound_path", PackedVector3Array()) as PackedVector3Array
	var driver_lot := lots[first] as Dictionary
	var side_local := (driver_lot.node as Node3D).to_local(outbound[2])
	if absf(side_local.x) < LOT_SIDE_CLEARANCE_FOR_TEST:
		_fail("parking citizen still uses the vehicle barrier instead of the side exit: %s" % outbound)
		return
	var recovery := presenter.call("_return_path_from", driver_record, outbound[mini(3, outbound.size() - 1)]) as PackedVector3Array
	if recovery.size() < 2 or recovery[recovery.size() - 1].distance_to(outbound[0]) > 0.1:
		_fail("mid-route citizen cannot recover a route back to the parked car")
		return
	var board_first := ((lots[first] as Dictionary).count_label as Label3D)
	var board_second := ((lots[second] as Dictionary).count_label as Label3D)
	if board_first.text != "05/12":
		_fail("the board reads '%s', expected 05/12" % board_first.text)
		return
	if board_second.text != "12/12" or ((lots[second] as Dictionary).title_label as Label3D).text != "FULL":
		_fail("a full lot does not say FULL: '%s' / '%s'" % [board_second.text, ((lots[second] as Dictionary).title_label as Label3D).text])
		return
	if board_second.modulate.r < 0.9 or board_second.modulate.g > 0.4 or board_first.modulate.g < 0.9:
		_fail("board colours: full lot %s, roomy lot %s" % [board_second.modulate, board_first.modulate])
		return
	var arm_open := float((lots[second] as Dictionary).arm_angle)
	var arm_shut := float((lots[first] as Dictionary).arm_angle)
	if arm_open < 1.0 or arm_shut > 0.05:
		_fail("barrier arms: open lot %.2f rad, shut lot %.2f rad" % [arm_open, arm_shut])
		return
	var triangle_seen := false
	for i in 60:
		await create_timer(0.02).timeout
		triangle_seen = triangle_seen or ((lots[second] as Dictionary).triangle as Node3D).visible
		if ((lots[first] as Dictionary).triangle as Node3D).visible:
			_fail("a lot with no refused car shows the warning triangle")
			return
	if not triangle_seen:
		_fail("the warning triangle never showed over the refused car's barrier")
		return
	# A closed barrier says CLOSED.
	packet.parking.lots[0].barrier_mode = "closed"
	presenter.call("_on_state", packet)
	await create_timer(0.2).timeout
	if board_first.text != "CLOSED":
		_fail("a closed lot reads '%s'" % board_first.text)
		return
	var report := presenter.call("lot_report", first) as Dictionary
	if int(report.free) != 7 or str(report.barrier_mode) != "closed" or (report.cars as Array).size() != 5:
		_fail("lot report is wrong: %s" % JSON.stringify(report))
		return
	# Parked cars follow the paths of their bays and stay on the lot surface.
	var path := presenter.call("path_for", lots[first], 4, true) as Array
	var end_pose := presenter.call("pose_on", path, 1.0) as Dictionary
	var surface := ((lots[first] as Dictionary).node as Node3D).find_child("Surface", true, false) as MeshInstance3D
	if surface.to_local(end_pose.p as Vector3).abs().x > 11.0:
		_fail("bay 4 is outside the lot surface")
		return
	print("PARKING LOT TEST PASSED: %d lots in %d zones, boards, barrier arms, warning triangle, closed state and reports verified" % [lots.size(), zones.size()])
	quit(0)
