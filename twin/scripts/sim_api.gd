extends Node

## Live device interface of the running city: every sensor and actuator, read and commanded in real time.
##
## This node owns no simulation state. Every answer is computed from the live scene at the moment of the request
## (the same nodes, metadata and systems the Sensors tab, the citizens and the SUMO bridge use), and every command
## goes through the same code path the control room's own buttons use. It is reached by the MCP server over a
## local line-delimited JSON socket (CITY_SIM_API_PORT); `handle_request` is the single entry point.

const BinWeight := preload("res://scripts/props/bin_weight.gd")
const Zones := preload("res://scripts/city_zones.gd")

const SIGNAL_STATE_VALUE := {"r": 0.0, "R": 0.0, "y": 1.0, "Y": 1.0, "g": 2.0, "G": 2.0}
const WEATHERS := ["clear", "cloudy", "rain", "fog", "storm", "auto"]
const SIGNAL_PHASES := ["ns_green", "ns_yellow", "all_red", "ew_green", "ew_yellow", "reset_cycle"]
const FAMILY_BY_PREFIX := {
	"TRAF": "traffic", "CAM": "camera", "AIR": "air", "NOISE": "noise", "WX": "weather", "FLOOD": "flood",
	"UTIL": "utility", "PWR": "power_meter", "WTR": "water_meter", "WGT": "waste",
	"SIG": "signal", "VLV": "water_valve", "SWT": "power_switch", "LGT": "lighting", "IRR": "irrigation",
	"RSV": "reservoir", "GRID": "grid", "ZONE": "service_zone", "SEIS": "seismic", "LOT": "parking",
}
const FAMILY_LABELS := {
	"traffic": "Traffic detectors", "camera": "Street cameras", "air": "Air quality", "noise": "Noise", "weather": "Weather",
	"flood": "Flood / level", "utility": "Utility telemetry", "power_meter": "Power meters", "water_meter": "Water meters",
	"waste": "Waste bin weights", "signal": "Traffic signals", "water_valve": "Water valves", "power_switch": "Power switches",
	"lighting": "Street lights", "irrigation": "Irrigation", "reservoir": "Reservoirs", "grid": "Power grid", "service_zone": "Service zones",
	"seismic": "Seismic sensors", "parking": "Parking lots",
}

var city: Node3D
var control_room: Node
var port := 0

var _server := TCPServer.new()
var _clients: Array[Dictionary] = []
## Sensors are sampled once per MCP tick (100 ms) however many clients stream them; any command invalidates the sample.
var snapshot_ttl_ms := 100
var _frame_cache := {"at_msec": -1000000, "sensors": [], "reads": {}}
var _requests_served := 0
var _in_request := false

func setup(city_node: Node3D, control_room_node: Node = null) -> void:
	city = city_node
	control_room = control_room_node

func start_server(listen_port: int) -> bool:
	if _server.is_listening():
		return true
	var error := _server.listen(listen_port, "127.0.0.1")
	if error != OK:
		push_warning("SimApi could not listen on %d (error %d)" % [listen_port, error])
		return false
	port = _server.get_local_port() if listen_port == 0 else listen_port
	return true

func _process(_delta: float) -> void:
	if not _server.is_listening():
		return
	while _server.is_connection_available():
		_clients.append({"peer": _server.take_connection(), "buffer": ""})
	for client in _clients.duplicate():
		var peer := client.peer as StreamPeerTCP
		peer.poll()
		if peer.get_status() != StreamPeerTCP.STATUS_CONNECTED:
			_clients.erase(client)
			continue
		var available := peer.get_available_bytes()
		if available > 0:
			client.buffer = str(client.buffer) + peer.get_utf8_string(available)
		while str(client.buffer).contains("\n"):
			var split := str(client.buffer).split("\n", true, 1)
			client.buffer = split[1]
			if str(split[0]).strip_edges().is_empty():
				continue
			var response := handle_line(str(split[0]))
			peer.put_data((response + "\n").to_utf8_buffer())

func handle_line(line: String) -> String:
	var parsed: Variant = JSON.parse_string(line)
	if not parsed is Dictionary:
		return JSON.stringify({"id": null, "ok": false, "error": {"code": "bad_request", "message": "request must be a JSON object"}})
	return JSON.stringify(handle_request(parsed as Dictionary))

## One request: {"id", "method", "params"} -> {"id", "ok", "result"} or {"id", "ok": false, "error": {code, message}}.
func handle_request(request: Dictionary) -> Dictionary:
	_in_request = true
	var response := _handle(request)
	_in_request = false
	return response

func _handle(request: Dictionary) -> Dictionary:
	_requests_served += 1
	var method := str(request.get("method", ""))
	var params := request.get("params", {}) as Dictionary if request.get("params", {}) is Dictionary else {}
	var result: Variant = null
	match method:
		"ping": result = {"pong": true, "frame": Engine.get_process_frames()}
		"info": result = info()
		"list_devices": result = list_devices(params)
		"read_all": result = read_all(params)
		"read": result = read_device(str(params.get("id", "")), params.get("metrics", []) as Array if params.get("metrics", []) is Array else [])
		"read_many": result = read_many(params.get("ids", []) as Array if params.get("ids", []) is Array else [])
		"summary": result = summary(str(params.get("family", "")))
		"command": result = command(str(params.get("id", "")), str(params.get("command", "")), params.get("value"))
		"list_commands": result = list_commands(str(params.get("id", "")))
		"list_parking_lots": result = list_parking_lots(params)
		"read_parking_lot": result = read_parking_lot(str(params.get("id", "")))
		"list_zones": result = list_zones()
		"read_zone": result = read_zone(str(params.get("zone", "")))
		"list_buildings": result = list_buildings(params)
		"list_earthquakes": result = earthquake_report()
		"list_vehicles": result = list_vehicles(params)
		"read_vehicle": result = read_vehicle(str(params.get("id", "")))
		"list_citizens": result = list_citizens(params)
		"read_citizen": result = read_citizen(str(params.get("id", "")))
		"list_incidents": result = list_incidents()
		"list_emergency_units": result = list_emergency_units(params)
		"snapshot": result = snapshot()
		"event_log": result = event_log(params)
		"ui_state": result = ui_state()
		_:
			return _error(request, "unknown_method", "unknown method '%s'" % method)
	if result is Dictionary and (result as Dictionary).has("error"):
		var failure := (result as Dictionary).error as Dictionary
		return _error(request, str(failure.get("code", "error")), str(failure.get("message", "")))
	return {"id": request.get("id"), "ok": true, "result": result}

func _error(request: Dictionary, code: String, message: String) -> Dictionary:
	return {"id": request.get("id"), "ok": false, "error": {"code": code, "message": message}}

func _fail(code: String, message: String) -> Dictionary:
	return {"error": {"code": code, "message": message}}

# --- catalogue -------------------------------------------------------------------------------------

func _environment() -> Node:
	return city.get_node_or_null("EnvironmentSystem") if city != null else null

## Every sensor-style device: the environment sensors (real nodes in the "sensors" group) plus the telemetry of
## signals, valves, switches, lights, irrigation, reservoirs and the grid.
func sensors() -> Array:
	var now := Time.get_ticks_msec()
	if now - int(_frame_cache.at_msec) < snapshot_ttl_ms:
		return _frame_cache.sensors as Array
	var all: Array = []
	var env := _environment()
	if env != null:
		for sensor in env.call("get_sensor_snapshot") as Array:
			all.append(sensor)
	all.append_array(extra_sensor_snapshot())
	_frame_cache = {"at_msec": now, "sensors": all, "reads": {}}
	return all

static func family_of(sensor_id: String) -> String:
	return str(FAMILY_BY_PREFIX.get(sensor_id.get_slice("-", 0), "other"))

func sensor_by_id(sensor_id: String) -> Dictionary:
	for sensor in sensors():
		if str((sensor as Dictionary).id) == sensor_id:
			return sensor as Dictionary
	return {}

func _device_json(sensor: Dictionary, with_readings := true) -> Dictionary:
	var position := sensor.get("position", Vector3.ZERO) as Vector3
	var sensor_id := str(sensor.id)
	var entry := {
		"id": sensor_id, "kind": "actuator" if _is_actuator(sensor_id) else "sensor", "family": family_of(sensor_id),
		"name": str(sensor.get("name", sensor_id)), "type": str(sensor.get("type", "")),
		"position": [snappedf(position.x, 0.01), snappedf(position.y, 0.01), snappedf(position.z, 0.01)],
		"location": _location(position), "zone": Zones.zone_of_position(position),
		"metrics": (sensor.readings as Dictionary).keys(),
	}
	var node := sensor.get("node") as Node
	entry["commands"] = _commands_for(sensor_id, node).keys()
	if with_readings:
		var readings := {}
		for metric in (sensor.readings as Dictionary).keys():
			readings[metric] = _reading_json((sensor.readings as Dictionary)[metric] as Dictionary)
		entry["readings"] = readings
		if sensor.has("state"):
			entry["state"] = sensor.state
		var source_node := sensor.get("node") as Node
		if source_node != null and source_node.has_meta("seis_waveform"):
			entry["waveform"] = source_node.get_meta("seis_waveform")
			entry["event"] = str(source_node.get_meta("seis_event", ""))
			var distance := float(source_node.get_meta("seis_distance", -1.0))
			entry["distance_to_affected_area_m"] = snappedf(distance, 0.1) if distance >= 0.0 else null
	return entry

