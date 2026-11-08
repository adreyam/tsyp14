extends Node3D

## Asset registry and visual authoring layer for the control room's Asset Map.
## Every registered object has a semantic group, operational metadata, and a
## service Marker3D so later citizen/tool interactions can target a stable point
## instead of guessing from an imported model pivot.

const CitySpatialSafety = preload("res://scripts/city_spatial_safety.gd")
const CityZones = preload("res://scripts/city_zones.gd")
const BuildingWindowBuilder = preload("res://scripts/building_window_builder.gd")
const BuildingWindowController = preload("res://scripts/building_window_controller.gd")
const SMOKE_SHADER := preload("res://shaders/soft_black_exhaust_smoke.gdshader")
const BUILDING_WINDOW_SHADER := preload("res://shaders/building_interior_window.gdshader")

## Building illumination may only reuse polygons already present in the imported
## model. A texture mask is used only for validated single-atlas models whose
## UVs isolate their windows. The hospital instead selects its circular caps by
## connected components while retaining the imported triangle indices exactly.
const BUILDING_LIGHT_PROFILES := {
	"Apartment building.glb":{"fixtures":[{"mount_height":2.55,"lateral":0.12,"place":"right masonry jamb of apartment lobby"}], "glass":[], "texture_mask":5},
	"Skyscraper.glb":{"fixtures":[{"mount_height":2.8,"lateral":0.11,"place":"right masonry jamb of tower lobby"}], "glass":[], "texture_mask":3},
	"House with driveway.glb":{"face":Vector3.FORWARD, "fixtures":[{"mount_height":2.35,"lateral":0.08,"place":"wall beside covered front porch"}], "glass":[], "texture_mask":0, "component_windows":"house_windows", "component_mask":6},
	"Large Building.glb":{"fixtures":[{"anchor_material":"door","anchor_side":1.0,"mount_height":2.5,"lateral":0.0,"place":"wall beside canopied main entrance"}], "glass":["window"], "texture_mask":0},
	"Large Building-ppwtREejXg.glb":{"fixtures":[{"anchor_material":"door","anchor_side":1.0,"mount_height":2.45,"lateral":0.0,"place":"right jamb of central entrance"}], "glass":["window"], "texture_mask":0},
	"Town House.glb":{"fixtures":[{"mount_height":2.45,"lateral":0.08,"place":"masonry beside townhouse entrance arch"}], "glass":["darkgrey"], "texture_mask":0},
	"Building.glb":{"fixtures":[{"mount_height":2.5,"lateral":0.12,"place":"masonry beside office entrance arch"}], "glass":["darkgrey"], "texture_mask":0},
	"Bar.glb":{"face":Vector3.RIGHT, "fixtures":[{"ground_from_pivot":true,"mount_height":2.45,"lateral":0.17,"place":"solid pier beside bar storefront door"}], "glass":[], "texture_mask":4, "sign_components":"bar_letters"},
	"Hotel Building.glb":{"face":Vector3.FORWARD, "fixtures":[{"mount_height":2.45,"lateral":0.34,"place":"wall beside hotel reception entrance"}], "glass":["mat24"], "texture_mask":0},
	"Hospital.glb":{"face":Vector3.RIGHT, "fixtures":[{"mount_height":2.65,"lateral":-0.30,"place":"public entrance wall"},{"mount_height":2.65,"lateral":0.26,"place":"emergency entrance wall"}], "glass":[], "texture_mask":0, "component_windows":"hospital_windows", "component_mask":0, "label_sign":"HOSPITAL"},
	"Building Green.glb":{"fixtures":[{"mount_height":2.45,"lateral":0.17,"place":"masonry beside shop entrance"}], "glass":["window_glass"], "texture_mask":0},
	"Building Red.glb":{"fixtures":[{"mount_height":2.45,"lateral":0.17,"place":"masonry beside double doors"}], "glass":["window_glass"], "texture_mask":0},
	"Building Red Corner.glb":{"fixtures":[{"anchor_material":"wood","anchor_side":1.0,"mount_height":2.45,"lateral":0.0,"place":"masonry beside corner-shop entrance"}], "glass":["window_glass"], "texture_mask":0},
	"Brown Building.glb":{"fixtures":[{"mount_height":2.45,"lateral":0.0,"place":"masonry beside residential entrance"}], "glass":["window_glass"], "texture_mask":0},
	"Big Building.glb":{"fixtures":[{"anchor_material":"brown","anchor_side":1.0,"mount_height":2.65,"lateral":0.0,"place":"wall beside columned main entrance"}], "glass":["darkgrey"], "texture_mask":0},
	"Pizza Corner.glb":{"fixtures":[{"anchor_material":"door_white","anchor_side":1.0,"mount_height":2.45,"lateral":0.0,"place":"masonry beside pizza-shop entrance"}], "glass":["window_glass"], "texture_mask":0},
}

## Window planes found inside the imported meshes (see building_window_builder.gd).
## "glass" models use their glazing material; "atlas_pane" models keep windows as
## quads of one atlas surface and are selected by the pane colour of that quad;
## "painted" models only have windows as pixels and are lit through a UV patch map.
const WINDOW_RULES := {
	"Apartment building.glb":{"select":"atlas_pane", "pane":{"lum":[0.58, 1.0], "sat":[0.0, 0.14]}, "max_area":3.0, "max_normal_y":0.35},
	"House with driveway.glb":{"select":"atlas_pane", "pane":{"lum":[0.10, 0.75], "sat":[0.0, 0.5], "blue_over_red":0.02}, "max_area":3.0, "max_normal_y":0.35},
	"Skyscraper.glb":{"select":"painted", "max_normal_y":0.35},
}

const NOISE_SITES := [
	Vector3(-176.0, 0.17, -62.0), Vector3(-81.0, 0.17, 128.0),
	Vector3(109.0, 0.17, 33.0), Vector3(176.0, 0.17, 128.0),
]
const WEATHER_SITES := [
	Vector3(-166.0, 0.17, 166.0), Vector3(-71.0, 0.17, -119.0),
	Vector3(119.0, 0.17, 71.0), Vector3(166.0, 0.17, -166.0),
]
const FLOOD_SITES := [
	Vector3(-119.0, 0.17, -176.0), Vector3(-24.0, 0.17, 109.0),
	Vector3(71.0, 0.17, -81.0), Vector3(166.0, 0.17, 109.0),
]
const IRRIGATION_SITES := [
	Vector3(-160.0, 0.17, 119.0), Vector3(-65.0, 0.17, 24.0),
	Vector3(30.0, 0.17, -166.0), Vector3(125.0, 0.17, 24.0),
]
## One seismograph per zone (A to P), requested near the zone centre; the placement pass moves each to a safe kerb spot.
const SEISMIC_SITE_OFFSET := Vector3(15.0, 0.17, 15.0)
const SMOKY_VEHICLE_INDICES := [3, 7, 11, 14, 17, 23]

var _materials: Dictionary = {}
var _window_glow_materials: Dictionary = {}
var _normalized_building_materials: Dictionary = {}
var _component_window_meshes: Dictionary = {}
var _wall_cache: Dictionary = {}
var _wall_atlas_images: Dictionary = {}


func _ready() -> void:
	_tag_facilities_and_fleets()
	_tag_existing_actuators()
	_upgrade_authored_sensors()
	_create_missing_sensor_families()
	_create_seismic_sensors()
	_create_missing_actuator_families()
	_create_building_night_lights()
	_resolve_device_placement()
	call_deferred("_attach_exhaust_emitters")


func _tag_facilities_and_fleets() -> void:
	var hospital_index := 0
	for value in get_tree().get_nodes_in_group("buildings") + get_tree().get_nodes_in_group("backdrop_buildings"):
		if not value is Node3D:
			continue
		var building := value as Node3D
		if "hospital" in building.scene_file_path.to_lower():
			hospital_index += 1
			_register_asset(building, ["hospitals", "emergency_facilities"], {
				"asset_type":"hospital", "facility_id":"HOSP-%02d" % hospital_index,
				"model_family":"external_poly_pizza_hospital",
				"future_interaction":"patient_arrival_and_clinical_service",
			})

	# Reuse a detailed, grounded external office building as the civic police
	# facility and add roof identity hardware. This avoids a duplicate building
	# footprint while providing an unambiguous operational model in the city.
	var police_building := get_parent().get_node_or_null("Buildings/Block_r1c2/Block_r1c2_Building_02") as Node3D
	if police_building != null:
		_register_asset(police_building, ["police_facilities", "emergency_facilities"], {
			"asset_type":"police_station", "facility_id":"POLICE-HQ-01",
			"model_family":"external_poly_pizza_small_office_civic_conversion",
			"future_interaction":"officer_dispatch_and_public_assistance",
		})
		_add_police_roof_identity(police_building)

	for value in get_tree().get_nodes_in_group("vehicles") + get_tree().get_nodes_in_group("backdrop_vehicles"):
		if not value is Node3D:
			continue
		var vehicle := value as Node3D
		var path := vehicle.scene_file_path.to_lower()
		if "police" in path:
			_register_asset(vehicle, ["police_vehicles", "emergency_vehicles"], {
				"asset_type":"police_vehicle", "future_interaction":"officer_dispatch_and_vehicle_access",
			})
		elif "ambulance" in path:
			_register_asset(vehicle, ["ambulance_vehicles", "emergency_vehicles"], {
				"asset_type":"ambulance", "future_interaction":"paramedic_dispatch_and_patient_transfer",
			})


func _tag_existing_actuators() -> void:
	for group_name in ["traffic_signals", "water_valves", "power_switches"]:
		for value in get_tree().get_nodes_in_group(group_name):
			if not value is Node3D:
				continue
			var actuator := value as Node3D
			actuator.add_to_group("actuators")
			actuator.set_meta("asset_type", group_name.trim_suffix("s"))
			actuator.set_meta("future_interaction", "authorized_%s_operation" % group_name.trim_suffix("s"))
			actuator.set_meta("highlightable", true)
			_ensure_service_anchor(actuator, Vector3(0.0, 1.05, 0.62))


