extends Node

## Authoritative, headless citizen and institution state.
##
## This node deliberately owns no meshes, transforms, animation players or
## SUMO vehicles. It decides why a citizen has an activity, preserves that
## citizen off screen, and records the effects of completed actions. The Godot
## character director is an embodiment adapter for this state.

const DEFAULT_POPULATION_SIZE := 200
const DEFAULT_HOUSEHOLD_COUNT := 80
const DEFAULT_VISIBLE_WORKERS := 10
const START_MINUTE_OF_DAY := 9 * 60
const WorldTime := preload("res://scripts/world_time.gd")
const HOUSEHOLD_SIZE_PATTERN := [1, 2, 2, 3, 3, 4, 2, 3]
const PHYSICAL_WORK_CLIPS := [
	"Interact", "PickUp_Table", "Farm_Harvest", "Farm_PlantSeed",
	"Farm_Watering", "InspectLow", "RepairMid", "RepairSurface",
	"OpenGroundContainer", "SetGroundItem", "OpenLatch", "PanelKnock",
	"InspectLabel"
]
const PRESENTATION_PROP_SCORE := {
	"power_meter": 320.0,
	"water_meter": 320.0,
	"aircon": 24.0,
	"power": 22.0,
	"hydrant": 22.0,
	"trash": 20.0,
	"dumpster": 20.0,
	"box": 18.0,
	"planter": 18.0,
	"flower_pot": 18.0,
	"greenhouse": 16.0,
	"cone": 16.0,
	"manhole": 14.0,
	"fire_exit": 10.0,
	"billboard": 4.0,
	"bicycle": -8.0,
	"bus_stop": -12.0,
	"mailbox": -18.0,
	"bench": -24.0,
	"poster": -80.0,
	"vending_machine": 6.0,
	"parking_meter": 4.0,
	"drinking_fountain": 4.0,
	"payphone": 2.0,
	"market_counter": 12.0,
	"coffee_cart": 12.0,
	"sandwich_board": 8.0,
	"hand_truck": 20.0,
	"ladder": 20.0,
	"wheelchair": 18.0,
	"tool_cart": 20.0,
	"toolbox": 22.0,
	"extinguisher": 18.0
}
const OCCUPATIONS := [
	"parcel courier",
	"sanitation worker",
	"utilities technician",
	"parks worker",
	"clinic assistant",
	"transit steward",
	"market stocker",
	"local resident",
	"road maintenance technician",
	"building maintenance technician",
	"public information technician",
	"horticulture specialist",
	"street vendor",
	"shopkeeper",
	"security guard",
	"paramedic",
	"electrician",
	"freight handler",
	"facade technician",
	"office clerk",
	"amenities attendant"
]
const ROLE_TO_INSTITUTION := {
	"parcel courier": "courier_cooperative",
	"sanitation worker": "sanitation_department",
	"utilities technician": "utility_department",
	"parks worker": "parks_department",
	"clinic assistant": "community_clinic",
	"transit steward": "transit_authority",
	"market stocker": "central_market",
	"local resident": "",
	"road maintenance technician": "road_maintenance_department",
	"building maintenance technician": "building_services",
	"public information technician": "public_information_office",
	"horticulture specialist": "horticulture_service",
	"street vendor": "street_vendors_guild",
	"shopkeeper": "central_market",
	"security guard": "city_security_service",
	"paramedic": "community_clinic",
	"electrician": "electrical_contractors",
	"freight handler": "freight_services",
	"facade technician": "building_trades",
	"office clerk": "civic_records_office",
	"amenities attendant": "public_amenities_department",
	"student": "district_school"
}
const ROLE_DAILY_WAGE := {
	"parcel courier": 74.0,
	"sanitation worker": 78.0,
	"utilities technician": 102.0,
	"parks worker": 76.0,
	"clinic assistant": 88.0,
	"transit steward": 82.0,
	"market stocker": 70.0,
	"road maintenance technician": 96.0,
	"building maintenance technician": 94.0,
	"public information technician": 90.0,
	"horticulture specialist": 86.0,
	"street vendor": 64.0,
	"shopkeeper": 80.0,
	"security guard": 84.0,
	"paramedic": 112.0,
	"electrician": 106.0,
	"freight handler": 78.0,
	"facade technician": 92.0,
	"office clerk": 88.0,
	"amenities attendant": 72.0
}
const FIRST_NAMES := [
	"Amira", "Youssef", "Leila", "Nabil", "Meriem", "Sami", "Ines", "Karim",
	"Rania", "Hatem", "Aya", "Walid", "Sana", "Fares", "Nour", "Rami",
	"Hiba", "Mehdi", "Salma", "Tarek", "Mouna", "Adel", "Maya", "Sofiene",
	"Nesrine", "Omar", "Yara", "Anis", "Lina", "Kais", "Rahma", "Marwen",
	"Farah", "Aziz", "Hana", "Bilel", "Sirine", "Malek", "Emna", "Skander"
]
const FAMILY_NAMES := [
	"Ben Salem", "Gharbi", "Trabelsi", "Mansour", "Jebali", "Kallel",
	"Ayadi", "Baccar", "Haddad", "Chouchane", "Khouaja", "Gdiri",
	"Bouzid", "Mhenni", "Saidi", "Cherif", "Jaziri", "Kammoun",
	"Ben Amor", "Hachicha", "Zidi", "Ben Yedder", "Boussetta", "Kefi",
	"Ben Youssef", "Dhouib", "Bousnina", "Charfi", "Ben Halima", "Mestiri"
]

var simulation_minute := float(START_MINUTE_OF_DAY)
var day_index := 0
var _seed := 760911
var _population: Dictionary = {}
var _households: Dictionary = {}
var _institutions: Dictionary = {}
var _relationships: Dictionary = {}
var _stations: Dictionary = {}
var _asset_state: Dictionary = {}
var _work_orders: Dictionary = {}
var _social_events: Dictionary = {}
var _trips: Dictionary = {}
var _active_trip_by_citizen: Dictionary = {}
var _assignments: Dictionary = {}
var _station_open_order: Dictionary = {}
var _station_reservations: Dictionary = {}
var _visible_ids: Array[String] = []
var _visible_by_index: Dictionary = {}
var _household_head_ids: Array[String] = []
var _events: Array[Dictionary] = []
var _event_sequence := 0
var _work_order_sequence := 0
var _trip_sequence := 0
var _last_processed_hour := int(START_MINUTE_OF_DAY / 60)
var _last_visible_refresh_minute := START_MINUTE_OF_DAY
var _last_validation_errors: Array[String] = []
var _transport_snapshot := {
	"source": "local_fallback",
	"sumo_connected": false,
	"simulated_vehicle_count": 0,
	"displayed_vehicle_count": 0,
	"stopped_vehicle_count": 0,
	"turning_vehicle_count": 0,
	"average_speed_mps": 0.0
}


func bootstrap(visible_roster: Array, population_size := DEFAULT_POPULATION_SIZE, household_count := DEFAULT_HOUSEHOLD_COUNT, seed := 760911) -> void:
	_seed = seed
	simulation_minute = float(START_MINUTE_OF_DAY)
	day_index = 0
	_population.clear()
	_households.clear()
	_institutions.clear()
	_relationships.clear()
	_stations.clear()
	_asset_state.clear()
	_work_orders.clear()
	_social_events.clear()
	_trips.clear()
	_active_trip_by_citizen.clear()
	_assignments.clear()
	_station_open_order.clear()
	_station_reservations.clear()
	_visible_ids.clear()
	_visible_by_index.clear()
	_household_head_ids.clear()
	_events.clear()
	_event_sequence = 0
	_work_order_sequence = 0
	_trip_sequence = 0
	_last_processed_hour = int(START_MINUTE_OF_DAY / 60)
	_last_visible_refresh_minute = START_MINUTE_OF_DAY
	_create_institutions()
	var sizes := _household_sizes(population_size, household_count)
	var next_person_number := 1
	for household_number in household_count:
		var household_id := "household_%03d" % (household_number + 1)
		var block_number := household_number % 16
		var household := {
			"household_id": household_id,
			"members": [],
			"home_block": "r%dc%d" % [int(block_number / 4) + 1, block_number % 4 + 1],
			"dwelling_id": "dwelling_%03d" % (household_number + 1),
			"vehicle_slots": 1 if _roll01("vehicle:%d" % household_number) < 0.58 else 0,
			"shared_balance": 1100.0 + float(_roll_int("balance:%d" % household_number, 0, 2600)),
			"rent_daily": 24.0 + float(_roll_int("rent:%d" % household_number, 0, 22)),
			"care_obligations": [],
			"joint_calendar": []
		}
		_households[household_id] = household
		var head_id := _citizen_id(next_person_number)
		next_person_number += 1
		var head_age := _roll_int("head_age:%d" % household_number, 25, 68)
		_population[head_id] = _base_citizen(head_id, household_id, head_age, "adult")
		_household_head_ids.append(head_id)
		_add_household_member(household_id, head_id)
	for household_number in household_count:
		var household_id := "household_%03d" % (household_number + 1)
		var household_size := int(sizes[household_number])
		var head_id := str(_household_head_ids[household_number])
		var head_age := int((_population[head_id] as Dictionary).age)
		for member_offset in range(1, household_size):
			var citizen_id := _citizen_id(next_person_number)
			next_person_number += 1
			var life_stage := "adult" if member_offset == 1 else "dependent"
			var age := clampi(head_age + _roll_int("partner_age:%s:%d" % [household_id, member_offset], -7, 6), 21, 72)
			if life_stage == "dependent":
				age = _roll_int("dependent_age:%s:%d" % [household_id, member_offset], 5, 19)
			_population[citizen_id] = _base_citizen(citizen_id, household_id, age, life_stage)
			_add_household_member(household_id, citizen_id)
	_apply_visible_roster(visible_roster)
	_assign_occupations()
	_build_relationship_network()
	_generate_all_agendas()
	_record_event("society_bootstrapped", {
		"population": _population.size(),
		"households": _households.size(),
		"institutions": _institutions.size(),
		"visible_citizens": _visible_ids.size(),
		"seed": _seed
	})
	_last_validation_errors = validate_invariants()


