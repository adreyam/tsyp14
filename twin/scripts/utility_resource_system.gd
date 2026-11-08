extends Node

const POWER_METER_SCENE := preload("res://assets/poly_pizza/city_props/Power Box.glb")
const WATER_METER_SCENE := preload("res://scenes/WaterMeterWorkProp.tscn")

## Deterministic utility simulation. Demand comes from each block's authored
## building mix, while water is assigned to the nearest municipal reservoir.
## It stays fully local and repeatable: no external service controls meters.

# Utilities consume at one quarter of the prior rate. Outages are still based
# on actual demand, but reserves now deplete gradually instead of cutting a
# service area too quickly during a normal session.
@export var simulation_hours_per_second := 0.0125
@export var auto_recharge_interval_seconds := 300.0
@export var auto_recharge_enabled := true
# Automatic service protection is deliberately enabled by default. It cuts a
# depleted reservoir's service area and makes one conservative power shed when
# the reserve is exhausted; operator-selected cuts are always honoured too.
@export var auto_outage_enabled := true
@export var power_capacity_kwh := 42000.0

const RESERVOIR_CAPACITIES := {
	"NorthCentralReservoir": 5200.0,
	"SouthCentralReservoir": 4400.0,
	"NorthWestReservoir": 3600.0,
	"NorthEastReservoir": 4000.0,
	"SouthWestReservoir": 3400.0,
	"SouthEastReservoir": 4600.0,
}
const RESERVOIR_START_FILL := {
	"NorthCentralReservoir": 0.93,
	"SouthCentralReservoir": 0.76,
	"NorthWestReservoir": 0.88,
	"NorthEastReservoir": 0.70,
	"SouthWestReservoir": 0.81,
	"SouthEastReservoir": 0.65,
}

var _city: Node3D
var _blocks: Array[Dictionary] = []
var _reservoirs: Dictionary = {}
var _power_available_kwh := 0.0
var _elapsed_seconds := 0.0
var _bookkeeping_elapsed := 0.0
var _next_recharge_seconds := 0.0
var _hud_power_label: Label
var _hud_power_bar: ProgressBar
var _hud_water_labels: Dictionary = {}
var _hud_water_bars: Dictionary = {}
var _hud_outage_label: Label
var _power_shed_cluster := ""
const BOOKKEEPING_INTERVAL := 0.2
var _water_outage_blocks: Array[String] = []
var _manual_water_clusters: Dictionary = {}
var _manual_power_clusters: Dictionary = {}
var _service_clusters: Dictionary = {}
var _power_meter_states: Dictionary = {}
var _water_meter_states: Dictionary = {}
var _water_outage_material: StandardMaterial3D
var _power_outage_material: StandardMaterial3D
var _water_outage_volume_material: StandardMaterial3D
var _power_outage_volume_material: StandardMaterial3D
var _water_outage_volume_mesh: ArrayMesh
var _power_outage_volume_mesh: ArrayMesh
var _reservoir_meter_labels: Dictionary = {}
var _reservoir_meter_fills: Dictionary = {}
var _reservoir_meter_materials: Dictionary = {}


func _ready() -> void:
	_city = get_parent() as Node3D
	_power_available_kwh = power_capacity_kwh * 0.84
	_build_reservoir_states()
	_build_block_profiles()
	_register_service_meter_states()
	_next_recharge_seconds = auto_recharge_interval_seconds
	_refresh_service_outages()
	_update_service_meter_readings()
	_sync_infrastructure_meter_metadata()
	call_deferred("_build_city_hud")
	call_deferred("_build_world_reservoir_meters")
	call_deferred("_build_physical_service_meters")


func _process(delta: float) -> void:
	_elapsed_seconds += delta
	# Meter bookkeeping, scene metadata and HUD/world readouts change slowly
	# and rewrite hundreds of nodes, labels, meshes and materials, so they run
	# on a fixed tick; manual cuts and recharges still refresh immediately.
	_bookkeeping_elapsed += delta
	if _bookkeeping_elapsed < BOOKKEEPING_INTERVAL:
		return
	var step := _bookkeeping_elapsed
	_bookkeeping_elapsed = 0.0
	_refresh_service_outages()
	_update_service_meter_readings()
	var simulation_hours := step * simulation_hours_per_second
	var power_demand := _total_power_demand_kw()
	_power_available_kwh = maxf(0.0, _power_available_kwh - power_demand * simulation_hours)
	for reservoir_name in _reservoirs:
		var reservoir := _reservoirs[reservoir_name] as Dictionary
		reservoir.current_m3 = maxf(0.0, float(reservoir.current_m3) - _active_reservoir_demand_m3h(str(reservoir_name)) * simulation_hours)
		_reservoirs[reservoir_name] = reservoir
	if auto_recharge_enabled and _elapsed_seconds >= _next_recharge_seconds:
		recharge_all()
	_refresh_service_outages()
	_update_service_meter_readings()
	_sync_infrastructure_meter_metadata()
	_update_world_reservoir_meters()
	_update_city_hud()


func _build_reservoir_states() -> void:
	for site_value in get_tree().get_nodes_in_group("water_reservoir_sites"):
		var site := site_value as Node3D
		var name := site.name
		var capacity := float(RESERVOIR_CAPACITIES.get(name, 3600.0))
		var initial_fill := float(RESERVOIR_START_FILL.get(name, 0.75))
		_reservoirs[name] = {
			"node": site,
			"capacity_m3": capacity,
			"current_m3": capacity * initial_fill,
			"demand_m3h": 0.0,
			"blocks": 0,
		}