func _upgrade_authored_sensors() -> void:
	var traffic_index := 0
	for value in get_tree().get_nodes_in_group("traffic_sensors"):
		if not value is Node3D:
			continue
		traffic_index += 1
		var sensor := value as Node3D
		_hide_legacy_sensor_visuals(sensor)
		sensor.global_position.y = 0.17
		_register_asset(sensor, ["smart_city_devices"], {
			"asset_type":"traffic_radar_detector", "sensor_id":"TRAF-%02d" % traffic_index,
			"sensed_metrics":["vehicle_count", "speed", "occupancy"],
			"model_family":"purpose_built_traffic_radar_v2",
			"future_interaction":"calibration_and_lens_cleaning",
		})
		_build_traffic_detector(sensor)

	var air_index := 0
	for value in get_tree().get_nodes_in_group("environment_sensors"):
		if not value is Node3D:
			continue
		air_index += 1
		var sensor := value as Node3D
		_hide_legacy_sensor_visuals(sensor)
		sensor.global_position.y = 0.17
		_register_asset(sensor, ["air_quality_monitors", "smart_city_devices"], {
			"asset_type":"air_quality_monitor", "sensor_id":"AIR-%02d" % air_index,
			"sensed_metrics":["pm2_5", "pm10", "nitrogen_dioxide", "vehicle_smoke"],
			"model_family":"purpose_built_air_quality_station_v2",
			"future_interaction":"filter_swap_and_sensor_calibration",
		})
		_build_air_quality_monitor(sensor)

	var utility_index := 0
	for value in get_tree().get_nodes_in_group("infrastructure_sensors"):
		if not value is Node3D:
			continue
		utility_index += 1
		var sensor := value as Node3D
		sensor.global_position.y = 0.17
		sensor.add_to_group("utility_telemetry_sensors")
		sensor.add_to_group("smart_city_devices")
		sensor.set_meta("asset_type", "utility_telemetry_gateway")
		sensor.set_meta("sensor_id", "UTIL-%02d" % utility_index)
		sensor.set_meta("sensed_metrics", ["voltage", "water_pressure", "cabinet_temperature"])
		sensor.set_meta("future_interaction", "telemetry_diagnostics_and_module_swap")
		var beacon := sensor.get_node_or_null("Beacon") as Node3D
		if beacon != null:
			beacon.visible = false
		_build_telemetry_detail(sensor)
		_ensure_service_anchor(sensor, Vector3(0.0, 1.0, 0.65))


func _create_missing_sensor_families() -> void:
	var root := Node3D.new()
	root.name = "PurposeBuiltSensors"
	add_child(root)
	for index in NOISE_SITES.size():
		var sensor := _new_registered_device(root, "NoiseMonitor_%02d" % (index + 1), NOISE_SITES[index], ["sensors", "noise_monitors"], {
			"asset_type":"noise_monitor", "sensor_id":"NOISE-%02d" % (index + 1),
			"sensed_metrics":["db_a", "peak_noise", "traffic_noise"],
			"model_family":"purpose_built_class_one_sound_monitor",
			"future_interaction":"microphone_calibration_and_windscreen_service",
		})
		_build_noise_monitor(sensor)
	for index in WEATHER_SITES.size():
		var sensor := _new_registered_device(root, "WeatherStation_%02d" % (index + 1), WEATHER_SITES[index], ["sensors", "weather_stations"], {
			"asset_type":"weather_station", "sensor_id":"WX-%02d" % (index + 1),
			"sensed_metrics":["wind_speed", "wind_direction", "rainfall", "temperature"],
			"model_family":"purpose_built_compact_weather_station",
			"future_interaction":"anemometer_and_rain_gauge_service",
		})
		_build_weather_station(sensor)
	for index in FLOOD_SITES.size():
		var sensor := _new_registered_device(root, "FloodLevelSensor_%02d" % (index + 1), FLOOD_SITES[index], ["sensors", "flood_level_sensors"], {
			"asset_type":"flood_level_sensor", "sensor_id":"FLOOD-%02d" % (index + 1),
			"sensed_metrics":["surface_water_depth", "rise_rate"],
			"model_family":"purpose_built_non_contact_level_radar",
			"future_interaction":"radar_alignment_and_drain_inspection",
		})
		_build_flood_sensor(sensor)


func _create_seismic_sensors() -> void:
	var seismic_root := Node3D.new()
	seismic_root.name = "SeismicSensors"
	add_child(seismic_root)
	for index in CityZones.IDS.size():
		var zone := str(CityZones.IDS[index])
		var site := CityZones.centre(zone) + SEISMIC_SITE_OFFSET
		var sensor := _new_registered_device(seismic_root, "SeismicSensor_%s" % zone, site, ["sensors", "seismic_sensors"], {
			"asset_type":"seismic_sensor", "sensor_id":"SEIS-%02d" % (index + 1), "zone":zone,
			"sensed_metrics":["ground_acceleration", "ground_velocity", "shaking_intensity"],
			"model_family":"purpose_built_strong_motion_accelerograph",
			"future_interaction":"accelerometer_calibration_and_enclosure_service",
		})
		_build_seismic_sensor(sensor)


func _build_seismic_sensor(sensor: Node3D) -> void:
	var model := _model_root(sensor, "StrongMotionAccelerographModel")
	var concrete := _material("seismic_concrete", Color(0.55, 0.55, 0.53), 0.9)
	var shell := _material("seismic_shell", Color(0.93, 0.52, 0.12), 0.5)
	var steel := _material("steel", Color(0.16, 0.19, 0.22), 0.34)
	var glass := _material("optical_glass", Color(0.012, 0.065, 0.09), 0.16)
	var amber := _emissive_material("seismic_amber", Color(1.0, 0.62, 0.05), 3.0)
	_add_mesh(model, "ConcretePier", _box(Vector3(0.9, 0.34, 0.9)), Vector3(0, 0.17, 0), concrete)
	_add_mesh(model, "Cabinet", _box(Vector3(0.55, 0.72, 0.42)), Vector3(0, 0.7, 0), shell)
	_add_mesh(model, "StatusStrip", _box(Vector3(0.34, 0.05, 0.02)), Vector3(0, 0.86, -0.215), amber)
	_add_mesh(model, "Antenna", _cylinder(0.025, 1.0), Vector3(0.2, 1.56, 0.1), steel)
	_add_mesh(model, "SolarPanel", _box(Vector3(0.6, 0.04, 0.4)), Vector3(0, 1.1, 0.0), glass).rotation.x = deg_to_rad(16.0)
	_ensure_service_anchor(sensor, Vector3(0.0, 0.8, 0.55))


func _create_missing_actuator_families() -> void:
	var light_root := Node3D.new()
	light_root.name = "SmartStreetLights"
	add_child(light_root)
	var street_light_sites := _expanded_street_light_sites()
	for index in street_light_sites.size():
		var light := Node3D.new()
		light.name = "SmartStreetLight_%02d" % (index + 1)
		light.position = street_light_sites[index]
		light.rotation.y = _street_light_yaw(light.position)
		light_root.add_child(light)
		_register_asset(light, ["smart_city_devices", "smart_street_lights", "lighting_actuators", "actuators"], {
			"asset_type":"adaptive_street_light", "actuator_id":"LIGHT-%02d" % (index + 1),
			"model_family":"integrated_led_roadway_luminaire",
			"actuated_function":"dimming_and_public_realm_lighting",
			"future_interaction":"luminaire_and_controller_service",
		})
		_build_integrated_street_light(light)
		_ensure_service_anchor(light, Vector3(0.0, 0.85, 0.45))

	var irrigation_root := Node3D.new()
	irrigation_root.name = "IrrigationActuators"
	add_child(irrigation_root)
	for index in IRRIGATION_SITES.size():
		var valve := _new_registered_device(irrigation_root, "IrrigationValve_%02d" % (index + 1), IRRIGATION_SITES[index], ["irrigation_valves", "actuators"], {
			"asset_type":"irrigation_control_valve", "actuator_id":"IRR-%02d" % (index + 1),
			"model_family":"purpose_built_irrigation_valve_assembly",
			"actuated_function":"park_irrigation_zone_flow",
			"future_interaction":"manual_override_and_valve_service",
		})
		_build_irrigation_valve(valve)


func _expanded_street_light_sites() -> Array[Vector3]:
	## Put a luminaire at the midpoint of every face of all 64 city blocks, just
	## inside the road edge and 2.8 m away from the citizen sidewalk graph. The
	## previous 4x4 loop covered only downtown even though CityBackdrop extends
	## the same street grid to 8x8. These pools overlap across the carriageway and
	## walking perimeter without putting a pole on either travel path.
	var sites: Array[Vector3] = []
	for row in range(8):
		for column in range(8):
			var centre := Vector3(-332.5 + float(column) * 95.0, 0.17, -332.5 + float(row) * 95.0)
			for offset in [Vector3(0, 0, -35.2), Vector3(35.2, 0, 0), Vector3(0, 0, 35.2), Vector3(-35.2, 0, 0)]:
				sites.append(centre + (offset as Vector3))
	return sites


func _street_light_yaw(position_value: Vector3) -> float:
	var road_lines: Array = CitySpatialSafety.ROAD_CENTERS
	var nearest_x: float = road_lines[0]
	var nearest_z: float = road_lines[0]
	for line in road_lines:
		if absf(float(line) - position_value.x) < absf(float(nearest_x) - position_value.x): nearest_x = float(line)
		if absf(float(line) - position_value.z) < absf(float(nearest_z) - position_value.z): nearest_z = float(line)
	var outward := Vector3(nearest_x - position_value.x, 0.0, 0.0)
	if absf(nearest_z - position_value.z) < absf(nearest_x - position_value.x):
		outward = Vector3(0.0, 0.0, nearest_z - position_value.z)
	return atan2(-outward.z, outward.x)