func _household_sizes(population_size: int, household_count: int) -> Array[int]:
	var sizes: Array[int] = []
	var total := 0
	for household_index in household_count:
		var size := int(HOUSEHOLD_SIZE_PATTERN[household_index % HOUSEHOLD_SIZE_PATTERN.size()])
		sizes.append(size)
		total += size
	while total < population_size:
		for index in sizes.size():
			if total >= population_size:
				break
			if sizes[index] < 5:
				sizes[index] += 1
				total += 1
	while total > population_size:
		for index in range(sizes.size() - 1, -1, -1):
			if total <= population_size:
				break
			if sizes[index] > 1:
				sizes[index] -= 1
				total -= 1
	return sizes


func _base_citizen(citizen_id: String, household_id: String, age: int, life_stage: String) -> Dictionary:
	var numeric_id := int(citizen_id.trim_prefix("citizen_"))
	var first_name := str(FIRST_NAMES[(numeric_id - 1) % FIRST_NAMES.size()])
	var family_name := str(FAMILY_NAMES[int((numeric_id - 1) / FIRST_NAMES.size()) % FAMILY_NAMES.size()])
	return {
		"citizen_id": citizen_id,
		"display_name": "%s %s" % [first_name, family_name],
		"age": age,
		"life_stage": life_stage,
		"household_id": household_id,
		"home_block": str((_households[household_id] as Dictionary).home_block),
		"occupation_key": "",
		"occupation_title": "",
		"employment_status": "not_assigned",
		"employer_id": "",
		"shift_start": 0,
		"shift_end": 0,
		"daily_wage": 0.0,
		"funds": 220.0 + float(_roll_int("funds:%s" % citizen_id, 0, 900)),
		"health": 0.72 + _roll01("health:%s" % citizen_id) * 0.28,
		"fatigue": 0.12 + _roll01("fatigue:%s" % citizen_id) * 0.22,
		"traits": {
			"openness": _roll01("trait:o:%s" % citizen_id),
			"conscientiousness": _roll01("trait:c:%s" % citizen_id),
			"extraversion": _roll01("trait:e:%s" % citizen_id),
			"agreeableness": _roll01("trait:a:%s" % citizen_id),
			"emotional_stability": _roll01("trait:n:%s" % citizen_id)
		},
		"known_places": [str((_households[household_id] as Dictionary).home_block)],
		"agenda": [],
		"current_activity": {},
		"current_action_reason": "",
		"registered_station_ids": [],
		"carried_items": [],
		"active_work_order_id": "",
		"active_social_event_id": "",
		"active_trip_id": "",
		"last_trip_minutes": 0.0,
		"perceived_network_delay": 0.0,
		"completed_work_orders": 0,
		"is_embodied": false,
		"display_index": -1,
		"model_path": "",
		"personality_text": "",
		"presentation_worker": false,
		"days_simulated": 0,
		"needs": {"energy": 0.85, "hunger": 0.75, "thirst": 0.8, "social": 0.7, "fun": 0.7},
		"mood": 0.75,
		"last_break": "",
		"breaks_taken": 0,
		"chats": 0
	}


func _add_household_member(household_id: String, citizen_id: String) -> void:
	var household := _households[household_id] as Dictionary
	var members := household.members as Array
	members.append(citizen_id)
	household.members = members
	_households[household_id] = household


func _apply_visible_roster(visible_roster: Array) -> void:
	var count := mini(visible_roster.size(), _household_head_ids.size())
	for index in count:
		var seed_record := visible_roster[index] as Dictionary
		var citizen_id := str(_household_head_ids[index])
		var citizen := _population[citizen_id] as Dictionary
		citizen.display_name = str(seed_record.get("display_name", citizen.display_name))
		citizen.occupation_key = str(seed_record.get("occupation_key", "local resident"))
		citizen.occupation_title = str(seed_record.get("occupation_title", citizen.occupation_key))
		citizen.personality_text = str(seed_record.get("personality", ""))
		citizen.model_path = str(seed_record.get("model_path", ""))
		citizen.display_index = int(seed_record.get("display_index", index))
		citizen.is_embodied = true
		_population[citizen_id] = citizen
		_visible_ids.append(citizen_id)
		_visible_by_index[int(citizen.display_index)] = citizen_id


func _assign_occupations() -> void:
	for citizen_id_value in _population.keys():
		var citizen_id := str(citizen_id_value)
		var citizen := _population[citizen_id] as Dictionary
		if int(citizen.age) < 20:
			citizen.occupation_key = "student"
			citizen.occupation_title = "student"
			citizen.employment_status = "student"
			citizen.employer_id = "district_school"
			citizen.shift_start = 8 * 60
			citizen.shift_end = 15 * 60
		else:
			if str(citizen.occupation_key).is_empty():
				var role_index := _roll_int("occupation:%s" % citizen_id, 0, OCCUPATIONS.size() - 1)
				citizen.occupation_key = str(OCCUPATIONS[role_index])
				citizen.occupation_title = str(citizen.occupation_key)
			if str(citizen.occupation_key) == "local resident":
				citizen.employment_status = "not_in_labor_force"
				citizen.employer_id = ""
				citizen.shift_start = 0
				citizen.shift_end = 0
			else:
				citizen.employment_status = "employed"
				citizen.employer_id = str(ROLE_TO_INSTITUTION.get(str(citizen.occupation_key), ""))
				citizen.shift_start = 7 * 60 + _roll_int("shift:%s" % citizen_id, 20, 120)
				citizen.shift_end = int(citizen.shift_start) + 8 * 60
				citizen.daily_wage = float(ROLE_DAILY_WAGE.get(str(citizen.occupation_key), 76.0))
		_population[citizen_id] = citizen
	for institution_id_value in _institutions.keys():
		var institution := _institutions[institution_id_value] as Dictionary
		institution.employee_ids = []
		_institutions[institution_id_value] = institution
	for citizen_id_value in _population.keys():
		var citizen := _population[citizen_id_value] as Dictionary
		var employer_id := str(citizen.employer_id)
		if employer_id.is_empty() or not _institutions.has(employer_id):
			continue
		var institution := _institutions[employer_id] as Dictionary
		var employees := institution.employee_ids as Array
		employees.append(str(citizen_id_value))
		institution.employee_ids = employees
		_institutions[employer_id] = institution


func _create_institutions() -> void:
	var definitions := [
		["courier_cooperative", "Courier Cooperative", "logistics", "r1c1"],
		["sanitation_department", "Sanitation Department", "public_service", "r3c1"],
		["utility_department", "Utility Department", "public_service", "r2c2"],
		["parks_department", "Parks Department", "public_service", "r3c3"],
		["community_clinic", "Community Clinic", "healthcare", "r1c3"],
		["transit_authority", "Transit Authority", "transport", "r4c3"],
		["central_market", "Central Market", "commerce", "r2c4"],
		["road_maintenance_department", "Road Maintenance", "public_service", "r2c1"],
		["building_services", "Building Services", "maintenance", "r4c2"],
		["public_information_office", "Public Information Office", "public_service", "r2c2"],
		["horticulture_service", "Horticulture Service", "public_service", "r3c4"],
		["street_vendors_guild", "Street Vendors Guild", "commerce", "r2c3"],
		["city_security_service", "City Security Service", "public_service", "r1c2"],
		["electrical_contractors", "Electrical Contractors", "maintenance", "r4c4"],
		["freight_services", "Freight Services", "logistics", "r3c2"],
		["building_trades", "Building Trades", "maintenance", "r4c2"],
		["civic_records_office", "Civic Records Office", "public_service", "r1c1"],
		["public_amenities_department", "Public Amenities Department", "public_service", "r3c3"],
		["district_school", "District School", "education", "r1c4"],
		["housing_authority", "Housing Authority", "housing", "r4c1"]
	]
	for definition_value in definitions:
		var definition := definition_value as Array
		var institution_id := str(definition[0])
		_institutions[institution_id] = {
			"institution_id": institution_id,
			"name": str(definition[1]),
			"kind": str(definition[2]),
			"home_block": str(definition[3]),
			"opening_minute": 7 * 60,
			"closing_minute": 19 * 60,
			"budget": 80000.0 + float(_roll_int("institution_budget:%s" % institution_id, 0, 40000)),
			"employee_ids": [],
			"service_backlog": 0,
			"completed_services": 0,
			"inventory_units": 500
		}


func _build_relationship_network() -> void:
	_relationships.clear()
	for household_value in _households.values():
		var members := (household_value as Dictionary).members as Array
		for first_index in members.size():
			for second_index in range(first_index + 1, members.size()):
				_add_relationship(str(members[first_index]), str(members[second_index]), "household", 0.92, 0.88)
	for institution_value in _institutions.values():
		var employees := (institution_value as Dictionary).employee_ids as Array
		if employees.size() < 2:
			continue
		for index in employees.size():
			var other_index := (index + 1) % employees.size()
			if other_index != index:
				_add_relationship(str(employees[index]), str(employees[other_index]), "workplace", 0.34, 0.42)
	for index in _household_head_ids.size():
		var next_index := (index + 1) % _household_head_ids.size()
		_add_relationship(str(_household_head_ids[index]), str(_household_head_ids[next_index]), "neighbourhood", 0.25, 0.31)