func _build_block_profiles() -> void:
	var candidates: Array[Node3D] = []
	var downtown := _city.get_node_or_null("Buildings") as Node3D
	if downtown != null:
		for child in downtown.get_children():
			if child is Node3D and (child as Node3D).name.begins_with("Block_"):
				candidates.append(child as Node3D)
	for district_value in get_tree().get_nodes_in_group("city_districts"):
		if district_value is Node3D:
			candidates.append(district_value as Node3D)
	var bounds := _candidate_city_bounds(candidates)
	# Attach every small service zone to one reservoir from its geographic centre.
	# This prevents a water reservoir boundary from splitting a UI/visual zone.
	var zone_centres: Dictionary = {}
	for block in candidates:
		var zone_name := _service_cluster_for(block.global_position, bounds)
		var centre_data := zone_centres.get(zone_name, {"sum": Vector3.ZERO, "count": 0}) as Dictionary
		centre_data.sum = (centre_data.sum as Vector3) + block.global_position
		centre_data.count = int(centre_data.count) + 1
		zone_centres[zone_name] = centre_data
	for block in candidates:
		var demand := _measure_block_demand(block)
		var service_cluster := _service_cluster_for(block.global_position, bounds)
		var centre_data := zone_centres[service_cluster] as Dictionary
		var reservoir_name := _nearest_reservoir((centre_data.sum as Vector3) / float(centre_data.count))
		_blocks.append({
			"name": block.name,
			"node": block,
			"power_kw": float(demand.get("power_kw", 0.0)),
			"water_m3h": float(demand.get("water_m3h", 0.0)),
			"reservoir": reservoir_name,
			"service_cluster": service_cluster,
			"building_count": int(demand.get("building_count", 0)),
			"criticality": int(demand.get("criticality", 0)),
			"power_connected": true,
			"water_connected": true,
		})
		var reservoir := _reservoirs[reservoir_name] as Dictionary
		reservoir.demand_m3h = float(reservoir.get("demand_m3h", 0.0)) + float(demand.get("water_m3h", 0.0))
		reservoir.blocks = int(reservoir.get("blocks", 0)) + 1
		_reservoirs[reservoir_name] = reservoir
		var cluster := _service_clusters.get(service_cluster, {"name": service_cluster, "blocks": 0, "power_kw": 0.0, "water_m3h": 0.0, "criticality": 0, "reservoirs": {}, "nodes": []}) as Dictionary
		cluster.blocks = int(cluster.blocks) + 1
		cluster.power_kw = float(cluster.power_kw) + float(demand.get("power_kw", 0.0))
		cluster.water_m3h = float(cluster.water_m3h) + float(demand.get("water_m3h", 0.0))
		cluster.criticality = maxi(int(cluster.criticality), int(demand.get("criticality", 0)))
		var cluster_reservoirs := cluster.reservoirs as Dictionary
		cluster_reservoirs[reservoir_name] = true
		cluster.reservoirs = cluster_reservoirs
		var cluster_nodes := cluster.nodes as Array
		cluster_nodes.append(block)
		cluster.nodes = cluster_nodes
		_service_clusters[service_cluster] = cluster


func _register_service_meter_states() -> void:
	var cluster_names := _service_clusters.keys()
	cluster_names.sort()
	for cluster_name_value in cluster_names:
		var cluster_name := str(cluster_name_value)
		var cluster := _service_clusters[cluster_name] as Dictionary
		var nodes := cluster.nodes as Array
		if nodes.is_empty():
			continue
		var first_block := nodes.front() as Node3D
		var last_block := nodes.back() as Node3D
		var reservoirs := (cluster.reservoirs as Dictionary).keys()
		reservoirs.sort()
		var reservoir_name := str(reservoirs.front())
		_power_meter_states[cluster_name] = {"cluster": cluster_name, "node": null, "position": first_block.global_position + Vector3(-29.0, 0.17, -29.0), "reading_kw": 0.0, "capacity_kw": float(cluster.power_kw), "reservoir": reservoir_name}
		_water_meter_states[cluster_name] = {"cluster": cluster_name, "node": null, "position": last_block.global_position + Vector3(29.0, 0.17, 29.0), "reading_m3h": 0.0, "capacity_m3h": float(cluster.water_m3h), "reservoir": reservoir_name}


func _build_physical_service_meters() -> void:
	if _city.has_node("ServiceMeters"):
		return
	var holder := Node3D.new()
	holder.name = "ServiceMeters"
	_city.add_child(holder)
	var meter_index := 0
	for cluster_name_value in _service_clusters.keys():
		meter_index += 1
		var cluster_name := str(cluster_name_value)
		var power_state := _power_meter_states[cluster_name] as Dictionary
		var power_meter := Node3D.new()
		power_meter.name = "PowerMeter_%s" % cluster_name
		power_meter.add_to_group("power_service_meters")
		power_meter.add_to_group("sensors")
		power_meter.position = power_state.position as Vector3
		power_meter.set_meta("service_cluster", cluster_name)
		power_meter.set_meta("meter_kind", "power")
		power_meter.set_meta("sensor_id", "PWR-%02d" % meter_index)
		power_meter.set_meta("asset_type", "zone_power_meter")
		power_meter.set_meta("sensed_metrics", ["power_demand", "supply_status"])
		power_meter.set_meta("highlightable", true)
		holder.add_child(power_meter)
		var power_asset := POWER_METER_SCENE.instantiate() as Node3D
		power_asset.name = "PowerMeterModel"
		power_meter.add_child(power_asset)
		_fit_meter_asset_to_height(power_asset, 1.60)
		power_state.node = power_meter
		_power_meter_states[cluster_name] = power_state
		var water_state := _water_meter_states[cluster_name] as Dictionary
		var water_meter := Node3D.new()
		water_meter.name = "WaterMeter_%s" % cluster_name
		water_meter.add_to_group("water_service_meters")
		water_meter.add_to_group("sensors")
		water_meter.position = water_state.position as Vector3
		water_meter.set_meta("service_cluster", cluster_name)
		water_meter.set_meta("meter_kind", "water")
		water_meter.set_meta("sensor_id", "WTR-%02d" % meter_index)
		water_meter.set_meta("asset_type", "zone_water_meter")
		water_meter.set_meta("sensed_metrics", ["water_flow", "supply_status"])
		water_meter.set_meta("highlightable", true)
		holder.add_child(water_meter)
		var water_asset := WATER_METER_SCENE.instantiate() as Node3D
		water_asset.name = "WaterMeterModel"
		water_meter.add_child(water_asset)
		_fit_meter_asset_to_height(water_asset, 1.25)
		water_state.node = water_meter
		_water_meter_states[cluster_name] = water_state
	_update_service_meter_readings()


