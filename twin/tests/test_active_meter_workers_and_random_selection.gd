extends SceneTree

const REQUIRED_PROP_WORKERS := 10
const REQUIRED_METER_WORKERS := 5
const RANDOM_SELECTION_SAMPLES := 48


func _init() -> void:
	call_deferred("_test")


func _fail(message: String) -> void:
	printerr("ACTIVE METER/SELECTION TEST FAILED: " + message)
	Engine.time_scale = 1.0
	quit(1)


func _test() -> void:
	var control_room := (load("res://scenes/ControlRoom.tscn") as PackedScene).instantiate()
	root.add_child(control_room)
	await process_frame
	await process_frame
	await create_timer(0.8).timeout
	var city := control_room.get("_city") as Node3D
	if city == null:
		_fail("control room did not instantiate the live City scene")
		return
	var director := city.get_node_or_null("NpcAi")
	if director == null:
		_fail("citizen director is unavailable")
		return
	if get_nodes_in_group("power_service_meters").size() != 16 or get_nodes_in_group("water_service_meters").size() != 16:
		_fail("the city does not expose all 16 power and 16 water service meters")
		return
	var meter_work_props := get_nodes_in_group("npc_meter_work_props")
	if meter_work_props.size() < 8:
		_fail("only %d explicit meter smart objects were authored" % meter_work_props.size())
		return
	for prop_value in meter_work_props:
		var prop := prop_value as Node3D
		if prop == null or not str(prop.get_meta("activity_prop_kind", "")).ends_with("_meter") or bool(prop.get_meta("power_pole_job_eligible", true)):
			_fail("%s is not an explicit non-pole meter work prop" % str(prop))
			return
	var minimum_prop_workers := 999
	var minimum_meter_workers := 999
	var minimum_power_workers := 999
	var minimum_water_workers := 999
	Engine.time_scale = 5.0
	for _sample in 120:
		await create_timer(0.035, true, false, true).timeout
		var total := 0
		var meters := 0
		var power := 0
		var water := 0
		for state_value in director.call("get_agent_debug"):
			var state := state_value as Dictionary
			if not bool(state.prop_interaction_active):
				continue
			total += 1
			var prop_kind := str(state.job_prop_kind)
			if prop_kind.contains("pole"):
				_fail("%s was assigned to a pole instead of a service asset" % str(state.display_name))
				return
			if prop_kind == "power_meter":
				meters += 1
				power += 1
			elif prop_kind == "water_meter":
				meters += 1
				water += 1
		minimum_prop_workers = mini(minimum_prop_workers, total)
		minimum_meter_workers = mini(minimum_meter_workers, meters)
		minimum_power_workers = mini(minimum_power_workers, power)
		minimum_water_workers = mini(minimum_water_workers, water)
	Engine.time_scale = 1.0
	if minimum_prop_workers < REQUIRED_PROP_WORKERS:
		_fail("physical prop workers dropped to %d" % minimum_prop_workers)
		return
	if minimum_meter_workers < REQUIRED_METER_WORKERS or minimum_power_workers < 2 or minimum_water_workers < 2:
		_fail("meter staffing dropped to %d total (%d power, %d water)" % [minimum_meter_workers, minimum_power_workers, minimum_water_workers])
		return
	var camera := city.get_node_or_null("EstablishingCamera") as Camera3D
	if camera == null:
		_fail("city follow camera is unavailable")
		return
	var selected_kinds := {}
	for _selection in RANDOM_SELECTION_SAMPLES:
		control_room.call("_follow_random_person")
		await process_frame
		var selected := camera.call("get_selected_target") as Node3D
		if selected == null:
			_fail("random active-citizen follow returned no target")
			return
		var state := director.call("get_agent_state", selected) as Dictionary
		var motion_state := str(state.get("motion_state", ""))
		if float(state.get("speed_mps", 0.0)) > 0.01 or motion_state.begins_with("walking") or motion_state.begins_with("travelling") or motion_state.begins_with("yielding"):
			_fail("random follow selected walking citizen %s in %s" % [str(state.get("display_name", selected.name)), motion_state])
			return
		var tangible := bool(state.get("prop_interaction_active", false)) or not str(state.get("interaction_id", "")).is_empty() or not str(state.get("active_tool_kind", "")).is_empty() or not str(state.get("active_hand_item_kind", "")).is_empty()
		if not tangible or not bool(state.get("stationary_action_readable", false)):
			_fail("random follow selected an inert citizen rather than a readable action")
			return
		selected_kinds["interaction" if not str(state.get("interaction_id", "")).is_empty() else str(state.get("job_prop_kind", "tool_or_item"))] = true
	print("ACTIVE_METER_RANDOM_SELECTION_TEST_OK prop_workers_min=%d meter_workers_min=%d power_min=%d water_min=%d random_nonwalking=%d selected_activity_kinds=%d" % [minimum_prop_workers, minimum_meter_workers, minimum_power_workers, minimum_water_workers, RANDOM_SELECTION_SAMPLES, selected_kinds.size()])
	quit(0)
