extends SceneTree


func _init() -> void:
	call_deferred("_test")


func _test() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	root.add_child(city)
	await process_frame
	var utilities := city.get_node_or_null("UtilityResources") as Node
	if utilities == null:
		_fail("City does not contain the utility resource simulation")
		return
	var status := utilities.call("get_utility_status") as Dictionary
	var blocks := status.get("blocks", []) as Array
	var reservoirs := status.get("reservoirs", []) as Array
	var service_clusters := status.get("service_clusters", []) as Array
	if blocks.size() != 64 or reservoirs.size() != 6:
		_fail("expected 64 block profiles and 6 reservoirs, got %d and %d" % [blocks.size(), reservoirs.size()])
		return
	if service_clusters.size() != 16:
		_fail("expected sixteen shared compact water/power zones, got %d" % service_clusters.size())
		return
	var power_meters := city.get_tree().get_nodes_in_group("power_service_meters")
	var water_meters := city.get_tree().get_nodes_in_group("water_service_meters")
	if power_meters.size() != 16 or water_meters.size() != 16:
		_fail("expected one physical power and water meter for each shared zone")
		return
	for meter_value in power_meters + water_meters:
		var meter := meter_value as Node3D
		if meter == null or str(meter.get_meta("service_cluster", "")).is_empty() or meter.get_child_count() == 0:
			_fail("physical utility meter has no model or shared-zone identity")
			return
	var meter_power_total := 0.0
	for cluster_value in service_clusters:
		meter_power_total += float((cluster_value as Dictionary).get("power_meter_kw", 0.0))
	var status_power := status.get("power", {}) as Dictionary
	if absf(meter_power_total - float(status_power.get("demand_kw", 0.0))) > 0.11:
		_fail("power consumption is not sourced from the physical zone meter readings")
		return
	var distinct_power: Dictionary = {}
	var reservoir_levels: Dictionary = {}
	var zone_reservoirs: Dictionary = {}
	for block_value in blocks:
		var block := block_value as Dictionary
		if float(block.get("power_kw", 0.0)) <= 0.0 or float(block.get("water_m3h", 0.0)) <= 0.0 or str(block.get("reservoir", "")).is_empty():
			_fail("block demand or reservoir assignment is invalid: %s" % block)
			return
		var zone_name := str(block.get("service_cluster", ""))
		if zone_name.is_empty():
			_fail("block is missing its shared utility zone")
			return
		if zone_reservoirs.has(zone_name) and str(zone_reservoirs[zone_name]) != str(block.get("reservoir", "")):
			_fail("water reservoir boundary splits shared service zone %s" % zone_name)
			return
		zone_reservoirs[zone_name] = str(block.get("reservoir", ""))
		distinct_power[snappedf(float(block.get("power_kw", 0.0)), 0.1)] = true
	if distinct_power.size() < 8:
		_fail("block demand is too uniform; expected authored-building variation")
		return
	for reservoir_value in reservoirs:
		var reservoir := reservoir_value as Dictionary
		if int(reservoir.get("blocks", 0)) <= 0 or float(reservoir.get("demand_m3h", 0.0)) <= 0.0:
			_fail("reservoir distribution is not tied to block demand: %s" % reservoir)
			return
		reservoir_levels[snappedf(float(reservoir.get("percent", 0.0)), 0.1)] = true
		var site := city.get_node_or_null("WaterReservoirs/%s" % str(reservoir.get("name", ""))) as Node3D
		if site == null or not site.has_meta("water_percent"):
			_fail("reservoir meter is not synchronized to its 3D utility site")
			return
	if reservoir_levels.size() < 4:
		_fail("reservoir levels are unrealistically uniform")
		return
	var world_meters := city.get_tree().get_nodes_in_group("water_reservoir_meters")
	if world_meters.size() != 6:
		_fail("expected one visible in-world meter above each of six reservoirs, got %d" % world_meters.size())
		return
	for meter_value in world_meters:
		var meter := meter_value as Node3D
		if meter == null or meter.get_node_or_null("LevelLabel") == null or meter.get_node_or_null("MeterFill") == null:
			_fail("a reservoir's world-space label/bar is incomplete")
			return
		var level_label := meter.get_node("LevelLabel") as Label3D
		if not level_label.text.ends_with("%") or level_label.text.contains("\n") or absf(level_label.position.y) > 0.001:
			_fail("world reservoir meter must show only percentage beside, not above, its bar")
			return
	if float(utilities.get("simulation_hours_per_second")) > 0.013:
		_fail("utility depletion is not configured for slower shedding")
		return
	var grid := city.get_node_or_null("PowerGrid") as Node3D
	if grid == null or not grid.has_meta("energy_percent"):
		_fail("power meter is not synchronized to the PowerGrid scene")
		return
	var initial_power := float((status.get("power", {}) as Dictionary).get("available_kwh", 0.0))
	var initial_water := float((status.get("water", {}) as Dictionary).get("current_m3", 0.0))
	await create_timer(1.0).timeout
	status = utilities.call("get_utility_status") as Dictionary
	if float((status.get("power", {}) as Dictionary).get("available_kwh", 0.0)) >= initial_power or float((status.get("water", {}) as Dictionary).get("current_m3", 0.0)) >= initial_water:
		_fail("utility levels did not decrease with simulation time")
		return
	var internal_reservoirs := utilities.get("_reservoirs") as Dictionary
	var selected_reservoir_name := "NorthWestReservoir"
	var selected_reservoir := internal_reservoirs[selected_reservoir_name] as Dictionary
	selected_reservoir.current_m3 = 0.0
	internal_reservoirs[selected_reservoir_name] = selected_reservoir
	utilities.set("_reservoirs", internal_reservoirs)
	utilities.call("_refresh_service_outages")
	await process_frame
	status = utilities.call("get_utility_status") as Dictionary
	var water_outages := status.get("water_outage_blocks", []) as Array
	if water_outages.is_empty() or city.get_tree().get_nodes_in_group("water_outage_blocks").size() != water_outages.size():
		_fail("empty reservoir did not blue-highlight every associated block")
		return
	var fading_volumes := city.get_tree().get_nodes_in_group("utility_outage_fading_volumes")
	if fading_volumes.size() != water_outages.size():
		_fail("each outage block must render exactly one full fading vertical volume")
		return
	for volume_value in fading_volumes:
		var volume := volume_value as MeshInstance3D
		if volume == null or not volume.mesh is ArrayMesh or not volume.material_override is StandardMaterial3D:
			_fail("outage effect is not a vertex-faded 3D block volume")
			return
	for block_value in status.get("blocks", []) as Array:
		var block := block_value as Dictionary
		if str(block.get("reservoir", "")) == selected_reservoir_name and bool(block.get("water_connected", true)):
			_fail("empty reservoir did not cut water to %s" % str(block.get("name", "")))
			return
	utilities.call("recharge_all")
	var manual_cluster := str((service_clusters[0] as Dictionary).get("name", ""))
	utilities.call("set_manual_water_cluster", manual_cluster, true)
	await process_frame
	status = utilities.call("get_utility_status") as Dictionary
	if not (status.get("manual_water_clusters", []) as Array).has(manual_cluster):
		_fail("manual water cluster cut was not retained")
		return
	utilities.call("set_manual_power_cluster", manual_cluster, true)
	await process_frame
	status = utilities.call("get_utility_status") as Dictionary
	if not (status.get("manual_power_clusters", []) as Array).has(manual_cluster):
		_fail("manual power cluster cut was not retained")
		return
	var manual_blocks := 0
	for block_value in status.get("blocks", []) as Array:
		var block := block_value as Dictionary
		if str(block.get("service_cluster", "")) == manual_cluster:
			manual_blocks += 1
			if bool(block.get("water_connected", true)) or bool(block.get("power_connected", true)):
				_fail("manual cluster cut did not disconnect every associated block")
				return
	if manual_blocks <= 0:
		_fail("manual cluster has no associated city blocks")
		return
	for meter_value in power_meters:
		var meter := meter_value as Node3D
		if str(meter.get_meta("service_cluster", "")) == manual_cluster and float(meter.get_meta("reading_kw", -1.0)) != 0.0:
			_fail("power cut did not update the exact physical zone meter")
			return
	for meter_value in water_meters:
		var meter := meter_value as Node3D
		if str(meter.get_meta("service_cluster", "")) == manual_cluster and float(meter.get_meta("reading_m3h", -1.0)) != 0.0:
			_fail("water cut did not update the exact physical zone meter")
			return
	utilities.call("set_manual_water_cluster", manual_cluster, false)
	utilities.call("set_manual_power_cluster", manual_cluster, false)
	utilities.set("_power_available_kwh", 0.0)
	utilities.call("_refresh_service_outages")
	await process_frame
	status = utilities.call("get_utility_status") as Dictionary
	var shed_cluster := str(status.get("power_shed_cluster", ""))
	if shed_cluster.is_empty():
		_fail("empty power reserve did not choose a shared load-shed zone")
		return
	var shed_count := 0
	for block_value in status.get("blocks", []) as Array:
		var block := block_value as Dictionary
		if str(block.get("service_cluster", "")) == shed_cluster:
			shed_count += 1
			if bool(block.get("power_connected", true)):
				_fail("selected power-shed zone still reports power service")
				return
	if shed_count <= 0 or city.get_tree().get_nodes_in_group("power_outage_blocks").size() != shed_count:
		_fail("power shedding did not visibly and logically cut exactly its selected zone")
		return
	utilities.call("recharge_all")
	await process_frame
	await process_frame
	status = utilities.call("get_utility_status") as Dictionary
	var power := status.get("power", {}) as Dictionary
	var water := status.get("water", {}) as Dictionary
	if float(power.get("available_kwh", 0.0)) / maxf(float(power.get("capacity_kwh", 0.0)), 0.01) < 0.995 or float(water.get("current_m3", 0.0)) / maxf(float(water.get("capacity_m3", 0.0)), 0.01) < 0.995:
		_fail("manual full recharge did not restore all utilities")
		return
	if not str(status.get("power_shed_block", "")).is_empty() or not (status.get("water_outage_blocks", []) as Array).is_empty() or city.get_tree().get_nodes_in_group("water_outage_blocks").size() != 0 or city.get_tree().get_nodes_in_group("power_outage_blocks").size() != 0:
		_fail("full recharge did not restore services and clear outage highlights")
		return
	var hud := city.get_node_or_null("CameraControls/UtilityMeters") as PanelContainer
	if hud == null or hud.get_child_count() == 0:
		_fail("normal simulation UI is missing utility meter bars")
		return
	print("UTILITY RESOURCE TEST PASSED: 64 varied blocks, independent reservoirs, blue water cutoffs, red logical power shedding, live HUD meters, timed consumption, and full recharge")
	quit(0)


func _fail(message: String) -> void:
	push_error("UTILITY RESOURCE TEST FAILED: " + message)
	quit(1)