func _add_relationship(a_id: String, b_id: String, layer: String, familiarity: float, trust: float) -> void:
	if a_id == b_id:
		return
	var key := _relationship_key(a_id, b_id, layer)
	if _relationships.has(key):
		return
	_relationships[key] = {
		"a_id": a_id,
		"b_id": b_id,
		"layer": layer,
		"familiarity": clampf(familiarity, 0.0, 1.0),
		"trust": clampf(trust, 0.0, 1.0),
		"affection": clampf((familiarity + trust) * 0.45, 0.0, 1.0),
		"obligation": 0.65 if layer == "household" else 0.16,
		"conflict": 0.0,
		"contact_count": 0,
		"last_contact_minute": -1.0
	}


func _relationship_key(a_id: String, b_id: String, layer: String) -> String:
	var first := a_id if a_id < b_id else b_id
	var second := b_id if a_id < b_id else a_id
	return "%s|%s|%s" % [first, second, layer]


func _generate_all_agendas() -> void:
	for citizen_id_value in _population.keys():
		var citizen_id := str(citizen_id_value)
		var citizen := _population[citizen_id] as Dictionary
		citizen.agenda = _generate_agenda(citizen)
		_population[citizen_id] = citizen
		_refresh_current_activity(citizen_id)


func _generate_agenda(citizen: Dictionary) -> Array:
	var citizen_id := str(citizen.citizen_id)
	var agenda: Array = []
	var wake := 6 * 60 + _roll_int("wake:%s:%d" % [citizen_id, day_index], 0, 75)
	agenda.append(_activity(citizen_id, "sleep", 0, wake, "home", true, "recover at the household dwelling"))
	if str(citizen.employment_status) == "employed":
		var shift_start := int(citizen.shift_start)
		var shift_end := int(citizen.shift_end)
		var commute_start := mini(maxi(wake + 20, shift_start - 45), shift_start)
		agenda.append(_activity(citizen_id, "household_morning", wake, commute_start, "home", true, "prepare and coordinate with the household"))
		agenda.append(_activity(citizen_id, "commute_to_work", commute_start, shift_start, str(citizen.employer_id), true, "reach a contracted work shift"))
		var break_start := mini(12 * 60 + _roll_int("lunch:%s:%d" % [citizen_id, day_index], -20, 20), shift_end - 45)
		agenda.append(_activity(citizen_id, "paid_work", shift_start, break_start, str(citizen.employer_id), true, "fulfil the current employment contract"))
		agenda.append(_activity(citizen_id, "meal_break", break_start, mini(break_start + 45, shift_end), str(citizen.employer_id), false, "eat and recover during the shift"))
		agenda.append(_activity(citizen_id, "paid_work", mini(break_start + 45, shift_end), shift_end, str(citizen.employer_id), true, "complete assigned institutional work"))
		agenda.append(_activity(citizen_id, "commute_home", shift_end, mini(shift_end + 50, 22 * 60), "home", true, "return to the household"))
		var evening_start := mini(shift_end + 50, 22 * 60)
		agenda.append(_activity(citizen_id, "errand_or_social", evening_start, mini(evening_start + 100, 22 * 60 + 30), "neighbourhood", false, "meet a household, shopping or relationship need"))
		agenda.append(_activity(citizen_id, "home_evening", mini(evening_start + 100, 22 * 60 + 30), 24 * 60, "home", true, "spend the evening with the household"))
	elif str(citizen.employment_status) == "student":
		agenda.append(_activity(citizen_id, "household_morning", wake, 7 * 60 + 25, "home", true, "prepare with the household"))
		agenda.append(_activity(citizen_id, "school_trip", 7 * 60 + 25, 8 * 60, "district_school", true, "reach the assigned school"))
		agenda.append(_activity(citizen_id, "education", 8 * 60, 15 * 60, "district_school", true, "attend scheduled education"))
		agenda.append(_activity(citizen_id, "home_and_leisure", 15 * 60, 24 * 60, "home", true, "return to household care and leisure"))
	else:
		agenda.append(_activity(citizen_id, "household_morning", wake, 9 * 60, "home", true, "complete household responsibilities"))
		agenda.append(_activity(citizen_id, "services_and_errands", 9 * 60, 12 * 60, "neighbourhood", false, "obtain needed goods and public services"))
		agenda.append(_activity(citizen_id, "home_midday", 12 * 60, 15 * 60, "home", true, "eat and maintain the household"))
		agenda.append(_activity(citizen_id, "social_or_leisure", 15 * 60, 19 * 60, "neighbourhood", false, "maintain relationships and wellbeing"))
		agenda.append(_activity(citizen_id, "home_evening", 19 * 60, 24 * 60, "home", true, "spend the evening at home"))
	return agenda


func _activity(citizen_id: String, purpose: String, start_minute: int, end_minute: int, location_id: String, mandatory: bool, reason: String) -> Dictionary:
	return {
		"activity_id": "%s:day_%03d:%s:%04d" % [citizen_id, day_index, purpose, start_minute],
		"purpose": purpose,
		"start_minute": clampi(start_minute, 0, 24 * 60),
		"end_minute": clampi(maxi(start_minute, end_minute), 0, 24 * 60),
		"location_id": location_id,
		"mandatory": mandatory,
		"reason": reason,
		"status": "planned"
	}


func _refresh_current_activity(citizen_id: String) -> void:
	if not _population.has(citizen_id):
		return
	var citizen := _population[citizen_id] as Dictionary
	var minute_of_day := int(fposmod(simulation_minute, 24.0 * 60.0))
	var current := {}
	for activity_value in citizen.agenda as Array:
		var activity := activity_value as Dictionary
		if minute_of_day >= int(activity.start_minute) and minute_of_day < int(activity.end_minute):
			current = activity.duplicate(true)
			current.status = "active"
			break
	citizen.current_activity = current
	if not current.is_empty() and str(citizen.current_action_reason).is_empty():
		citizen.current_action_reason = str(current.reason)
	_population[citizen_id] = citizen


func register_station(station_data: Dictionary) -> void:
	var station_id := str(station_data.get("station_id", ""))
	if station_id.is_empty():
		return
	var record := station_data.duplicate(true)
	record.condition = 0.35 + _roll01("condition:%s" % station_id) * 0.42
	record.service_count = 0
	record.last_service_minute = -1.0
	_stations[station_id] = record
	_asset_state[station_id] = {
		"condition": float(record.condition),
		"service_count": 0,
		"last_service_minute": -1.0
	}
	var citizen_id := str(record.get("owner_citizen_id", ""))
	if _population.has(citizen_id):
		var citizen := _population[citizen_id] as Dictionary
		var station_ids := citizen.registered_station_ids as Array
		station_ids.append(station_id)
		citizen.registered_station_ids = station_ids
		_population[citizen_id] = citizen


func plan_visible_interactions(interaction_specs: Array) -> Array[Dictionary]:
	var output: Array[Dictionary] = []
	var reserved := {}
	for definition_index in interaction_specs.size():
		var definition := interaction_specs[definition_index] as Dictionary
		var a_id := _choose_visible_for_role(str(definition.get("a_occupation", "")), reserved, "", "a:%s" % str(definition.id))
		if a_id.is_empty():
			a_id = visible_id_for_index(int(definition.get("a", -1)))
		var b_id := _choose_visible_for_role(str(definition.get("b_occupation", "")), reserved, a_id, "b:%s" % str(definition.id))
		if b_id.is_empty():
			b_id = visible_id_for_index(int(definition.get("b", -1)))
		if a_id.is_empty() or b_id.is_empty() or a_id == b_id or reserved.has(a_id) or reserved.has(b_id):
			continue
		reserved[a_id] = true
		reserved[b_id] = true
		var event_id := "social_%03d_%s" % [definition_index + 1, str(definition.id)]
		var item_owners := definition.get("item_owners", []) as Array
		var initial_owner_side := int(item_owners[0]) if not item_owners.is_empty() else -1
		var event_record := {
			"event_id": event_id,
			"interaction_id": str(definition.id),
			"a_id": a_id,
			"b_id": b_id,
			"purpose": str(definition.get("title", "shared city activity")),
			"status": "active",
			"started_minute": simulation_minute,
			"completed_minute": -1.0,
			"cause": _interaction_cause(str(definition.id)),
			"current_phase": -1,
			"current_speaker_id": "",
			"item_kind": str(definition.get("hand_item", "")),
			"initial_item_owner_id": a_id if initial_owner_side == 0 else (b_id if initial_owner_side == 1 else ""),
			"item_owner_id": "",
			"custody_history": []
		}
		_social_events[event_id] = event_record
		for citizen_id in [a_id, b_id]:
			var citizen := _population[str(citizen_id)] as Dictionary
			citizen.active_social_event_id = event_id
			citizen.current_action_reason = str(event_record.cause)
			_population[str(citizen_id)] = citizen
		output.append({
			"definition_index": definition_index,
			"a_index": int((_population[a_id] as Dictionary).display_index),
			"b_index": int((_population[b_id] as Dictionary).display_index),
			"event_id": event_id
		})
		_record_event("social_event_started", event_record)
	return output


func _choose_visible_for_role(role: String, reserved: Dictionary, partner_id: String, salt: String) -> String:
	var selected := ""
	var selected_score := -INF
	for citizen_id in _visible_ids:
		if reserved.has(citizen_id):
			continue
		var citizen := _population[citizen_id] as Dictionary
		if not role.is_empty() and str(citizen.occupation_key) != role:
			continue
		var score := _roll01("interaction:%s:%s" % [salt, citizen_id])
		if not partner_id.is_empty():
			score += _relationship_strength(citizen_id, partner_id)
		if score > selected_score:
			selected = citizen_id
			selected_score = score
	return selected


