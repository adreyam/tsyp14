extends SceneTree

const MINIMUM_SEPARATION := 1.35
const REJECTED_SOLO_CLIPS := ["Idle_Neutral", "Idle", "Idle_Talking", "Yes"]


func _initialize() -> void:
	call_deferred("_run")


func _fail(message: String) -> void:
	Engine.time_scale = 1.0
	printerr("CITIZEN ACTIVITY QUALITY TEST FAILED: " + message)
	quit(1)


func _stage_for(clip: String) -> String:
	var stages := {
		"Chest_Open":"accessing_container", "OpenGroundContainer":"accessing_container", "OpenLatch":"opening_fixture",
		"PickUp_Table":"handling_item", "Farm_Harvest":"collecting_material", "SetGroundItem":"placing_item",
		"Farm_PlantSeed":"installing_material", "Farm_Watering":"applying_material",
		"RepairMid":"performing_repair", "RepairSurface":"performing_repair",
		"InspectLow":"close_inspection", "InspectLabel":"verifying_result", "PanelKnock":"testing_fixture",
		"RecordReading":"recording_result", "Idle_TalkingPhone_Loop":"recording_result",
		"Interact":"operating_fixture", "Consume":"using_personal_item", "PresentWork":"presenting_result"
	}
	return str(stages.get(clip, ""))