func _create_building_night_lights() -> void:
	var fixture_metal := _material("building_lantern_metal", Color(0.055, 0.065, 0.075), 0.24)
	var fixture_trim := _material("building_lantern_trim", Color(0.18, 0.13, 0.075), 0.28)
	var lamp_material := _emissive_material("building_lantern_lens", Color(1.0, 0.72, 0.35), 13.0)
	var building_index := 0
	var seen_buildings := {}
	for value in get_tree().get_nodes_in_group("buildings") + get_tree().get_nodes_in_group("backdrop_buildings"):
		if not value is Node3D or not (value as Node3D).visible:
			continue
		var building := value as Node3D
		if seen_buildings.has(building.get_instance_id()):
			continue
		seen_buildings[building.get_instance_id()] = true
		var bounds := CitySpatialSafety.world_bounds(building)
		if bounds.size == Vector3.ZERO:
			continue
		building_index += 1
		var model_name := building.scene_file_path.get_file()
		var profile := BUILDING_LIGHT_PROFILES.get(model_name, {}) as Dictionary
		_disable_imported_building_emission(building)
		# Windows first: the street-side lanterns are placed around them.
		_add_building_window_glow(building, building_index, profile)
		var outward := _street_outward(bounds.get_center())
		var fixture_definitions: Array = profile.get("fixtures", []) as Array
		for fixture_slot in fixture_definitions.size():
			_add_reviewed_building_fixture(building, building_index, fixture_slot, model_name, fixture_definitions[fixture_slot] as Dictionary, bounds, outward, fixture_metal, fixture_trim, lamp_material)
		if not str(profile.get("label_sign", "")).is_empty():
			_add_building_sign_label(building, building_index, str(profile.label_sign), bounds, outward)


func _street_outward(position_value: Vector3) -> Vector3:
	## The facade that faces the street: toward the nearest road centre line.
	var road_lines: Array = CitySpatialSafety.ROAD_CENTERS
	var nearest_x: float = road_lines[0]
	var nearest_z: float = road_lines[0]
	for line in road_lines:
		if absf(float(line) - position_value.x) < absf(float(nearest_x) - position_value.x): nearest_x = float(line)
		if absf(float(line) - position_value.z) < absf(float(nearest_z) - position_value.z): nearest_z = float(line)
	if absf(nearest_z - position_value.z) < absf(nearest_x - position_value.x):
		return Vector3(0.0, 0.0, signf(nearest_z - position_value.z))
	return Vector3(signf(nearest_x - position_value.x), 0.0, 0.0)


func _add_reviewed_building_fixture(building: Node3D, building_index: int, fixture_slot: int, model_name: String, definition: Dictionary, bounds: AABB, outward: Vector3, fixture_metal: StandardMaterial3D, fixture_trim: StandardMaterial3D, lamp_material: StandardMaterial3D) -> void:
	var ground_y := building.global_position.y if bool(definition.get("ground_from_pivot", false)) else bounds.position.y
	var mount_height := float(definition.get("mount_height", 2.45))
	var fixture_outward := outward
	var mounting_y := ground_y + mount_height
	var tangent := Vector3(-outward.z, 0.0, outward.x)
	var facade_width := bounds.size.z if absf(outward.x) > 0.5 else bounds.size.x
	var lateral := clampf(float(definition.get("lateral", 0.0)), -0.3, 0.3)
	var anchor_material := str(definition.get("anchor_material", ""))
	if not anchor_material.is_empty():
		# An entrance on the street side: hang the lantern beside it.
		var anchor_bounds := _material_surface_world_bounds(building, anchor_material)
		if anchor_bounds.size != Vector3.ZERO:
			var depth_offset := (anchor_bounds.get_center() - bounds.get_center()).dot(outward)
			var half_depth := (bounds.size.x if absf(outward.x) > 0.5 else bounds.size.z) * 0.5
			if depth_offset > half_depth * 0.55:
				var anchor_span := absf(anchor_bounds.size.z) if absf(outward.x) > 0.5 else absf(anchor_bounds.size.x)
				var door_ratio := (anchor_bounds.get_center() - bounds.get_center()).dot(tangent) / maxf(facade_width, 0.01)
				lateral = door_ratio + float(definition.get("anchor_side", 1.0)) * (anchor_span * 0.5 + 0.45) / maxf(facade_width, 0.01)
	var surface := {"hit":false}
	for height_offset in [0.0, 0.15, 0.3, 0.55]:
		surface = _clear_street_wall(building, bounds, outward, mounting_y + float(height_offset), lateral)
		if bool(surface.get("hit", false)):
			mount_height += float(height_offset)
			break
	if not bool(surface.get("hit", false)):
		push_warning("No clear street wall for %s %s" % [model_name, definition.get("place", "entrance")])
		return
	lateral = float(surface.get("resolved_lateral", lateral))
	var fixture := Node3D.new()
	fixture.name = "BuildingEntranceLantern_%03d_%02d" % [building_index, fixture_slot + 1]
	fixture.add_to_group("building_night_lights")
	fixture.set_meta("source_building", building.get_path())
	fixture.set_meta("mounting_outward", fixture_outward)
	fixture.set_meta("mounting_gap", 0.035)
	fixture.set_meta("rendered_facade_hit", true)
	fixture.set_meta("mount_surface_material", str(surface.get("material", "")))
	fixture.set_meta("model_profile", model_name)
	fixture.set_meta("fixture_slot", fixture_slot)
	fixture.set_meta("logical_placement", str(definition.get("place", "entrance wall")))
	fixture.set_meta("mount_height_metres", mount_height)
	fixture.set_meta("reviewed_lateral", lateral)
	building.add_child(fixture)
	fixture.top_level = true
	fixture.global_transform = Transform3D(Basis.IDENTITY, (surface.position as Vector3) + fixture_outward * 0.035)
	fixture.look_at(fixture.global_position + fixture_outward, Vector3.UP)
	# The luminous element is inside a complete wall lantern, not a pasted plane.
	var backplate := _add_mesh(fixture, "WallBackplate", _cylinder(0.18, 0.07), Vector3.ZERO, fixture_metal)
	backplate.rotation.x = PI * 0.5
	var arm := _add_mesh(fixture, "LampArm", _cylinder(0.045, 0.30), Vector3(0.0, 0.08, -0.15), fixture_metal)
	arm.rotation.x = PI * 0.5
	var hood := _add_mesh(fixture, "LampHood", _cylinder(0.24, 0.13, 0.14), Vector3(0.0, 0.09, -0.32), fixture_metal)
	var trim := _add_mesh(fixture, "LampTrim", _cylinder(0.16, 0.055), Vector3(0.0, -0.30, -0.32), fixture_trim)
	var lens := _add_mesh(fixture, "BuildingLampGlow", _cylinder(0.105, 0.29, 0.085), Vector3(0.0, -0.12, -0.32), lamp_material)
	lens.visible = false
	lens.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	var cage_parts: Array[MeshInstance3D] = []
	for cage_x in [-0.13, 0.13]:
		cage_parts.append(_add_mesh(fixture, "CageRail", _cylinder(0.016, 0.32), Vector3(cage_x, -0.13, -0.32), fixture_metal))
	for cage_z in [-0.21, -0.43]:
		cage_parts.append(_add_mesh(fixture, "CageRail", _cylinder(0.016, 0.32), Vector3(0.0, -0.13, cage_z), fixture_metal))
	for part in [backplate, arm, hood, trim, lens] + cage_parts:
		(part as GeometryInstance3D).visibility_range_end = 0.0
		(part as GeometryInstance3D).visibility_range_end_margin = 0.0
	var light := SpotLight3D.new()
	light.name = "BuildingFacadeLight"
	light.position = Vector3(0.0, -0.15, -0.34)
	light.light_color = Color(1.0, 0.72, 0.38)
	light.light_energy = 0.0
	light.spot_range = 14.0
	light.spot_angle = 58.0
	light.shadow_enabled = false
	light.distance_fade_enabled = true
	light.distance_fade_begin = 260.0
	light.distance_fade_length = 120.0
	fixture.add_child(light)
	light.look_at(light.global_position + fixture_outward * 2.5 + Vector3.DOWN * 6.0, Vector3.UP)


func _material_surface_world_bounds(building: Node3D, requested_material: String) -> AABB:
	var result := AABB()
	var started := false
	for mesh_value in building.find_children("*", "MeshInstance3D", true, false):
		var mesh_node := mesh_value as MeshInstance3D
		if mesh_node.mesh == null or mesh_node.is_in_group("building_window_lights"):
			continue
		for surface_index in mesh_node.mesh.get_surface_count():
			var material := mesh_node.get_active_material(surface_index)
			if material == null or material.resource_name.to_lower() != requested_material.to_lower():
				continue
			var arrays := mesh_node.mesh.surface_get_arrays(surface_index)
			var vertices := arrays[Mesh.ARRAY_VERTEX] as PackedVector3Array
			for vertex in vertices:
				var world_vertex := mesh_node.to_global(vertex)
				if started:
					result = result.expand(world_vertex)
				else:
					result = AABB(world_vertex, Vector3.ZERO)
					started = true
	return result


func _wall_triangles(building: Node3D, bounds: AABB, outward: Vector3, low_y: float, high_y: float) -> Array:
	## World triangles of this building that face the street within a height band.
	var result: Array = []
	for mesh_value in building.find_children("*", "MeshInstance3D", true, false):
		var mesh_node := mesh_value as MeshInstance3D
		if mesh_node.mesh == null or not mesh_node.mesh is ArrayMesh or mesh_node.is_in_group("building_window_lights"):
			continue
		var transform := mesh_node.global_transform
		for surface_index in mesh_node.mesh.get_surface_count():
			var active_material := mesh_node.get_active_material(surface_index)
			var material_name := "" if active_material == null else active_material.resource_name.to_lower()
			var walls := _surface_wall_cache(mesh_node.mesh, surface_index)
			var vertices := walls.vertices as PackedVector3Array
			var uvs := walls.uvs as PackedVector2Array
			for tri in int(vertices.size() / 3):
				var a := transform * vertices[tri * 3]
				var b := transform * vertices[tri * 3 + 1]
				var c := transform * vertices[tri * 3 + 2]
				if maxf(a.y, maxf(b.y, c.y)) < low_y or minf(a.y, minf(b.y, c.y)) > high_y:
					continue
				var normal := (b - a).cross(c - a)
				if normal.length_squared() < 1.0e-12:
					continue
				normal = normal.normalized()
				if absf(normal.dot(outward)) < 0.9:
					continue
				result.append({"a":a, "b":b, "c":c, "material":material_name, "uv":[uvs[tri * 3], uvs[tri * 3 + 1], uvs[tri * 3 + 2]] if uvs.size() > tri * 3 + 2 else [], "texture":active_material.albedo_texture if active_material is StandardMaterial3D else null})
	return result