func _update_service_meter_readings() -> void:
	for cluster_name_value in _service_clusters.keys():
		var cluster_name := str(cluster_name_value)
		var power_reading := 0.0
		var water_reading := 0.0
		for block_value in _blocks:
			var block := block_value as Dictionary
			if str(block.get("service_cluster", "")) != cluster_name:
				continue
			if bool(block.get("power_connected", true)):
				power_reading += float(block.get("power_kw", 0.0))
			if bool(block.get("water_connected", true)):
				water_reading += float(block.get("water_m3h", 0.0))
		var power_state := _power_meter_states[cluster_name] as Dictionary
		power_state.reading_kw = snappedf(power_reading, 0.1)
		_power_meter_states[cluster_name] = power_state
		var power_node := power_state.get("node") as Node3D
		if power_node != null:
			power_node.set_meta("reading_kw", power_state.reading_kw)
			power_node.set_meta("capacity_kw", power_state.capacity_kw)
			power_node.set_meta("service_online", power_reading > 0.0)
		var water_state := _water_meter_states[cluster_name] as Dictionary
		water_state.reading_m3h = snappedf(water_reading, 0.1)
		_water_meter_states[cluster_name] = water_state
		var water_node := water_state.get("node") as Node3D
		if water_node != null:
			water_node.set_meta("reading_m3h", water_state.reading_m3h)
			water_node.set_meta("capacity_m3h", water_state.capacity_m3h)
			water_node.set_meta("reservoir", water_state.reservoir)
			water_node.set_meta("service_online", water_reading > 0.0)


func _fit_meter_asset_to_height(asset: Node3D, height_value: float) -> void:
	var bounds := _meter_local_bounds(asset)
	if bounds.size.y > 0.001:
		asset.scale *= height_value / bounds.size.y
		asset.position.y = 0.17 - bounds.position.y * asset.scale.y


func _meter_local_bounds(target: Node3D) -> AABB:
	var result := AABB()
	var has_bounds := false
	for mesh_value in target.find_children("*", "MeshInstance3D", true, false):
		var mesh := mesh_value as MeshInstance3D
		if mesh.mesh == null:
			continue
		for corner in _aabb_corners(mesh.mesh.get_aabb()):
			var point := target.to_local(mesh.to_global(corner))
			result = AABB(point, Vector3.ZERO) if not has_bounds else result.expand(point)
			has_bounds = true
	return result


func _candidate_city_bounds(candidates: Array[Node3D]) -> AABB:
	var minimum := Vector3(INF, 0.0, INF)
	var maximum := Vector3(-INF, 0.0, -INF)
	for candidate in candidates:
		minimum.x = minf(minimum.x, candidate.global_position.x)
		minimum.z = minf(minimum.z, candidate.global_position.z)
		maximum.x = maxf(maximum.x, candidate.global_position.x)
		maximum.z = maxf(maximum.z, candidate.global_position.z)
	return AABB(minimum, Vector3(maxf(1.0, maximum.x - minimum.x), 1.0, maxf(1.0, maximum.z - minimum.z)))


func _service_cluster_for(location: Vector3, bounds: AABB) -> String:
	# Sixteen compact geographic zones (a 4 × 4 city grid) are shared by both
	# utilities. Therefore a UI zone, its block selection and its 3D volume can
	# never disagree about membership.
	var column := mini(3, int(clampf((location.x - bounds.position.x) / bounds.size.x, 0.0, 0.9999) * 4.0))
	var row := mini(3, int(clampf((location.z - bounds.position.z) / bounds.size.z, 0.0, 0.9999) * 4.0))
	var bands := ["North", "NorthCentral", "SouthCentral", "South"]
	return "Zone_%s_%d" % [str(bands[row]), column + 1]


func _measure_block_demand(block: Node3D) -> Dictionary:
	var power_kw := 3.0
	var water_m3h := 1.2
	var building_count := 0
	var criticality := 15
	for child in block.get_children():
		if not child is Node3D or not (child as Node3D).is_in_group("buildings"):
			continue
		building_count += 1
		var profile := _building_profile(child as Node3D)
		power_kw += float(profile.power_kw)
		water_m3h += float(profile.water_m3h)
		criticality = maxi(criticality, int(profile.criticality))
	# A stable small district factor stops same-use blocks looking artificially
	# identical while remaining deterministic across launches.
	var variation := 0.92 + float(_stable_code(block.name) % 17) * 0.01
	return {"power_kw": snappedf(power_kw * variation, 0.1), "water_m3h": snappedf(water_m3h * variation, 0.1), "building_count": building_count, "criticality": criticality}


