extends SceneTree

const SocietyKernel := preload("res://scripts/society_kernel.gd")
const ROLE_SEQUENCE := [
	"local resident", "public information technician", "clinic assistant", "road maintenance technician",
	"parcel courier", "local resident", "parks worker", "transit steward",
	"market stocker", "local resident", "sanitation worker", "parcel courier",
	"local resident", "clinic assistant", "market stocker", "utilities technician",
	"public information technician", "horticulture specialist", "parcel courier", "road maintenance technician",
	"transit steward", "local resident", "building maintenance technician", "parcel courier",
	"parks worker", "market stocker", "utilities technician", "parcel courier",
	"local resident", "sanitation worker", "public information technician", "horticulture specialist"
]


func _init() -> void:
	call_deferred("_test")


func _fail(message: String) -> void:
	printerr("SOCIETY KERNEL TEST FAILED: " + message)
	quit(1)


func _roster() -> Array:
	var roster: Array = []
	for index in ROLE_SEQUENCE.size():
		roster.append({
			"display_index": index,
			"display_name": "Test Citizen %02d" % (index + 1),
			"occupation_key": str(ROLE_SEQUENCE[index]),
			"occupation_title": str(ROLE_SEQUENCE[index]),
			"personality": "persistent test profile",
			"model_path": "res://test_model_%02d.glb" % index
		})
	return roster


func _register_stations(kernel: Node) -> void:
	for index in ROLE_SEQUENCE.size():
		var citizen_id := str(kernel.call("visible_id_for_index", index))
		for stop_index in 2:
			kernel.call("register_station", {
				"station_id": "test_%02d_%d" % [index, stop_index],
				"owner_citizen_id": citizen_id,
				"owner_index": index,
				"role": str(ROLE_SEQUENCE[index]),
				"prop_kind": ["box", "power", "planter", "cone"][(index + stop_index) % 4],
				"clips": ["InspectLow", "RepairMid", "Interact"],
				"block": "r%dc%d" % [int(index % 16 / 4) + 1, index % 4 + 1]
			})


func _interaction_specs() -> Array:
	return [
		{"id":"parcel_handoff","a":4,"b":0,"a_occupation":"parcel courier","b_occupation":"local resident","title":"parcel handoff"},
		{"id":"clinic_check_in","a":2,"b":12,"a_occupation":"clinic assistant","b_occupation":"local resident","title":"clinic check-in"},
		{"id":"utility_briefing","a":15,"b":1,"a_occupation":"utilities technician","b_occupation":"public information technician","title":"utility briefing"},
		{"id":"market_purchase","a":14,"b":9,"a_occupation":"market stocker","b_occupation":"local resident","title":"market purchase"},
		{"id":"street_service_request","a":10,"b":18,"a_occupation":"sanitation worker","b_occupation":"parcel courier","title":"street service review"},
		{"id":"route_assistance","a":7,"b":20,"a_occupation":"transit steward","b_occupation":"local resident","title":"route assistance"}
	]


func _configured_kernel() -> Node:
	var kernel := SocietyKernel.new()
	root.add_child(kernel)
	kernel.call("bootstrap", _roster(), 200, 80, 760911)
	_register_stations(kernel)
	var interactions := kernel.call("plan_visible_interactions", _interaction_specs()) as Array
	kernel.call("finalize_visible_day", interactions, 10)
	return kernel