func _surface_wall_cache(mesh: Mesh, surface_index: int) -> Dictionary:
	## Non-degenerate triangles of one imported surface, mesh space. The mesh may
	## be rotated (Z-up exports), so facing is judged later, in the world.
	var key := "%d:%d" % [mesh.get_rid().get_id(), surface_index]
	if _wall_cache.has(key):
		return _wall_cache[key] as Dictionary
	var arrays := mesh.surface_get_arrays(surface_index)
	var vertices := arrays[Mesh.ARRAY_VERTEX] as PackedVector3Array
	var source_uvs: Variant = arrays[Mesh.ARRAY_TEX_UV]
	var source_indices: Variant = arrays[Mesh.ARRAY_INDEX]
	var indices := PackedInt32Array()
	if source_indices == null or (source_indices as PackedInt32Array).is_empty():
		for vertex_index in vertices.size():
			indices.append(vertex_index)
	else:
		indices = source_indices as PackedInt32Array
	var out_vertices := PackedVector3Array()
	var out_uvs := PackedVector2Array()
	for tri in int(indices.size() / 3):
		var a := vertices[indices[tri * 3]]
		var b := vertices[indices[tri * 3 + 1]]
		var c := vertices[indices[tri * 3 + 2]]
		var cross := (b - a).cross(c - a)
		if cross.length_squared() < 1.0e-14:
			continue
		out_vertices.append_array([a, b, c])
		for corner in 3:
			out_uvs.append((source_uvs as PackedVector2Array)[indices[tri * 3 + corner]] if source_uvs != null else Vector2.ZERO)
	var entry := {"vertices":out_vertices, "uvs":out_uvs}
	_wall_cache[key] = entry
	return entry


func _ray_wall(triangles: Array, origin: Vector3, direction: Vector3) -> Dictionary:
	var best := INF
	var found := {}
	for triangle in triangles:
		var hit: Variant = Geometry3D.ray_intersects_triangle(origin, direction, triangle.a, triangle.b, triangle.c)
		if hit is Vector3:
			var distance := origin.distance_to(hit as Vector3)
			if distance < best:
				best = distance
				found = {"point":hit, "triangle":triangle, "distance":distance}
	return found


func _clear_street_wall(building: Node3D, bounds: AABB, outward: Vector3, mounting_y: float, preferred_lateral: float) -> Dictionary:
	## Scans the street-facing facade for a flat stretch of solid wall at the
	## mounting height that is clear of windows, glazing, doors and the building
	## edges, and returns the clear spot closest to the preferred position.
	var centre := bounds.get_center()
	var tangent := Vector3(-outward.z, 0.0, outward.x)
	var facade_width := bounds.size.z if absf(outward.x) > 0.5 else bounds.size.x
	var start_distance := maxf(bounds.size.x, bounds.size.z) + 8.0
	var triangles := _wall_triangles(building, bounds, outward, mounting_y - 0.7, mounting_y + 0.7)
	if triangles.is_empty():
		return {"hit":false}
	var windows: Array[Dictionary] = _window_obstacles(building)
	var ratios: Array = []
	var steps := 36
	for step in range(steps + 1):
		ratios.append(-0.45 + 0.9 * float(step) / float(steps))
	ratios.sort_custom(func(a: float, b: float) -> bool: return absf(a - preferred_lateral) < absf(b - preferred_lateral))
	for ratio_value in ratios:
		var ratio := float(ratio_value)
		var base := Vector3(centre.x, mounting_y, centre.z) + tangent * facade_width * ratio
		var primary := _ray_wall(triangles, base + outward * start_distance, -outward)
		if primary.is_empty() or _invalid_lantern_surface(str(primary.triangle.material)):
			continue
		var point := primary.point as Vector3
		var clear := true
		# The wall around the lantern must be one flat, solid, non-glazed plane.
		for offset in [Vector2(0.28, 0.0), Vector2(-0.28, 0.0), Vector2(0.0, 0.3), Vector2(0.0, -0.3)]:
			var probe_origin := point + outward * 3.0 + tangent * (offset as Vector2).x + Vector3.UP * (offset as Vector2).y
			var neighbour := _ray_wall(triangles, probe_origin, -outward)
			if neighbour.is_empty() or absf(float(neighbour.distance) - 3.0) > 0.3 or _invalid_lantern_surface(str(neighbour.triangle.material)):
				clear = false
				break
			if not _atlas_pixel_is_solid_wall(neighbour.triangle, neighbour.point as Vector3):
				clear = false
				break
		if not clear or not _atlas_pixel_is_solid_wall(primary.triangle, point):
			continue
		for window in windows:
			var offset_from_window := point - (window.position as Vector3)
			var across := absf(offset_from_window.dot(tangent))
			var vertical := absf(offset_from_window.y)
			if across < float(window.half_width) + 0.45 and vertical < float(window.half_height) + 0.45 and absf(offset_from_window.dot(outward)) < 1.5:
				clear = false
				break
		if clear:
			return {"position":point, "hit":true, "material":str(primary.triangle.material), "resolved_lateral":ratio}
	return {"hit":false}


func _window_obstacles(building: Node3D) -> Array[Dictionary]:
	## Every window of this building as a world-space box (position, half size).
	var result: Array[Dictionary] = []
	var controller := get_node_or_null("BuildingWindowController")
	if controller != null:
		for window in controller.get_windows(building):
			var size := window.size as Vector2
			result.append({"position":window.position, "half_width":size.x * 0.5, "half_height":size.y * 0.5})
	# Imported-component windows (hospital caps, bar letters) are not in the
	# controller: use their own triangles.
	for value in building.find_children("InteriorWindowGlow*", "MeshInstance3D", true, false):
		var glow := value as MeshInstance3D
		if glow.mesh == null or glow.get_meta("source_surface", "").to_lower().contains("panes") or glow.get_meta("source_surface", "").to_lower().contains("painted"):
			continue
		if controller != null and glow.material_override is ShaderMaterial and (glow.material_override as ShaderMaterial).get_shader_parameter("controlled") == true:
			continue
		var faces := glow.mesh.get_faces()
		for tri in range(0, faces.size(), 3):
			var corners: Array = [glow.global_transform * faces[tri], glow.global_transform * faces[tri + 1], glow.global_transform * faces[tri + 2]]
			var centroid: Vector3 = ((corners[0] as Vector3) + (corners[1] as Vector3) + (corners[2] as Vector3)) / 3.0
			var radius := maxf((corners[0] as Vector3).distance_to(centroid), maxf((corners[1] as Vector3).distance_to(centroid), (corners[2] as Vector3).distance_to(centroid)))
			result.append({"position":centroid, "half_width":radius, "half_height":radius})
	return result


func _atlas_pixel_is_solid_wall(triangle: Dictionary, point: Vector3) -> bool:
	## On atlas-textured models a window or door can be painted on a wall quad:
	## sample the atlas where the lantern would hang and refuse pane colours.
	var texture := triangle.texture as Texture2D
	var uvs := triangle.uv as Array
	if texture == null or uvs.size() < 3:
		return true
	var image := _wall_atlas_image(texture)
	if image == null:
		return true
	var a := triangle.a as Vector3
	var b := triangle.b as Vector3
	var c := triangle.c as Vector3
	var v0 := b - a
	var v1 := c - a
	var v2 := point - a
	var d00 := v0.dot(v0)
	var d01 := v0.dot(v1)
	var d11 := v1.dot(v1)
	var d20 := v2.dot(v0)
	var d21 := v2.dot(v1)
	var denominator := d00 * d11 - d01 * d01
	if absf(denominator) < 1.0e-12:
		return true
	var v := (d11 * d20 - d01 * d21) / denominator
	var w := (d00 * d21 - d01 * d20) / denominator
	var u := 1.0 - v - w
	var uv := (uvs[0] as Vector2) * u + (uvs[1] as Vector2) * v + (uvs[2] as Vector2) * w
	var pixel := image.get_pixel(clampi(int(fposmod(uv.x, 1.0) * image.get_width()), 0, image.get_width() - 1), clampi(int(fposmod(uv.y, 1.0) * image.get_height()), 0, image.get_height() - 1))
	var maximum := maxf(pixel.r, maxf(pixel.g, pixel.b))
	var saturation := maximum - minf(pixel.r, minf(pixel.g, pixel.b))
	var luminance := pixel.r * 0.2126 + pixel.g * 0.7152 + pixel.b * 0.0722
	# Pale blinds / glass (bright, neutral or cool) and dark navy panes are not wall.
	if pixel.b - pixel.r > 0.04 and luminance < 0.55:
		return false
	if saturation > 0.25 and pixel.b > pixel.r:
		return false
	return true


func _wall_atlas_image(texture: Texture2D) -> Image:
	var key := texture.get_rid().get_id()
	if not _wall_atlas_images.has(key):
		var image := texture.get_image()
		if image != null and image.is_compressed():
			image.decompress()
		_wall_atlas_images[key] = image
	return _wall_atlas_images[key] as Image


func _invalid_lantern_surface(material_name: String) -> bool:
	for forbidden in ["window", "glass", "door", "wood", "darkgrey", "lightyellow", "mat24"]:
		if forbidden in material_name:
			return true
	return false