func _building_profile(building: Node3D) -> Dictionary:
	var source := (building.scene_file_path + " " + building.name).to_lower()
	if "hospital" in source:
		return {"power_kw": 26.0, "water_m3h": 16.0, "criticality": 100}
	if "skyscraper" in source:
		return {"power_kw": 22.0, "water_m3h": 10.0, "criticality": 48}
	if "hotel" in source:
		return {"power_kw": 18.0, "water_m3h": 13.0, "criticality": 58}
	if "large building" in source or "big building" in source:
		return {"power_kw": 15.0, "water_m3h": 8.0, "criticality": 38}
	if "apartment" in source:
		return {"power_kw": 13.0, "water_m3h": 11.0, "criticality": 72}
	if "pizza" in source or "bar" in source or "corner" in source:
		return {"power_kw": 11.0, "water_m3h": 7.0, "criticality": 25}
	if "building" in source:
		return {"power_kw": 9.0, "water_m3h": 5.0, "criticality": 35}
	if "town house" in source or "house" in source:
		return {"power_kw": 5.0, "water_m3h": 4.0, "criticality": 75}
	return {"power_kw": 6.5, "water_m3h": 4.5, "criticality": 45}


func _nearest_reservoir(location: Vector3) -> String:
	var nearest_name := ""
	var nearest_distance := INF
	for reservoir_name in _reservoirs:
		var site := (_reservoirs[reservoir_name] as Dictionary).node as Node3D
		var distance := location.distance_squared_to(site.global_position)
		if distance < nearest_distance:
			nearest_distance = distance
			nearest_name = str(reservoir_name)
	return nearest_name


func _stable_code(text_value: String) -> int:
	# Node names are short. This explicit loop avoids randomized hash values.
	var code := 0
	for index in text_value.length():
		code = (code * 31 + text_value.unicode_at(index)) % 100000
	return code


func _total_power_demand_kw() -> float:
	var total := 0.0
	for meter_state_value in _power_meter_states.values():
		total += float((meter_state_value as Dictionary).get("reading_kw", 0.0))
	return total


func _active_reservoir_demand_m3h(reservoir_name: String) -> float:
	var total := 0.0
	for meter_state_value in _water_meter_states.values():
		var meter_state := meter_state_value as Dictionary
		if str(meter_state.get("reservoir", "")) == reservoir_name:
			total += float(meter_state.get("reading_m3h", 0.0))
	return total


func _refresh_service_outages() -> void:
	var changed := false
	var next_water_outages: Array[String] = []
	for index in _blocks.size():
		var block := _blocks[index]
		var reservoir := _reservoirs[str(block.get("reservoir", ""))] as Dictionary
		var water_available := not auto_outage_enabled or float(reservoir.get("current_m3", 0.0)) > 0.01
		var manually_cut := _manual_water_clusters.has(str(block.get("service_cluster", "")))
		var should_connect := water_available and not manually_cut
		if bool(block.get("water_connected", true)) != should_connect:
			block.water_connected = should_connect
			_blocks[index] = block
			changed = true
		if not should_connect:
			next_water_outages.append(str(block.get("name", "")))
	if _water_outage_blocks != next_water_outages:
		_water_outage_blocks = next_water_outages
		changed = true
	if not auto_outage_enabled or _power_available_kwh > 0.01:
		if not _power_shed_cluster.is_empty():
			_power_shed_cluster = ""
			changed = true
	elif _power_shed_cluster.is_empty():
		_power_shed_cluster = _select_power_shed_cluster()
		if not _power_shed_cluster.is_empty():
			changed = true
	for index in _blocks.size():
		var block := _blocks[index]
		var manually_cut := _manual_power_clusters.has(str(block.get("service_cluster", "")))
		var automatically_shed := str(block.get("service_cluster", "")) == _power_shed_cluster
		var should_connect := not manually_cut and not automatically_shed
		if bool(block.get("power_connected", true)) != should_connect:
			block.power_connected = should_connect
			_blocks[index] = block
			changed = true
	if changed:
		_rebuild_outage_highlights()


func _select_power_shed_cluster() -> String:
	# Automatic shedding uses the same compact zone the operator sees. Zones
	# containing critical services are protected; otherwise higher load is shed
	# first when criticality is equal.
	var selected_name := ""
	for cluster_name in _service_clusters:
		if _manual_power_clusters.has(str(cluster_name)):
			continue
		var candidate := _service_clusters[cluster_name] as Dictionary
		if selected_name.is_empty():
			selected_name = str(cluster_name)
			continue
		var selected := _service_clusters[selected_name] as Dictionary
		if int(candidate.criticality) < int(selected.criticality) or (int(candidate.criticality) == int(selected.criticality) and float(candidate.power_kw) > float(selected.power_kw)):
			selected_name = str(cluster_name)
	return selected_name


func _rebuild_outage_highlights() -> void:
	# Retain one holder and replace only its children. Queueing the entire holder
	# and immediately creating another one leaves same-named nodes alive for one
	# frame, which can briefly show stale blue/red service areas.
	var holder := _city.get_node_or_null("UtilityOutageHighlights") as Node3D
	if holder == null:
		holder = Node3D.new()
		holder.name = "UtilityOutageHighlights"
		_city.add_child(holder)
	for child in holder.get_children():
		child.queue_free()
	if _water_outage_blocks.is_empty() and _power_shed_cluster.is_empty() and _manual_power_clusters.is_empty():
		return
	for block in _blocks:
		var state := block as Dictionary
		var block_node := state.get("node") as Node3D
		if block_node == null:
			continue
		if not bool(state.get("water_connected", true)):
			_add_block_outage_overlay(holder, block_node, _get_outage_material(Color(0.06, 0.48, 1.0), true), "water_outage_blocks")
		if not bool(state.get("power_connected", true)):
			_add_block_outage_overlay(holder, block_node, _get_outage_material(Color(1.0, 0.06, 0.04), false), "power_outage_blocks")