func _reading_json(reading: Dictionary) -> Dictionary:
	return {"value": float(reading.get("value", 0.0)), "unit": str(reading.get("unit", ""))}

func _is_actuator(sensor_id: String) -> bool:
	return family_of(sensor_id) in ["signal", "water_valve", "power_switch", "lighting", "irrigation", "service_zone", "parking"]

func info() -> Dictionary:
	var env := _environment()
	var conditions := env.call("get_conditions") as Dictionary if env != null else {}
	var counts := {}
	for sensor in sensors():
		var family := family_of(str((sensor as Dictionary).id))
		counts[family] = int(counts.get(family, 0)) + 1
	return {
		"name": "City Ground Zero", "frame": Engine.get_process_frames(), "device_count": sensors().size(), "families": counts,
		"clock": str(conditions.get("clock_text", "")), "time": str(conditions.get("time_text", "")), "day": int(conditions.get("day", 1)),
		"weather": str(conditions.get("weather", "")), "period": str(conditions.get("period", "")),
		"paused": bool(conditions.get("paused", false)), "speed": float(conditions.get("speed", 1.0)),
		"clock_rate": float(conditions.get("clock_rate", 1.0)), "requests_served": _requests_served,
		"family_labels": FAMILY_LABELS, "traffic": _traffic_summary(), "citizens_shown": _agents().size(), "earthquakes_active": _active_earthquakes(),
	}

func _traffic_summary() -> Dictionary:
	var state := _traffic_state()
	return {"connected": not state.is_empty(), "simulated_vehicles": int(state.get("simulated_vehicle_count", 0)), "stopped_vehicles": int(state.get("stopped_vehicle_count", 0)),
		"turning_vehicles": int(state.get("turning_vehicle_count", 0)), "time_s": float(state.get("time", 0.0))}

func _active_earthquakes() -> int:
	var system := _earthquakes()
	return int(earthquake_report().get("active", 0)) if system != null else 0

func list_devices(params: Dictionary) -> Dictionary:
	var family := str(params.get("family", "")).to_lower()
	var kind := str(params.get("kind", "")).to_lower()
	var query := str(params.get("query", "")).to_lower()
	var limit := clampi(int(params.get("limit", 50)), 1, 500)
	var offset := maxi(0, int(params.get("offset", 0)))
	var matches: Array = []
	for sensor in sensors():
		var entry := sensor as Dictionary
		var sensor_id := str(entry.id)
		if not family.is_empty() and family_of(sensor_id) != family:
			continue
		if not kind.is_empty() and ("actuator" if _is_actuator(sensor_id) else "sensor") != kind:
			continue
		if not query.is_empty() and not ("%s %s %s" % [sensor_id, str(entry.get("name", "")), str(entry.get("type", ""))]).to_lower().contains(query):
			continue
		matches.append(entry)
	var page: Array = []
	for index in range(offset, mini(offset + limit, matches.size())):
		page.append(_device_json(matches[index] as Dictionary, false))
	return {"total": matches.size(), "offset": offset, "count": page.size(), "devices": page}

## Every device's live readings in one answer (optionally one family or zone): what a dashboard needs to stay in step with the city.
func read_all(params: Dictionary) -> Dictionary:
	var family := str(params.get("family", "")).to_lower()
	var zone := str(params.get("zone", "")).to_upper()
	var all_sensors := sensors()
	var cache_key := family + "|" + zone
	if (_frame_cache.reads as Dictionary).has(cache_key):
		return (_frame_cache.reads as Dictionary)[cache_key] as Dictionary
	var rows: Array = []
	for value in all_sensors:
		var sensor := value as Dictionary
		var sensor_id := str(sensor.id)
		var position := sensor.get("position", Vector3.ZERO) as Vector3
		if not family.is_empty() and family_of(sensor_id) != family:
			continue
		if not zone.is_empty() and Zones.zone_of_position(position) != zone:
			continue
		var readings := {}
		for metric in (sensor.readings as Dictionary).keys():
			readings[metric] = _reading_json((sensor.readings as Dictionary)[metric] as Dictionary)
		var row := {"id": sensor_id, "family": family_of(sensor_id), "kind": "actuator" if _is_actuator(sensor_id) else "sensor", "name": str(sensor.get("name", sensor_id)),
			"type": str(sensor.get("type", "")), "zone": Zones.zone_of_position(position), "location": _location(position), "readings": readings}
		if sensor.has("state"):
			row["state"] = sensor.state
		var node := sensor.get("node") as Node
		if node != null and node.has_meta("seis_waveform"):
			row["waveform"] = node.get_meta("seis_waveform")
			var distance := float(node.get_meta("seis_distance", -1.0))
			row["distance_to_affected_area_m"] = snappedf(distance, 0.1) if distance >= 0.0 else null
		rows.append(row)
	var env := _environment()
	var clock := str((env.call("get_conditions") as Dictionary).get("clock_text", "")) if env != null else ""
	var result := {"count": rows.size(), "devices": rows, "sampled_at": {"frame": Engine.get_process_frames(), "unix_msec": int(Time.get_unix_time_from_system() * 1000.0), "clock": clock}}
	(_frame_cache.reads as Dictionary)[cache_key] = result
	return result

func read_device(sensor_id: String, metrics: Array = []) -> Dictionary:
	var sensor := sensor_by_id(sensor_id)
	if sensor.is_empty():
		return _fail("unknown_device", "no device with id '%s' (use list_devices to see ids)" % sensor_id)
	var entry := _device_json(sensor)
	if not metrics.is_empty():
		var wanted := {}
		for metric in metrics:
			if not (entry.readings as Dictionary).has(str(metric)):
				return _fail("unknown_metric", "%s has no metric '%s' (available: %s)" % [sensor_id, str(metric), ", ".join(PackedStringArray((entry.metrics as Array).map(func(m): return str(m))))])
			wanted[str(metric)] = (entry.readings as Dictionary)[str(metric)]
		entry["readings"] = wanted
	var env := _environment()
	entry["sampled_at"] = {"frame": Engine.get_process_frames(), "unix_msec": int(Time.get_unix_time_from_system() * 1000.0), "clock": str((env.call("get_conditions") as Dictionary).get("clock_text", "")) if env != null else ""}
	return entry

func read_many(ids: Array) -> Dictionary:
	var out: Array = []
	var missing: Array = []
	for value in ids:
		var result := read_device(str(value))
		if result.has("error"):
			missing.append(str(value))
		else:
			out.append(result)
	return {"devices": out, "unknown_ids": missing}

func summary(family: String) -> Dictionary:
	var rows := {}
	for sensor in sensors():
		var entry := sensor as Dictionary
		var fam := family_of(str(entry.id))
		if not family.is_empty() and fam != family.to_lower():
			continue
		var bucket := rows.get(fam, {}) as Dictionary
		for metric in (entry.readings as Dictionary).keys():
			var reading := (entry.readings as Dictionary)[metric] as Dictionary
			var stats := bucket.get(metric, {"count": 0, "min": INF, "max": -INF, "sum": 0.0, "unit": str(reading.get("unit", ""))}) as Dictionary
			var value := float(reading.get("value", 0.0))
			stats.count = int(stats.count) + 1
			stats.min = minf(float(stats.min), value)
			stats.max = maxf(float(stats.max), value)
			stats.sum = float(stats.sum) + value
			bucket[metric] = stats
		rows[fam] = bucket
	var out := {}
	for fam in rows:
		var metrics := {}
		for metric in (rows[fam] as Dictionary):
			var stats := (rows[fam] as Dictionary)[metric] as Dictionary
			metrics[metric] = {"devices": int(stats.count), "min": snappedf(float(stats.min), 0.01), "max": snappedf(float(stats.max), 0.01), "mean": snappedf(float(stats.sum) / maxf(1.0, float(stats.count)), 0.01), "unit": str(stats.unit)}
		out[fam] = metrics
	return {"families": out}

# --- extra telemetry ---------------------------------------------------------------------------------