func _add_building_sign_label(building: Node3D, building_index: int, text_value: String, bounds: AABB, outward: Vector3) -> void:
	# The hospital's wordmark is baked into its atlas rather than separate mesh
	# letters. Place only luminous glyphs on the actual rendered sign wall; there
	# is no backing rectangle and the facade ray prevents a floating label.
	var surface := _clear_street_wall(building, bounds, outward, bounds.position.y + 4.15, -0.31)
	if not bool(surface.get("hit", false)):
		return
	var label := Label3D.new()
	label.name = "BuildingSign_%03d" % building_index
	label.text = text_value
	label.font_size = 64
	label.pixel_size = 0.013
	label.modulate = Color(1.0, 0.62, 0.22)
	label.outline_size = 2
	label.outline_modulate = Color(0.22, 0.08, 0.01, 0.9)
	label.billboard = BaseMaterial3D.BILLBOARD_DISABLED
	label.double_sided = false
	label.no_depth_test = false
	label.visible = false
	label.add_to_group("building_sign_lights")
	label.set_meta("source_building", building.get_path())
	label.set_meta("rendered_facade_hit", true)
	label.set_meta("logical_placement", "hospital facade wordmark")
	building.add_child(label)
	label.top_level = true
	label.global_position = (surface.position as Vector3) + outward * 0.028
	label.look_at(label.global_position + outward, Vector3.UP)


func _add_building_window_glow(building: Node3D, building_index: int, profile: Dictionary) -> void:
	var model_name := building.scene_file_path.get_file()
	var rule := WINDOW_RULES.get(model_name, {}) as Dictionary
	var glass_materials: Array = profile.get("glass", []) as Array
	var mask_mode := int(profile.get("texture_mask", 0))
	var component_windows := str(profile.get("component_windows", ""))
	var component_mask := int(profile.get("component_mask", 0))
	var sign_components := str(profile.get("sign_components", ""))
	var select := str(rule.get("select", "glass" if not glass_materials.is_empty() else ""))
	var controller := _window_controller()
	var entry: Dictionary = {}
	var building_material: ShaderMaterial = null
	if controller != null and select != "":
		var occupancy := 0.55 + 0.45 * float((building_index * 2654435761) % 1000) / 1000.0
		entry = controller.create_building(building, building_index, occupancy, select == "painted")
		building_material = _controlled_window_material(select == "painted")
	var overlay_index := 0
	for mesh_value in building.find_children("*", "MeshInstance3D", true, false):
		var source_mesh := mesh_value as MeshInstance3D
		if source_mesh.mesh == null or source_mesh.is_in_group("building_window_lights"):
			continue
		for surface_index in source_mesh.mesh.get_surface_count():
			var source_material := source_mesh.get_active_material(surface_index) as StandardMaterial3D
			if source_material == null:
				continue
			if not sign_components.is_empty():
				var sign_mesh := _exact_mesh_components(source_mesh.mesh, surface_index, sign_components)
				if sign_mesh.get_surface_count() > 0:
					overlay_index += 1
					var sign_glow := _add_exact_window_glow(source_mesh, sign_mesh, source_material, building, building_index, overlay_index, "exact imported %s" % sign_components, 0)
					sign_glow.add_to_group("building_sign_lights")
			if not component_windows.is_empty():
				var component_mesh := _exact_mesh_components(source_mesh.mesh, surface_index, component_windows)
				if component_mesh.get_surface_count() == 0:
					continue
				overlay_index += 1
				_add_exact_window_glow(source_mesh, component_mesh, source_material, building, building_index, overlay_index, "exact imported %s" % component_windows, component_mask)
				continue
			var material_name := source_material.resource_name.to_lower()
			if select == "painted" and source_material.albedo_texture != null and not entry.is_empty():
				var painted_mesh := BuildingWindowBuilder.painted_surface(source_mesh.mesh, surface_index, float(rule.get("max_normal_y", 0.35)))
				if painted_mesh.get_surface_count() > 0:
					overlay_index += 1
					var painted := _add_exact_window_glow(source_mesh, painted_mesh, source_material, building, building_index, overlay_index, "%s:%d:%s painted" % [source_mesh.name, surface_index, material_name], 3, building_material)
					controller.add_part(entry, source_mesh, painted, [], building_material)
				continue
			if select == "atlas_pane" and not entry.is_empty():
				var found := BuildingWindowBuilder.windows_for_surface(source_mesh.mesh, surface_index, source_material, _scaled_rule(rule, source_mesh))
				if found.mesh != null:
					overlay_index += 1
					var overlay := _add_exact_window_glow(source_mesh, found.mesh as ArrayMesh, source_material, building, building_index, overlay_index, "%s:%d:%s panes" % [source_mesh.name, surface_index, material_name], 0, building_material)
					controller.add_part(entry, source_mesh, overlay, found.windows as Array, building_material)
				continue
			var selected_glass := false
			for candidate_value in glass_materials:
				if material_name == str(candidate_value).to_lower():
					selected_glass = true
					break
			if select == "glass" and selected_glass and not entry.is_empty():
				var glass := BuildingWindowBuilder.windows_for_surface(source_mesh.mesh, surface_index, source_material, _scaled_rule({"select":"glass"}, source_mesh))
				if glass.mesh != null:
					overlay_index += 1
					var glass_overlay := _add_exact_window_glow(source_mesh, glass.mesh as ArrayMesh, source_material, building, building_index, overlay_index, "%s:%d:%s" % [source_mesh.name, surface_index, material_name], 0, building_material)
					controller.add_part(entry, source_mesh, glass_overlay, glass.windows as Array, building_material)
				continue
			var texture_masked := mask_mode > 0 and source_material.albedo_texture != null
			if not selected_glass and not texture_masked:
				continue
			overlay_index += 1
			_add_exact_window_glow(source_mesh, _single_surface_mesh(source_mesh.mesh, surface_index), source_material, building, building_index, overlay_index, "%s:%d:%s" % [source_mesh.name, surface_index, material_name], mask_mode if texture_masked else 0)
	if not entry.is_empty():
		controller.finish_building(entry)
	building.set_meta("interior_window_glow_surfaces", overlay_index)


func _scaled_rule(rule: Dictionary, source_mesh: MeshInstance3D) -> Dictionary:
	var scaled := rule.duplicate()
	var scale := source_mesh.global_basis.get_scale().abs()
	scaled["scale"] = snappedf((scale.x + scale.y + scale.z) / 3.0, 0.01)
	return scaled


func _window_controller() -> Node:
	var existing := get_node_or_null("BuildingWindowController")
	if existing != null:
		return existing
	var controller := BuildingWindowController.new()
	controller.environment = get_parent().get_node_or_null("EnvironmentSystem")
	add_child(controller)
	return controller


func _controlled_window_material(painted: bool) -> ShaderMaterial:
	## Each building owns its material: its window-state texture is its own.
	var material := ShaderMaterial.new()
	material.shader = BUILDING_WINDOW_SHADER
	material.set_shader_parameter("interior_color", Color(1.0, 0.61, 0.27))
	material.set_shader_parameter("emission_strength", 3.4)
	material.set_shader_parameter("controlled", not painted)
	material.set_shader_parameter("uv_patches", painted)
	return material


func _add_exact_window_glow(source_mesh: MeshInstance3D, exact_mesh: ArrayMesh, source_material: StandardMaterial3D, building: Node3D, building_index: int, overlay_index: int, source_description: String, mask_mode: int, material_override: ShaderMaterial = null) -> MeshInstance3D:
	var glow := MeshInstance3D.new()
	glow.name = "InteriorWindowGlow_%03d_%02d" % [building_index, overlay_index]
	glow.mesh = exact_mesh
	glow.material_override = material_override if material_override != null else _building_window_material(source_material.albedo_texture if mask_mode > 0 else null, mask_mode)
	glow.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	glow.visibility_range_end = 0.0
	glow.visibility_range_end_margin = 0.0
	glow.visible = false
	glow.add_to_group("building_window_lights")
	glow.set_meta("source_building", building.get_path())
	glow.set_meta("source_surface", source_description)
	# The duplicate uses the source mesh's local vertex coordinates. It must be a
	# sibling with the same transform; parenting it under the source mesh applies
	# that transform twice and visibly pulls panes away from their frames.
	var source_parent := source_mesh.get_parent()
	source_parent.add_child(glow)
	glow.transform = source_mesh.transform
	glow.set_instance_shader_parameter("night_strength", 0.0)
	return glow


func _exact_mesh_components(source: Mesh, surface_index: int, selection: String) -> ArrayMesh:
	## Single-material models can still expose semantic objects as disconnected
	## imported components. Retain their original indices exactly: hospital round
	## window caps and the three extruded BAR letters are never synthesized.
	var cache_key := "%s:%d:%s" % [source.get_rid().get_id(), surface_index, selection]
	if _component_window_meshes.has(cache_key):
		return _component_window_meshes[cache_key] as ArrayMesh
	var arrays := source.surface_get_arrays(surface_index)
	var vertices := arrays[Mesh.ARRAY_VERTEX] as PackedVector3Array
	var source_indices: Variant = arrays[Mesh.ARRAY_INDEX]
	if source_indices == null or source_indices.size() == 0:
		source_indices = PackedInt32Array()
		for vertex_index in vertices.size():
			source_indices.append(vertex_index)
	var triangle_count := int(source_indices.size() / 3)
	var triangles_by_position := {}
	var triangle_positions: Array = []
	for triangle_index in triangle_count:
		var keys: Array[String] = []
		for corner in 3:
			var point := vertices[source_indices[triangle_index * 3 + corner]]
			var key := "%.4f,%.4f,%.4f" % [point.x, point.y, point.z]
			keys.append(key)
			if not triangles_by_position.has(key):
				triangles_by_position[key] = []
			(triangles_by_position[key] as Array).append(triangle_index)
		triangle_positions.append(keys)
	var visited := {}
	var selected_indices := PackedInt32Array()
	for seed in triangle_count:
		if visited.has(seed):
			continue
		var pending := [seed]
		var component: Array[int] = []
		visited[seed] = true
		var component_bounds := AABB()
		var bounds_started := false
		while not pending.is_empty():
			var triangle_index := int(pending.pop_back())
			component.append(triangle_index)
			for corner in 3:
				var point := vertices[source_indices[triangle_index * 3 + corner]]
				if bounds_started:
					component_bounds = component_bounds.expand(point)
				else:
					component_bounds = AABB(point, Vector3.ZERO)
					bounds_started = true
			for key_value in triangle_positions[triangle_index]:
				for neighbor_value in triangles_by_position[key_value]:
					var neighbor := int(neighbor_value)
					if not visited.has(neighbor):
						visited[neighbor] = true
						pending.append(neighbor)
		var selected := false
		if selection == "hospital_windows":
			var dimensions := [component_bounds.size.x, component_bounds.size.y, component_bounds.size.z]
			dimensions.sort()
			selected = float(dimensions[0]) <= 9.5 and float(dimensions[1]) >= 18.0 and float(dimensions[2]) <= 55.0 and float(dimensions[2]) / maxf(float(dimensions[1]), 0.001) <= 1.15 and component.size() >= 40
		elif selection == "bar_letters":
			selected = component_bounds.position.x >= 132.0 and component_bounds.position.y >= 112.0 and component_bounds.end.y <= 133.0 and component_bounds.size.x <= 5.0 and component_bounds.size.z >= 10.0 and component_bounds.size.z <= 16.0
		elif selection == "house_windows":
			var dimensions := [component_bounds.size.x, component_bounds.size.y, component_bounds.size.z]
			dimensions.sort()
			selected = component.size() == 48 and float(dimensions[0]) < 0.25 and float(dimensions[1]) > 2.6 and float(dimensions[2]) < 2.9 and component_bounds.position.y > 2.3
		if not selected:
			continue
		for triangle_index in component:
			for corner in 3:
				selected_indices.append(source_indices[triangle_index * 3 + corner])
	var result := ArrayMesh.new()
	if not selected_indices.is_empty():
		arrays[Mesh.ARRAY_INDEX] = selected_indices
		result.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arrays)
	_component_window_meshes[cache_key] = result
	return result


