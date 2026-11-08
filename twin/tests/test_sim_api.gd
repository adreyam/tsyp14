extends SceneTree

## Live device API: every sensor and actuator family is reachable, readings are taken from the running scene at the
## instant of the request, commands change the real systems, citizens throwing waste raise a bin's weight, and the
## same requests work through the real TCP socket the MCP server uses.

const BinWeight := preload("res://scripts/props/bin_weight.gd")

var _control_room: Node
var _api: Node

func _init() -> void:
	call_deferred("_run")

func _fail(message: String) -> void:
	push_error("SIM API TEST FAILED: " + message)
	print("SIM API TEST FAILED: " + message)
	quit(1)

func _ask(method: String, params := {}) -> Dictionary:
	return _api.call("handle_request", {"id": 1, "method": method, "params": params}) as Dictionary

func _run() -> void:
	_control_room = (load("res://scenes/ControlRoom.tscn") as PackedScene).instantiate()
	root.add_child(_control_room)
	for i in 120:
		await process_frame
	_api = _control_room.get("_sim_api")
	if _api == null:
		_fail("control room did not install the SimApi node")
		return
	_api.set("snapshot_ttl_ms", 0)    # the tests change the scene and read it straight back: no shared sample
	var info := _ask("info")
	var families := (info.result as Dictionary).families as Dictionary
	for family in ["traffic", "camera", "air", "noise", "weather", "flood", "utility", "power_meter", "water_meter", "waste", "signal", "water_valve",
			"power_switch", "lighting", "irrigation", "reservoir", "grid", "service_zone", "seismic", "parking"]:
		if int(families.get(family, 0)) <= 0:
			_fail("family '%s' has no devices in the sensor registry" % family)
			return
	var trash_cans := 0
	for value in get_nodes_in_group("urban_props"):
		var prop := value as Node3D
		trash_cans += int(str(prop.get_meta("urban_prop_kind", "")) == "trash" and prop.visible and not prop.is_queued_for_deletion())
	if int(families.waste) != trash_cans + get_nodes_in_group("garbage_bins").size() or trash_cans < 20:
		_fail("weight sensors (%d) do not cover every bin (%d cans + %d containers)" % [int(families.waste), trash_cans, get_nodes_in_group("garbage_bins").size()])
		return
	# Every id is unique and every device answers a read with at least one numeric reading.
	var listing := (_ask("list_devices", {"limit": 500}).result as Dictionary)
	var seen := {}
	var offset := 0
	while offset < int(listing.total):
		listing = (_ask("list_devices", {"limit": 500, "offset": offset}).result as Dictionary)
		for device_value in listing.devices as Array:
			var device := device_value as Dictionary
			if seen.has(device.id):
				_fail("duplicate device id " + str(device.id))
				return
			seen[device.id] = true
		offset += 500
	if seen.size() != int(info.result.device_count):
		_fail("listing (%d) and info (%d) disagree" % [seen.size(), int(info.result.device_count)])
		return
	for sample_id in ["AIR-01", "TRAF-01", "CAM-01", "PWR-01", "WTR-01", "SWT-01", "VLV-01", "IRR-01", "LGT-001", "GRID-01", "WGT-T001", "WGT-C001"]:
		var read := _ask("read", {"id": sample_id})
		if not bool(read.ok) or (read.result.readings as Dictionary).is_empty():
			_fail("cannot read %s: %s" % [sample_id, JSON.stringify(read)])
			return
	if bool(_ask("read", {"id": "NOPE-1"}).ok) or bool(_ask("read", {"id": "AIR-01", "metrics": ["nonsense"]}).ok):
		_fail("unknown device / metric was accepted")
		return
	if bool(_ask("not_a_method").ok):
		_fail("unknown method was accepted")
		return

	# --- bin weights: deposit and emptying change what the sensor reads, immediately.
	var can := _first_bin("can")
	var container := _first_bin("container")
	var can_id := str(can.get_meta("sensor_id"))
	var weight0 := float(_ask("read", {"id": can_id}).result.readings.bin_weight.value)
	BinWeight.deposit(can, 2.4, 1.0)
	var weight1 := float(_ask("read", {"id": can_id}).result.readings.bin_weight.value)
	if absf(weight1 - weight0 - 2.4) > 0.011:
		_fail("a 2.4 kg deposit moved the reading from %.2f to %.2f" % [weight0, weight1])
		return
	var fill := float(_ask("read", {"id": can_id}).result.readings.fill_level.value)
	if absf(fill - weight1 / BinWeight.STREET_CAN_CAPACITY_KG * 100.0) > 0.2:
		_fail("fill level %.1f%% does not match %.2f kg of %.0f kg" % [fill, weight1, BinWeight.STREET_CAN_CAPACITY_KG])
		return
	var emptied := _ask("command", {"id": can_id, "command": "empty"})
	if not bool(emptied.ok) or float(emptied.result.after.bin_weight.value) > 0.21:
		_fail("emptying a bin did not clear it: " + JSON.stringify(emptied))
		return
	if str(container.get_meta("sensor_id")).begins_with("WGT-C") == false or float(container.get_meta("bin_capacity_kg")) != BinWeight.CONTAINER_CAPACITY_KG:
		_fail("refuse containers are not registered as containers")
		return

	# --- actuators change the real systems.
	var switch_off := _ask("command", {"id": "SWT-01", "command": "open"})
	if not bool(switch_off.ok) or float(switch_off.result.after.switch_closed.value) != 0.0:
		_fail("power switch did not open: " + JSON.stringify(switch_off))
		return
	var consumers_off := int(_ask("read", {"id": "SWT-01"}).result.readings.powered.value)
	var switch_on := _ask("command", {"id": "SWT-01", "command": "close"})
	if not bool(switch_on.ok) or float(switch_on.result.after.switch_closed.value) != 100.0:
		_fail("power switch did not close")
		return
	if consumers_off != 0:
		_fail("an opened switch cabinet still reports power")
		return
	var light := _ask("command", {"id": "LGT-001", "command": "on"})
	if not bool(light.ok) or float(light.result.after.light_level.value) < 99.0:
		_fail("forcing a street light on in daylight did not light it: " + JSON.stringify(light))
		return
	_ask("command", {"id": "LGT-001", "command": "auto"})
	var valve := _ask("command", {"id": "VLV-01", "command": "close"})
	if not bool(valve.ok) or float(valve.result.after.valve_open.value) != 0.0:
		_fail("water valve did not close")
		return
	var irrigation := _ask("command", {"id": "IRR-01", "command": "open"})
	if not bool(irrigation.ok) or float(irrigation.result.after.irrigation_flow.value) <= 0.0:
		_fail("irrigation valve did not start flowing")
		return
	var zone_id := ""
	for device_value in (_ask("list_devices", {"family": "service_zone", "limit": 5}).result.devices as Array):
		zone_id = str((device_value as Dictionary).id)
		break
	var cut := _ask("command", {"id": zone_id, "command": "cut_power"})
	if not bool(cut.ok) or float(cut.result.after.power_supplied.value) != 0.0:
		_fail("cutting zone power did not show in the zone's telemetry: " + JSON.stringify(cut))
		return
	var restored := _ask("command", {"id": zone_id, "command": "restore_power"})
	if float(restored.result.after.power_supplied.value) != 100.0:
		_fail("restoring zone power did not show")
		return
	if bool(_ask("command", {"id": "AIR-01", "command": "open"}).ok) or bool(_ask("command", {"id": "SWT-01", "command": "explode"}).ok):
		_fail("a read-only sensor / unknown command was accepted")
		return
	var weather := _ask("command", {"id": "WEATHER", "command": "set", "value": "storm"})
	if not bool(weather.ok) or str(_ask("info").result.weather) != "storm" or float(_ask("read", {"id": "WX-01"}).result.readings.wind_speed.value) < 8.0:
		_fail("storm weather was not applied to the weather stations")
		return
	_ask("command", {"id": "WEATHER", "command": "set", "value": "clear"})
	var clock := _ask("command", {"id": "CLOCK", "command": "set_time", "value": "23:15"})
	if not bool(clock.ok) or str(_ask("info").result.time) != "23:15":
		_fail("clock was not set to 23:15: " + JSON.stringify(clock))
		return
	if not bool(_ask("command", {"id": "SIGNALS", "command": "phase", "value": "all_red"}).ok) or bool(_ask("command", {"id": "SIGNALS", "command": "phase", "value": "purple"}).ok):
		_fail("signal phase command validation is wrong")
		return

	# --- zones, buildings, seismic sensors, earthquakes, parking lots, vehicles, citizens, incidents
	var zones := _ask("list_zones").result as Dictionary
	if int(zones.count) != 16:
		_fail("list_zones returned %d zones" % int(zones.count))
		return
	var shown := _ask("command", {"id": "ZONES", "command": "show", "value": ["B", "D"]})
	if not bool(shown.ok) or shown.result.shown_zones != ["B", "D"]:
		_fail("showing zones B and D: " + JSON.stringify(shown))
		return
	var hidden := _ask("command", {"id": "ZONES", "command": "hide", "value": "ALL"})
	if not bool(hidden.ok) or (hidden.result.shown_zones as Array).size() != 0 or bool(_ask("command", {"id": "ZONES", "command": "show", "value": "Z"}).ok):
		_fail("hiding zones or validating them failed")
		return
	# panel actions: bulk lights and utilities work on their own; camera / highlight / event log need the control room
	var lights := _ask("command", {"id": "LIGHTS", "command": "dim", "value": 0.3})
	if not bool(lights.ok) or int(lights.result.lights) < 1 or bool(_ask("command", {"id": "LIGHTS", "command": "dim", "value": "x"}).ok):
		_fail("LIGHTS dim: " + JSON.stringify(lights))
		return
	if not bool(_ask("command", {"id": "LIGHTS", "command": "auto"}).ok) or not bool(_ask("command", {"id": "UTILITY", "command": "recharge_all"}).ok):
		_fail("LIGHTS auto / UTILITY recharge_all failed")
		return
	for view in ["CAM-03", "street", "overview", "reset"]:
		var moved := _ask("command", {"id": "CAMERA", "command": "view", "value": view})
		if not bool(moved.ok) or not moved.result.ui.has("camera_status"):
			_fail("CAMERA view %s: %s" % [view, JSON.stringify(moved)])
			return
	if bool(_ask("command", {"id": "CAMERA", "command": "view", "value": "CAM-99"}).ok) or bool(_ask("command", {"id": "CAMERA", "command": "follow", "value": "nobody_here"}).ok):
		_fail("CAMERA accepted a bad view or an unknown follow target")
		return
	var framed := _ask("command", {"id": "HIGHLIGHT", "command": "show", "value": "hospitals"})
	if not bool(framed.ok) or framed.result.ui.highlight != "hospitals" or (framed.result.ui.highlight_kinds as Array).size() < 10:
		_fail("HIGHLIGHT show: " + JSON.stringify(framed).substr(0, 300))
		return
	if not bool(_ask("command", {"id": "HIGHLIGHT", "command": "clear"}).ok) or str(_ask("ui_state").result.highlight) != "":
		_fail("HIGHLIGHT clear failed")
		return
	if bool(_ask("command", {"id": "HIGHLIGHT", "command": "show", "value": "nonsense"}).ok):
		_fail("an unknown highlight kind was accepted")
		return
	var network := _ask("command", {"id":"HIGHLIGHT", "command":"show", "value":"water_network"})
	if not bool(network.ok) or network.result.ui.highlight != "water_network":
		_fail("water-network panel highlight failed: " + JSON.stringify(network).substr(0, 300))
		return
	var focused := _ask("command", {"id":"FOCUS", "command":"device", "value":"AIR-01"})
	if not bool(focused.ok) or focused.result.ui.focused_device != "AIR-01" or bool(_ask("command", {"id":"FOCUS", "command":"device", "value":"NOPE"}).ok):
		_fail("physical device focus failed: " + JSON.stringify(focused).substr(0, 300))
		return
	for layout in ["full", "panel"]:
		var changed_layout := _ask("command", {"id":"VIEWPORT", "command":layout})
		if not bool(changed_layout.ok) or bool(changed_layout.result.ui.focus_view) != (layout == "full"):
			_fail("viewport layout %s failed: %s" % [layout, JSON.stringify(changed_layout)])
			return
	var snap := _ask("snapshot")
	if not bool(snap.ok) or int((snap.result.devices as Dictionary).count) < 500 or not snap.result.has("vehicles") or not snap.result.has("events") or snap.result.ui == null:
		_fail("snapshot is thin: " + JSON.stringify(snap).substr(0, 300))
		return
	var log := _ask("event_log", {"limit": 5})
	if not bool(log.ok) or int(log.result.count) < 1 or (log.result.events as Array).size() > 5:
		_fail("event_log: " + JSON.stringify(log).substr(0, 300))
		return
	if bool(_ask("command", {"id": "REPLAY", "command": "play", "value": "INC-none"}).ok):
		_fail("a replay of an unknown incident was accepted")
		return
	var zone_report := _ask("read_zone", {"zone": "F"})
	if not bool(zone_report.ok) or int(zone_report.result.buildings) < 5 or int(zone_report.result.devices) < 10 or not (zone_report.result.sensor_summary as Dictionary).has("air.pm2_5"):
		_fail("read_zone F is thin: " + JSON.stringify(zone_report).substr(0, 300))
		return
	if bool(_ask("read_zone", {"zone": "Z"}).ok):
		_fail("an unknown zone was accepted")
		return
	if int((_ask("list_buildings", {"limit": 1}).result as Dictionary).total) != 224:
		_fail("list_buildings does not list all 224 buildings")
		return
	if int(families.seismic) != 16 or int(families.parking) < 6:
		_fail("seismic %d, parking %d devices" % [int(families.seismic), int(families.parking)])
		return
	var seismic := _ask("read", {"id": "SEIS-06"})
	if not bool(seismic.ok) or not (seismic.result as Dictionary).has("waveform") or str(seismic.result.zone) != "F":
		_fail("a seismic sensor read lacks its waveform or zone: " + JSON.stringify(seismic).substr(0, 200))
		return
	var quake := _ask("command", {"id": "EARTHQUAKE", "command": "trigger", "value": {"zone": "F", "magnitude": 6.2}})
	if not bool(quake.ok):
		_fail("earthquake command failed: " + JSON.stringify(quake))
		return
	await create_timer(0.4).timeout
	if int((_ask("list_earthquakes").result as Dictionary).active) != 1:
		_fail("the triggered earthquake is not listed as active")
		return
	if bool(_ask("command", {"id": "EARTHQUAKE", "command": "trigger", "value": {"zone": "Q"}}).ok):
		_fail("an earthquake in an unknown zone was accepted")
		return
	_ask("command", {"id": "EARTHQUAKE", "command": "clear"})
	if int((_ask("list_earthquakes").result as Dictionary).earthquakes.size()) != 0:
		_fail("clear left earthquakes behind")
		return
	var lots := _ask("list_parking_lots").result as Dictionary
	if int(lots.count) < 6 or int(lots.capacity) < 72:
		_fail("list_parking_lots reports %d lots" % int(lots.count))
		return
	var lot_id := str((lots.lots as Array)[0].id)
	var closed := _ask("command", {"id": lot_id, "command": "closed"})
	if not bool(closed.ok) or str(_ask("read_parking_lot", {"id": lot_id}).result.barrier_mode) != "closed":
		_fail("closing a lot's barrier did not register: " + JSON.stringify(closed))
		return
	_ask("command", {"id": lot_id, "command": "auto"})
	for method in ["list_vehicles", "list_citizens", "list_incidents", "list_emergency_units"]:
		if not bool(_ask(method).ok):
			_fail("%s failed" % method)
			return
	if not (_ask("list_citizens", {"limit": 1}).result as Dictionary).has("citizens") or bool(_ask("read_citizen", {"id": "nobody"}).ok) or bool(_ask("read_vehicle", {"id": "nope"}).ok):
		_fail("citizen / vehicle lookups misbehave")
		return
	var first_citizen := str(((_ask("list_citizens", {"limit": 1}).result as Dictionary).citizens as Array)[0].id)
	if not bool(_ask("read_citizen", {"id": first_citizen}).ok):
		_fail("read_citizen could not read a listed citizen")
		return

	# --- the Sensors tab lists every family, including the bins and the actuators' telemetry.
	await create_timer(0.8).timeout
	var catalog := _control_room.get("_sensor_catalog") as Array
	var tab_families := {}
	for entry in catalog:
		tab_families[SimApiFamily.of(str((entry as Dictionary).id))] = true
	for family in families:
		if not tab_families.has(family):
			_fail("the Sensors tab does not list the '%s' family" % family)
			return
	for family in ["waste", "signal", "power_switch", "lighting"]:
		if _control_room.find_child("SensorCategory_%s" % family, true, false) == null:
			_fail("the Sensors tab has no '%s' category" % family)
			return

	# --- a citizen carrying litter walks to a bin and throws it in: the sensor reads the extra weight.
	var npc := (_control_room.get("_city") as Node).get_node("NpcAi")
	var target_bins: Array[Node3D] = []
	for value in get_nodes_in_group("bin_weight_sensors"):
		if str((value as Node).get_meta("bin_kind")) == "can":
			target_bins.append(value as Node3D)
	var before := {}
	for bin in target_bins:
		before[bin.get_instance_id()] = float(bin.get_meta("bin_weight_kg"))
	var started := 0
	var agents := npc.get("_agents") as Array
	for index in agents.size():
		var agent := agents[index] as Dictionary
		if bool(agent.guaranteed_prop_job) or not str(agent.pair_id).is_empty():
			continue
		if bool(npc.call("_begin_amenity_visit", index, ["trash"], "tidy", 400.0, "can")):
			started += 1
		if started >= 3:
			break
	if started == 0:
		_fail("no citizen could be sent to a bin")
		return
	Engine.time_scale = 6.0
	var waited := 0.0
	var gained := 0.0
	var saw_item_in_hand := false
	while waited < 360.0 and gained < 0.3:
		await create_timer(0.5, true, false, true).timeout
		waited += 3.0
		for agent_value in agents:
			var held := ((agent_value as Dictionary).director as RefCounted).call("held_items") as Array
			saw_item_in_hand = saw_item_in_hand or "can" in held
		gained = 0.0
		for bin in target_bins:
			gained += float(bin.get_meta("bin_weight_kg")) - float(before[bin.get_instance_id()])
	Engine.time_scale = 1.0
	if gained < 0.3:
		_fail("%d citizens were sent to bins but no bin gained weight in %.0f simulated seconds" % [started, waited])
		return
	if not saw_item_in_hand:
		_fail("the weight rose but nobody was seen holding the can they threw")
		return

	# --- the same requests over the real TCP socket (the path the MCP server uses).
	if not bool(_api.call("start_server", 0)):
		_fail("device API server could not listen")
		return
	var client := StreamPeerTCP.new()
	client.connect_to_host("127.0.0.1", int(_api.get("port")))
	for i in 20:
		await process_frame
		client.poll()
	if client.get_status() != StreamPeerTCP.STATUS_CONNECTED:
		_fail("socket client did not connect")
		return
	client.put_data((JSON.stringify({"id": 41, "method": "read", "params": {"id": can_id}}) + "\n").to_utf8_buffer())
	var reply := ""
	for i in 60:
		await process_frame
		client.poll()
		if client.get_available_bytes() > 0:
			reply += client.get_utf8_string(client.get_available_bytes())
		if reply.contains("\n"):
			break
	var parsed: Variant = JSON.parse_string(reply)
	if not parsed is Dictionary or int((parsed as Dictionary).get("id", 0)) != 41 or not bool((parsed as Dictionary).get("ok", false)):
		_fail("socket round trip failed: " + reply)
		return
	client.put_data("this is not json\n".to_utf8_buffer())
	reply = ""
	for i in 60:
		await process_frame
		client.poll()
		if client.get_available_bytes() > 0:
			reply += client.get_utf8_string(client.get_available_bytes())
		if reply.contains("\n"):
			break
	if bool((JSON.parse_string(reply) as Dictionary).get("ok", true)):
		_fail("malformed request was accepted")
		return
	print("SIM API TEST PASSED: %d devices in %d families, bins weigh live (+%.2f kg thrown by citizens), %d actuator classes commanded, socket ok" % [seen.size(), families.size(), gained, 7])
	quit(0)

func _first_bin(kind: String) -> Node3D:
	for value in get_nodes_in_group("bin_weight_sensors"):
		if str((value as Node).get_meta("bin_kind")) == kind:
			return value as Node3D
	return null

class SimApiFamily:
	static func of(sensor_id: String) -> String:
		return (load("res://scripts/sim_api.gd") as GDScript).call("family_of", sensor_id)