func extra_sensor_snapshot() -> Array:
	var out: Array = []
	if city == null:
		return out
	var tree := city.get_tree()
	var life := city.get_node_or_null("CityLife")
	for value in tree.get_nodes_in_group("traffic_signals"):
		var node := value as Node3D
		if node == null:
			continue
		var signal_name: String = str(life.call("_sumo_signal_id_for", node)) if life != null else node.name
		var states: Dictionary = (life.call("signal_states") as Dictionary).get(signal_name, {}) if life != null and life.has_method("signal_states") else {}
		var ns_state := str(states.get("north", "r"))
		var ew_state := str(states.get("east", "r"))
		out.append(_entry("SIG-%s" % signal_name, node.name, "Traffic signal", node, {
			"ns_signal": [float(SIGNAL_STATE_VALUE.get(ns_state, 0.0)), "state"],
			"ew_signal": [float(SIGNAL_STATE_VALUE.get(ew_state, 0.0)), "state"],
		}, {"legend": "0 red · 1 amber · 2 green", "north": str(states.get("north", "r")), "south": str(states.get("south", "r")), "east": str(states.get("east", "r")), "west": str(states.get("west", "r"))}))
	var valve_number := 0
	for value in tree.get_nodes_in_group("water_valves"):
		var valve := value as Node3D
		valve_number += 1
		var open := bool(valve.get_meta("valve_open", true))
		out.append(_entry("VLV-%02d" % valve_number, valve.name, "Water valve", valve, {"valve_open": [100.0 if open else 0.0, "%"]}, {"state": "open" if open else "closed"}))
	var switch_number := 0
	var grid := city.get_node_or_null("PowerGrid")
	for value in tree.get_nodes_in_group("power_switches"):
		var power_switch := value as Node3D
		switch_number += 1
		var closed := bool(power_switch.get_meta("switch_closed", true))
		out.append(_entry("SWT-%02d" % switch_number, power_switch.name, "Power switch", power_switch, {
			"switch_closed": [100.0 if closed else 0.0, "%"], "powered": [100.0 if bool(power_switch.get_meta("power_online", false)) else 0.0, "%"],
		}, {"state": "closed" if closed else "open"}))
	var light_number := 0
	for value in tree.get_nodes_in_group("smart_street_lights"):
		var light := value as Node3D
		light_number += 1
		out.append(_entry("LGT-%03d" % light_number, light.name, "Smart street light", light, {
			"light_level": [float(light.get_meta("light_level", 0.0)) * 100.0, "%"],
		}, {"mode": str(light.get_meta("light_mode", "auto")), "state": str(light.get_meta("actuator_state", ""))}))
	var irrigation_number := 0
	for value in tree.get_nodes_in_group("irrigation_valves"):
		var irrigation := value as Node3D
		irrigation_number += 1
		var flowing := bool(irrigation.get_meta("valve_open", false))
		out.append(_entry("IRR-%02d" % irrigation_number, irrigation.name, "Irrigation valve", irrigation, {
			"valve_open": [100.0 if flowing else 0.0, "%"], "irrigation_flow": [1.8 if flowing else 0.0, "m³/h"],
		}, {"state": "open" if flowing else "closed"}))
	var utilities := city.get_node_or_null("UtilityResources")
	if utilities != null and utilities.has_method("get_utility_status"):
		var status := utilities.call("get_utility_status") as Dictionary
		for reservoir_value in status.get("reservoirs", []) as Array:
			var reservoir := reservoir_value as Dictionary
			var site := _reservoir_node(tree, str(reservoir.name))
			out.append(_entry("RSV-%s" % str(reservoir.name).replace("Reservoir", "").replace("_", ""), str(reservoir.name), "Reservoir", site, {
				"water_level": [float(reservoir.percent), "%"], "water_volume": [float(reservoir.current_m3), "m³"], "water_demand": [float(reservoir.demand_m3h), "m³/h"],
			}, {}))
		var power := status.get("power", {}) as Dictionary
		out.append(_entry("GRID-01", "Power grid", "Grid storage", grid as Node3D, {
			"energy_level": [float(power.get("percent", 0.0)), "%"], "energy_available": [float(power.get("available_kwh", 0.0)), "kWh"], "grid_demand": [float(power.get("demand_kw", 0.0)), "kW"],
		}, {}))
		var power_cuts := status.get("manual_power_clusters", []) as Array
		var water_cuts := status.get("manual_water_clusters", []) as Array
		for cluster_value in status.get("service_clusters", []) as Array:
			var cluster := cluster_value as Dictionary
			var cluster_name := str(cluster.get("name", ""))
			var power_ok := not power_cuts.has(cluster_name) and str(status.get("power_shed_cluster", "")) != cluster_name
			out.append(_entry("ZONE-%s" % cluster_name, cluster_name, "Service zone", null, {
				"power_supplied": [100.0 if power_ok else 0.0, "%"], "water_supplied": [0.0 if water_cuts.has(cluster_name) else 100.0, "%"],
				"zone_power": [float(cluster.get("power_meter_kw", 0.0)), "kW"], "zone_water": [float(cluster.get("water_meter_m3h", 0.0)), "m³/h"],
			}, {"power_cut": power_cuts.has(cluster_name), "water_cut": water_cuts.has(cluster_name)}))
	var presenter := _parking()
	if presenter != null:
		for report_value in parking_lots():
			var report := report_value as Dictionary
			var lot_node := (presenter.get("lots") as Dictionary).get(str(report.id), {}).get("node") as Node3D
			var capacity := maxi(int(report.capacity), 1)
			out.append(_entry(str(report.id), "Parking %s" % str(report.id), "Smart car park", lot_node, {
				"occupancy": [float(report.occupied), "cars"], "capacity": [float(report.capacity), "cars"],
				"occupancy_percent": [float(report.occupied) / float(capacity) * 100.0, "%"], "denied_entries": [float(report.denied_entries), "cars"],
				"entered": [float(report.entered_total), "cars"], "left": [float(report.left_total), "cars"],
			}, {"barrier_mode": report.barrier_mode, "gate_open": report.gate_open, "warning": report.warning, "incoming": report.incoming, "redirected": report.redirected_total, "edge": report.edge}))
	out.sort_custom(func(a: Dictionary, b: Dictionary) -> bool: return str(a.id) < str(b.id))
	return out

func _reservoir_node(tree: SceneTree, reservoir_name: String) -> Node3D:
	for value in tree.get_nodes_in_group("water_reservoir_sites"):
		if (value as Node).name == reservoir_name or str((value as Node).name).contains(reservoir_name):
			return value as Node3D
	return null

func _entry(sensor_id: String, display_name: String, type_text: String, node: Node3D, metrics: Dictionary, state: Dictionary) -> Dictionary:
	var readings := {}
	for metric in metrics:
		var pair := metrics[metric] as Array
		readings[metric] = {"value": snappedf(float(pair[0]), 0.1), "unit": str(pair[1])}
	return {"id": sensor_id, "name": display_name, "type": type_text, "position": node.global_position if node != null else Vector3.ZERO, "node": node, "sample_sequence": Engine.get_process_frames(), "readings": readings, "state": state}

# --- zones, buildings, earthquakes ---------------------------------------------------------------

static func _location(position: Vector3) -> Dictionary:
	return {"x": snappedf(position.x, 0.01), "y": snappedf(position.y, 0.01), "z": snappedf(position.z, 0.01)}

func _earthquakes() -> Node:
	return city.get_node_or_null("Earthquakes") if city != null else null

func _buildings_all() -> Array:
	var out: Array = []
	if city == null:
		return out
	var tree := city.get_tree()
	for group_name in ["buildings", "backdrop_buildings"]:
		for value in tree.get_nodes_in_group(group_name):
			if value is Node3D:
				out.append(value)
	return out

func list_zones() -> Dictionary:
	var rows: Array = []
	for zone in Zones.IDS:
		rows.append(_zone_summary(str(zone)))
	var overlay := city.get_node_or_null("ZoneOverlay") if city != null else null
	return {"zones": rows, "count": rows.size(), "shown_zones": overlay.call("shown_zones") if overlay != null else [], "layout": "4 x 4 grid, A (north-west) to P (south-east), 2 x 2 blocks each"}

func _zone_summary(zone: String) -> Dictionary:
	var families := {}
	var device_count := 0
	for sensor in sensors():
		var entry := sensor as Dictionary
		if Zones.zone_of_position(entry.get("position", Vector3.ZERO) as Vector3) != zone:
			continue
		if entry.get("node") == null and family_of(str(entry.id)) in ["service_zone", "grid", "reservoir"]:
			continue
		var family := family_of(str(entry.id))
		families[family] = int(families.get(family, 0)) + 1
		device_count += 1
	var buildings := 0
	var collapsed := 0
	for value in _buildings_all():
		var building := value as Node3D
		if Zones.zone_of_position(building.global_position) != zone:
			continue
		buildings += 1
		collapsed += int(building.has_meta("collapsed"))
	var centre := Zones.centre(zone)
	return {"zone": zone, "name": Zones.label(zone), "centre": _location(centre), "bounds": {"x": Zones.rect(zone).position.x, "z": Zones.rect(zone).position.y, "size": Zones.ZONE_SIZE},
		"neighbours": Zones.neighbours(zone), "devices": device_count, "devices_by_family": families, "buildings": buildings, "collapsed_buildings": collapsed}

