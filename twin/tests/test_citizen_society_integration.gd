extends SceneTree


func _init() -> void:
	call_deferred("_test")


func _fail(message: String) -> void:
	printerr("CITIZEN SOCIETY INTEGRATION TEST FAILED: " + message)
	Engine.time_scale = 1.0
	quit(1)


func _test() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	root.add_child(city)
	await process_frame
	await create_timer(0.8).timeout
	var director := city.get_node("NpcAi")
	var summary := director.call("get_society_debug") as Dictionary
	if int(summary.population) != 200 or int(summary.households) != 80 or int(summary.institutions) != 20:
		_fail("authoritative world is incomplete: %s" % str(summary))
		return
	if int(summary.visible_citizens) != 32 or int(summary.presentation_workers) != 10:
		_fail("visible population/presentation workforce was not bound to society state")
		return
	if int(summary.relationship_edges) < 190 or int(summary.social_events) != 6:
		_fail("relationships or causal shared events are missing")
		return
	if int(summary.trips_total) != 4 or int(summary.trips_active) != 4:
		_fail("the four opening commuters did not enter persistent trip state: %s" % str(summary))
		return
	if not (summary.invariant_errors as Array).is_empty():
		_fail("SocietyKernel reported initial invariant failures: %s" % str(summary.invariant_errors))
		return
	var states := director.call("get_agent_debug") as Array
	var causal_prop_workers := 0
	var presentation_workers := 0
	var social_participants := 0
	var interaction_roles := {}
	for state_value in states:
		var state := state_value as Dictionary
		if str(state.citizen_id).is_empty() or str(state.household_id).is_empty():
			_fail("%s has no persistent citizen/household identity" % str(state.npc_id))
			return
		if str(state.decision_reason).is_empty():
			_fail("%s has no inspectable action cause" % str(state.citizen_id))
			return
		if bool(state.presentation_worker):
			presentation_workers += 1
		if bool(state.prop_interaction_active):
			var order := state.get("active_work_order", {}) as Dictionary
			if str(state.work_order_id).is_empty() or order.is_empty():
				_fail("%s performs physical work without an institution work order" % str(state.citizen_id))
				return
			if not str(order.get("status", "")) in ["assigned", "in_progress"]:
				_fail("%s has invalid active order state %s" % [str(state.citizen_id), str(order.get("status", ""))])
				return
			causal_prop_workers += 1
		if not str(state.interaction_id).is_empty():
			if str(state.social_event_id).is_empty() or (state.get("active_social_event", {}) as Dictionary).is_empty():
				_fail("%s performs a person interaction without a causal social event" % str(state.citizen_id))
				return
			social_participants += 1
			interaction_roles[str(state.interaction_id) + ":" + str(state.role)] = true
	if presentation_workers != 10:
		_fail("expected ten persistent presentation workers, got %d" % presentation_workers)
		return
	if causal_prop_workers < 10:
		_fail("only %d stationary prop workers have causal work orders" % causal_prop_workers)
		return
	if social_participants != 12:
		_fail("expected 12 citizens in six institution/social events, got %d" % social_participants)
		return
	if not interaction_roles.has("route_assistance:local resident"):
		_fail("route assistance did not dynamically select a resident traveller")
		return
	var completed_before := int(summary.work_orders_completed)
	Engine.time_scale = 8.0
	await create_timer(6.5, true, false, true).timeout
	Engine.time_scale = 1.0
	summary = director.call("get_society_debug") as Dictionary
	if int(summary.work_orders_completed) <= completed_before:
		_fail("visible animation completion did not commit work-order effects")
		return
	if int(summary.social_events_completed) != 6 or int(summary.social_events_active) != 0:
		_fail("shared events remained an infinite animation loop instead of committing relationship history")
		return
	var visible_interaction_props := 0
	for prop_value in get_nodes_in_group("npc_interaction_props"):
		if (prop_value as Node3D).visible:
			visible_interaction_props += 1
	if visible_interaction_props != 0:
		_fail("%d completed shared-event props remained visibly staged" % visible_interaction_props)
		return
	var spatial := director.call("get_spatial_debug") as Dictionary
	if int(spatial.active_prop_workers) < 10 or int(spatial.minimum_observed_prop_workers) < 10:
		_fail("causal work-order turnover broke the physical-prop occupancy invariant")
		return
	var events := director.call("get_society_recent_events", 400) as Array
	var saw_completion := false
	for event_value in events:
		if str((event_value as Dictionary).kind) == "work_order_completed":
			saw_completion = true
			break
	if not saw_completion:
		_fail("no completion event was written to the authoritative ledger")
		return
	if not (summary.invariant_errors as Array).is_empty():
		_fail("work turnover produced invariant failures: %s" % str(summary.invariant_errors))
		return
	var snapshot := director.call("snapshot_society_state") as Dictionary
	if snapshot.is_empty() or (snapshot.population as Dictionary).size() != 200:
		_fail("persistent save-state snapshot is incomplete")
		return
	print("CITIZEN_SOCIETY_INTEGRATION_TEST_OK population=%d households=%d relationships=%d causal_prop_workers=%d completed_orders=%d social_participants=%d trips=%d/%d" % [
		int(summary.population),
		int(summary.households),
		int(summary.relationship_edges),
		causal_prop_workers,
		int(summary.work_orders_completed),
		social_participants,
		int(summary.trips_completed),
		int(summary.trips_total)
	])
	quit(0)