func _run() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	root.add_child(city)
	await process_frame
	await create_timer(0.45).timeout
	var director := city.get_node("NpcAi") as Node
	var agents := director.get("_agents") as Array
	var stations := director.get("_stations") as Dictionary
	if stations.size() != 64:
		_fail("expected 64 role-owned work stations, got %d" % stations.size())
		return
	# Static authoring gate: every job site must contain a sequence, not a lone
	# look/idle pose, and every stage must exist on its assigned production rig.
	for station_value in stations.values():
		var station := station_value as Dictionary
		var clips := director.call("_readable_station_clips", station) as Array
		var authored_stages := {}
		if clips.size() < 3:
			_fail("%s has only %d readable work clips" % [str(station.id), clips.size()])
			return
		var owner := agents[int(station.owner)] as Dictionary
		var player := owner.animation as AnimationPlayer
		for clip_value in clips:
			var clip := str(clip_value)
			var stage := _stage_for(clip)
			if stage.is_empty():
				_fail("%s contains an unclassified work clip %s" % [str(station.id), clip])
				return
			authored_stages[stage] = true
			if str(director.call("_find_clip", player, clip)).is_empty():
				_fail("%s lacks authored clip %s on its assigned character" % [str(station.id), clip])
				return
		if authored_stages.size() < 3:
			_fail("%s does not contain three distinct work stages: %s" % [str(station.id), str(authored_stages.keys())])
			return

	var initial_pairs := {}
	var initial_positions := {}
	for state_value in director.call("get_agent_debug"):
		var state := state_value as Dictionary
		if not str(state.interaction_id).is_empty():
			initial_pairs[str(state.citizen_id)] = str(state.interaction_id)
			initial_positions[str(state.citizen_id)] = (state.node as Node3D).global_position
	if initial_pairs.size() != 12:
		_fail("expected twelve citizens in six purposeful opening exchanges")
		return

	var last_positions := {}
	var last_directions := {}
	var reversals := {}
	var yield_runs := {}
	var longest_yield := {}
	var observed_work_stages := {}
	var observed_hand_items := {}
	var observed_tools := {}
	var interaction_turns := {}
	var interaction_holders := {}
	var inert_samples := 0
	var minimum_separation := INF
	var minimum_prop_workers := 999
	Engine.time_scale = 5.0
	for frame in 360:
		await physics_frame
		if frame % 4 != 0:
			continue
		var states := director.call("get_agent_debug") as Array
		var active_prop_workers := 0
		for first in states.size():
			var first_node := (states[first] as Dictionary).node as Node3D
			for second in range(first + 1, states.size()):
				minimum_separation = minf(minimum_separation, first_node.global_position.distance_to(((states[second] as Dictionary).node as Node3D).global_position))
		for state_value in states:
			var state := state_value as Dictionary
			var citizen_id := str(state.citizen_id)
			var interaction_id := str(state.interaction_id)
			var motion := str(state.motion_state)
			if bool(state.prop_interaction_active):
				active_prop_workers += 1
			if motion.begins_with("performing_"):
				if not bool(state.stationary_action_readable):
					inert_samples += 1
				if REJECTED_SOLO_CLIPS.has(str(state.animation_clip)):
					_fail("%s is performing solo work with rejected inert clip %s" % [citizen_id, str(state.animation_clip)])
					return
				if str(state.work_stage).is_empty():
					_fail("%s reports work without a concrete workflow stage" % citizen_id)
					return
				var sequence_key := "%s|%s" % [citizen_id, str(state.task)]
				var stages := observed_work_stages.get(sequence_key, {}) as Dictionary
				stages[str(state.work_stage)] = true
				observed_work_stages[sequence_key] = stages
				if not str(state.active_hand_item_kind).is_empty(): observed_hand_items[str(state.active_hand_item_kind)] = true
				if not str(state.active_tool_kind).is_empty(): observed_tools[str(state.active_tool_kind)] = true
			if not interaction_id.is_empty():
				var phase_key := "%s|%d" % [interaction_id, int(state.interaction_phase)]
				var turns := interaction_turns.get(phase_key, {}) as Dictionary
				turns[str(state.interaction_turn)] = true
				interaction_turns[phase_key] = turns
				if not str(state.active_hand_item_kind).is_empty():
					var holders := interaction_holders.get(phase_key, {}) as Dictionary
					holders[citizen_id] = true
					interaction_holders[phase_key] = holders
			if initial_pairs.has(citizen_id):
				var position := (state.node as Node3D).global_position
				if last_positions.has(citizen_id):
					var displacement := position - (last_positions[citizen_id] as Vector3)
					displacement.y = 0.0
					if displacement.length() > 0.025:
						var direction := displacement.normalized()
						if last_directions.has(citizen_id) and direction.dot(last_directions[citizen_id] as Vector3) < -0.35:
							reversals[citizen_id] = int(reversals.get(citizen_id, 0)) + 1
						last_directions[citizen_id] = direction
				last_positions[citizen_id] = position
				if motion.begins_with("yielding_"):
					yield_runs[citizen_id] = int(yield_runs.get(citizen_id, 0)) + 1
					longest_yield[citizen_id] = maxi(int(longest_yield.get(citizen_id, 0)), int(yield_runs[citizen_id]))
				else:
					yield_runs[citizen_id] = 0
		minimum_prop_workers = mini(minimum_prop_workers, active_prop_workers)
	Engine.time_scale = 1.0

	if inert_samples != 0:
		_fail("sampled %d unreadable stationary work states" % inert_samples)
		return
	if minimum_prop_workers < 10:
		_fail("physical prop work dropped below ten citizens (%d)" % minimum_prop_workers)
		return
	if minimum_separation < MINIMUM_SEPARATION:
		_fail("citizens crossed the %.2f m separation floor (%.2f m)" % [MINIMUM_SEPARATION, minimum_separation])
		return
	var complete_runtime_sequences := 0
	for stage_value in observed_work_stages.values():
		if (stage_value as Dictionary).size() >= 3:
			complete_runtime_sequences += 1
	if complete_runtime_sequences < 10:
		_fail("only %d sampled citizens completed readable three-stage work sequences" % complete_runtime_sequences)
		return
	if observed_hand_items.size() < 4 or observed_tools.size() < 3:
		_fail("insufficient handled-item/tool variety: items=%s tools=%s" % [str(observed_hand_items.keys()), str(observed_tools.keys())])
		return
	if interaction_turns.size() != 18 or interaction_holders.size() != 18:
		_fail("six exchanges did not expose all three turn/item phases: turns=%d holders=%d" % [interaction_turns.size(), interaction_holders.size()])
		return
	for phase_key in interaction_turns.keys():
		var turns := interaction_turns[phase_key] as Dictionary
		var holders := interaction_holders.get(phase_key, {}) as Dictionary
		if not turns.has("speaking") or not turns.has("listening") or holders.size() != 1:
			_fail("%s lacks one speaker, one listener, or one tangible item holder" % str(phase_key))
			return
	var transferred_interactions := 0
	for interaction_id_value in initial_pairs.values():
		var interaction_id := str(interaction_id_value)
		var phase_zero := interaction_holders.get("%s|0" % interaction_id, {}) as Dictionary
		var phase_one := interaction_holders.get("%s|1" % interaction_id, {}) as Dictionary
		if phase_zero.keys() != phase_one.keys():
			transferred_interactions += 1
	# initial_pairs contains each interaction twice.
	if transferred_interactions < 8:
		_fail("fewer than four exchanges visibly transferred item custody")
		return
	var final_states := director.call("get_agent_debug") as Array
	var released := 0
	var dispersed := 0
	for state_value in final_states:
		var state := state_value as Dictionary
		var citizen_id := str(state.citizen_id)
		if not initial_pairs.has(citizen_id):
			continue
		if str(state.interaction_id).is_empty():
			released += 1
		if (state.node as Node3D).global_position.distance_to(initial_positions[citizen_id] as Vector3) > 2.0:
			dispersed += 1
		if int(reversals.get(citizen_id, 0)) > 1:
			_fail("%s reversed %d times after its exchange" % [citizen_id, int(reversals[citizen_id])])
			return
		if int(longest_yield.get(citizen_id, 0)) > 6:
			_fail("%s remained yielded for %d samples" % [citizen_id, int(longest_yield[citizen_id])])
			return
	if released != 12 or dispersed != 12:
		_fail("opening exchanges did not release and disperse all citizens: released=%d dispersed=%d" % [released, dispersed])
		return
	var society_snapshot := director.call("snapshot_society_state") as Dictionary
	var social_events := society_snapshot.get("social_events", {}) as Dictionary
	var population := society_snapshot.get("population", {}) as Dictionary
	if social_events.size() != 6:
		_fail("persistent society state does not contain all six exchanges")
		return
	for event_value in social_events.values():
		var event := event_value as Dictionary
		if str(event.status) != "completed" or (event.get("custody_history", []) as Array).size() != 3:
			_fail("%s did not persist its three communication/custody phases" % str(event.event_id))
			return
		var final_owner_id := str(event.item_owner_id)
		var inventory := (population[final_owner_id] as Dictionary).get("carried_items", []) as Array
		var persisted_item := false
		for item_value in inventory:
			if str((item_value as Dictionary).get("acquired_from_event_id", "")) == str(event.event_id):
				persisted_item = true
		if not persisted_item:
			_fail("%s did not persist the final item with its recipient" % str(event.event_id))
			return
	for pair_value in director.get("_pairs") as Array:
		var pair := pair_value as Dictionary
		for item_key in ["a_item", "b_item"]:
			var item := pair.get(item_key) as Node3D
			# The exchanged object is set down on the shared prop, never left
			# stuck to a hand once the exchange is over.
			if is_instance_valid(item) and item.visible and str(item.get_meta("npc_carry", "right_hand")) != "rest":
				_fail("completed exchange left %s attached to a hand" % str(item.name))
				return
	print("CITIZEN_ACTIVITY_QUALITY_TEST_OK stations=64 complete_runtime_sequences=%d item_kinds=%d tool_kinds=%d pair_phases=18 transferred_exchanges=%d persisted_custody_events=6 minimum_separation=%.2f released=12 dispersed=12" % [complete_runtime_sequences, observed_hand_items.size(), observed_tools.size(), int(transferred_interactions / 2), minimum_separation])
	quit(0)