func read_zone(zone: String) -> Dictionary:
	if not Zones.is_zone(zone):
		return _fail("unknown_zone", "zone must be one of %s" % ", ".join(PackedStringArray(Zones.IDS)))
	var target := zone.to_upper()
	var report := _zone_summary(target)
	var metrics := {}
	for sensor in sensors():
		var entry := sensor as Dictionary
		if Zones.zone_of_position(entry.get("position", Vector3.ZERO) as Vector3) != target or entry.get("node") == null:
			continue
		var family := family_of(str(entry.id))
		if family in ["lighting", "signal", "waste"]:
			continue
		for metric in (entry.readings as Dictionary).keys():
			var key := "%s.%s" % [family, metric]
			var reading := (entry.readings as Dictionary)[metric] as Dictionary
			var bucket := metrics.get(key, {"devices": 0, "min": INF, "max": -INF, "sum": 0.0, "unit": str(reading.get("unit", ""))}) as Dictionary
			var value := float(reading.get("value", 0.0))
			bucket.devices = int(bucket.devices) + 1
			bucket.min = minf(float(bucket.min), value)
			bucket.max = maxf(float(bucket.max), value)
			bucket.sum = float(bucket.sum) + value
			metrics[key] = bucket
	var summary := {}
	for key in metrics:
		var bucket := metrics[key] as Dictionary
		summary[key] = {"devices": bucket.devices, "min": snappedf(float(bucket.min), 0.001), "max": snappedf(float(bucket.max), 0.001), "mean": snappedf(float(bucket.sum) / maxf(1.0, float(bucket.devices)), 0.001), "unit": bucket.unit}
	report["sensor_summary"] = summary
	var bins := 0
	var bin_kg := 0.0
	for value in city.get_tree().get_nodes_in_group("bin_weight_sensors"):
		if Zones.zone_of_position((value as Node3D).global_position) == target:
			bins += 1
			bin_kg += BinWeight.weight_of(value as Node)
	report["waste"] = {"bins": bins, "total_weight_kg": snappedf(bin_kg, 0.01)}
	var quakes: Array = []
	var system := _earthquakes()
	if system != null:
		for event_value in system.call("public_events") as Array:
			var event := event_value as Dictionary
			if str(event.zone) in [target, "ALL"]:
				quakes.append(event)
	report["earthquakes"] = quakes
	var lots: Array = []
	for lot in parking_lots():
		if str((lot as Dictionary).get("zone", "")) == target:
			lots.append(lot)
	report["parking_lots"] = lots
	return report

func list_buildings(params: Dictionary) -> Dictionary:
	var zone := str(params.get("zone", "")).to_upper()
	var only_collapsed := bool(params.get("collapsed_only", false))
	var limit := clampi(int(params.get("limit", 50)), 1, 500)
	var offset := maxi(0, int(params.get("offset", 0)))
	var rows: Array = []
	for value in _buildings_all():
		var building := value as Node3D
		var building_zone := Zones.zone_of_position(building.global_position)
		if not zone.is_empty() and building_zone != zone:
			continue
		var is_collapsed := building.has_meta("collapsed")
		if only_collapsed and not is_collapsed:
			continue
		rows.append({"id": str(building.name), "zone": building_zone, "location": _location(building.global_position),
			"state": "collapsed" if is_collapsed else ("collapsing" if building.has_meta("collapse_pending") else "standing"),
			"district": "core" if building.is_in_group("buildings") else "outer"})
	rows.sort_custom(func(a: Dictionary, b: Dictionary) -> bool: return str(a.id) < str(b.id))
	var page := rows.slice(offset, offset + limit)
	return {"total": rows.size(), "offset": offset, "count": page.size(), "buildings": page}

func earthquake_report() -> Dictionary:
	var system := _earthquakes()
	if system == null:
		return _fail("unavailable", "the earthquake system is not available")
	var events := system.call("public_events") as Array
	var active := 0
	for event in events:
		active += int(str((event as Dictionary).status) == "active")
	return {"earthquakes": events, "active": active, "collapsed_buildings": int(system.call("collapsed_count"))}

# --- vehicles, citizens, incidents, emergency units -------------------------------------------------

func _traffic_state() -> Dictionary:
	var life := city.get_node_or_null("CityLife") if city != null else null
	return (life.get("_last_state") as Dictionary) if life != null and life.get("_last_state") is Dictionary else {}

func _vehicle_json(entry: Dictionary) -> Dictionary:
	var position := Vector3(float(entry.get("x", 0.0)), 0.0, float(entry.get("z", 0.0)))
	return {
		"id": str(entry.get("id", "")), "class": str(entry.get("cls", "")), "type": str(entry.get("type", "")),
		"location": _location(position), "zone": Zones.zone_of_position(position), "speed_mps": snappedf(float(entry.get("speed", 0.0)), 0.01),
		"heading_deg": snappedf(float(entry.get("angle", 0.0)), 0.1), "road": str(entry.get("road", "")),
		"stopped": bool(entry.get("stopped", false)), "stop_reason": str(entry.get("stop_reason", "")),
		"unit": str(entry.get("unit", "")), "incident": str(entry.get("incident", "")),
	}

func list_vehicles(params: Dictionary) -> Dictionary:
	var wanted_class := str(params.get("class", "")).to_lower()
	var zone := str(params.get("zone", "")).to_upper()
	var limit := clampi(int(params.get("limit", 50)), 1, 500)
	var offset := maxi(0, int(params.get("offset", 0)))
	var rows: Array = []
	var state := _traffic_state()
	for value in state.get("vehicles", []) as Array:
		var row := _vehicle_json(value as Dictionary)
		if not wanted_class.is_empty() and str(row["class"]) != wanted_class:
			continue
		if not zone.is_empty() and str(row.zone) != zone:
			continue
		rows.append(row)
	rows.sort_custom(func(a: Dictionary, b: Dictionary) -> bool: return str(a.id) < str(b.id))
	var page := rows.slice(offset, offset + limit)
	return {"total": rows.size(), "offset": offset, "count": page.size(), "vehicles": page, "simulated": int(state.get("simulated_vehicle_count", 0)), "time_s": float(state.get("time", 0.0))}

func read_vehicle(vehicle_id: String) -> Dictionary:
	for value in _traffic_state().get("vehicles", []) as Array:
		var entry := value as Dictionary
		if str(entry.get("id", "")) == vehicle_id or str(entry.get("unit", "")) == vehicle_id:
			var row := _vehicle_json(entry)
			row["length_m"] = float(entry.get("len", 0.0))
			row["waiting_s"] = float(entry.get("waiting", 0.0))
			return row
	return _fail("unknown_vehicle", "no vehicle '%s' is on the road right now (use list_vehicles)" % vehicle_id)

func _agents() -> Array:
	var npc := city.get_node_or_null("NpcAi") if city != null else null
	return npc.call("get_agent_debug") as Array if npc != null and npc.has_method("get_agent_debug") else []

func _citizen_json(state: Dictionary) -> Dictionary:
	var node := state.get("node") as Node3D
	var position := node.global_position if node != null else Vector3.ZERO
	return {
		"id": str(state.get("citizen_id", "")), "name": str(state.get("display_name", "")), "role": str(state.get("role", "")),
		"household_id": str(state.get("household_id", "")), "action": str(state.get("action", "")), "action_target": str(state.get("action_target", "")),
		"motion_state": str(state.get("motion_state", "")), "speed_mps": snappedf(float(state.get("speed_mps", 0.0)), 0.01),
		"station_id": str(state.get("station_id", "")), "decision_reason": str(state.get("decision_reason", "")),
		"completed_actions": int(state.get("completed_actions", 0)), "location": _location(position), "zone": Zones.zone_of_position(position), "embodied": true,
	}

func list_citizens(params: Dictionary) -> Dictionary:
	var zone := str(params.get("zone", "")).to_upper()
	var role := str(params.get("role", "")).to_lower()
	var limit := clampi(int(params.get("limit", 50)), 1, 500)
	var offset := maxi(0, int(params.get("offset", 0)))
	var rows: Array = []
	for value in _agents():
		var row := _citizen_json(value as Dictionary)
		if not zone.is_empty() and str(row.zone) != zone:
			continue
		if not role.is_empty() and not str(row.role).to_lower().contains(role):
			continue
		rows.append(row)
	var page := rows.slice(offset, offset + limit)
	var npc := city.get_node_or_null("NpcAi")
	var society := npc.call("get_society_debug") as Dictionary if npc != null and npc.has_method("get_society_debug") else {}
	return {"total_visible": rows.size(), "offset": offset, "count": page.size(), "citizens": page, "population": int(society.get("citizens", society.get("population", 0)))}

func read_citizen(citizen_id: String) -> Dictionary:
	for value in _agents():
		var state := value as Dictionary
		if str(state.get("citizen_id", "")) == citizen_id or str(state.get("npc_id", "")) == citizen_id:
			var row := _citizen_json(state)
			row["society"] = _society_view(citizen_id)
			return row
	var record := _society_view(citizen_id)
	if record.is_empty():
		return _fail("unknown_citizen", "no citizen '%s' (use list_citizens for the ones currently shown)" % citizen_id)
	return {"id": citizen_id, "embodied": false, "society": record}

func _society_view(citizen_id: String) -> Dictionary:
	var npc := city.get_node_or_null("NpcAi") if city != null else null
	if npc == null:
		return {}
	var society = npc.get("_society")
	if society == null or not society.has_method("get_citizen_public_state"):
		return {}
	return _plain(society.call("get_citizen_public_state", citizen_id)) as Dictionary

## Strips engine types from a state dictionary so it serialises as plain JSON.
func _plain(value: Variant) -> Variant:
	if value is Dictionary:
		var out := {}
		for key in value:
			out[str(key)] = _plain(value[key])
		return out
	if value is Array:
		var list: Array = []
		for item in value:
			list.append(_plain(item))
		return list
	if value is Vector3:
		return _location(value)
	if value is Node:
		return str((value as Node).name)
	if value is StringName:
		return str(value)
	return value