func _add_block_outage_overlay(parent: Node3D, block: Node3D, material: StandardMaterial3D, group_name: String) -> void:
	# A real full-block 3D volume. Its four broad side walls start coloured at
	# the ground and use vertex alpha to become transparent toward the open top.
	# There is deliberately no top face, ring, bloom, or overlapping blur mesh.
	var marker := Node3D.new()
	marker.name = "OutageVolume_%s" % block.name
	marker.add_to_group(group_name)
	parent.add_child(marker)
	marker.global_position = block.global_position
	var ground := MeshInstance3D.new()
	ground.name = "GroundBase"
	var ground_mesh := BoxMesh.new()
	ground_mesh.size = Vector3(78.0, 0.16, 78.0)
	ground.mesh = ground_mesh
	ground.material_override = material
	ground.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	marker.add_child(ground)
	ground.position = Vector3(0.0, 0.34, 0.0)
	var water := group_name == "water_outage_blocks"
	var volume := MeshInstance3D.new()
	volume.name = "FadingVerticalBlock"
	volume.mesh = _get_outage_volume_mesh(water)
	volume.material_override = _get_outage_volume_material(water)
	volume.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	volume.add_to_group("utility_outage_fading_volumes")
	marker.add_child(volume)


func _get_outage_volume_mesh(water: bool) -> ArrayMesh:
	var cached := _water_outage_volume_mesh if water else _power_outage_volume_mesh
	if cached != null:
		return cached
	var tool := SurfaceTool.new()
	tool.begin(Mesh.PRIMITIVE_TRIANGLES)
	var bottom_colour := Color(0.06, 0.48, 1.0, 0.34) if water else Color(1.0, 0.06, 0.04, 0.34)
	var top_colour := Color(bottom_colour.r, bottom_colour.g, bottom_colour.b, 0.0)
	const HALF_WIDTH := 38.8
	const HEIGHT := 24.0
	# Four open side walls; each quad shares a coloured ground edge and a fully
	# transparent upper edge, so the visible block genuinely fades vertically.
	_add_fading_volume_side(tool, Vector3(-HALF_WIDTH, 0.42, -HALF_WIDTH), Vector3(HALF_WIDTH, 0.42, -HALF_WIDTH), Vector3(HALF_WIDTH, HEIGHT + 0.42, -HALF_WIDTH), Vector3(-HALF_WIDTH, HEIGHT + 0.42, -HALF_WIDTH), bottom_colour, top_colour)
	_add_fading_volume_side(tool, Vector3(HALF_WIDTH, 0.42, -HALF_WIDTH), Vector3(HALF_WIDTH, 0.42, HALF_WIDTH), Vector3(HALF_WIDTH, HEIGHT + 0.42, HALF_WIDTH), Vector3(HALF_WIDTH, HEIGHT + 0.42, -HALF_WIDTH), bottom_colour, top_colour)
	_add_fading_volume_side(tool, Vector3(HALF_WIDTH, 0.42, HALF_WIDTH), Vector3(-HALF_WIDTH, 0.42, HALF_WIDTH), Vector3(-HALF_WIDTH, HEIGHT + 0.42, HALF_WIDTH), Vector3(HALF_WIDTH, HEIGHT + 0.42, HALF_WIDTH), bottom_colour, top_colour)
	_add_fading_volume_side(tool, Vector3(-HALF_WIDTH, 0.42, HALF_WIDTH), Vector3(-HALF_WIDTH, 0.42, -HALF_WIDTH), Vector3(-HALF_WIDTH, HEIGHT + 0.42, -HALF_WIDTH), Vector3(-HALF_WIDTH, HEIGHT + 0.42, HALF_WIDTH), bottom_colour, top_colour)
	var mesh := tool.commit()
	if water:
		_water_outage_volume_mesh = mesh
	else:
		_power_outage_volume_mesh = mesh
	return mesh


func _add_fading_volume_side(tool: SurfaceTool, bottom_left: Vector3, bottom_right: Vector3, top_right: Vector3, top_left: Vector3, bottom_colour: Color, top_colour: Color) -> void:
	tool.set_color(bottom_colour)
	tool.add_vertex(bottom_left)
	tool.set_color(bottom_colour)
	tool.add_vertex(bottom_right)
	tool.set_color(top_colour)
	tool.add_vertex(top_right)
	tool.set_color(bottom_colour)
	tool.add_vertex(bottom_left)
	tool.set_color(top_colour)
	tool.add_vertex(top_right)
	tool.set_color(top_colour)
	tool.add_vertex(top_left)


func _get_outage_material(colour: Color, water: bool) -> StandardMaterial3D:
	var material := _water_outage_material if water else _power_outage_material
	if material != null:
		return material
	material = StandardMaterial3D.new()
	material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	material.albedo_color = Color(colour.r, colour.g, colour.b, 0.22)
	if water:
		_water_outage_material = material
	else:
		_power_outage_material = material
	return material


func _get_outage_volume_material(water: bool) -> StandardMaterial3D:
	var material := _water_outage_volume_material if water else _power_outage_volume_material
	if material != null:
		return material
	material = StandardMaterial3D.new()
	material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	material.vertex_color_use_as_albedo = true
	material.cull_mode = BaseMaterial3D.CULL_DISABLED
	if water:
		_water_outage_volume_material = material
	else:
		_power_outage_volume_material = material
	return material


