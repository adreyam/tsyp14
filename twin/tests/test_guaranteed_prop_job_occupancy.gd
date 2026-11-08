extends SceneTree

const PHYSICAL_PROP_CLIPS := ["Interact", "PickUp_Table", "Farm_Harvest", "Farm_PlantSeed", "Farm_Watering", "InspectLow", "RepairMid", "RepairSurface", "OpenGroundContainer", "SetGroundItem", "OpenLatch", "PanelKnock", "InspectLabel"]
const REQUIRED_AT_ALL_TIMES := 10

func _init() -> void:
	call_deferred("_test")

func _fail(message: String) -> void:
	printerr("GUARANTEED PROP-JOB OCCUPANCY TEST FAILED: " + message)
	Engine.time_scale = 1.0
	quit(1)

func _test() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	root.add_child(city)
	await process_frame
	await create_timer(0.5).timeout
	var director := city.get_node("NpcAi")
	var minimum_active := 999
	var maximum_active := 0
	var observed_roles := {}
	var observed_props := {}
	var observed_clips := {}
	Engine.time_scale = 5.0
	for sample in 120:
		await create_timer(0.035, true, false, true).timeout
		var active := 0
		for state_value in director.call("get_agent_debug"):
			var state := state_value as Dictionary
			if not bool(state.prop_interaction_active):
				continue
			active += 1
			if not str(state.interaction_id).is_empty():
				_fail("%s counted a person interaction as prop work" % str(state.npc_id)); return
			if float(state.speed_mps) > 0.01 or not str(state.motion_state).begins_with("performing_"):
				_fail("%s counted walking/non-stationary work" % str(state.npc_id)); return
			if not PHYSICAL_PROP_CLIPS.has(str(state.animation_clip)):
				_fail("%s counted non-physical clip %s" % [str(state.npc_id), str(state.animation_clip)]); return
			if str(state.job_prop_kind).is_empty():
				_fail("%s has no physical job prop kind" % str(state.npc_id)); return
			observed_roles[str(state.role)] = true
			observed_props[str(state.job_prop_kind)] = true
			observed_clips[str(state.animation_clip)] = true
		minimum_active = mini(minimum_active, active)
		maximum_active = maxi(maximum_active, active)
		if active < REQUIRED_AT_ALL_TIMES:
			_fail("sample %d had only %d stationary physical-prop workers" % [sample, active]); return
	Engine.time_scale = 1.0
	var debug := director.call("get_spatial_debug") as Dictionary
	if int(debug.minimum_observed_prop_workers) < REQUIRED_AT_ALL_TIMES:
		_fail("director recorded a minimum of only %d" % int(debug.minimum_observed_prop_workers)); return
	if int(debug.safe_station_props) != int(debug.total_station_props):
		_fail("only %d/%d job stations have collision-safe props" % [int(debug.safe_station_props), int(debug.total_station_props)]); return
	if observed_roles.size() < 3 or observed_props.size() < 3 or observed_clips.size() < 4:
		_fail("variety too low: %d roles, %d props, %d physical poses" % [observed_roles.size(), observed_props.size(), observed_clips.size()]); return
	print("GUARANTEED PROP-JOB OCCUPANCY TEST PASSED: %d-%d stationary prop workers in every sample; %d roles, %d props, %d physical poses" % [minimum_active, maximum_active, observed_roles.size(), observed_props.size(), observed_clips.size()])
	quit(0)
