extends SceneTree

## Zones and earthquakes: the 16 zones tile the city, every zone has a seismograph, a quake in one zone shakes that zone's
## sensors far more than distant ones and brings buildings of that zone down (animated, hidden originals), a whole-city quake
## shakes every sensor, and clearing restores every building exactly.

const Zones := preload("res://scripts/city_zones.gd")

func _init() -> void:
	call_deferred("_run")

func _fail(message: String) -> void:
	push_error("EARTHQUAKE TEST FAILED: " + message)
	print("EARTHQUAKE TEST FAILED: " + message)
	quit(1)

func _sensor(zone: String) -> Node3D:
	for value in get_nodes_in_group("seismic_sensors"):
		if str((value as Node).get_meta("zone")) == zone:
			return value as Node3D
	return null

func _wait(seconds: float) -> void:
	Engine.time_scale = 6.0
	await create_timer(seconds / 6.0, true, false, true).timeout
	Engine.time_scale = 1.0

func _run() -> void:
	# --- zones
	if Zones.zone_of(-379.0, -379.0) != "A" or Zones.zone_of(379.0, 379.0) != "P" or Zones.zone_of(0.5, 0.5) != "K" or Zones.zone_of(-100.0, -100.0) != "F":
		_fail("zone lookup is wrong")
		return
	if Zones.neighbours("F").size() != 4 or Zones.neighbours("A").size() != 2 or Zones.centre("A") != Vector3(-285, 0, -285):
		_fail("zone geometry is wrong")
		return
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	root.add_child(city)
	for i in 20:
		await process_frame
	await create_timer(0.5).timeout
	var overlay := city.get_node("ZoneOverlay")
	if (overlay.call("shown_zones") as Array).size() != 0 or overlay.get_child_count() != 32:
		_fail("the zone overlay should start hidden with 16 washes and 16 letters")
		return
	overlay.call("set_zone_visible", "C", true)
	overlay.call("set_zone_visible", "F", true)
	if overlay.call("shown_zones") != ["C", "F"]:
		_fail("showing two zones shows %s" % str(overlay.call("shown_zones")))
		return
	overlay.call("set_all_visible", true)
	if (overlay.call("shown_zones") as Array).size() != 16:
		_fail("show all failed")
		return
	overlay.call("set_all_visible", false)
	if (overlay.call("shown_zones") as Array).size() != 0:
		_fail("hide all failed")
		return
	var buildings: Array = []
	for group_name in ["buildings", "backdrop_buildings"]:
		buildings.append_array(get_nodes_in_group(group_name))
	var per_zone := {}
	for building in buildings:
		var zone := Zones.zone_of_position((building as Node3D).global_position)
		per_zone[zone] = int(per_zone.get(zone, 0)) + 1
	if per_zone.size() != 16 or buildings.size() != 224:
		_fail("%d buildings across %d zones" % [buildings.size(), per_zone.size()])
		return
	# --- seismographs
	if get_nodes_in_group("seismic_sensors").size() != 16:
		_fail("%d seismic sensors" % get_nodes_in_group("seismic_sensors").size())
		return
	for zone in Zones.IDS:
		var sensor := _sensor(str(zone))
		if sensor == null or Zones.zone_of_position(sensor.global_position) != zone:
			_fail("zone %s has no seismograph inside it" % zone)
			return
		if not bool(sensor.get_meta("device_site_spatially_safe", false)):
			_fail("seismograph of zone %s is not on a verified site" % zone)
			return
	var quakes := city.get_node("Earthquakes")
	await _wait(2.0)
	var quiet := float(_sensor("F").get_meta("seis_pga"))
	if quiet > 0.01:
		_fail("a quiet city reads %.4f m/s2" % quiet)
		return
	# --- one zone
	var event := quakes.call("trigger", "F", 6.8) as Dictionary
	if event.has("error") or int(event.buildings_planned) < 5:
		_fail("trigger failed: %s" % JSON.stringify(event))
		return
	await _wait(5.0)
	var near := float(_sensor("F").get_meta("seis_pga"))
	var far := float(_sensor("P").get_meta("seis_pga"))
	if near < 0.3 or far > near * 0.25:
		_fail("shaking near %.3f, far %.3f m/s2" % [near, far])
		return
	# Strength depends on the distance from the shaken zone, not from its middle: the same everywhere inside, then falling off.
	var live := quakes.call("active_events") as Array
	var inside_corner := float(quakes.call("peak_acceleration", live[0], Vector3(-185.0, 0.0, -185.0)))
	var inside_middle := float(quakes.call("peak_acceleration", live[0], Zones.centre("F")))
	var next_door := float(quakes.call("peak_acceleration", live[0], Vector3(-95.0, 0.0, 95.0)))
	var two_away := float(quakes.call("peak_acceleration", live[0], Vector3(-95.0, 0.0, 285.0)))
	var across_city := float(quakes.call("peak_acceleration", live[0], Vector3(285.0, 0.0, 285.0)))
	if absf(inside_corner - inside_middle) > 0.0001 or not (inside_middle > next_door and next_door > two_away and two_away > across_city and across_city > 0.0):
		_fail("attenuation is wrong: inside %.3f/%.3f, next door %.3f, farther %.3f, far %.3f" % [inside_corner, inside_middle, next_door, two_away, across_city])
		return
	if float(quakes.call("distance_to_affected", live[0], Vector3(-95.0, 0.0, 95.0))) < 90.0 or float(quakes.call("distance_to_affected", live[0], Zones.centre("F"))) != 0.0:
		_fail("distance to the affected area is wrong")
		return
	var waveform := _sensor("F").get_meta("seis_waveform") as Dictionary
	if int(waveform.samplingRateHz) != 20 or (waveform.components.vertical_z as Array).size() != 40:
		_fail("waveform is malformed: %s" % JSON.stringify(waveform).substr(0, 200))
		return
	Engine.time_scale = 6.0
	var waited := 0.0
	while waited < 60.0:
		await create_timer(0.5, true, false, true).timeout
		waited += 0.5
		var unfinished := 0
		for child in quakes.get_children():
			unfinished += int(not bool(child.get("finished")))
		if unfinished == 0 and int(quakes.call("collapsed_count")) >= int(event.buildings_planned):
			break
	Engine.time_scale = 1.0
	var report := quakes.call("public_events") as Array
	var collapsed_names: Array = (report[0] as Dictionary).collapsed_buildings
	if collapsed_names.size() != int(event.buildings_planned) or int(quakes.call("collapsed_count")) != collapsed_names.size():
		_fail("planned %d, collapsed %d" % [int(event.buildings_planned), collapsed_names.size()])
		return
	for building in buildings:
		var node := building as Node3D
		if node.has_meta("cleared_for_parking"):
			continue          # demolished for a car park, not by the quake
		var fallen := str(node.name) in collapsed_names
		if fallen and (node.visible or Zones.zone_of_position(node.global_position) != "F"):
			_fail("%s fell but is still shown, or is not in zone F" % node.name)
			return
		if not fallen and not node.visible:
			_fail("%s is hidden though it did not fall" % node.name)
			return
	var ruins := 0
	for child in quakes.get_children():
		ruins += int(child.has_node("Rubble"))
		if not child.has_node("Rubble"):
			print("UNFINISHED ", child.name, " started=", child.get("started"), " clock=", child.get("_clock"), " finished=", child.get("finished"))
	if ruins != collapsed_names.size():
		_fail("%d ruins for %d fallen buildings" % [ruins, collapsed_names.size()])
		return
	# --- clean up
	var cleared := quakes.call("clear_all") as Dictionary
	await create_timer(0.3).timeout
	if int(cleared.restored_buildings) != collapsed_names.size() or int(quakes.call("collapsed_count")) != 0:
		_fail("clean-up restored %d of %d" % [int(cleared.restored_buildings), collapsed_names.size()])
		return
	for building in buildings:
		if (not (building as Node3D).visible and not (building as Node3D).has_meta("cleared_for_parking")) or (building as Node3D).has_meta("collapsed"):
			_fail("%s is not back after the clean-up" % (building as Node3D).name)
			return
	if quakes.get_child_count() != 0 or (quakes.call("public_events") as Array).size() != 0:
		_fail("traces of the earthquake remain")
		return
	# --- whole city
	var citywide := quakes.call("trigger", "ALL", 6.5) as Dictionary
	await _wait(5.0)
	for zone in ["A", "F", "P"]:
		if float(_sensor(zone).get_meta("seis_pga")) < 0.3:
			_fail("a whole-city quake barely registers in zone %s" % zone)
			return
	if int(citywide.buildings_planned) < 20:
		_fail("a whole-city quake plans only %d buildings" % int(citywide.buildings_planned))
		return
	quakes.call("clear_all")
	if not (quakes.call("trigger", "Z", 6.0) as Dictionary).has("error"):
		_fail("an unknown zone was accepted")
		return
	print("EARTHQUAKE TEST PASSED: 16 zones and seismographs, zone quake near %.2f vs far %.2f m/s2, %d buildings fell and were restored, whole-city quake planned %d" % [near, far, collapsed_names.size(), int(citywide.buildings_planned)])
	quit(0)