func _relationship_strength(a_id: String, b_id: String) -> float:
	var total := 0.0
	for layer in ["household", "workplace", "neighbourhood", "service", "friendship"]:
		var key := _relationship_key(a_id, b_id, layer)
		if _relationships.has(key):
			var relationship := _relationships[key] as Dictionary
			total += float(relationship.familiarity) * 0.55 + float(relationship.trust) * 0.45
	return total


func _interaction_cause(interaction_id: String) -> String:
	var causes := {
		"parcel_handoff": "a household delivery created a courier-customer handoff",
		"clinic_check_in": "a scheduled clinic visit requires a staffed check-in",
		"utility_briefing": "a completed meter inspection requires a service briefing",
		"market_purchase": "a household shopping activity created a market transaction",
		"street_service_request": "a public service request requires completion review",
		"route_assistance": "a traveller requested help with a transit connection"
	}
	return str(causes.get(interaction_id, "a shared obligation brought both citizens to the same place"))


func finalize_visible_day(interaction_assignments: Array, presentation_worker_target := DEFAULT_VISIBLE_WORKERS) -> void:
	var interaction_indices := {}
	for assignment_value in interaction_assignments:
		var assignment := assignment_value as Dictionary
		interaction_indices[int(assignment.a_index)] = true
		interaction_indices[int(assignment.b_index)] = true
	for station_id_value in _stations.keys():
		_ensure_open_work_order(str(station_id_value), "")
	var selected_worker_ids: Array[String] = []
	var selected_roles := {}
	while selected_worker_ids.size() < presentation_worker_target:
		var candidate_id := ""
		var candidate_station := ""
		var candidate_score := -INF
		for citizen_id in _visible_ids:
			var citizen := _population[citizen_id] as Dictionary
			if interaction_indices.has(int(citizen.display_index)) or selected_worker_ids.has(citizen_id):
				continue
			var station_id := _best_physical_station(citizen_id)
			if station_id.is_empty():
				continue
			var role_bonus := 100.0 if not selected_roles.has(str(citizen.occupation_key)) else 0.0
			var asset := _asset_state[station_id] as Dictionary
			var pose_variety := float(_physical_clip_count(station_id))
			var score := role_bonus + pose_variety * 32.0 + _presentation_prop_score(station_id) + (1.0 - float(asset.condition)) * 24.0 + _roll01("presentation:%s" % citizen_id)
			if score > candidate_score:
				candidate_id = citizen_id
				candidate_station = station_id
				candidate_score = score
		if candidate_id.is_empty():
			break
		selected_worker_ids.append(candidate_id)
		var selected_citizen := _population[candidate_id] as Dictionary
		selected_roles[str(selected_citizen.occupation_key)] = true
		selected_citizen.presentation_worker = true
		_population[candidate_id] = selected_citizen
		_assign_station_work(candidate_id, candidate_station, true, "a staffed institution assigned a visible, physical service order")
	var non_pair_non_anchor: Array[String] = []
	for citizen_id in _visible_ids:
		var citizen := _population[citizen_id] as Dictionary
		if interaction_indices.has(int(citizen.display_index)):
			continue
		if bool(citizen.presentation_worker):
			continue
		non_pair_non_anchor.append(citizen_id)
	var commuters: Array[String] = []
	while commuters.size() < mini(4, non_pair_non_anchor.size()):
		var selected := ""
		var best := -INF
		for citizen_id in non_pair_non_anchor:
			if commuters.has(citizen_id):
				continue
			var citizen := _population[citizen_id] as Dictionary
			var score := float(citizen.shift_start) + _roll01("opening_commute:%s" % citizen_id) * 30.0
			if score > best:
				best = score
				selected = citizen_id
		if selected.is_empty():
			break
		commuters.append(selected)
	for citizen_id in non_pair_non_anchor:
		var station_id := _choose_owned_station(citizen_id, "opening")
		if station_id.is_empty():
			continue
		var start_active := not commuters.has(citizen_id)
		var reason := "the citizen is travelling to a scheduled shift and reserved workplace"
		if start_active:
			reason = "the current daily agenda and employer backlog require this staffed task"
		_assign_station_work(citizen_id, station_id, start_active, reason)
	_record_event("visible_day_finalized", {
		"presentation_workers": selected_worker_ids.duplicate(),
		"commuters": commuters.duplicate(),
		"paired_citizens": interaction_indices.size(),
		"work_orders": _work_orders.size()
	})
	_last_validation_errors = validate_invariants()


func _best_physical_station(citizen_id: String) -> String:
	if not _population.has(citizen_id):
		return ""
	var citizen := _population[citizen_id] as Dictionary
	# Meter crews are balanced deterministically across both real utility asset
	# classes before the general presentation score is evaluated. This prevents
	# every visible technician from converging on the first (power) station.
	var preferred_meter_kind := "power_meter" if int(citizen.display_index) % 2 == 0 else "water_meter"
	for station_id_value in citizen.registered_station_ids as Array:
		var preferred_station_id := str(station_id_value)
		if _stations.has(preferred_station_id) and str((_stations[preferred_station_id] as Dictionary).get("prop_kind", "")) == preferred_meter_kind and _station_has_physical_work(preferred_station_id):
			return preferred_station_id
	var selected := ""
	var best_score := -INF
	for station_id_value in citizen.registered_station_ids as Array:
		var station_id := str(station_id_value)
		if not _station_has_physical_work(station_id):
			continue
		var condition := float((_asset_state[station_id] as Dictionary).condition)
		var score := float(_physical_clip_count(station_id)) * 10.0 + _presentation_prop_score(station_id) + (1.0 - condition)
		if score > best_score:
			best_score = score
			selected = station_id
	return selected


func _station_has_physical_work(station_id: String) -> bool:
	return _physical_clip_count(station_id) > 0


func _physical_clip_count(station_id: String) -> int:
	if not _stations.has(station_id):
		return 0
	var clips := {}
	for clip_value in (_stations[station_id] as Dictionary).get("clips", []):
		if PHYSICAL_WORK_CLIPS.has(str(clip_value)):
			clips[str(clip_value)] = true
	return clips.size()


func _presentation_prop_score(station_id: String) -> float:
	if not _stations.has(station_id):
		return -100.0
	var prop_kind := str((_stations[station_id] as Dictionary).get("prop_kind", ""))
	return float(PRESENTATION_PROP_SCORE.get(prop_kind, 0.0))


func _choose_owned_station(citizen_id: String, salt: String) -> String:
	if not _population.has(citizen_id):
		return ""
	var station_ids := (_population[citizen_id] as Dictionary).registered_station_ids as Array
	if station_ids.is_empty():
		return ""
	var index := _roll_int("station_choice:%s:%s" % [citizen_id, salt], 0, station_ids.size() - 1)
	return str(station_ids[index])


func _assign_station_work(citizen_id: String, station_id: String, start_active: bool, reason: String) -> void:
	var work_order_id := _ensure_open_work_order(station_id, citizen_id)
	if work_order_id.is_empty():
		return
	_assign_work_order(work_order_id, citizen_id)
	var citizen := _population[citizen_id] as Dictionary
	citizen.active_work_order_id = work_order_id
	citizen.current_action_reason = reason
	_population[citizen_id] = citizen
	_assignments[citizen_id] = {
		"citizen_id": citizen_id,
		"station_id": station_id,
		"work_order_id": work_order_id,
		"start_active": start_active,
		"presentation_worker": bool(citizen.presentation_worker),
		"reason": reason,
		"activity": (citizen.current_activity as Dictionary).duplicate(true)
	}


func _ensure_open_work_order(station_id: String, preferred_citizen_id: String) -> String:
	if _station_open_order.has(station_id):
		var existing_id := str(_station_open_order[station_id])
		if _work_orders.has(existing_id):
			var existing := _work_orders[existing_id] as Dictionary
			if str(existing.status) in ["open", "assigned", "in_progress"]:
				if preferred_citizen_id.is_empty() or str(existing.assigned_citizen_id).is_empty() or str(existing.assigned_citizen_id) == preferred_citizen_id:
					return existing_id
	var work_order_id := _create_work_order(station_id)
	_station_open_order[station_id] = work_order_id
	return work_order_id


func _create_work_order(station_id: String) -> String:
	if not _stations.has(station_id):
		return ""
	_work_order_sequence += 1
	var work_order_id := "work_order_%05d" % _work_order_sequence
	var station := _stations[station_id] as Dictionary
	var owner_id := str(station.get("owner_citizen_id", ""))
	var owner := _population.get(owner_id, {}) as Dictionary
	var institution_id := str(owner.get("employer_id", ""))
	var asset := _asset_state[station_id] as Dictionary
	var prop_kind := str(station.get("prop_kind", "asset"))
	var cause := "scheduled preventive service"
	if float(asset.condition) < 0.48:
		cause = "%s condition fell below the preventive-service threshold" % prop_kind.replace("_", " ")
	elif prop_kind in ["box", "trash", "dumpster", "planter", "flower_pot"]:
		cause = "routine inventory or public-space service became due"
	var work_order := {
		"work_order_id": work_order_id,
		"station_id": station_id,
		"institution_id": institution_id,
		"required_role": str(station.get("role", "")),
		"prop_kind": prop_kind,
		"status": "open",
		"priority": 1.0 - float(asset.condition),
		"cause": cause,
		"created_minute": simulation_minute,
		"started_minute": -1.0,
		"completed_minute": -1.0,
		"assigned_citizen_id": "",
		"duration_minutes": 18.0 + float(_roll_int("duration:%s" % work_order_id, 0, 28)),
		"effect": "restore asset condition and reduce the institution service backlog",
		"completion_clip": ""
	}
	_work_orders[work_order_id] = work_order
	if _institutions.has(institution_id):
		var institution := _institutions[institution_id] as Dictionary
		institution.service_backlog = int(institution.service_backlog) + 1
		_institutions[institution_id] = institution
	_record_event("work_order_created", {
		"work_order_id": work_order_id,
		"station_id": station_id,
		"institution_id": institution_id,
		"cause": cause
	})
	return work_order_id