func _building_for_fire(zone: String) -> Dictionary:
	## A building with road access for a fire triggered through this API, picked as the control room's incident
	## panel picks one (its _building_address); with a zone letter, a building of that zone. A building's zone is
	## the zone of its footprint centre (address x, z): many building nodes sit at their block's origin, so their
	## node position alone put every building outside the zone asked for ("no building with road access found").
	## When the zone has no suitable building, the suitable building nearest the zone is used.
	if control_room == null or city == null:
		return {}
	var panel: Object = control_room.get("_incident_panel")
	if panel == null or not panel.has_method("_building_address"):
		return {}
	var candidates: Array = []
	var nearest := {}
	var nearest_distance := INF
	var target := Zones.centre(zone) if Zones.is_zone(zone) else Vector3.ZERO
	for value in city.get_tree().get_nodes_in_group("buildings"):
		var building := value as Node3D
		if building == null or not building.is_visible_in_tree() or building.is_in_group("hospitals") or building.is_in_group("police_facilities"):
			continue
		if building.has_meta("collapsed") or bool(building.get_meta("on_fire", false)):
			continue
		var address := panel.call("_building_address", building) as Dictionary
		if address.is_empty() or float(address.get("distance", 99.0)) >= 22.0:
			continue
		var x := float(address.get("x", building.global_position.x))
		var z := float(address.get("z", building.global_position.z))
		if zone.is_empty() or Zones.zone_of(x, z) == zone:
			candidates.append(address)
		elif candidates.is_empty():
			var distance := Vector2(x - target.x, z - target.z).length()
			if distance < nearest_distance:
				nearest_distance = distance
				nearest = address
	if candidates.is_empty():
		return nearest
	return candidates[randi() % candidates.size()] as Dictionary


func _waste_fill(spec: Dictionary) -> Dictionary:
	## Fills bins for a scenario: the listed ones (WGT-... sensor ids or BIN-.. container ids), or the containers
	## (or street cans) of a zone, to the given levels (one level each, the last one repeated).
	var levels: Array = (spec.get("levels") as Array) if spec.get("levels") is Array else [spec.get("level", 95.0)]
	if levels.is_empty():
		levels = [95.0]
	var nodes: Array = []
	var manager := _incident_manager()
	var containers: Node = manager.get("garbage_bins") if manager != null else null
	if spec.get("bins") is Array:
		for item in spec.get("bins") as Array:
			var ident := str(item)
			var node: Node3D = null
			if ident.begins_with("BIN-") and containers != null:
				node = containers.call("bin_node", ident) as Node3D
			else:
				var sensor := sensor_by_id(ident)
				node = sensor.get("node") as Node3D if not sensor.is_empty() else null
			if node != null and node.has_meta("bin_weight_kg"):
				nodes.append(node)
	else:
		var zone := str(spec.get("zone", "")).to_upper()
		if not Zones.is_zone(zone):
			return _fail("bad_value", "WASTE fill needs {zone: A..P, levels} or {bins: [ids], levels}")
		var want_containers := bool(spec.get("containers", true))
		for value in city.get_tree().get_nodes_in_group("bin_weight_sensors"):
			var bin := value as Node3D
			if bin == null or Zones.zone_of_position(bin.global_position) != zone:
				continue
			if (str(bin.get_meta("bin_kind", "can")) == "container") != want_containers:
				continue
			nodes.append(bin)
		nodes.sort_custom(func(a: Node3D, b: Node3D) -> bool: return str(a.get_meta("sensor_id", "")) < str(b.get_meta("sensor_id", "")))
		# Containers: every container of the zone (the truck round empties them all); street cans: one per level.
		var count := int(spec.get("count", nodes.size() if want_containers else levels.size()))
		nodes = nodes.slice(0, maxi(0, count))
	if nodes.is_empty():
		return _fail("unavailable", "no bin with a weight sensor matched")
	var filled: Array = []
	for index in nodes.size():
		var level := float(levels[mini(index, levels.size() - 1)])
		var node := nodes[index] as Node3D
		filled.append({"id": str(node.get_meta("sensor_id", node.name)), "fill_level": BinWeight.fill_to(node, level / 100.0)})
	_frame_cache.at_msec = -1000000
	return {"id": "WASTE", "applied": "%d bin(s) filled" % filled.size(), "bins": filled}


func _air_release_zone(spec: Dictionary) -> Dictionary:
	var zone := str(spec.get("zone", "")).to_upper()
	if not Zones.is_zone(zone) or not (spec.get("ug") is float or spec.get("ug") is int):
		return _fail("bad_value", "AIR release needs {zone: A..P, ug, seconds?}")
	var monitors: Array = []
	var nearest: Dictionary = {}
	var nearest_distance := INF
	var centre := Zones.centre(zone)
	for value in sensors():
		var sensor := value as Dictionary
		if family_of(str(sensor.id)) != "air" or sensor.get("node") == null:
			continue
		var position := sensor.get("position", Vector3.ZERO) as Vector3
		if Zones.zone_of_position(position) == zone:
			monitors.append(sensor)
		var distance := Vector2(position.x - centre.x, position.z - centre.z).length()
		if distance < nearest_distance:
			nearest_distance = distance
			nearest = sensor
	if monitors.is_empty() and not nearest.is_empty():
		monitors.append(nearest)
	if monitors.is_empty():
		return _fail("unavailable", "no air monitor in the city")
	var ids: Array = []
	for sensor in monitors:
		var node := (sensor as Dictionary).node as Node3D
		node.set_meta("air_release_ug", clampf(float(spec.ug), 0.0, 200.0))
		node.set_meta("air_release_seconds", clampf(float(spec.get("seconds", 90.0)), 10.0, 900.0))
		node.set_meta("air_release_start_msec", Time.get_ticks_msec())
		ids.append(str((sensor as Dictionary).id))
	_frame_cache.at_msec = -1000000
	return {"id": "AIR", "applied": "release of +%.0f µg/m³ PM2.5 on %s" % [float(spec.ug), ", ".join(PackedStringArray(ids))], "monitors": ids}


func _incident_manager() -> Node:
	return city.get_node_or_null("Incidents") if city != null else null

func list_incidents() -> Dictionary:
	var manager := _incident_manager()
	if manager == null:
		return _fail("unavailable", "the incident system is not available")
	var state := manager.get("last_state") as Dictionary
	return {"incidents": _plain(state.get("incidents", [])), "resolved_recently": _plain(state.get("resolved_incidents", [])),
		"closures": _plain(state.get("closures", [])), "time_s": float(state.get("time", 0.0))}

func list_emergency_units(params: Dictionary) -> Dictionary:
	var manager := _incident_manager()
	if manager == null:
		return _fail("unavailable", "the incident system is not available")
	var kind := str(params.get("kind", "")).to_lower()
	var status := str(params.get("status", "")).to_lower()
	var vehicles := {}
	for value in _traffic_state().get("vehicles", []) as Array:
		var entry := value as Dictionary
		if not str(entry.get("unit", "")).is_empty():
			vehicles[str(entry.unit)] = entry
	var rows: Array = []
	for unit_value in (manager.get("last_state") as Dictionary).get("fleet", []) as Array:
		var unit := unit_value as Dictionary
		if not kind.is_empty() and str(unit.get("kind", "")).to_lower() != kind:
			continue
		if not status.is_empty() and str(unit.get("status", "")).to_lower() != status:
			continue
		var row := _plain(unit) as Dictionary
		var live := vehicles.get(str(unit.get("unit", "")), {}) as Dictionary
		if not live.is_empty():
			var position := Vector3(float(live.get("x", 0.0)), 0.0, float(live.get("z", 0.0)))
			row["location"] = _location(position)
			row["zone"] = Zones.zone_of_position(position)
			row["speed_mps"] = snappedf(float(live.get("speed", 0.0)), 0.01)
		rows.append(row)
	return {"count": rows.size(), "units": rows}

func _parking() -> Node:
	return city.get_node_or_null("ParkingLots") if city != null else null

func parking_lots() -> Array:
	var presenter := _parking()
	return presenter.call("all_reports") as Array if presenter != null and presenter.has_method("all_reports") else []

func list_parking_lots(params: Dictionary) -> Dictionary:
	var zone := str(params.get("zone", "")).to_upper()
	var rows: Array = []
	for lot in parking_lots():
		if zone.is_empty() or str((lot as Dictionary).zone) == zone:
			var row := (lot as Dictionary).duplicate(true)
			row.erase("cars")
			rows.append(row)
	var parked := 0
	var capacity := 0
	for row in rows:
		parked += int((row as Dictionary).occupied)
		capacity += int((row as Dictionary).capacity)
	return {"count": rows.size(), "lots": rows, "parked": parked, "capacity": capacity}

func read_parking_lot(lot_id: String) -> Dictionary:
	var presenter := _parking()
	var report := presenter.call("lot_report", lot_id) as Dictionary if presenter != null else {}
	if report.is_empty():
		return _fail("unknown_device", "no parking lot '%s' (use list_parking_lots)" % lot_id)
	return report

# --- actuators ---------------------------------------------------------------------------------------