func recharge_all() -> void:
	_power_available_kwh = power_capacity_kwh
	for reservoir_name in _reservoirs:
		var reservoir := _reservoirs[reservoir_name] as Dictionary
		reservoir.current_m3 = float(reservoir.capacity_m3)
		_reservoirs[reservoir_name] = reservoir
	_power_shed_cluster = ""
	_water_outage_blocks.clear()
	_refresh_service_outages()
	_update_service_meter_readings()
	_next_recharge_seconds = _elapsed_seconds + auto_recharge_interval_seconds
	_sync_infrastructure_meter_metadata()
	_update_city_hud()


func set_power_reserve_percent(percent: float) -> void:
	## Energy scenarios (sim_api UTILITY set_reserve): the grid storage set to a percentage; the automatic recharge waits
	## a full interval so the low reserve lasts long enough for the Brain to act on it.
	_power_available_kwh = clampf(percent, 0.0, 100.0) / 100.0 * power_capacity_kwh
	_next_recharge_seconds = _elapsed_seconds + auto_recharge_interval_seconds
	_refresh_service_outages()
	_update_service_meter_readings()
	_sync_infrastructure_meter_metadata()
	_update_city_hud()


func set_auto_recharge_enabled(enabled: bool) -> void:
	auto_recharge_enabled = enabled
	if enabled:
		_next_recharge_seconds = _elapsed_seconds + auto_recharge_interval_seconds


func _request_bookkeeping() -> void:
	## Operator actions show on meters and HUD on the very next frame.
	_bookkeeping_elapsed = maxf(_bookkeeping_elapsed, BOOKKEEPING_INTERVAL)


func set_manual_water_cluster(cluster_name: String, cut: bool) -> void:
	if not _service_clusters.has(cluster_name):
		return
	if cut:
		_manual_water_clusters[cluster_name] = true
	else:
		_manual_water_clusters.erase(cluster_name)
	_refresh_service_outages()
	_update_service_meter_readings()
	_request_bookkeeping()


func set_manual_power_cluster(cluster_name: String, cut: bool) -> void:
	if not _service_clusters.has(cluster_name):
		return
	if cut:
		_manual_power_clusters[cluster_name] = true
	else:
		_manual_power_clusters.erase(cluster_name)
	_refresh_service_outages()
	_update_service_meter_readings()
	_request_bookkeeping()


func _sync_infrastructure_meter_metadata() -> void:
	var grid := _city.get_node_or_null("PowerGrid") as Node3D
	if grid != null:
		grid.set_meta("energy_available_kwh", _power_available_kwh)
		grid.set_meta("energy_capacity_kwh", power_capacity_kwh)
		grid.set_meta("energy_percent", _power_available_kwh / power_capacity_kwh * 100.0)
	for reservoir_name in _reservoirs:
		var reservoir := _reservoirs[reservoir_name] as Dictionary
		var site := reservoir.node as Node3D
		site.set_meta("water_current_m3", float(reservoir.current_m3))
		site.set_meta("water_capacity_m3", float(reservoir.capacity_m3))
		site.set_meta("water_percent", float(reservoir.current_m3) / float(reservoir.capacity_m3) * 100.0)
		site.set_meta("water_demand_m3h", float(reservoir.demand_m3h))
	for block in _blocks:
		var state := block as Dictionary
		var block_node := state.get("node") as Node3D
		if block_node != null:
			block_node.set_meta("power_connected", bool(state.get("power_connected", true)))
			block_node.set_meta("water_connected", bool(state.get("water_connected", true)))
			block_node.set_meta("service_cluster", str(state.get("service_cluster", "")))


func _build_world_reservoir_meters() -> void:
	if _city.has_node("WaterReservoirMeters"):
		return
	var holder := Node3D.new()
	holder.name = "WaterReservoirMeters"
	_city.add_child(holder)
	var reservoir_names := _reservoirs.keys()
	reservoir_names.sort()
	for reservoir_name in reservoir_names:
		var reservoir := _reservoirs[reservoir_name] as Dictionary
		var site := reservoir.node as Node3D
		var meter := Node3D.new()
		meter.name = "%s_Meter" % reservoir_name
		meter.add_to_group("water_reservoir_meters")
		holder.add_child(meter)
		meter.global_position = site.global_position + Vector3(0.0, _site_meter_height(site), 0.0)
		var label := Label3D.new()
		label.name = "LevelLabel"
		label.billboard = BaseMaterial3D.BILLBOARD_ENABLED
		label.pixel_size = 0.008
		label.font_size = 52
		label.outline_size = 8
		label.modulate = Color(0.50, 0.86, 1.0)
		# World meter copy is intentionally minimal: a percentage positioned at
		# the bar's right edge, never stacked above the bar or reservoir model.
		label.position = Vector3(8.0, 0.0, 0.0)
		meter.add_child(label)
		var background := _world_meter_quad("MeterBackground", Vector2(13.0, 1.15), Color(0.015, 0.03, 0.05, 0.9))
		background.position = Vector3(0.0, 0.0, 0.0)
		meter.add_child(background)
		var fill := _world_meter_quad("MeterFill", Vector2(1.0, 0.78), Color(0.08, 0.58, 1.0, 0.95))
		fill.position = Vector3(-5.9, 0.0, -0.02)
		meter.add_child(fill)
		_reservoir_meter_labels[reservoir_name] = label
		_reservoir_meter_fills[reservoir_name] = fill
		_reservoir_meter_materials[reservoir_name] = fill.material_override as StandardMaterial3D
	_update_world_reservoir_meters()