func _assign_work_order(work_order_id: String, citizen_id: String) -> void:
	if not _work_orders.has(work_order_id) or not _population.has(citizen_id):
		return
	var work_order := _work_orders[work_order_id] as Dictionary
	var station_id := str(work_order.station_id)
	if _station_reservations.has(station_id) and str(_station_reservations[station_id]) != citizen_id:
		return
	work_order.assigned_citizen_id = citizen_id
	work_order.status = "assigned"
	_work_orders[work_order_id] = work_order
	_station_reservations[station_id] = citizen_id
	_record_event("work_order_assigned", {
		"work_order_id": work_order_id,
		"citizen_id": citizen_id,
		"station_id": station_id
	})


func begin_work_order(citizen_id: String, work_order_id: String) -> bool:
	if not _work_orders.has(work_order_id):
		return false
	var work_order := _work_orders[work_order_id] as Dictionary
	if str(work_order.assigned_citizen_id) != citizen_id:
		return false
	if str(work_order.status) == "assigned":
		work_order.status = "in_progress"
		work_order.started_minute = simulation_minute
		_work_orders[work_order_id] = work_order
		_record_event("work_order_started", {
			"work_order_id": work_order_id,
			"citizen_id": citizen_id,
			"station_id": str(work_order.station_id)
		})
	return str(work_order.status) == "in_progress"


func complete_visible_work(citizen_id: String, work_order_id: String, animation_clip: String) -> Dictionary:
	if not _work_orders.has(work_order_id) or not _population.has(citizen_id):
		return request_next_assignment(citizen_id)
	var work_order := _work_orders[work_order_id] as Dictionary
	if str(work_order.assigned_citizen_id) != citizen_id or not str(work_order.status) in ["assigned", "in_progress"]:
		return request_next_assignment(citizen_id)
	var station_id := str(work_order.station_id)
	work_order.status = "completed"
	work_order.completed_minute = simulation_minute
	work_order.completion_clip = animation_clip
	_work_orders[work_order_id] = work_order
	_station_open_order.erase(station_id)
	_station_reservations.erase(station_id)
	var asset := _asset_state[station_id] as Dictionary
	asset.condition = clampf(float(asset.condition) + 0.18 + _roll01("repair:%s" % work_order_id) * 0.16, 0.0, 1.0)
	asset.service_count = int(asset.service_count) + 1
	asset.last_service_minute = simulation_minute
	_asset_state[station_id] = asset
	var citizen := _population[citizen_id] as Dictionary
	citizen.completed_work_orders = int(citizen.completed_work_orders) + 1
	var task_payment := float(citizen.daily_wage) / 8.0
	citizen.funds = float(citizen.funds) + task_payment
	citizen.fatigue = clampf(float(citizen.fatigue) + 0.035, 0.0, 1.0)
	citizen.active_work_order_id = ""
	_population[citizen_id] = citizen
	var institution_id := str(work_order.institution_id)
	if _institutions.has(institution_id):
		var institution := _institutions[institution_id] as Dictionary
		institution.service_backlog = maxi(0, int(institution.service_backlog) - 1)
		institution.completed_services = int(institution.completed_services) + 1
		institution.budget = maxf(0.0, float(institution.budget) - task_payment)
		_institutions[institution_id] = institution
	_record_event("work_order_completed", {
		"work_order_id": work_order_id,
		"citizen_id": citizen_id,
		"station_id": station_id,
		"animation_clip": animation_clip,
		"asset_condition": float(asset.condition),
		"payment": task_payment
	})
	return request_next_assignment(citizen_id, station_id)


func request_next_assignment(citizen_id: String, completed_station_id := "", preferred_station_id := "") -> Dictionary:
	if not _population.has(citizen_id):
		return {}
	var citizen := _population[citizen_id] as Dictionary
	var station_ids := citizen.registered_station_ids as Array
	var station_id := preferred_station_id if not preferred_station_id.is_empty() and station_ids.has(preferred_station_id) else completed_station_id
	if not bool(citizen.presentation_worker) and preferred_station_id.is_empty():
		if station_ids.size() > 1:
			var current_index := station_ids.find(completed_station_id)
			station_id = str(station_ids[(current_index + 1) % station_ids.size()])
	if station_id.is_empty():
		station_id = _choose_owned_station(citizen_id, "next:%d" % int(citizen.completed_work_orders))
	if station_id.is_empty():
		return {}
	var reason := "the employer assigned the next queued service order"
	if bool(citizen.presentation_worker):
		reason = "continuous staffed city operations produced the next service order at this workplace"
	_assign_station_work(citizen_id, station_id, bool(citizen.presentation_worker), reason)
	return get_visible_assignment(citizen_id)


func record_transport_snapshot(snapshot: Dictionary) -> void:
	if snapshot.is_empty():
		return
	_transport_snapshot = snapshot.duplicate(true)
	_transport_snapshot.source = "SUMO 1.25.0 aggregate feedback" if bool(snapshot.get("sumo_connected", false)) else "local traffic fallback"


func begin_trip(citizen_id: String, origin_id: String, destination_id: String, mode: String, expected_minutes: float) -> String:
	if not _population.has(citizen_id):
		return ""
	if _active_trip_by_citizen.has(citizen_id):
		var existing_id := str(_active_trip_by_citizen[citizen_id])
		if _trips.has(existing_id) and str((_trips[existing_id] as Dictionary).status) == "travelling":
			return existing_id
	_trip_sequence += 1
	var trip_id := "trip_%06d" % _trip_sequence
	var simulated_count := int(_transport_snapshot.get("simulated_vehicle_count", 0))
	var stopped_count := int(_transport_snapshot.get("stopped_vehicle_count", 0))
	var congestion_ratio := float(stopped_count) / float(maxi(1, simulated_count))
	var adjusted_expected := maxf(0.1, expected_minutes)
	if mode in ["car", "taxi", "bus"]:
		adjusted_expected *= 1.0 + congestion_ratio * 0.75
	var trip := {
		"trip_id": trip_id,
		"citizen_id": citizen_id,
		"origin_id": origin_id,
		"destination_id": destination_id,
		"mode": mode,
		"status": "travelling",
		"started_minute": simulation_minute,
		"arrived_minute": -1.0,
		"expected_minutes": adjusted_expected,
		"actual_minutes": 0.0,
		"progress": 0.0,
		"transport_context": _transport_snapshot.duplicate(true)
	}
	_trips[trip_id] = trip
	_active_trip_by_citizen[citizen_id] = trip_id
	var citizen := _population[citizen_id] as Dictionary
	citizen.active_trip_id = trip_id
	citizen.current_action_reason = "the daily agenda requires travel from %s to %s by %s" % [origin_id, destination_id, mode]
	_population[citizen_id] = citizen
	_record_event("trip_started", trip)
	return trip_id


func update_trip_progress(citizen_id: String, trip_id: String, progress: float) -> void:
	if not _trips.has(trip_id):
		return
	var trip := _trips[trip_id] as Dictionary
	if str(trip.citizen_id) != citizen_id or str(trip.status) != "travelling":
		return
	trip.progress = clampf(progress, 0.0, 1.0)
	_trips[trip_id] = trip


func complete_trip(citizen_id: String, trip_id: String, arrival_status := "arrived") -> void:
	if not _trips.has(trip_id) or not _population.has(citizen_id):
		return
	var trip := _trips[trip_id] as Dictionary
	if str(trip.citizen_id) != citizen_id or str(trip.status) != "travelling":
		return
	trip.status = arrival_status
	trip.progress = 1.0
	trip.arrived_minute = simulation_minute
	trip.actual_minutes = maxf(0.0, simulation_minute - float(trip.started_minute))
	_trips[trip_id] = trip
	_active_trip_by_citizen.erase(citizen_id)
	var citizen := _population[citizen_id] as Dictionary
	citizen.active_trip_id = ""
	citizen.last_trip_minutes = float(trip.actual_minutes)
	citizen.perceived_network_delay = lerpf(float(citizen.perceived_network_delay), float(trip.actual_minutes) - float(trip.expected_minutes), 0.35)
	citizen.current_action_reason = "the citizen arrived through a validated route and can begin the reserved activity"
	_population[citizen_id] = citizen
	_record_event("trip_completed", trip)