## Everything a live view needs, in one answer (so a dashboard costs the city one request per tick, not ten).
func snapshot() -> Dictionary:
	var out := {"status": info(), "devices": read_all({}), "vehicles": list_vehicles({"limit": 500}), "citizens": list_citizens({"limit": 200}),
		"incidents": list_incidents(), "units": list_emergency_units({}), "parking": list_parking_lots({}), "earthquakes": earthquake_report()}
	if control_room != null and control_room.has_method("api_event_log"):
		out["events"] = event_log({"limit": 60})
		out["ui"] = ui_state()
	for key in out:
		if (out[key] as Dictionary).has("error"):
			out[key] = null
	return out

func event_log(params: Dictionary) -> Dictionary:
	if control_room == null or not control_room.has_method("api_event_log"):
		return _fail("unavailable", "the control room event log is not attached")
	var rows := control_room.call("api_event_log", int(params.get("limit", 60))) as Array
	return {"count": rows.size(), "events": rows}

func ui_state() -> Dictionary:
	if control_room == null or not control_room.has_method("api_ui_state"):
		return _fail("unavailable", "the control room is not attached")
	return control_room.call("api_ui_state") as Dictionary

func list_commands(sensor_id: String) -> Dictionary:
	if sensor_id.is_empty():
		var global := {}
		for key in _global_commands():
			global[key] = _global_commands()[key]
		var by_family := {}
		for sensor in sensors():
			var entry := sensor as Dictionary
			var family := family_of(str(entry.id))
			if not by_family.has(family):
				for key in _commands_for(str(entry.id), entry.get("node") as Node):
					by_family[family] = by_family.get(family, {})
					(by_family[family] as Dictionary)[key] = (_commands_for(str(entry.id), entry.get("node") as Node))[key]
		return {"global": global, "by_family": by_family}
	var sensor := sensor_by_id(sensor_id)
	if sensor.is_empty() and not _global_commands().has(sensor_id):
		return _fail("unknown_device", "no device with id '%s'" % sensor_id)
	if _global_commands().has(sensor_id):
		return {"id": sensor_id, "commands": _global_commands()[sensor_id]}
	return {"id": sensor_id, "commands": _commands_for(sensor_id, sensor.get("node") as Node)}

func _global_commands() -> Dictionary:
	return {
		"WEATHER": {"set": "value: " + ", ".join(PackedStringArray(WEATHERS))},
		"CLOCK": {"set_time": "value: minute of day 0-1439 or 'HH:MM'", "set_speed": "value: simulation speed 1-8", "set_rate": "value: clock seconds per simulated second",
			"pause": "pause SUMO and the city", "resume": "resume SUMO and the city"},
		"SIGNALS": {"phase": "value: " + ", ".join(PackedStringArray(SIGNAL_PHASES))},
		"UTILITY": {"auto_recharge_on": "turn automatic energy recharge on", "auto_recharge_off": "turn automatic energy recharge off", "recharge_all": "refill every utility reserve now", "set_reserve": "value: 0-100 - set the grid storage to that percentage"},
		"CAMERA": {"view": "value: CAM-01..CAM-16, overview, street, reset or city", "follow": "value: person or car (random one), or a citizen/vehicle id"},
		"HIGHLIGHT": {"show": "value: an asset kind (see ui_state.highlight_kinds) - frame those assets in the city", "clear": "remove the highlight"},
		"FOCUS": {"device": "value: a device id - select it and frame its physical model in the viewport"},
		"VIEWPORT": {"full": "make the simulation canvas fill the Godot window", "panel": "restore the control-room panels"},
		"LIGHTS": {"on": "force every street light on", "off": "force every street light off", "auto": "return every street light to the adaptive schedule", "dim": "value: 0.0-1.0 output scale for every street light"},
		"REPLAY": {"play": "value: incident id", "stop": "stop", "pause":"pause playback", "resume":"resume playback", "restart":"seek to the beginning", "seek":"value: seconds", "speed":"value: 0.25-4", "camera":"value: orbit, chase, driver, street or overhead"},
		"ZONES": {"show": "value: a zone letter, a list of letters or \"ALL\" - show the zone tint and letter on the ground", "hide": "value: the same - hide them (all are hidden by default)",
			"lockdown": "value: {zone: A..P, reason: earthquake|flood|...} - close the zone: its four entrances get red lights, barriers and police cars, traffic goes round it", "reopen": "value: {zone} (or {reason}) - lift the lockdown"},
		"EARTHQUAKE": {"trigger": "value: {\"zone\": \"A\"..\"P\" or \"ALL\", \"magnitude\": 4.0-8.0}", "clear": "remove every earthquake and restore every collapsed building"},
		"AIR": {"release": "value: {zone: A..P, ug: extra PM2.5 in µg/m³, seconds?: 90}: a pollution release on the zone's air monitors (the nearest monitor when the zone has none) that rises, holds and fades (scenarios)"},
		"WASTE": {"fill": "value: {zone: A..P, levels: [96, 88, ...], containers?: true (the green refuse containers, default) or false (street cans)}: the bins of the zone nearest its first container fill to those levels (scenarios); or {bins: [ids], levels}"},
		"INCIDENTS": {"trigger": "value: incident kind (rear_end, red_light_tbone, unsafe_overtake, breakdown, vehicle_fire, lost_load, building_fire, water_main_break, signal_failure, garbage_collection), or {kind, junction?, edge?, pos?, vehicle?, bin?, zone? (building_fire, garbage_collection), near?: {x, z} (a place in Godot metres), jam?: N (breakdown: N nearby cars queue behind it)}",
			"resolve_all": "fast-forward every open incident", "resolve":"value: incident id", "watch":"value: incident id", "dispatch":"value: {id, kind: police|ambulance|fire|tow}",
			"send_unit": "value: {kind: police|ambulance|fire|tow, edge, pos, tag} - send a unit to a stop in a lane without an incident", "recall_unit": "value: unit id - release a unit sent with send_unit"},
	}

func _commands_for(sensor_id: String, _node: Node) -> Dictionary:
	match family_of(sensor_id):
		"power_switch": return {"open": "open the switch (isolate its feeder)", "close": "close the switch (energise its feeder)"}
		"water_valve", "irrigation": return {"open": "open the valve", "close": "close the valve"}
		"lighting": return {"on": "force the luminaire on", "off": "force the luminaire off", "auto": "return to the adaptive night schedule", "dim": "value: 0.0-1.0 output scale"}
		"service_zone": return {"cut_power": "cut the zone's power", "restore_power": "restore the zone's power", "cut_water": "cut the zone's water", "restore_water": "restore the zone's water"}
		"waste": return {"empty": "send a crew to empty the bin (weight returns to the liner residue)", "fill": "value: fill level in % (0-105): the bin fills up to it (scenarios)"}
		"air": return {"release": "value: extra PM2.5 in µg/m³, or {ug, seconds}: a pollution release around the monitor that rises, holds and fades (scenarios)"}
		"signal": return {"ns_green": "north-south green, east-west red", "ns_yellow": "north-south amber", "all_red": "all-red clearance", "ew_green": "east-west green, north-south red", "ew_yellow": "east-west amber", "reset_cycle": "restart the normal cycle"}
		"parking": return {"auto": "barrier opens for arriving cars while there is room", "open": "admit every arriving car while there is room", "closed": "keep the barrier shut: arriving cars are turned away"}
	return {}