func _world_meter_quad(node_name: String, size_value: Vector2, colour: Color) -> MeshInstance3D:
	var quad := MeshInstance3D.new()
	quad.name = node_name
	var mesh := QuadMesh.new()
	mesh.size = size_value
	quad.mesh = mesh
	var material := StandardMaterial3D.new()
	material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	material.albedo_color = colour
	material.emission_enabled = true
	material.emission = colour
	material.emission_energy_multiplier = 0.55
	material.billboard_mode = BaseMaterial3D.BILLBOARD_ENABLED
	material.no_depth_test = true
	quad.material_override = material
	quad.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	return quad


func _site_meter_height(site: Node3D) -> float:
	var highest := 0.0
	for mesh_value in site.find_children("*", "MeshInstance3D", true, false):
		var mesh := mesh_value as MeshInstance3D
		if mesh.mesh == null:
			continue
		for corner in _aabb_corners(mesh.get_aabb()):
			highest = maxf(highest, site.to_local(mesh.to_global(corner)).y)
	return maxf(16.0, highest + 4.0)


func _aabb_corners(bounds: AABB) -> Array[Vector3]:
	return [
		Vector3(bounds.position.x, bounds.position.y, bounds.position.z),
		Vector3(bounds.end.x, bounds.position.y, bounds.position.z),
		Vector3(bounds.position.x, bounds.end.y, bounds.position.z),
		Vector3(bounds.end.x, bounds.end.y, bounds.position.z),
		Vector3(bounds.position.x, bounds.position.y, bounds.end.z),
		Vector3(bounds.end.x, bounds.position.y, bounds.end.z),
		Vector3(bounds.position.x, bounds.end.y, bounds.end.z),
		Vector3(bounds.end.x, bounds.end.y, bounds.end.z),
	]


func _update_world_reservoir_meters() -> void:
	if _reservoir_meter_labels.is_empty():
		return
	for reservoir_name in _reservoirs:
		var reservoir := _reservoirs[reservoir_name] as Dictionary
		var percent := clampf(float(reservoir.current_m3) / float(reservoir.capacity_m3) * 100.0, 0.0, 100.0)
		var label := _reservoir_meter_labels.get(reservoir_name) as Label3D
		var fill := _reservoir_meter_fills.get(reservoir_name) as MeshInstance3D
		var material := _reservoir_meter_materials.get(reservoir_name) as StandardMaterial3D
		if label != null:
			label.text = "%.0f%%" % percent
			label.modulate = Color(1.0, 0.30, 0.26) if percent <= 0.01 else Color(0.50, 0.86, 1.0)
		if fill != null:
			var mesh := fill.mesh as QuadMesh
			mesh.size = Vector2(maxf(0.16, 12.45 * percent / 100.0), 0.78)
			fill.position.x = -6.1 + mesh.size.x * 0.5
		if material != null:
			var colour := Color(1.0, 0.06, 0.05, 0.96) if percent <= 0.01 else Color(0.08, 0.58, 1.0, 0.96)
			material.albedo_color = colour
			material.emission = colour


func get_utility_status() -> Dictionary:
	var reservoirs: Array[Dictionary] = []
	var total_water := 0.0
	var total_capacity := 0.0
	var reservoir_names := _reservoirs.keys()
	reservoir_names.sort()
	for reservoir_name in reservoir_names:
		var reservoir := _reservoirs[reservoir_name] as Dictionary
		var capacity := float(reservoir.capacity_m3)
		var current := float(reservoir.current_m3)
		total_water += current
		total_capacity += capacity
		reservoirs.append({
			"name": reservoir_name,
			"current_m3": current,
			"capacity_m3": capacity,
			"percent": current / capacity * 100.0,
			"demand_m3h": float(reservoir.demand_m3h),
			"blocks": int(reservoir.blocks),
		})
	var public_blocks: Array[Dictionary] = []
	for block in _blocks:
		var state := block as Dictionary
		public_blocks.append({
			"name": str(state.get("name", "")),
			"power_kw": float(state.get("power_kw", 0.0)),
			"water_m3h": float(state.get("water_m3h", 0.0)),
			"reservoir": str(state.get("reservoir", "")),
			"service_cluster": str(state.get("service_cluster", "")),
			"building_count": int(state.get("building_count", 0)),
			"criticality": int(state.get("criticality", 0)),
			"power_connected": bool(state.get("power_connected", true)),
			"water_connected": bool(state.get("water_connected", true)),
		})
	var public_clusters: Array[Dictionary] = []
	var cluster_names := _service_clusters.keys()
	cluster_names.sort()
	for cluster_name in cluster_names:
		var cluster := _service_clusters[cluster_name] as Dictionary
		var power_meter := _power_meter_states.get(cluster_name, {}) as Dictionary
		var water_meter := _water_meter_states.get(cluster_name, {}) as Dictionary
		var reservoirs_for_cluster := (cluster.get("reservoirs", {}) as Dictionary).keys()
		reservoirs_for_cluster.sort()
		public_clusters.append({
			"name": str(cluster_name),
			"blocks": int(cluster.get("blocks", 0)),
			"power_kw": float(cluster.get("power_kw", 0.0)),
			"water_m3h": float(cluster.get("water_m3h", 0.0)),
			"criticality": int(cluster.get("criticality", 0)),
			"reservoirs": reservoirs_for_cluster,
			"power_meter_kw": float(power_meter.get("reading_kw", 0.0)),
			"water_meter_m3h": float(water_meter.get("reading_m3h", 0.0)),
		})
	return {
		"simulation_hours": _elapsed_seconds * simulation_hours_per_second,
		"power": {"available_kwh": _power_available_kwh, "capacity_kwh": power_capacity_kwh, "percent": _power_available_kwh / power_capacity_kwh * 100.0, "demand_kw": _total_power_demand_kw()},
		"water": {"current_m3": total_water, "capacity_m3": total_capacity, "percent": total_water / total_capacity * 100.0},
		"reservoirs": reservoirs,
		"blocks": public_blocks,
		"service_clusters": public_clusters,
		"power_shed_cluster": _power_shed_cluster,
		"power_shed_block": _power_shed_cluster,
		"water_outage_blocks": _water_outage_blocks.duplicate(),
		"auto_outage_enabled": auto_outage_enabled,
		"manual_water_clusters": _manual_water_clusters.keys(),
		"manual_power_clusters": _manual_power_clusters.keys(),
		"auto_recharge_enabled": auto_recharge_enabled,
		"seconds_until_recharge": maxf(0.0, _next_recharge_seconds - _elapsed_seconds) if auto_recharge_enabled else -1.0,
	}