func complete_social_event(event_id: String) -> void:
	if not _social_events.has(event_id):
		return
	var event_record := _social_events[event_id] as Dictionary
	if str(event_record.status) == "completed":
		return
	event_record.status = "completed"
	event_record.completed_minute = simulation_minute
	_social_events[event_id] = event_record
	var a_id := str(event_record.a_id)
	var b_id := str(event_record.b_id)
	var relationship_key := _relationship_key(a_id, b_id, "service")
	if not _relationships.has(relationship_key):
		_add_relationship(a_id, b_id, "service", 0.16, 0.22)
	var relationship := _relationships[relationship_key] as Dictionary
	relationship.familiarity = clampf(float(relationship.familiarity) + 0.08, 0.0, 1.0)
	relationship.trust = clampf(float(relationship.trust) + 0.04, 0.0, 1.0)
	relationship.contact_count = int(relationship.contact_count) + 1
	relationship.last_contact_minute = simulation_minute
	_relationships[relationship_key] = relationship
	for citizen_id in [a_id, b_id]:
		var citizen := _population[str(citizen_id)] as Dictionary
		citizen.active_social_event_id = ""
		citizen.current_action_reason = "the completed shared event now changes their relationship history"
		_population[str(citizen_id)] = citizen
	var final_owner_id := str(event_record.get("item_owner_id", ""))
	var item_kind := str(event_record.get("item_kind", ""))
	if not final_owner_id.is_empty() and not item_kind.is_empty() and _population.has(final_owner_id):
		var owner := _population[final_owner_id] as Dictionary
		var carried_items := owner.get("carried_items", []) as Array
		carried_items.append({
			"item_id": "%s_%s" % [event_id, item_kind],
			"kind": item_kind,
			"acquired_from_event_id": event_id,
			"acquired_minute": simulation_minute
		})
		owner.carried_items = carried_items
		_population[final_owner_id] = owner
	_record_event("social_event_completed", event_record)


func update_social_event_phase(event_id: String, phase: int, speaker_id: String, holder_id: String, item_kind: String) -> void:
	if not _social_events.has(event_id):
		return
	var event_record := _social_events[event_id] as Dictionary
	if str(event_record.status) != "active" or phase <= int(event_record.get("current_phase", -1)):
		return
	if speaker_id != str(event_record.a_id) and speaker_id != str(event_record.b_id):
		return
	if not holder_id.is_empty() and holder_id != str(event_record.a_id) and holder_id != str(event_record.b_id):
		return
	event_record.current_phase = phase
	event_record.current_speaker_id = speaker_id
	event_record.item_kind = item_kind
	event_record.item_owner_id = holder_id
	var history := event_record.get("custody_history", []) as Array
	history.append({
		"phase": phase,
		"speaker_id": speaker_id,
		"listener_id": str(event_record.b_id) if speaker_id == str(event_record.a_id) else str(event_record.a_id),
		"item_kind": item_kind,
		"item_owner_id": holder_id,
		"simulation_minute": simulation_minute
	})
	event_record.custody_history = history
	_social_events[event_id] = event_record
	_record_event("social_event_phase_changed", {"event_id":event_id,"phase":phase,"speaker_id":speaker_id,"item_kind":item_kind,"item_owner_id":holder_id})


func get_visible_assignment(citizen_id: String) -> Dictionary:
	return (_assignments.get(citizen_id, {}) as Dictionary).duplicate(true)


func visible_id_for_index(display_index: int) -> String:
	return str(_visible_by_index.get(display_index, ""))


func citizen_for_visible_index(display_index: int) -> Dictionary:
	var citizen_id := visible_id_for_index(display_index)
	return get_citizen_public_state(citizen_id)


func get_citizen_public_state(citizen_id: String) -> Dictionary:
	if not _population.has(citizen_id):
		return {}
	var citizen := _population[citizen_id] as Dictionary
	var household := _households.get(str(citizen.household_id), {}) as Dictionary
	var current := citizen.current_activity as Dictionary
	var next_activity := _next_activity(citizen.agenda as Array)
	var work_order := _work_orders.get(str(citizen.active_work_order_id), {}) as Dictionary
	var social_event := _social_events.get(str(citizen.active_social_event_id), {}) as Dictionary
	var active_trip := _trips.get(str(citizen.active_trip_id), {}) as Dictionary
	return {
		"citizen_id": citizen_id,
		"display_name": str(citizen.display_name),
		"age": int(citizen.age),
		"life_stage": str(citizen.life_stage),
		"household_id": str(citizen.household_id),
		"household_size": (household.get("members", []) as Array).size(),
		"home_block": str(citizen.home_block),
		"occupation": str(citizen.occupation_title),
		"personality": str(citizen.personality_text),
		"model_path": str(citizen.model_path),
		"employment_status": str(citizen.employment_status),
		"employer_id": str(citizen.employer_id),
		"funds": float(citizen.funds),
		"health": float(citizen.health),
		"fatigue": float(citizen.fatigue),
		"needs": (citizen.get("needs", {}) as Dictionary).duplicate(true),
		"mood": float(citizen.get("mood", 0.7)),
		"last_break": str(citizen.get("last_break", "")),
		"breaks_taken": int(citizen.get("breaks_taken", 0)),
		"chats": int(citizen.get("chats", 0)),
		"traits": (citizen.traits as Dictionary).duplicate(true),
		"carried_items": (citizen.get("carried_items", []) as Array).duplicate(true),
		"current_activity": current.duplicate(true),
		"next_activity": next_activity,
		"decision_reason": str(citizen.current_action_reason),
		"active_work_order": work_order.duplicate(true),
		"active_social_event": social_event.duplicate(true),
		"active_trip": active_trip.duplicate(true),
		"last_trip_minutes": float(citizen.last_trip_minutes),
		"perceived_network_delay": float(citizen.perceived_network_delay),
		"transport_snapshot": _transport_snapshot.duplicate(true),
		"presentation_worker": bool(citizen.presentation_worker),
		"completed_work_orders": int(citizen.completed_work_orders),
		"relationship_count": _relationship_count(citizen_id),
		"simulation_day": day_index,
		"simulation_minute_of_day": int(fposmod(simulation_minute, 24.0 * 60.0))
	}


func _next_activity(agenda: Array) -> Dictionary:
	var minute_of_day := int(fposmod(simulation_minute, 24.0 * 60.0))
	for activity_value in agenda:
		var activity := activity_value as Dictionary
		if int(activity.start_minute) > minute_of_day:
			return activity.duplicate(true)
	return {}


func _relationship_count(citizen_id: String) -> int:
	var count := 0
	for relationship_value in _relationships.values():
		var relationship := relationship_value as Dictionary
		if str(relationship.a_id) == citizen_id or str(relationship.b_id) == citizen_id:
			count += 1
	return count


func advance(delta_seconds: float) -> void:
	if delta_seconds <= 0.0:
		return
	# The shared city clock rate: the society keeps exactly the time the
	# environment clock shows (and stands still while SUMO is paused).
	advance_simulation_minutes(WorldTime.advance_minutes(delta_seconds))


func set_time_of_day(minute_of_day: int) -> void:
	## Operator clock changes preserve the current simulated day while making
	## daily agendas immediately authoritative at the selected time.
	var current_day := int(floor(simulation_minute / (24.0 * 60.0)))
	simulation_minute = float(current_day * 24 * 60 + clampi(minute_of_day, 0, 1439))
	day_index = current_day
	_last_processed_hour = int(floor(simulation_minute / 60.0))
	_last_visible_refresh_minute = int(floor(simulation_minute))
	for citizen_id_value in _population.keys():
		_refresh_current_activity(str(citizen_id_value))


func advance_simulation_minutes(minutes: float) -> void:
	if minutes <= 0.0:
		return
	var target := simulation_minute + minutes
	while float(_last_processed_hour + 1) * 60.0 <= target:
		_last_processed_hour += 1
		simulation_minute = float(_last_processed_hour) * 60.0
		_process_hour()
	simulation_minute = target
	var target_day := int(simulation_minute / (24.0 * 60.0))
	while day_index < target_day:
		day_index += 1
		_process_day()
	# Agendas have minute boundaries. Refreshing and deep-copying all visible
	# activity dictionaries on every physics tick created thousands of needless
	# allocations per second without changing a decision. Update only when the
	# authoritative society minute advances.
	_decay_needs(minutes)
	var current_minute := int(floor(simulation_minute))
	if current_minute != _last_visible_refresh_minute:
		_last_visible_refresh_minute = current_minute
		for citizen_id in _visible_ids:
			_refresh_current_activity(citizen_id)


## Needs fall with simulated time (a busy extravert tires of silence faster, a hot day dries
## people out) and are refilled only by doing something: sitting, drinking, eating, talking.
const NEED_DECAY_PER_MINUTE := {"energy": 0.0016, "hunger": 0.0020, "thirst": 0.0028, "social": 0.0013, "fun": 0.0011}

func _decay_needs(minutes: float) -> void:
	for citizen_id in _visible_ids:
		var citizen := _population.get(citizen_id, {}) as Dictionary
		if citizen.is_empty():
			continue
		var needs := citizen.needs as Dictionary
		var traits := citizen.traits as Dictionary
		for need_name in NEED_DECAY_PER_MINUTE:
			# Each person runs a little faster or slower, and hard physical work wears energy and thirst down faster.
			var rate := float(NEED_DECAY_PER_MINUTE[need_name]) * minutes * (0.7 + 0.6 * _roll01("need:%s:%s" % [need_name, citizen_id]))
			if str(citizen.employment_status) == "employed" and need_name in ["energy", "thirst"]:
				rate *= 1.25
			if need_name == "social":
				rate *= 0.6 + float(traits.get("extraversion", 0.5)) * 0.9
			needs[need_name] = clampf(float(needs.get(need_name, 0.7)) - rate, 0.0, 1.0)
		citizen.needs = needs
		var total := 0.0
		for value in needs.values():
			total += float(value)
		citizen.mood = clampf(total / float(needs.size()) * 0.7 + float(citizen.health) * 0.3, 0.0, 1.0)
		_population[citizen_id] = citizen

