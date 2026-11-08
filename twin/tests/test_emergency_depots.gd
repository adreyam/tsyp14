extends SceneTree

## Re-validates sumo/emergency_depots.json against the live scene: every bay,
## pull-out and turn-in sweep must clear all static obstacles, and every unit
## must be parked in its bay at start-up with its exact class geometry.

const WorldObstacleIndex := preload("res://scripts/incidents/world_obstacle_index.gd")
const DepotPaths := preload("res://scripts/incidents/depot_paths.gd")
const RoadGeometry := preload("res://scripts/incidents/road_geometry.gd")


func _initialize() -> void:
	call_deferred("_test")


func _fail(message: String) -> void:
	printerr("EMERGENCY DEPOT TEST FAILED: " + message)
	quit(1)


func _test() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	root.add_child(city)
	for _frame in 8:
		await process_frame
	var index := WorldObstacleIndex.new()
	index.build(self, city.get_node("Buildings").find_children("Tree_*", "Node3D", true, false))
	var layout := JSON.parse_string(FileAccess.get_file_as_string("res://sumo/emergency_depots.json")) as Dictionary
	var life := city.get_node("CityLife")
	var bays := 0
	for depot_value in layout.get("depots", []):
		var depot := depot_value as Dictionary
		var edge := RoadGeometry.parse_edge(str(depot.edge))
		for bay_value in depot.get("bays", []):
			var bay := bay_value as Dictionary
			bays += 1
			var blockers := index.oriented_box_blockers(Vector3(float(bay.x), 0.0, float(bay.z)), float(bay.yaw), float(bay.length), float(bay.width), 0.2)
			if not blockers.is_empty():
				_fail("%s bay for %s overlaps %s" % [depot.id, bay.unit, blockers]); return
			for curve in [DepotPaths.pull_out_curve(edge, bay), DepotPaths.turn_in_curve(edge, bay)]:
				var swept := DepotPaths.sweep_blockers(index, curve, float(bay.length), float(bay.width), 0.25)
				if not swept.is_empty():
					_fail("%s %s driveway sweep hits %s" % [depot.id, bay.unit, swept]); return
			if float(bay.exit_pos) > RoadGeometry.LANE_LENGTH - 1.0 or float(bay.arrive_pos) < 1.0:
				_fail("%s %s merges outside its lane" % [depot.id, bay.unit]); return
			var node := life.call("get_keyed_vehicle", str(bay.unit)) as Node3D
			if node == null or not node.visible:
				_fail("%s is not parked in its bay at start-up" % bay.unit); return
			var pose := life.call("get_keyed_pose", str(bay.unit)) as Dictionary
			if (pose.centre as Vector3).distance_to(Vector3(float(bay.x), (pose.centre as Vector3).y, float(bay.z))) > 0.05:
				_fail("%s is not centred in its bay" % bay.unit); return
	if bays < 9:
		_fail("expected at least 9 garaged unit bays, found %d" % bays); return
	print("EMERGENCY_DEPOT_TEST_OK depots=%d bays=%d swept_paths=%d obstacles=%d" % [(layout.depots as Array).size(), bays, bays * 2, index.entry_count()])
	quit(0)