func command(target: String, action: String, value: Variant = null) -> Dictionary:
	if target.is_empty() or action.is_empty():
		return _fail("bad_request", "command needs an 'id' and a 'command'")
	var env := _environment()
	_frame_cache.at_msec = -1000000
	var before := read_device(target) if not _global_commands().has(target) else {}
	match target:
		"WEATHER":
			if action != "set" or not str(value) in WEATHERS:
				return _fail("bad_value", "WEATHER set needs one of: %s" % ", ".join(PackedStringArray(WEATHERS)))
			if control_room != null and control_room.has_method("_set_weather"):
				control_room.call("_set_weather", str(value))
			elif env != null:
				if str(value) == "auto": env.call("set_automatic_weather", true)
				else: env.call("set_weather", str(value))
			_frame_cache.at_msec = -1000000
			return {"id": target, "applied": "weather -> %s" % str(value), "after": info()}
		"CLOCK":
			return _clock_command(env, action, value)
		"SIGNALS":
			if action != "phase" or not str(value) in SIGNAL_PHASES:
				return _fail("bad_value", "SIGNALS phase needs one of: %s" % ", ".join(PackedStringArray(SIGNAL_PHASES)))
			_send_sumo(str(value))
			return {"id": target, "applied": "all signals -> %s (sent to the SUMO controller)" % str(value)}
		"ZONES":
			if action in ["lockdown", "reopen"]:
				# Close a zone (earthquake, flood): its four entrances blocked by red lights, barriers and police cars,
				# the traffic sent round it (road_blocks.py in the bridge); reopen lifts it.
				var lock_spec := value as Dictionary if value is Dictionary else {"zone": str(value) if value != null else ""}
				var lock_zone := str(lock_spec.get("zone", "")).to_upper()
				if action == "lockdown" and not Zones.is_zone(lock_zone):
					return _fail("bad_value", "lockdown needs {zone: A..P, reason}")
				var lock_manager := _incident_manager()
				if lock_manager == null or lock_manager.get("roadblocks") == null:
					return _fail("unavailable", "the incident manager is not running (no SUMO bridge)")
				var lock_request := str(lock_manager.roadblocks.call("lockdown" if action == "lockdown" else "reopen", lock_zone, str(lock_spec.get("reason", "emergency" if action == "lockdown" else ""))))
				return {"id": target, "applied": "zone %s %s" % [lock_zone if not lock_zone.is_empty() else "(all)", "closing: entrances blocked" if action == "lockdown" else "reopening"], "request": lock_request}
			var overlay := city.get_node_or_null("ZoneOverlay")
			if overlay == null or not action in ["show", "hide"]:
				return _fail("bad_command", "ZONES accepts show, hide, lockdown and reopen")
			var wanted: Array = []
			if value is Array:
				for item in value:
					wanted.append(str(item).to_upper())
			elif value != null:
				wanted.append(str(value).to_upper())
			for item in wanted:
				if item != "ALL" and not Zones.is_zone(item):
					return _fail("bad_value", "zones must be letters A to P or ALL")
			for item in wanted:
				if item == "ALL":
					overlay.call("set_all_visible", action == "show")
				else:
					overlay.call("set_zone_visible", item, action == "show")
			if control_room != null and control_room.has_method("_sync_zone_buttons"):
				control_room.call("_sync_zone_buttons")
			return {"id": target, "applied": "zones %s %s" % [", ".join(PackedStringArray(wanted)), "shown" if action == "show" else "hidden"], "shown_zones": overlay.call("shown_zones")}
		"EARTHQUAKE":
			var system := _earthquakes()
			if system == null:
				return _fail("unavailable", "the earthquake system is not available")
			if action == "clear":
				return {"id": target, "applied": "earthquake damage cleaned up", "result": system.call("clear_all")}
			if action == "trigger":
				var spec := value as Dictionary if value is Dictionary else {}
				var result := system.call("trigger", str(spec.get("zone", "ALL")), float(spec.get("magnitude", 6.0))) as Dictionary
				if result.has("error"):
					return result
				return {"id": target, "applied": "earthquake %s triggered" % result.id, "earthquake": result}
			return _fail("bad_command", "EARTHQUAKE accepts trigger and clear")
		"INCIDENTS":
			var manager := _incident_manager()
			if manager == null:
				return _fail("unavailable", "the incident system is not available")
			if action == "resolve_all":
				manager.link.call("send", {"action": "resolve_all"})
				return {"id": target, "applied": "resolve all incidents requested"}
			if action == "resolve":
				manager.link.call("send", {"action":"resolve", "incident":str(value)})
				return {"id":target, "applied":"resolve %s requested" % str(value)}
			if action == "dispatch":
				var dispatch := value as Dictionary if value is Dictionary else {}
				var unit_kind := str(dispatch.get("kind", ""))
				if str(dispatch.get("id", "")).is_empty() or not unit_kind in ["police", "ambulance", "fire", "tow"]:
					return _fail("bad_value", "dispatch needs {id, kind: police|ambulance|fire|tow}")
				manager.link.call("send", {"action":"dispatch_extra", "incident":str(dispatch.id), "kind":unit_kind})
				return {"id":target, "applied":"extra %s dispatched to %s" % [unit_kind, str(dispatch.id)]}
			if action == "watch":
				if control_room == null or not control_room.has_method("api_watch_incident"):
					return _fail("unavailable", "the control room is not attached")
				var watched := control_room.call("api_watch_incident", str(value)) as Dictionary
				if watched.has("error"): return watched
				return {"id":target, "applied":"viewport watching %s" % str(value), "ui":watched}
			if action == "trigger":
				var spec := value as Dictionary if value is Dictionary else {}
				var kind := str(value) if value is String else str(spec.get("kind", ""))
				if kind.is_empty():
					return _fail("bad_value", "INCIDENTS trigger needs an incident kind")
				# The place, when given (the incident types already accept it): a junction for collisions and signal
				# failures, an edge and lane position for a water-main break, a vehicle for breakdowns, a bin for a
				# garbage round. A building fire gets a building with road access, in the zone when one is given.
				var params := {"source": "device_api"}
				# near / x, z: where a scenario wants it (Godot metres); jam: cars routed into a breakdown's queue
				for key in ["junction", "edge", "pos", "vehicle", "bin", "near", "x", "z", "jam"]:
					if spec.has(key):
						params[key] = spec[key]
				if kind == "building_fire":
					var zone_letter := str(spec.get("zone", "")).to_upper()
					var address := _building_for_fire(zone_letter)
					if address.is_empty():
						return _fail("unavailable", "no building with road access found" + ((" in zone " + zone_letter) if not zone_letter.is_empty() else "") + " (the control room must be attached)")
					params["building"] = address
				if kind == "garbage_collection" and Zones.is_zone(str(spec.get("zone", ""))):
					params["zone"] = str(spec.zone).to_upper()   # the round stays in that zone
				var request := str(manager.link.call("send", {"action": "trigger", "type": kind, "params": params}))
				# The viewport takes the new incident as soon as the bridge opens it, exactly as for the control
				# room's own buttons (a fire is framed, a garbage round is followed truck-first).
				var panel: Object = control_room.get("_incident_panel") if control_room != null else null
				if panel != null and panel.has_method("track_request"):
					panel.call("track_request", request, kind)
				return {"id": target, "applied": "incident %s requested" % kind, "request": request}
			if action == "send_unit":
				# A unit to a stop in a lane, without an incident (e.g. police directing traffic away from a flood).
				var order := value as Dictionary if value is Dictionary else {}
				var send_kind := str(order.get("kind", "police"))
				if str(order.get("edge", "")).is_empty() or not send_kind in ["police", "ambulance", "fire", "tow"]:
					return _fail("bad_value", "send_unit needs {kind: police|ambulance|fire|tow, edge, pos, tag}")
				var send_request := str(manager.link.call("send", {"action": "send_unit", "kind": send_kind, "edge": str(order.get("edge", "")), "pos": float(order.get("pos", 30.0)), "incident": str(order.get("tag", "manual"))}))
				return {"id": target, "applied": "%s sent to %s" % [send_kind, str(order.get("edge", ""))], "request": send_request}
			if action == "recall_unit":
				if str(value).is_empty():
					return _fail("bad_value", "recall_unit needs a unit id")
				var recall_request := str(manager.link.call("send", {"action": "recall_unit", "unit": str(value)}))
				return {"id": target, "applied": "recall %s requested" % str(value), "request": recall_request}
			return _fail("bad_command", "INCIDENTS accepts trigger, resolve_all, resolve, dispatch, send_unit, recall_unit and watch")
		"UTILITY":
			var utilities := city.get_node_or_null("UtilityResources")
			if utilities == null or not action in ["auto_recharge_on", "auto_recharge_off", "recharge_all", "set_reserve"]:
				return _fail("bad_command", "UTILITY supports auto_recharge_on / auto_recharge_off / recharge_all / set_reserve")
			if action == "set_reserve":
				# Energy scenarios: the grid storage set to a percentage (the automatic recharge waits a full interval).
				var reserve := float(value) if (value is float or value is int) else (float(str(value)) if str(value).is_valid_float() else -1.0)
				if reserve < 0.0 or reserve > 100.0:
					return _fail("bad_value", "set_reserve needs a percentage 0-100")
				utilities.call("set_power_reserve_percent", reserve)
				return {"id": target, "applied": "grid reserve set to %.0f %%" % reserve}
			if action == "recharge_all":
				if control_room != null and control_room.has_method("api_recharge"):
					control_room.call("api_recharge")
				else:
					utilities.call("recharge_all")
			else:
				utilities.call("set_auto_recharge_enabled", action == "auto_recharge_on")
			return {"id": target, "applied": action}
		"AIR":
			if action != "release":
				return _fail("bad_command", "AIR accepts release")
			return _air_release_zone(value as Dictionary if value is Dictionary else {})
		"WASTE":
			if action != "fill":
				return _fail("bad_command", "WASTE accepts fill")
			return _waste_fill(value as Dictionary if value is Dictionary else {})
		"CAMERA", "HIGHLIGHT":
			if control_room == null or not control_room.has_method("api_camera"):
				return _fail("unavailable", "the control room is not attached")
			var shown: Dictionary
			if target == "CAMERA" and action == "view":
				shown = control_room.call("api_camera", str(value)) as Dictionary
			elif target == "CAMERA" and action == "follow":
				var who := str(value)
				shown = control_room.call("api_follow", who if who in ["person", "car"] else "", "" if who in ["person", "car"] else who) as Dictionary
			elif target == "HIGHLIGHT" and action in ["show", "clear"]:
				shown = control_room.call("api_highlight", str(value) if action == "show" else "clear") as Dictionary
			else:
				return _fail("bad_command", "%s does not accept '%s'" % [target, action])
			if shown.has("error"):
				return shown
			return {"id": target, "applied": "%s %s" % [action, str(value) if value != null else ""], "ui": shown}
		"FOCUS":
			if action != "device" or control_room == null or not control_room.has_method("api_focus_device"):
				return _fail("bad_command", "FOCUS accepts device when the control room is attached")
			var focused := control_room.call("api_focus_device", str(value)) as Dictionary
			if focused.has("error"): return focused
			return {"id":target, "applied":"focused device %s" % str(value), "ui":focused}
		"VIEWPORT":
			if not action in ["full", "panel"] or control_room == null or not control_room.has_method("api_set_view_only"):
				return _fail("bad_command", "VIEWPORT accepts full and panel when the control room is attached")
			var layout := control_room.call("api_set_view_only", action == "full") as Dictionary
			return {"id":target, "applied":"viewport layout %s" % action, "ui":layout}
		"LIGHTS":
			if not action in ["on", "off", "auto", "dim"]:
				return _fail("bad_command", "LIGHTS accepts on, off, auto and dim")
			if action == "dim" and not (value is float or value is int):
				return _fail("bad_value", "dim needs a number from 0.0 to 1.0")
			var changed := 0
			for sensor_entry in sensors():
				var entry := sensor_entry as Dictionary
				if family_of(str(entry.id)) != "lighting":
					continue
				var light := entry.get("node") as Node3D
				if light == null:
					continue
				if action == "dim":
					light.set_meta("light_dim", clampf(float(value), 0.0, 1.0))
				else:
					light.set_meta("light_mode", action)
				changed += 1
			if env != null:
				env.call("_apply_environment")
			return {"id": target, "applied": "%d street lights -> %s" % [changed, action], "lights": changed}
		"REPLAY":
			var replay_manager := _incident_manager()
			if replay_manager == null or replay_manager.get("replay") == null:
				return _fail("unavailable", "the incident replay is not available")
			if action == "stop":
				replay_manager.replay.call("stop")
				return {"id": target, "applied": "replay stopped"}
			if action == "play":
				var camera := city.get_viewport().get_camera_3d()
				if not bool(replay_manager.replay.call("play", str(value), camera)):
					return _fail("no_clip", "no recorded collision for '%s'" % str(value))
				return {"id": target, "applied": "replaying %s" % str(value)}
			if action == "pause" or action == "resume":
				replay_manager.replay.set("paused", action == "pause")
			elif action == "restart":
				replay_manager.replay.call("seek", 0.0)
				replay_manager.replay.set("paused", false)
			elif action == "seek" and (value is float or value is int):
				replay_manager.replay.call("seek", maxf(0.0, float(value)))
			elif action == "speed" and (value is float or value is int) and float(value) >= 0.25 and float(value) <= 4.0:
				replay_manager.replay.set("playback_speed", float(value))
			elif action == "camera" and str(value) in ["orbit", "chase", "driver", "street", "overhead"]:
				replay_manager.replay.set("camera_mode", str(value))
			else:
				return _fail("bad_command", "REPLAY accepts play, stop, pause, resume, restart, seek, speed and camera")
			return {"id":target, "applied":"replay %s %s" % [action, str(value) if value != null else ""]}
	var sensor := sensor_by_id(target)
	if sensor.is_empty():
		return _fail("unknown_device", "no device with id '%s' (use list_devices to see ids)" % target)
	var commands := _commands_for(target, sensor.get("node") as Node)
	if not commands.has(action):
		return _fail("bad_command", "%s does not accept '%s' (accepts: %s)" % [target, action, ", ".join(PackedStringArray(commands.keys())) if not commands.is_empty() else "no commands: it is a read-only sensor"])
	var node := sensor.get("node") as Node3D
	var applied := ""
	match family_of(target):
		"power_switch":
			var grid := city.get_node_or_null("PowerGrid")
			if grid == null or node == null:
				return _fail("unavailable", "power grid is not available")
			grid.call("set_switch_enabled", node.name, action == "close")
			node.set_meta("switch_closed", action == "close")
			applied = "%s %s" % [node.name, "closed" if action == "close" else "opened"]
		"water_valve", "irrigation":
			node.set_meta("valve_open", action == "open")
			_tint_indicator(node, action == "open")
			applied = "%s %s" % [node.name, "opened" if action == "open" else "closed"]
		"lighting":
			if action == "dim":
				if not (value is float or value is int):
					return _fail("bad_value", "dim needs a number from 0.0 to 1.0")
				node.set_meta("light_dim", clampf(float(value), 0.0, 1.0))
				applied = "%s output scale %.2f" % [node.name, clampf(float(value), 0.0, 1.0)]
			else:
				node.set_meta("light_mode", action)
				applied = "%s mode %s" % [node.name, action]
			if env != null:
				env.call("_apply_environment")
		"service_zone":
			var utilities := city.get_node_or_null("UtilityResources")
			if utilities == null:
				return _fail("unavailable", "utility system is not available")
			var cluster := target.trim_prefix("ZONE-")
			var kind := "power" if action.ends_with("power") else "water"
			utilities.call("set_manual_%s_cluster" % kind, cluster, action.begins_with("cut"))
			applied = "%s %s for %s" % [kind, "cut" if action.begins_with("cut") else "restored", cluster]
		"waste":
			if action == "fill":
				if not (value is float or value is int):
					return _fail("bad_value", "fill needs a fill level in % (0-105)")
				applied = "%s filled to %.0f %%" % [target, BinWeight.fill_to(node, float(value) / 100.0)]
			else:
				var removed := BinWeight.empty(node)
				applied = "%s emptied (%.2f kg removed)" % [target, removed]
		"air":
			var release := value as Dictionary if value is Dictionary else {"ug": value}
			if not (release.get("ug") is float or release.get("ug") is int) or float(release.get("ug")) < 0.0:
				return _fail("bad_value", "release needs extra PM2.5 in µg/m³ (or {ug, seconds})")
			node.set_meta("air_release_ug", minf(float(release.get("ug")), 200.0))
			node.set_meta("air_release_seconds", clampf(float(release.get("seconds", 90.0)), 10.0, 900.0))
			node.set_meta("air_release_start_msec", Time.get_ticks_msec())
			applied = "%s: release of +%.0f µg/m³ PM2.5 for %.0f s" % [target, float(node.get_meta("air_release_ug")), float(node.get_meta("air_release_seconds"))]
		"signal":
			if control_room == null or not control_room.has_method("_send_bridge"):
				return _fail("unavailable", "the traffic bridge is not connected")
			control_room.call("_send_bridge", {"command": action, "signal": target.trim_prefix("SIG-")})
			applied = "%s set to %s (sent to the SUMO controller)" % [target, action]
		"parking":
			if control_room == null or not control_room.has_method("_send_bridge"):
				return _fail("unavailable", "the traffic bridge is not connected")
			control_room.call("_send_bridge", {"command": "parking", "lot": target, "mode": action})
			var lots_node := _parking()
			if lots_node != null:
				lots_node.call("note_mode", target, action)
			applied = "%s barrier set to %s" % [target, action]
	_frame_cache.at_msec = -1000000
	return {"id": target, "applied": applied, "before": (before.get("readings", {}) as Dictionary), "after": (read_device(target).get("readings", {}) as Dictionary)}