func _disable_imported_building_emission(building: Node3D) -> void:
	## Several GLBs arrive with emission enabled on their only atlas material,
	## which makes plaster, roofs and garage doors glow at night. Preserve the
	## authored albedo while replacing only that accidental whole-model emission.
	for mesh_value in building.find_children("*", "MeshInstance3D", true, false):
		var mesh_node := mesh_value as MeshInstance3D
		if mesh_node.mesh == null:
			continue
		for surface_index in mesh_node.mesh.get_surface_count():
			var source := mesh_node.get_active_material(surface_index) as StandardMaterial3D
			if source == null or not source.emission_enabled:
				continue
			var key := "%s|%s|%s" % [source.resource_path, source.resource_name, source.get_instance_id()]
			var normalized := _normalized_building_materials.get(key) as StandardMaterial3D
			if normalized == null:
				normalized = source.duplicate() as StandardMaterial3D
				normalized.emission_enabled = false
				_normalized_building_materials[key] = normalized
			mesh_node.set_surface_override_material(surface_index, normalized)
	building.set_meta("imported_emission_normalized", true)


func _single_surface_mesh(source: Mesh, surface_index: int) -> ArrayMesh:
	var result := ArrayMesh.new()
	result.add_surface_from_arrays(source.surface_get_primitive_type(surface_index), source.surface_get_arrays(surface_index))
	return result


func _building_window_material(source_texture: Texture2D, mask_mode: int) -> ShaderMaterial:
	var texture_key := source_texture.resource_path if source_texture != null else "solid_glass"
	var key := "%s:%d" % [texture_key, mask_mode]
	if _window_glow_materials.has(key):
		return _window_glow_materials[key] as ShaderMaterial
	var material := ShaderMaterial.new()
	material.shader = BUILDING_WINDOW_SHADER
	material.set_shader_parameter("mask_mode", mask_mode)
	material.set_shader_parameter("interior_color", Color(1.0, 0.61, 0.27))
	material.set_shader_parameter("emission_strength", 3.8)
	if source_texture != null:
		material.set_shader_parameter("source_texture", source_texture)
	_window_glow_materials[key] = material
	return material


func _build_integrated_street_light(light: Node3D) -> void:
	## A complete roadway luminaire authored in the same mesh-first style as the
	## procedural traffic signals and cars. The emissive lens is physically
	## inside the housing; no detached billboard/sphere represents the lamp.
	var metal := _material("street_light_powdercoat", Color(0.055, 0.07, 0.085), 0.28)
	var trim := _material("street_light_trim", Color(0.13, 0.16, 0.18), 0.22)
	var lens_material := _emissive_material("street_light_led_lens", Color(1.0, 0.78, 0.42), 18.0)
	var base := _add_mesh(light, "AnchorBase", _cylinder(0.42, 0.30, 0.34), Vector3(0.0, 0.15, 0.0), metal)
	var plinth := _add_mesh(light, "ServicePlinth", _cylinder(0.31, 0.78, 0.25), Vector3(0.0, 0.61, 0.0), metal)
	var pole := _add_mesh(light, "TaperedPole", _cylinder(0.18, 7.10, 0.10), Vector3(0.0, 4.03, 0.0), metal)
	var collar := _add_mesh(light, "ArmCollar", _cylinder(0.19, 0.42, 0.15), Vector3(0.0, 7.66, 0.0), trim)
	var arm_start := Vector3(0.0, 7.76, 0.0)
	var arm_end := Vector3(2.08, 8.60, 0.0)
	var arm := _add_street_light_beam(light, "SweptArm", arm_start, arm_end, 0.11, metal)
	var housing := _add_mesh(light, "LEDHousing", _box(Vector3(1.38, 0.28, 0.60)), Vector3(2.55, 8.58, 0.0), metal)
	housing.rotation.z = deg_to_rad(-7.0)
	var cap := _add_mesh(light, "DriverCompartment", _box(Vector3(0.58, 0.23, 0.50)), Vector3(2.08, 8.75, 0.0), trim)
	cap.rotation.z = deg_to_rad(-7.0)
	var lens := _add_mesh(light, "LuminaireGlow", _box(Vector3(0.98, 0.05, 0.43)), Vector3(2.68, 8.42, 0.0), lens_material)
	lens.rotation.z = deg_to_rad(-7.0)
	lens.visible = false
	lens.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	# Street-light silhouettes and their emissive lenses remain visible from the
	# overview. This does not change any shadow distance or shadow policy.
	for part in [base, plinth, pole, collar, arm, housing, cap, lens]:
		(part as GeometryInstance3D).visibility_range_end = 0.0
		(part as GeometryInstance3D).visibility_range_end_margin = 0.0
	var illumination := SpotLight3D.new()
	illumination.name = "AdaptiveLuminaire"
	illumination.position = Vector3(2.68, 8.38, 0.0)
	illumination.light_color = Color(1.0, 0.78, 0.48)
	illumination.light_energy = 0.0
	illumination.spot_range = 42.0
	illumination.spot_angle = 62.0
	illumination.shadow_enabled = false
	illumination.distance_fade_enabled = false
	light.add_child(illumination)
	illumination.look_at(illumination.global_position + light.global_basis * Vector3(0.55, -9.0, 0.0), Vector3.UP)
	light.set_meta("actuator_state", "daylight_off")


func _add_street_light_beam(parent: Node3D, beam_name: String, start: Vector3, finish: Vector3, radius: float, material: StandardMaterial3D) -> MeshInstance3D:
	var direction := finish - start
	var beam := _add_mesh(parent, beam_name, _cylinder(radius, direction.length()), (start + finish) * 0.5, material)
	beam.quaternion = Quaternion(Vector3.UP, direction.normalized())
	return beam


func _new_registered_device(parent: Node3D, device_name: String, world_position: Vector3, groups: Array, metadata: Dictionary) -> Node3D:
	var device := Node3D.new()
	device.name = device_name
	device.position = world_position
	parent.add_child(device)
	var all_groups := groups.duplicate()
	all_groups.append("smart_city_devices")
	_register_asset(device, all_groups, metadata)
	return device


func _register_asset(asset: Node3D, groups: Array, metadata: Dictionary) -> void:
	for group_value in groups:
		var group_name := str(group_value)
		if not group_name.is_empty() and not asset.is_in_group(group_name):
			asset.add_to_group(group_name)
	for key in metadata:
		asset.set_meta(str(key), metadata[key])
	asset.set_meta("asset_registry_ready", true)
	asset.set_meta("highlightable", true)
	if str(metadata.get("future_interaction", "")).is_empty() == false:
		_ensure_service_anchor(asset, Vector3(0.0, 1.0, 0.6))


func _hide_legacy_sensor_visuals(sensor: Node3D) -> void:
	for child in sensor.get_children():
		if child is Node3D:
			(child as Node3D).visible = false


func _build_traffic_detector(sensor: Node3D) -> void:
	var model := _model_root(sensor, "TrafficRadarModel")
	var steel := _material("steel", Color(0.16, 0.19, 0.22), 0.34)
	var shell := _material("sensor_shell", Color(0.76, 0.80, 0.82), 0.48)
	var glass := _material("optical_glass", Color(0.012, 0.065, 0.09), 0.16)
	var cyan := _emissive_material("sensor_cyan", Color(0.02, 0.86, 1.0), 3.2)
	_add_mesh(model, "Foundation", _cylinder(0.23, 0.22), Vector3(0, 0.11, 0), steel)
	_add_mesh(model, "Pole", _cylinder(0.075, 2.55), Vector3(0, 1.31, 0), steel)
	_add_mesh(model, "RadarHousing", _box(Vector3(0.54, 0.42, 0.44)), Vector3(0, 2.18, -0.18), shell)
	var lens := _add_mesh(model, "DopplerLens", _cylinder(0.14, 0.08), Vector3(0, 2.18, -0.43), glass)
	lens.rotation.x = PI * 0.5
	_add_mesh(model, "StatusLED", _sphere(0.045), Vector3(0.19, 2.31, -0.43), cyan)
	var panel := _add_mesh(model, "SolarPanel", _box(Vector3(0.62, 0.055, 0.42)), Vector3(0, 2.65, 0.02), glass)
	panel.rotation.x = deg_to_rad(18.0)
	_ensure_service_anchor(sensor, Vector3(0.0, 1.15, 0.65))