## The most pressing need below `threshold` for a visible citizen, or "" when nothing presses.
func most_pressing_need(citizen_id: String, threshold := 0.45) -> String:
	var citizen := _population.get(citizen_id, {}) as Dictionary
	if citizen.is_empty():
		return ""
	var worst := ""
	var worst_value := threshold
	for need_name in (citizen.needs as Dictionary):
		var value := float((citizen.needs as Dictionary)[need_name])
		if value < worst_value:
			worst_value = value
			worst = str(need_name)
	return worst

func record_need_satisfied(citizen_id: String, need_name: String, amount: float, place: String, cost := 0.0) -> void:
	var citizen := _population.get(citizen_id, {}) as Dictionary
	if citizen.is_empty():
		return
	var needs := citizen.needs as Dictionary
	needs[need_name] = clampf(float(needs.get(need_name, 0.5)) + amount, 0.0, 1.0)
	citizen.needs = needs
	citizen.last_break = "%s at %s" % [need_name, place]
	citizen.breaks_taken = int(citizen.breaks_taken) + 1
	if cost > 0.0:
		citizen.funds = maxf(0.0, float(citizen.funds) - cost)
	_population[citizen_id] = citizen
	_record_event("need_satisfied", {"citizen_id": citizen_id, "need": need_name, "place": place, "cost": cost})

func record_street_chat(a_id: String, b_id: String, topic: String) -> void:
	if not _population.has(a_id) or not _population.has(b_id) or a_id == b_id:
		return
	_add_relationship(a_id, b_id, "street", 0.12, 0.2)
	var key := _relationship_key(a_id, b_id, "street")
	var relation := _relationships[key] as Dictionary
	relation.familiarity = clampf(float(relation.familiarity) + 0.06, 0.0, 1.0)
	relation.trust = clampf(float(relation.trust) + 0.03, 0.0, 1.0)
	relation.contact_count = int(relation.contact_count) + 1
	relation.last_contact_minute = simulation_minute
	_relationships[key] = relation
	for citizen_id in [a_id, b_id]:
		var citizen := _population[citizen_id] as Dictionary
		var needs := citizen.needs as Dictionary
		needs.social = clampf(float(needs.social) + 0.28, 0.0, 1.0)
		citizen.needs = needs
		citizen.chats = int(citizen.chats) + 1
		_population[citizen_id] = citizen
	_record_event("street_chat", {"a": a_id, "b": b_id, "topic": topic})

func _process_hour() -> void:
	for citizen_id_value in _population.keys():
		_refresh_current_activity(str(citizen_id_value))
	for station_id_value in _asset_state.keys():
		var station_id := str(station_id_value)
		var asset := _asset_state[station_id] as Dictionary
		asset.condition = maxf(0.0, float(asset.condition) - 0.0007)
		_asset_state[station_id] = asset


func _process_day() -> void:
	for household_id_value in _households.keys():
		var household_id := str(household_id_value)
		var household := _households[household_id] as Dictionary
		var income := 0.0
		for citizen_id_value in household.members as Array:
			var citizen := _population[str(citizen_id_value)] as Dictionary
			if str(citizen.employment_status) == "employed":
				income += float(citizen.daily_wage)
				citizen.funds = float(citizen.funds) + float(citizen.daily_wage) * 0.18
			citizen.days_simulated = int(citizen.days_simulated) + 1
			if day_index > 0 and day_index % 365 == 0:
				citizen.age = int(citizen.age) + 1
			citizen.agenda = _generate_agenda(citizen)
			_population[str(citizen_id_value)] = citizen
		var food_and_services := 13.0 + float((household.members as Array).size()) * 6.5
		household.shared_balance = maxf(0.0, float(household.shared_balance) + income * 0.72 - float(household.rent_daily) - food_and_services)
		_households[household_id] = household
	for institution_id_value in _institutions.keys():
		var institution_id := str(institution_id_value)
		var institution := _institutions[institution_id] as Dictionary
		institution.budget = float(institution.budget) + 180.0
		if str(institution.kind) == "commerce":
			institution.inventory_units = maxi(0, int(institution.inventory_units) - _roll_int("sales:%s:%d" % [institution_id, day_index], 8, 28))
		_institutions[institution_id] = institution
	if day_index > 0 and day_index % 30 == 0:
		_process_month()
	_record_event("society_day_closed", {
		"day": day_index,
		"population": _population.size(),
		"active_work_orders": _count_work_orders(["open", "assigned", "in_progress"])
	})
	_last_validation_errors = validate_invariants()


func _process_month() -> void:
	var employment_changes := 0
	var health_changes := 0
	var household_moves := 0
	for citizen_id_value in _population.keys():
		var citizen_id := str(citizen_id_value)
		var citizen := _population[citizen_id] as Dictionary
		var health_delta := (_roll01("monthly_health:%s:%d" % [citizen_id, day_index]) - 0.5) * 0.08
		citizen.health = clampf(float(citizen.health) + health_delta, 0.18, 1.0)
		if absf(health_delta) > 0.03:
			health_changes += 1
		if not bool(citizen.is_embodied) and int(citizen.age) >= 20:
			var transition_roll := _roll01("employment_transition:%s:%d" % [citizen_id, day_index])
			if str(citizen.employment_status) != "employed" and transition_roll < 0.08:
				var role_index := _roll_int("new_job:%s:%d" % [citizen_id, day_index], 0, OCCUPATIONS.size() - 2)
				var role := str(OCCUPATIONS[role_index])
				if role == "local resident":
					role = "market stocker"
				_set_employment_fields(citizen, role)
				employment_changes += 1
			elif str(citizen.employment_status) == "employed" and transition_roll < 0.018:
				citizen.occupation_key = "local resident"
				citizen.occupation_title = "local resident"
				citizen.employment_status = "not_in_labor_force"
				citizen.employer_id = ""
				citizen.shift_start = 0
				citizen.shift_end = 0
				citizen.daily_wage = 0.0
				employment_changes += 1
		citizen.agenda = _generate_agenda(citizen)
		_population[citizen_id] = citizen
	for household_id_value in _households.keys():
		var household_id := str(household_id_value)
		var household := _households[household_id] as Dictionary
		var move_pressure := 0.14 if float(household.shared_balance) < 450.0 else 0.012
		if _roll01("move:%s:%d" % [household_id, day_index]) >= move_pressure:
			continue
		var current_block := str(household.home_block)
		var current_number := _block_number(current_block)
		var offset := -1 if _roll01("move_direction:%s:%d" % [household_id, day_index]) < 0.5 else 1
		var new_number := posmod(current_number + offset, 16)
		var new_block := "r%dc%d" % [int(new_number / 4) + 1, new_number % 4 + 1]
		household.home_block = new_block
		household.dwelling_id = "dwelling_%03d_month_%03d" % [new_number + 1, int(day_index / 30)]
		_households[household_id] = household
		for citizen_id_value in household.members as Array:
			var citizen := _population[str(citizen_id_value)] as Dictionary
			citizen.home_block = new_block
			var known_places := citizen.known_places as Array
			if not known_places.has(new_block):
				known_places.append(new_block)
			citizen.known_places = known_places
			_population[str(citizen_id_value)] = citizen
		household_moves += 1
	for relationship_key_value in _relationships.keys():
		var relationship_key := str(relationship_key_value)
		var relationship := _relationships[relationship_key] as Dictionary
		if str(relationship.layer) != "household":
			relationship.familiarity = clampf(float(relationship.familiarity) * 0.985, 0.0, 1.0)
			relationship.conflict = clampf(float(relationship.conflict) * 0.94, 0.0, 1.0)
			_relationships[relationship_key] = relationship
	_rebuild_institution_employees()
	_record_event("life_course_month_processed", {
		"month": int(day_index / 30),
		"employment_changes": employment_changes,
		"health_changes": health_changes,
		"household_moves": household_moves
	})


func _set_employment_fields(citizen: Dictionary, role: String) -> void:
	citizen.occupation_key = role
	citizen.occupation_title = role
	citizen.employment_status = "employed"
	citizen.employer_id = str(ROLE_TO_INSTITUTION.get(role, "central_market"))
	citizen.shift_start = 7 * 60 + _roll_int("rehire_shift:%s:%d" % [str(citizen.citizen_id), day_index], 20, 120)
	citizen.shift_end = int(citizen.shift_start) + 8 * 60
	citizen.daily_wage = float(ROLE_DAILY_WAGE.get(role, 76.0))


func _rebuild_institution_employees() -> void:
	for institution_id_value in _institutions.keys():
		var institution := _institutions[institution_id_value] as Dictionary
		institution.employee_ids = []
		_institutions[institution_id_value] = institution
	for citizen_id_value in _population.keys():
		var citizen := _population[citizen_id_value] as Dictionary
		var employer_id := str(citizen.employer_id)
		if employer_id.is_empty() or not _institutions.has(employer_id):
			continue
		var institution := _institutions[employer_id] as Dictionary
		var employees := institution.employee_ids as Array
		employees.append(str(citizen_id_value))
		institution.employee_ids = employees
		_institutions[employer_id] = institution


func _block_number(block: String) -> int:
	var pieces := block.split("c")
	if pieces.size() != 2:
		return 0
	var row := int(str(pieces[0]).trim_prefix("r")) - 1
	var column := int(pieces[1]) - 1
	return clampi(row, 0, 3) * 4 + clampi(column, 0, 3)