func _build_city_hud() -> void:
	var hud := _city.get_node_or_null("CameraControls") as CanvasLayer
	if hud == null or hud.has_node("UtilityMeters"):
		return
	var panel := PanelContainer.new()
	panel.name = "UtilityMeters"
	panel.anchor_left = 0.73
	panel.anchor_top = 0.02
	panel.anchor_right = 0.99
	panel.anchor_bottom = 0.46
	panel.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var style := StyleBoxFlat.new()
	style.bg_color = Color(0.025, 0.04, 0.06, 0.80)
	style.border_color = Color(0.18, 0.44, 0.62, 0.95)
	style.set_border_width_all(1)
	style.set_corner_radius_all(4)
	panel.add_theme_stylebox_override("panel", style)
	hud.add_child(panel)
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 3)
	panel.add_child(box)
	var title := Label.new()
	title.text = "LIVE UTILITIES"
	title.add_theme_font_size_override("font_size", 15)
	title.add_theme_color_override("font_color", Color(0.78, 0.9, 1.0))
	box.add_child(title)
	_hud_power_label = _hud_label("POWER — loading")
	box.add_child(_hud_power_label)
	_hud_power_bar = _hud_bar(Color(1.0, 0.69, 0.20))
	box.add_child(_hud_power_bar)
	_hud_outage_label = _hud_label("SERVICE STATUS — all blocks supplied")
	box.add_child(_hud_outage_label)
	var water_title := _hud_label("WATER STORAGE")
	water_title.add_theme_color_override("font_color", Color(0.32, 0.82, 1.0))
	box.add_child(water_title)
	var reservoir_names := _reservoirs.keys()
	reservoir_names.sort()
	for reservoir_name in reservoir_names:
		var label := _hud_label(str(reservoir_name).replace("Reservoir", ""))
		box.add_child(label)
		var bar := _hud_bar(Color(0.18, 0.66, 1.0))
		box.add_child(bar)
		_hud_water_labels[reservoir_name] = label
		_hud_water_bars[reservoir_name] = bar
	_update_city_hud()


func _hud_label(text_value: String) -> Label:
	var label := Label.new()
	label.text = text_value
	label.add_theme_font_size_override("font_size", 11)
	label.add_theme_color_override("font_color", Color(0.88, 0.93, 0.98))
	return label


func _hud_bar(colour: Color) -> ProgressBar:
	var bar := ProgressBar.new()
	bar.show_percentage = false
	bar.custom_minimum_size = Vector2(0, 11)
	bar.max_value = 100.0
	var fill := StyleBoxFlat.new()
	fill.bg_color = colour
	fill.set_corner_radius_all(2)
	bar.add_theme_stylebox_override("fill", fill)
	return bar


func _update_city_hud() -> void:
	if _hud_power_label == null or _hud_power_bar == null:
		return
	var status := get_utility_status()
	var power := status.power as Dictionary
	_hud_power_label.text = "POWER  %.0f%%  ·  %.0f / %.0f kWh  ·  %.0f kW load" % [float(power.percent), float(power.available_kwh), float(power.capacity_kwh), float(power.demand_kw)]
	_hud_power_bar.value = float(power.percent)
	if _hud_outage_label != null:
		var water_outages := status.get("water_outage_blocks", []) as Array
		var power_shed := str(status.get("power_shed_block", ""))
		if not power_shed.is_empty():
			_hud_outage_label.text = "POWER OUTAGE — %s load-shed (RED)" % power_shed
			_hud_outage_label.add_theme_color_override("font_color", Color(1.0, 0.32, 0.28))
		elif not water_outages.is_empty():
			_hud_outage_label.text = "WATER OUTAGE — %d block(s) cut off (BLUE)" % water_outages.size()
			_hud_outage_label.add_theme_color_override("font_color", Color(0.30, 0.72, 1.0))
		else:
			_hud_outage_label.text = "SERVICE STATUS — all blocks supplied"
			_hud_outage_label.add_theme_color_override("font_color", Color(0.55, 1.0, 0.66))
	for reservoir_value in status.reservoirs:
		var reservoir := reservoir_value as Dictionary
		var name := str(reservoir.name)
		var label := _hud_water_labels.get(name) as Label
		var bar := _hud_water_bars.get(name) as ProgressBar
		if label != null:
			label.text = "%s  %.0f%%  ·  %.0f / %.0f m³" % [name.replace("Reservoir", ""), float(reservoir.percent), float(reservoir.current_m3), float(reservoir.capacity_m3)]
		if bar != null:
			bar.value = float(reservoir.percent)