func _clock_command(env: Node, action: String, value: Variant) -> Dictionary:
	if env == null:
		return _fail("unavailable", "environment system is not available")
	match action:
		"set_time":
			var minute := -1
			if value is String and (value as String).contains(":"):
				var parts := (value as String).split(":")
				if parts.size() == 2 and parts[0].is_valid_int() and parts[1].is_valid_int():
					minute = int(parts[0]) * 60 + int(parts[1])
			elif value is float or value is int:
				minute = int(value)
			if minute < 0 or minute > 1439:
				return _fail("bad_value", "set_time needs a minute of day 0-1439 or 'HH:MM'")
			if control_room != null and control_room.has_method("_set_time_of_day"):
				control_room.call("_set_time_of_day", minute)
			else:
				env.call("set_time_of_day", minute)
		"set_speed":
			if not (value is float or value is int) or float(value) < 1.0 or float(value) > 8.0:
				return _fail("bad_value", "set_speed needs a number from 1 to 8")
			if control_room != null and control_room.has_method("_set_simulation_speed"):
				control_room.call("_set_simulation_speed", float(value))
			else:
				env.call("set_simulation_speed", float(value))
		"set_rate":
			if not (value is float or value is int) or float(value) <= 0.0:
				return _fail("bad_value", "set_rate needs a positive number")
			if control_room != null and control_room.has_method("_set_clock_rate"):
				control_room.call("_set_clock_rate", float(value))
			else:
				env.call("set_clock_rate", float(value))
		"pause", "resume":
			_send_sumo(action)
		_:
			return _fail("bad_command", "CLOCK accepts set_time, set_speed, set_rate, pause, resume")
	_frame_cache.at_msec = -1000000
	return {"id": "CLOCK", "applied": "%s %s" % [action, str(value) if value != null else ""], "after": info()}

func _send_sumo(command_name: String) -> void:
	if control_room != null and control_room.has_method("_send_sumo_command"):
		control_room.call("_send_sumo_command", command_name)
		return
	var env := _environment()
	if env != null:
		env.call("_send_command", {"command": command_name})

func _tint_indicator(valve: Node3D, open: bool) -> void:
	var indicator := valve.get_node_or_null("Indicator") as MeshInstance3D
	if indicator == null or not indicator.material_override is StandardMaterial3D:
		return
	var material := (indicator.material_override as StandardMaterial3D).duplicate() as StandardMaterial3D
	var colour := Color(0.1, 0.95, 0.35) if open else Color(1.0, 0.18, 0.12)
	material.albedo_color = colour
	material.emission_enabled = true
	material.emission = colour
	indicator.material_override = material
