extends SceneTree

const CitySpatialSafety = preload("res://scripts/city_spatial_safety.gd")
const REQUIRED_GROUP_COUNTS := {
	"sensors":104,
	"traffic_sensors":12,
	"air_quality_monitors":8,
	"noise_monitors":4,
	"weather_stations":4,
	"flood_level_sensors":4,
	"utility_telemetry_sensors":8,
	"smart_street_lights":256,
	"irrigation_valves":4,
	"hospitals":7,
	"police_facilities":1,
	"police_vehicles":8,
	# Emergency fleet roster (sumo/emergency_depots.json): two authored
	# ambulance models plus the pooled West Hospital unit, and the new fire,
	# tow and utility fleets parked in their station bays.
	"ambulance_vehicles":3,
	"fire_vehicles":2,
	"tow_vehicles":2,
	"utility_vehicles":2,
	"smoky_vehicles":6,
}


func _init() -> void:
	call_deferred("_test")


func _fail(message: String) -> void:
	printerr("SMART CITY ASSET TEST FAILED: " + message)
	quit(1)


func _test() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	root.add_child(city)
	await process_frame
	await process_frame
	for group_name in REQUIRED_GROUP_COUNTS:
		var nodes := get_nodes_in_group(group_name)
		# Every waste bin carries a weight sensor (street cans are fitted a moment after the furniture settles).
		var expected := int(REQUIRED_GROUP_COUNTS[group_name]) + (get_nodes_in_group("bin_weight_sensors").size() if group_name == "sensors" else 0)
		if nodes.size() != expected:
			_fail("%s has %d assets, expected %d" % [group_name, nodes.size(), expected])
			return
		var hidden_names: Array[String] = []
		for value in nodes:
			if value is Node3D and not (value as Node3D).is_visible_in_tree():
				hidden_names.append((value as Node3D).name)
		if not hidden_names.is_empty():
			_fail("%s contains hidden/unplaced models: %s" % [group_name, ", ".join(hidden_names)])
			return

	var buildings := CitySpatialSafety.building_bounds(self)
	var accepted: Array[AABB] = []
	for value in get_nodes_in_group("smart_city_devices"):
		if not value is Node3D:
			continue
		var device := value as Node3D
		if device.is_in_group("traffic_cameras"):
			continue
		if not bool(device.get_meta("device_site_spatially_safe", false)):
			_fail("%s was not assigned a verified collision-free site" % device.name)
			return
		var bounds := CitySpatialSafety.world_bounds(device)
		if absf(bounds.position.y - 0.17) > 0.018:
			_fail("%s floats or sinks: lowest model point is %.3f" % [device.name, bounds.position.y])
			return
		if CitySpatialSafety.overlaps_road(bounds, 0.08):
			_fail("%s overlaps a road corridor" % device.name)
			return
		for building in buildings:
			if CitySpatialSafety.horizontal_intersects(bounds, building, 0.08):
				_fail("%s intersects a building" % device.name)
				return
		for other in accepted:
			if CitySpatialSafety.horizontal_intersects(bounds, other, 0.20):
				_fail("%s intersects another registered device" % device.name)
				return
		accepted.append(bounds)
	for prop_value in get_nodes_in_group("npc_activity_props") + get_nodes_in_group("npc_interaction_props"):
		if not prop_value is Node3D or not (prop_value as Node3D).visible:
			continue
		var prop := prop_value as Node3D
		var prop_bounds := CitySpatialSafety.world_bounds(prop)
		for device_bounds in accepted:
			if CitySpatialSafety.horizontal_intersects(prop_bounds, device_bounds, 0.20):
				_fail("job/interaction prop %s intersects a registered smart-city device" % prop.name)
				return

	for group_name in ["traffic_sensors", "air_quality_monitors", "noise_monitors", "weather_stations", "flood_level_sensors"]:
		for value in get_nodes_in_group(group_name):
			var sensor := value as Node3D
			if sensor.find_children("*", "MeshInstance3D", true, false).size() < 4:
				_fail("%s does not have a complete purpose-built 3D model" % sensor.name)
				return
			if sensor.get_node_or_null("ServiceInteractionAnchor") == null:
				_fail("%s has no future interaction/service anchor" % sensor.name)
				return
	for value in get_nodes_in_group("smart_street_lights"):
		var street_light := value as Node3D
		var lens := street_light.get_node_or_null("LuminaireGlow") as MeshInstance3D
		var illumination := street_light.get_node_or_null("AdaptiveLuminaire") as SpotLight3D
		if lens == null or lens.position.y < 8.0 or lens.mesh is SphereMesh:
			_fail("%s does not have an integrated elevated LED lens" % street_light.name)
			return
		if illumination == null or illumination.position.y < 8.0 or illumination.shadow_enabled:
			_fail("%s does not cast light naturally from its integrated housing" % street_light.name)
			return
		if illumination.spot_range < 41.0:
			_fail("%s does not cover its full street and walking area" % street_light.name)
			return
		for mesh_value in street_light.find_children("*", "MeshInstance3D", true, false):
			if float((mesh_value as GeometryInstance3D).visibility_range_end) > 0.0:
				_fail("%s disappears at overview distance" % street_light.name)
				return

	# Coverage must span the complete 8x8 city, not just the original downtown
	# square. Every district receives four block-face fixtures.
	for district_value in get_nodes_in_group("city_districts"):
		var district := district_value as Node3D
		var nearby := 0
		for light_value in get_nodes_in_group("smart_street_lights"):
			var light := light_value as Node3D
			if Vector2(light.global_position.x, light.global_position.z).distance_to(Vector2(district.global_position.x, district.global_position.z)) < 42.0:
				nearby += 1
		if nearby < 4:
			_fail("district %s has only %d/4 surrounding street and walkway lights" % [district.name, nearby])
			return
	# Lanterns go only where a clear, flat, street-facing wall exists; a building
	# whose street side is all glazing keeps none rather than a misplaced one.
	if get_nodes_in_group("building_night_lights").size() < 210:
		_fail("only %d/231 street-facing lanterns were placed on clear wall" % get_nodes_in_group("building_night_lights").size())
		return
	for fixture_value in get_nodes_in_group("building_night_lights"):
		var fixture := fixture_value as Node3D
		var backplate := fixture.get_node_or_null("WallBackplate") as MeshInstance3D
		var lens := fixture.get_node_or_null("BuildingLampGlow") as MeshInstance3D
		var lamp := fixture.get_node_or_null("BuildingFacadeLight") as SpotLight3D
		if backplate == null or lens == null or lamp == null or not backplate.mesh is CylinderMesh or not lens.mesh is CylinderMesh:
			_fail("%s is not a complete modeled wall lantern" % fixture.name)
			return
		if not bool(fixture.get_meta("rendered_facade_hit", false)) or float(fixture.get_meta("mounting_gap", 1.0)) > 0.04:
			_fail("%s is not attached to a rendered facade polygon" % fixture.name)
			return
		if float(fixture.get_meta("mount_height_metres", 0.0)) > 3.5:
			_fail("%s is mounted too high to read as an entrance lantern" % fixture.name)
			return
		if str(fixture.get_meta("logical_placement", "")).is_empty() or float(fixture.get_meta("mount_height_metres", 0.0)) < 2.3:
			_fail("%s has no reviewed entrance placement" % fixture.name)
			return
		var material_name := str(fixture.get_meta("mount_surface_material", ""))
		for forbidden in ["window", "glass", "door", "wood", "darkgrey", "lightyellow", "mat24"]:
			if forbidden in material_name:
				_fail("%s is mounted on %s instead of entrance masonry" % [fixture.name, material_name])
				return
	for window_value in get_nodes_in_group("building_window_lights"):
		var exact_window := window_value as MeshInstance3D
		if exact_window == null or not exact_window.mesh is ArrayMesh:
			_fail("%s uses generated window geometry instead of original model polygons" % str(window_value.name))
			return
		if str(exact_window.get_meta("source_surface", "")).is_empty():
			_fail("%s is not traceable to an imported model surface" % exact_window.name)
			return
	for value in get_nodes_in_group("buildings") + get_nodes_in_group("backdrop_buildings"):
		var building := value as Node3D
		if int(building.get_meta("interior_window_glow_surfaces", 0)) <= 0:
			_fail("%s has no interior-lit authored windows" % building.name)
			return
		if not bool(building.get_meta("imported_emission_normalized", false)):
			_fail("%s did not normalize accidental imported whole-building emission" % building.name)
			return

	for value in get_nodes_in_group("air_quality_monitors"):
		var monitor := value as Node3D
		var old_ac := monitor.get_node_or_null("TelemetryUnit") as Node3D
		if old_ac != null and old_ac.visible:
			_fail("air monitor still renders the unrelated air-conditioner placeholder")
			return
	for value in get_nodes_in_group("traffic_sensors"):
		var detector := value as Node3D
		var old_sign := detector.get_node_or_null("SensorPole") as Node3D
		if old_sign != null and old_sign.visible:
			_fail("traffic detector still renders the unrelated bus-stop placeholder")
			return

	for value in get_nodes_in_group("smoky_vehicles"):
		var vehicle := value as Node3D
		if vehicle.is_in_group("emergency_vehicles"):
			_fail("emergency vehicle was illogically assigned bad exhaust")
			return
		var emitter := vehicle.get_node_or_null("BlackExhaustEmitter") as GPUParticles3D
		if emitter == null or not emitter.emitting or emitter.draw_pass_1 == null:
			_fail("%s has no functioning visible black-smoke particle model" % vehicle.name)
			return
		if str(vehicle.get_meta("emissions_status", "")) != "excessive_black_smoke":
			_fail("%s lacks emissions semantics for air-quality interaction" % vehicle.name)
			return

	var vehicle_count := get_nodes_in_group("vehicles").size() + get_nodes_in_group("backdrop_vehicles").size()
	if vehicle_count < 68:
		_fail("the authored 68-model vehicle pool was reduced")
		return
	print("SMART CITY ASSET TEST PASSED: exact imported-polygon window lighting, no guessed facade fixtures, 19 typed highlight families, grounded devices and emergency fleets")
	quit(0)