func validate_invariants() -> Array[String]:
	var errors: Array[String] = []
	if _population.size() < _visible_ids.size():
		errors.append("visible population exceeds authoritative population")
	for household_id_value in _households.keys():
		var household_id := str(household_id_value)
		var household := _households[household_id] as Dictionary
		var members := household.members as Array
		if members.is_empty():
			errors.append("%s has no members" % household_id)
			continue
		var has_adult := false
		for citizen_id_value in members:
			var citizen_id := str(citizen_id_value)
			if not _population.has(citizen_id):
				errors.append("%s references missing %s" % [household_id, citizen_id])
				continue
			var citizen := _population[citizen_id] as Dictionary
			if str(citizen.household_id) != household_id:
				errors.append("%s has inconsistent household ownership" % citizen_id)
			if int(citizen.age) >= 20:
				has_adult = true
		if not has_adult:
			errors.append("%s has no adult" % household_id)
		if float(household.shared_balance) < 0.0:
			errors.append("%s has negative shared balance" % household_id)
	for citizen_id_value in _population.keys():
		var citizen_id := str(citizen_id_value)
		var citizen := _population[citizen_id] as Dictionary
		if not _households.has(str(citizen.household_id)):
			errors.append("%s has no valid household" % citizen_id)
		if float(citizen.funds) < 0.0:
			errors.append("%s has negative funds" % citizen_id)
		var previous_end := 0
		for activity_value in citizen.agenda as Array:
			var activity := activity_value as Dictionary
			if int(activity.start_minute) < previous_end or int(activity.end_minute) < int(activity.start_minute):
				errors.append("%s has an overlapping or reversed agenda" % citizen_id)
				break
			previous_end = int(activity.end_minute)
	for station_id_value in _station_reservations.keys():
		var station_id := str(station_id_value)
		var citizen_id := str(_station_reservations[station_id])
		if not _stations.has(station_id) or not _population.has(citizen_id):
			errors.append("invalid station reservation %s -> %s" % [station_id, citizen_id])
	for work_order_id_value in _work_orders.keys():
		var work_order_id := str(work_order_id_value)
		var work_order := _work_orders[work_order_id] as Dictionary
		if not _stations.has(str(work_order.station_id)):
			errors.append("%s references a missing station" % work_order_id)
		var assigned_id := str(work_order.assigned_citizen_id)
		if not assigned_id.is_empty() and not _population.has(assigned_id):
			errors.append("%s references a missing citizen" % work_order_id)
	for citizen_id_value in _active_trip_by_citizen.keys():
		var citizen_id := str(citizen_id_value)
		var trip_id := str(_active_trip_by_citizen[citizen_id])
		if not _population.has(citizen_id) or not _trips.has(trip_id):
			errors.append("invalid active trip binding %s -> %s" % [citizen_id, trip_id])
			continue
		var trip := _trips[trip_id] as Dictionary
		if str(trip.status) != "travelling" or str(trip.citizen_id) != citizen_id:
			errors.append("inconsistent active trip %s" % trip_id)
	for visible_id in _visible_ids:
		if not _population.has(visible_id) or not bool((_population[visible_id] as Dictionary).is_embodied):
			errors.append("invalid embodied citizen %s" % visible_id)
	return errors


func get_debug_summary() -> Dictionary:
	var presentation_workers := 0
	for citizen_id in _visible_ids:
		if bool((_population[citizen_id] as Dictionary).presentation_worker):
			presentation_workers += 1
	return {
		"population": _population.size(),
		"households": _households.size(),
		"institutions": _institutions.size(),
		"visible_citizens": _visible_ids.size(),
		"relationship_edges": _relationships.size(),
		"stations": _stations.size(),
		"work_orders_total": _work_orders.size(),
		"work_orders_open": _count_work_orders(["open", "assigned", "in_progress"]),
		"work_orders_completed": _count_work_orders(["completed"]),
		"social_events": _social_events.size(),
		"social_events_active": _count_social_events(["active"]),
		"social_events_completed": _count_social_events(["completed"]),
		"trips_total": _trips.size(),
		"trips_active": _active_trip_by_citizen.size(),
		"trips_completed": _count_trips(["arrived"]),
		"presentation_workers": presentation_workers,
		"event_count": _events.size(),
		"simulation_day": day_index,
		"simulation_minute_of_day": int(fposmod(simulation_minute, 24.0 * 60.0)),
		"seed": _seed,
		"transport_snapshot": _transport_snapshot.duplicate(true),
		"invariant_errors": _last_validation_errors.duplicate()
	}


func _count_work_orders(statuses: Array) -> int:
	var count := 0
	for work_order_value in _work_orders.values():
		if statuses.has(str((work_order_value as Dictionary).status)):
			count += 1
	return count


func _count_trips(statuses: Array) -> int:
	var count := 0
	for trip_value in _trips.values():
		if statuses.has(str((trip_value as Dictionary).status)):
			count += 1
	return count


func _count_social_events(statuses: Array) -> int:
	var count := 0
	for event_value in _social_events.values():
		if statuses.has(str((event_value as Dictionary).status)):
			count += 1
	return count


func get_recent_events(limit := 20) -> Array[Dictionary]:
	var output: Array[Dictionary] = []
	var start := maxi(0, _events.size() - limit)
	for index in range(start, _events.size()):
		output.append((_events[index] as Dictionary).duplicate(true))
	return output


func snapshot_state() -> Dictionary:
	return {
		"simulation_minute": simulation_minute,
		"day_index": day_index,
		"seed": _seed,
		"population": _population.duplicate(true),
		"households": _households.duplicate(true),
		"institutions": _institutions.duplicate(true),
		"relationships": _relationships.duplicate(true),
		"stations": _stations.duplicate(true),
		"asset_state": _asset_state.duplicate(true),
		"work_orders": _work_orders.duplicate(true),
		"social_events": _social_events.duplicate(true),
		"trips": _trips.duplicate(true),
		"active_trip_by_citizen": _active_trip_by_citizen.duplicate(true),
		"assignments": _assignments.duplicate(true),
		"station_open_order": _station_open_order.duplicate(true),
		"station_reservations": _station_reservations.duplicate(true),
		"visible_ids": _visible_ids.duplicate(),
		"visible_by_index": _visible_by_index.duplicate(true),
		"household_head_ids": _household_head_ids.duplicate(),
		"events": _events.duplicate(true),
		"event_sequence": _event_sequence,
		"work_order_sequence": _work_order_sequence,
		"trip_sequence": _trip_sequence,
		"transport_snapshot": _transport_snapshot.duplicate(true),
		"last_processed_hour": _last_processed_hour,
		"last_visible_refresh_minute": _last_visible_refresh_minute
	}


func restore_state(snapshot: Dictionary) -> void:
	simulation_minute = float(snapshot.simulation_minute)
	day_index = int(snapshot.day_index)
	_seed = int(snapshot.seed)
	_population = (snapshot.population as Dictionary).duplicate(true)
	_households = (snapshot.households as Dictionary).duplicate(true)
	_institutions = (snapshot.institutions as Dictionary).duplicate(true)
	_relationships = (snapshot.relationships as Dictionary).duplicate(true)
	_stations = (snapshot.stations as Dictionary).duplicate(true)
	_asset_state = (snapshot.asset_state as Dictionary).duplicate(true)
	_work_orders = (snapshot.work_orders as Dictionary).duplicate(true)
	_social_events = (snapshot.social_events as Dictionary).duplicate(true)
	_trips = (snapshot.trips as Dictionary).duplicate(true)
	_active_trip_by_citizen = (snapshot.active_trip_by_citizen as Dictionary).duplicate(true)
	_assignments = (snapshot.assignments as Dictionary).duplicate(true)
	_station_open_order = (snapshot.station_open_order as Dictionary).duplicate(true)
	_station_reservations = (snapshot.station_reservations as Dictionary).duplicate(true)
	_visible_ids.assign(snapshot.visible_ids as Array)
	_visible_by_index = (snapshot.visible_by_index as Dictionary).duplicate(true)
	_household_head_ids.assign(snapshot.household_head_ids as Array)
	_events.assign(snapshot.events as Array)
	_event_sequence = int(snapshot.event_sequence)
	_work_order_sequence = int(snapshot.work_order_sequence)
	_trip_sequence = int(snapshot.trip_sequence)
	_transport_snapshot = (snapshot.transport_snapshot as Dictionary).duplicate(true)
	_last_processed_hour = int(snapshot.last_processed_hour)
	_last_visible_refresh_minute = int(snapshot.get("last_visible_refresh_minute", floori(simulation_minute)))
	_last_validation_errors = validate_invariants()


func state_fingerprint() -> int:
	var stable_state := {
		"simulation_minute": simulation_minute,
		"day_index": day_index,
		"population": _population,
		"households": _households,
		"institutions": _institutions,
		"relationships": _relationships,
		"work_orders": _work_orders,
		"social_events": _social_events,
		"trips": _trips,
		"transport_snapshot": _transport_snapshot,
		"assignments": _assignments,
		"events": _events
	}
	return hash(JSON.stringify(stable_state))


func _record_event(kind: String, payload: Dictionary) -> void:
	_event_sequence += 1
	_events.append({
		"event_id": "event_%07d" % _event_sequence,
		"kind": kind,
		"simulation_minute": simulation_minute,
		"day": day_index,
		"payload": payload.duplicate(true)
	})
	if _events.size() > 4000:
		_events.pop_front()


func _citizen_id(number: int) -> String:
	return "citizen_%04d" % number


func _roll01(key: String) -> float:
	var rng := RandomNumberGenerator.new()
	rng.seed = int(hash("%d|%s" % [_seed, key])) & 0x7fffffff
	return rng.randf()


func _roll_int(key: String, minimum: int, maximum: int) -> int:
	if maximum <= minimum:
		return minimum
	var rng := RandomNumberGenerator.new()
	rng.seed = int(hash("%d|%s" % [_seed, key])) & 0x7fffffff
	return rng.randi_range(minimum, maximum)