func _build_air_quality_monitor(sensor: Node3D) -> void:
	var model := _model_root(sensor, "AirQualityStationModel")
	var shell := _material("aq_shell", Color(0.83, 0.86, 0.84), 0.62)
	var steel := _material("steel", Color(0.16, 0.19, 0.22), 0.34)
	var dark := _material("vent_dark", Color(0.035, 0.05, 0.055), 0.54)
	var green := _emissive_material("aq_green", Color(0.12, 1.0, 0.42), 2.8)
	_add_mesh(model, "Pedestal", _box(Vector3(0.18, 0.78, 0.18)), Vector3(0, 0.39, 0), steel)
	_add_mesh(model, "WeatherproofCabinet", _box(Vector3(0.74, 0.92, 0.46)), Vector3(0, 1.12, 0), shell)
	for vent_index in 3:
		_add_mesh(model, "Vent_%d" % vent_index, _box(Vector3(0.42, 0.035, 0.025)), Vector3(0, 0.92 + vent_index * 0.14, -0.245), dark)
	_add_mesh(model, "ParticleInlet", _cylinder(0.075, 0.42), Vector3(-0.19, 1.80, 0), steel)
	_add_mesh(model, "InletCap", _cylinder(0.14, 0.06), Vector3(-0.19, 2.02, 0), shell)
	_add_mesh(model, "StatusScreen", _box(Vector3(0.25, 0.16, 0.025)), Vector3(0.16, 1.34, -0.245), green)
	_ensure_service_anchor(sensor, Vector3(0.0, 1.05, -0.72))


func _build_telemetry_detail(sensor: Node3D) -> void:
	var model := _model_root(sensor, "TelemetryInstrumentation")
	var dark := _material("vent_dark", Color(0.035, 0.05, 0.055), 0.54)
	var cyan := _emissive_material("sensor_cyan", Color(0.02, 0.86, 1.0), 3.2)
	var steel := _material("steel", Color(0.16, 0.19, 0.22), 0.34)
	_add_mesh(model, "DiagnosticDisplay", _box(Vector3(0.34, 0.22, 0.035)), Vector3(0, 1.16, -0.38), dark)
	_add_mesh(model, "DisplayLiveBar", _box(Vector3(0.24, 0.035, 0.018)), Vector3(0, 1.16, -0.405), cyan)
	_add_mesh(model, "Antenna", _cylinder(0.035, 0.68), Vector3(0.28, 1.78, 0), steel)
	_add_mesh(model, "AntennaTip", _sphere(0.06), Vector3(0.28, 2.13, 0), cyan)


func _build_noise_monitor(sensor: Node3D) -> void:
	var model := _model_root(sensor, "ClassOneNoiseMonitorModel")
	var steel := _material("steel", Color(0.16, 0.19, 0.22), 0.34)
	var shell := _material("sensor_shell", Color(0.76, 0.80, 0.82), 0.48)
	var foam := _material("microphone_foam", Color(0.035, 0.04, 0.045), 0.9)
	var violet := _emissive_material("noise_violet", Color(0.73, 0.25, 1.0), 3.0)
	_add_mesh(model, "Base", _cylinder(0.21, 0.24), Vector3(0, 0.12, 0), steel)
	_add_mesh(model, "Mast", _cylinder(0.065, 2.35), Vector3(0, 1.29, 0), steel)
	_add_mesh(model, "Logger", _box(Vector3(0.48, 0.54, 0.35)), Vector3(0, 1.18, 0), shell)
	_add_mesh(model, "MicrophoneStem", _cylinder(0.035, 0.40), Vector3(0, 2.55, 0), steel)
	_add_mesh(model, "Windscreen", _sphere(0.13), Vector3(0, 2.82, 0), foam)
	_add_mesh(model, "StatusLED", _sphere(0.04), Vector3(0.17, 1.33, -0.20), violet)
	_ensure_service_anchor(sensor, Vector3(0.0, 1.12, 0.58))


func _build_weather_station(sensor: Node3D) -> void:
	var model := _model_root(sensor, "CompactWeatherStationModel")
	var steel := _material("steel", Color(0.16, 0.19, 0.22), 0.34)
	var shell := _material("weather_shell", Color(0.91, 0.91, 0.87), 0.58)
	var orange := _emissive_material("weather_orange", Color(1.0, 0.48, 0.08), 2.7)
	_add_mesh(model, "Base", _cylinder(0.25, 0.20), Vector3(0, 0.10, 0), steel)
	_add_mesh(model, "Mast", _cylinder(0.065, 3.25), Vector3(0, 1.73, 0), steel)
	_add_mesh(model, "CrossArm", _box(Vector3(1.18, 0.07, 0.07)), Vector3(0, 3.02, 0), steel)
	_add_mesh(model, "RainCollector", _cylinder(0.18, 0.26, 0.28), Vector3(-0.43, 3.22, 0), shell)
	_add_mesh(model, "WindHub", _sphere(0.09), Vector3(0.42, 3.18, 0), steel)
	for arm_index in 3:
		var angle := TAU * float(arm_index) / 3.0
		var arm := _add_mesh(model, "CupArm_%d" % arm_index, _box(Vector3(0.42, 0.035, 0.035)), Vector3(0.42 + cos(angle) * 0.18, 3.18, sin(angle) * 0.18), steel)
		arm.rotation.y = -angle
		_add_mesh(model, "WindCup_%d" % arm_index, _sphere(0.085), Vector3(0.42 + cos(angle) * 0.40, 3.18, sin(angle) * 0.40), shell)
	_add_mesh(model, "StatusLED", _sphere(0.05), Vector3(0, 2.56, -0.09), orange)
	_ensure_service_anchor(sensor, Vector3(0.0, 1.10, 0.58))


func _build_flood_sensor(sensor: Node3D) -> void:
	var model := _model_root(sensor, "FloodRadarModel")
	var steel := _material("steel", Color(0.16, 0.19, 0.22), 0.34)
	var shell := _material("flood_shell", Color(0.15, 0.31, 0.43), 0.52)
	var glass := _material("optical_glass", Color(0.012, 0.065, 0.09), 0.16)
	var blue := _emissive_material("flood_blue", Color(0.05, 0.48, 1.0), 3.2)
	_add_mesh(model, "Base", _box(Vector3(0.52, 0.16, 0.52)), Vector3(0, 0.08, 0), steel)
	_add_mesh(model, "Cabinet", _box(Vector3(0.62, 0.92, 0.48)), Vector3(0, 0.62, 0), shell)
	_add_mesh(model, "RadarArm", _box(Vector3(0.08, 0.08, 0.72)), Vector3(0, 1.14, -0.26), steel)
	var radar := _add_mesh(model, "DownwardRadar", _cylinder(0.15, 0.16), Vector3(0, 1.07, -0.66), glass)
	radar.rotation.z = PI * 0.5
	_add_mesh(model, "StatusLED", _sphere(0.045), Vector3(0.21, 0.82, -0.25), blue)
	_ensure_service_anchor(sensor, Vector3(0.0, 0.75, 0.62))


func _build_irrigation_valve(valve: Node3D) -> void:
	var model := _model_root(valve, "IrrigationValveModel")
	var steel := _material("steel", Color(0.16, 0.19, 0.22), 0.34)
	var blue := _material("irrigation_blue", Color(0.055, 0.28, 0.62), 0.42)
	var cyan := _emissive_material("sensor_cyan", Color(0.02, 0.86, 1.0), 3.2)
	_add_mesh(model, "GroundBox", _box(Vector3(0.82, 0.18, 0.68)), Vector3(0, 0.09, 0), steel)
	_add_mesh(model, "ValveBody", _cylinder(0.25, 0.58), Vector3(0, 0.43, 0), blue)
	_add_mesh(model, "PipeLeft", _cylinder(0.12, 0.62), Vector3(-0.40, 0.43, 0), blue).rotation.z = PI * 0.5
	_add_mesh(model, "PipeRight", _cylinder(0.12, 0.62), Vector3(0.40, 0.43, 0), blue).rotation.z = PI * 0.5
	var wheel_mesh := TorusMesh.new()
	wheel_mesh.inner_radius = 0.15
	wheel_mesh.outer_radius = 0.24
	var wheel := _add_mesh(model, "ManualOverrideWheel", wheel_mesh, Vector3(0, 0.93, 0), steel)
	wheel.rotation.x = PI * 0.5
	_add_mesh(model, "ControllerLED", _sphere(0.045), Vector3(0.20, 0.69, -0.21), cyan)
	_ensure_service_anchor(valve, Vector3(0.0, 0.75, 0.68))


func _add_police_roof_identity(building: Node3D) -> void:
	var bounds := CitySpatialSafety.world_bounds(building)
	if bounds.size == Vector3.ZERO:
		return
	var identity := Node3D.new()
	identity.name = "PoliceFacilityRoofIdentity"
	identity.top_level = true
	building.add_child(identity)
	identity.global_position = Vector3(bounds.get_center().x, bounds.end.y + 0.28, bounds.get_center().z)
	var dark := _material("police_dark", Color(0.025, 0.055, 0.10), 0.32)
	var red := _emissive_material("police_red", Color(1.0, 0.035, 0.025), 4.0)
	var blue := _emissive_material("police_blue", Color(0.025, 0.24, 1.0), 4.0)
	_add_mesh(identity, "BeaconBar", _box(Vector3(2.2, 0.16, 0.42)), Vector3.ZERO, dark)
	_add_mesh(identity, "BlueBeacon", _box(Vector3(0.88, 0.22, 0.34)), Vector3(-0.52, 0.10, 0), blue)
	_add_mesh(identity, "RedBeacon", _box(Vector3(0.88, 0.22, 0.34)), Vector3(0.52, 0.10, 0), red)
	var label := Label3D.new()
	label.name = "PoliceLabel"
	label.text = "POLICE"
	label.font_size = 64
	label.pixel_size = 0.018
	label.outline_size = 10
	label.modulate = Color(0.82, 0.92, 1.0)
	label.position = Vector3(0, 0.82, 0)
	label.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	identity.add_child(label)