func _test() -> void:
	var kernel := _configured_kernel()
	var summary := kernel.call("get_debug_summary") as Dictionary
	if int(summary.population) != 200 or int(summary.households) != 80:
		_fail("expected 200 persistent citizens in 80 households, got %d/%d" % [int(summary.population), int(summary.households)])
		return
	if int(summary.visible_citizens) != 32 or int(summary.institutions) < 13:
		_fail("persistent world did not bind 32 visible citizens and 13 institutions")
		return
	if int(summary.relationship_edges) < 190:
		_fail("social graph is too sparse: %d edges" % int(summary.relationship_edges))
		return
	if int(summary.stations) != 64 or int(summary.social_events) != 6:
		_fail("expected 64 registered smart objects and six causal social events")
		return
	if int(summary.presentation_workers) != 10:
		_fail("expected ten institution-backed presentation workers, got %d" % int(summary.presentation_workers))
		return
	var invariant_errors := kernel.call("validate_invariants") as Array
	if not invariant_errors.is_empty():
		_fail("initial invariants failed: %s" % str(invariant_errors))
		return
	var traveller_id := str(kernel.call("visible_id_for_index", 0))
	kernel.call("record_transport_snapshot", {
		"sumo_connected": true,
		"simulated_vehicle_count": 220,
		"displayed_vehicle_count": 68,
		"stopped_vehicle_count": 44,
		"turning_vehicle_count": 12,
		"average_speed_mps": 8.5
	})
	var trip_id := str(kernel.call("begin_trip", traveller_id, "r1c1:0", "test_00_0", "bus", 12.0))
	if trip_id.is_empty():
		_fail("a scheduled trip could not enter the transport ledger")
		return
	kernel.call("update_trip_progress", traveller_id, trip_id, 0.5)
	kernel.call("advance_simulation_minutes", 14.0)
	kernel.call("complete_trip", traveller_id, trip_id, "arrived")
	summary = kernel.call("get_debug_summary") as Dictionary
	if int(summary.trips_completed) != 1 or int(summary.trips_active) != 0:
		_fail("transport feedback did not produce one completed trip")
		return
	var completed_before := int(summary.work_orders_completed)
	var completed_citizen := ""
	for index in 32:
		var citizen_id := str(kernel.call("visible_id_for_index", index))
		var assignment := kernel.call("get_visible_assignment", citizen_id) as Dictionary
		if assignment.is_empty() or not bool(assignment.presentation_worker):
			continue
		if not bool(kernel.call("begin_work_order", citizen_id, str(assignment.work_order_id))):
			_fail("presentation work order could not begin")
			return
		var replacement := kernel.call("complete_visible_work", citizen_id, str(assignment.work_order_id), "InspectLow") as Dictionary
		if replacement.is_empty() or not bool(replacement.presentation_worker):
			_fail("continuous institution work did not create a causal replacement")
			return
		completed_citizen = citizen_id
		break
	if completed_citizen.is_empty():
		_fail("no presentation worker assignment was available")
		return
	summary = kernel.call("get_debug_summary") as Dictionary
	if int(summary.work_orders_completed) != completed_before + 1:
		_fail("completed work did not change the work-order ledger")
		return
	var public_state := kernel.call("get_citizen_public_state", completed_citizen) as Dictionary
	if str(public_state.household_id).is_empty() or str(public_state.employer_id).is_empty() or int(public_state.completed_work_orders) < 1:
		_fail("completed work did not persist on the citizen, household and employer state")
		return
	var snapshot := kernel.call("snapshot_state") as Dictionary
	var snapshot_fingerprint := int(kernel.call("state_fingerprint"))
	kernel.call("advance_simulation_minutes", 180.0)
	kernel.call("restore_state", snapshot)
	if int(kernel.call("state_fingerprint")) != snapshot_fingerprint:
		_fail("save/restore changed the authoritative society state")
		return
	kernel.call("advance_simulation_minutes", 30.0 * 24.0 * 60.0)
	invariant_errors = kernel.call("validate_invariants") as Array
	if not invariant_errors.is_empty():
		_fail("30-day accelerated run failed invariants: %s" % str(invariant_errors))
		return
	summary = kernel.call("get_debug_summary") as Dictionary
	if int(summary.simulation_day) < 30:
		_fail("long-run clock did not advance through 30 simulated days")
		return
	var comparison := _configured_kernel()
	var comparison_summary := comparison.call("get_debug_summary") as Dictionary
	if int(comparison_summary.population) != 200 or int(comparison_summary.presentation_workers) != 10:
		_fail("same-seed reconstruction changed the population or presentation contract")
		return
	print("SOCIETY_KERNEL_TEST_OK population=%d households=%d institutions=%d relationships=%d completed_orders=%d completed_trips=%d day=%d deterministic_seed=%d" % [
		int(summary.population),
		int(summary.households),
		int(summary.institutions),
		int(summary.relationship_edges),
		int(summary.work_orders_completed),
		int(summary.trips_completed),
		int(summary.simulation_day),
		int(summary.seed)
	])
	quit(0)