func _resolve_device_placement() -> void:
	var obstacles: Array[AABB] = CitySpatialSafety.building_bounds(get_tree())
	for group_name in ["city_trees", "urban_props", "power_poles", "traffic_cameras", "traffic_signals", "water_valves", "power_switches", "power_service_meters", "water_service_meters"]:
		for value in get_tree().get_nodes_in_group(group_name):
			if value is Node3D and (value as Node3D).visible:
				var bounds := CitySpatialSafety.world_bounds(value as Node3D)
				if bounds.size != Vector3.ZERO:
					obstacles.append(bounds)
	var accepted: Array[AABB] = []
	for value in get_tree().get_nodes_in_group("smart_city_devices"):
		if not value is Node3D:
			continue
		var device := value as Node3D
		if device.is_in_group("traffic_cameras"):
			continue
		CitySpatialSafety.ground_rendered_node(device, 0.17)
		var requested := device.global_position
		var found := _try_device_candidates(device, requested, obstacles, accepted)
		device.set_meta("requested_device_position", requested)
		device.set_meta("device_site_spatially_safe", found)
		if not found:
			device.visible = false
			device.set_meta("spatial_safety_hidden", "no_clear_device_site")


func _try_device_candidates(device: Node3D, requested: Vector3, obstacles: Array[AABB], accepted: Array[AABB]) -> bool:
	var offsets: Array[Vector2] = [Vector2.ZERO]
	var directions := [
		Vector2(1, 0), Vector2(-1, 0), Vector2(0, 1), Vector2(0, -1),
		Vector2(1, 1).normalized(), Vector2(-1, 1).normalized(),
		Vector2(1, -1).normalized(), Vector2(-1, -1).normalized(),
	]
	# Some legacy sensor pivots were deep inside a building footprint. Search in
	# concentric rings, nearest first, until a genuine public-realm site is found.
	for radius in [2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 15.0, 18.0, 22.0, 26.0, 30.0, 34.0, 38.0]:
		for direction_value in directions:
			offsets.append((direction_value as Vector2) * float(radius))
	for offset_value in offsets:
		var offset := offset_value as Vector2
		device.global_position = Vector3(requested.x + offset.x, requested.y, requested.z + offset.y)
		device.force_update_transform()
		var bounds := CitySpatialSafety.world_bounds(device)
		if bounds.size == Vector3.ZERO or CitySpatialSafety.overlaps_road(bounds, 0.08):
			continue
		if _intersects_any(bounds, obstacles, 0.08) or _intersects_any(bounds, accepted, 0.22):
			continue
		accepted.append(bounds)
		device.set_meta("device_site_adjustment", device.global_position - requested)
		return true
	device.global_position = requested
	return false


func _intersects_any(bounds: AABB, candidates: Array[AABB], margin: float) -> bool:
	for other in candidates:
		if CitySpatialSafety.horizontal_intersects(bounds, other, margin):
			return true
	return false


func _attach_exhaust_emitters() -> void:
	# CityLife has now normalized and grounded every imported model. Derive the
	# tailpipe from the final vehicle bounds, then counter-scale the particles so
	# smoke size remains consistent across differently authored source assets.
	for value in get_tree().get_nodes_in_group("vehicles"):
		if not value is Node3D:
			continue
		var vehicle := value as Node3D
		var vehicle_index := int(vehicle.get_meta("vehicle_index", -1))
		if vehicle_index not in SMOKY_VEHICLE_INDICES or vehicle.is_in_group("emergency_vehicles"):
			continue
		var bounds := _local_model_bounds(vehicle)
		if bounds.size == Vector3.ZERO:
			continue
		vehicle.add_to_group("smoky_vehicles")
		vehicle.add_to_group("emission_sources")
		vehicle.set_meta("asset_type", "high_emission_vehicle")
		vehicle.set_meta("emissions_status", "excessive_black_smoke")
		vehicle.set_meta("estimated_pm_factor", 4.8 + float(vehicle_index % 4) * 0.7)
		vehicle.set_meta("sensor_detectable", true)
		vehicle.set_meta("future_interaction", "roadside_emissions_inspection_and_exhaust_repair")
		var emitter := _make_smoke_emitter()
		emitter.name = "BlackExhaustEmitter"
		emitter.position = Vector3(bounds.get_center().x + bounds.size.x * 0.20, bounds.position.y + bounds.size.y * 0.22, bounds.position.z - 0.08)
		var scale_value := vehicle.scale.abs()
		emitter.scale = Vector3(
			1.0 / maxf(scale_value.x, 0.001),
			1.0 / maxf(scale_value.y, 0.001),
			1.0 / maxf(scale_value.z, 0.001)
		)
		vehicle.add_child(emitter)
		emitter.add_to_group("vehicle_exhaust_emitters")
		_ensure_service_anchor(vehicle, Vector3(bounds.get_center().x, bounds.position.y + bounds.size.y * 0.35, bounds.position.z - 0.45))


func _make_smoke_emitter() -> GPUParticles3D:
	var particles := GPUParticles3D.new()
	particles.amount = 24
	particles.lifetime = 2.65
	particles.randomness = 0.72
	particles.visibility_aabb = AABB(Vector3(-4, -1, -5), Vector3(8, 7, 10))
	particles.local_coords = true
	particles.emitting = true
	var process := ParticleProcessMaterial.new()
	process.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_SPHERE
	process.emission_sphere_radius = 0.08
	process.direction = Vector3(0.0, 0.42, -1.0)
	process.spread = 27.0
	process.gravity = Vector3(0.0, 0.92, 0.0)
	process.initial_velocity_min = 1.1
	process.initial_velocity_max = 2.2
	process.scale_min = 0.42
	process.scale_max = 1.05
	var gradient := Gradient.new()
	gradient.offsets = PackedFloat32Array([0.0, 0.22, 0.72, 1.0])
	gradient.colors = PackedColorArray([
		Color(0.045, 0.05, 0.055, 0.12), Color(0.025, 0.028, 0.032, 0.90),
		Color(0.09, 0.095, 0.10, 0.56), Color(0.15, 0.16, 0.17, 0.0),
	])
	var ramp := GradientTexture1D.new()
	ramp.gradient = gradient
	process.color_ramp = ramp
	particles.process_material = process
	var quad := QuadMesh.new()
	quad.size = Vector2(0.92, 0.92)
	var smoke_material := ShaderMaterial.new()
	smoke_material.shader = SMOKE_SHADER
	quad.material = smoke_material
	particles.draw_pass_1 = quad
	return particles


func _local_model_bounds(owner: Node3D) -> AABB:
	var found := false
	var result := AABB()
	for value in owner.find_children("*", "MeshInstance3D", true, false):
		var mesh_node := value as MeshInstance3D
		if mesh_node.mesh == null:
			continue
		var bounds := mesh_node.mesh.get_aabb()
		for corner in _aabb_corners(bounds):
			var point := owner.to_local(mesh_node.to_global(corner))
			result = AABB(point, Vector3.ZERO) if not found else result.expand(point)
			found = true
	return result if found else AABB()


func _aabb_corners(bounds: AABB) -> Array[Vector3]:
	return [
		bounds.position,
		Vector3(bounds.end.x, bounds.position.y, bounds.position.z),
		Vector3(bounds.position.x, bounds.end.y, bounds.position.z),
		Vector3(bounds.end.x, bounds.end.y, bounds.position.z),
		Vector3(bounds.position.x, bounds.position.y, bounds.end.z),
		Vector3(bounds.end.x, bounds.position.y, bounds.end.z),
		Vector3(bounds.position.x, bounds.end.y, bounds.end.z),
		bounds.end,
	]


func _ensure_service_anchor(asset: Node3D, local_position: Vector3) -> void:
	var anchor := asset.get_node_or_null("ServiceInteractionAnchor") as Marker3D
	if anchor == null:
		anchor = Marker3D.new()
		anchor.name = "ServiceInteractionAnchor"
		asset.add_child(anchor)
	anchor.position = local_position
	anchor.set_meta("interaction_anchor", true)


func _model_root(parent: Node3D, model_name: String) -> Node3D:
	var model := Node3D.new()
	model.name = model_name
	parent.add_child(model)
	return model


func _add_mesh(parent: Node3D, mesh_name: String, mesh: PrimitiveMesh, local_position: Vector3, material: StandardMaterial3D) -> MeshInstance3D:
	var instance := MeshInstance3D.new()
	instance.name = mesh_name
	instance.mesh = mesh
	instance.position = local_position
	instance.material_override = material
	instance.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
	_apply_detail_visibility(instance)
	parent.add_child(instance)
	return instance


func _apply_detail_visibility(instance: MeshInstance3D) -> void:
	# Device detail is imperceptible in the full-city overview. Distance LOD keeps
	# those hundreds of small meshes out of the render list until a street-level
	# view can actually resolve them; highlight frames remain visible at any range.
	instance.visibility_range_end = 360.0
	instance.visibility_range_end_margin = 42.0
	instance.visibility_range_fade_mode = GeometryInstance3D.VISIBILITY_RANGE_FADE_SELF


func _box(size_value: Vector3) -> BoxMesh:
	var mesh := BoxMesh.new()
	mesh.size = size_value
	return mesh


func _cylinder(radius: float, height_value: float, top_radius := -1.0) -> CylinderMesh:
	var mesh := CylinderMesh.new()
	mesh.bottom_radius = radius
	mesh.top_radius = radius if top_radius < 0.0 else top_radius
	mesh.height = height_value
	mesh.radial_segments = 12
	return mesh


func _sphere(radius: float) -> SphereMesh:
	var mesh := SphereMesh.new()
	mesh.radius = radius
	mesh.height = radius * 2.0
	mesh.radial_segments = 12
	mesh.rings = 6
	return mesh


func _material(key: String, color: Color, roughness_value: float) -> StandardMaterial3D:
	if _materials.has(key):
		return _materials[key] as StandardMaterial3D
	var material := StandardMaterial3D.new()
	material.albedo_color = color
	material.metallic = 0.34
	material.roughness = roughness_value
	_materials[key] = material
	return material


func _emissive_material(key: String, color: Color, energy: float) -> StandardMaterial3D:
	if _materials.has(key):
		return _materials[key] as StandardMaterial3D
	var material := StandardMaterial3D.new()
	material.albedo_color = color
	material.roughness = 0.26
	material.emission_enabled = true
	material.emission = color
	material.emission_energy_multiplier = energy
	_materials[key] = material
	return material
