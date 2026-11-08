extends SceneTree

const CitySpatialSafety = preload("res://scripts/city_spatial_safety.gd")


func _initialize() -> void:
	call_deferred("_test")


func _test() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	root.add_child(city)
	await process_frame
	var obstacle_nodes: Array[Node3D] = []
	for value in get_nodes_in_group("buildings") + get_nodes_in_group("city_trees"):
		if value is Node3D:
			obstacle_nodes.append(value as Node3D)
	for value in city.get_node("Buildings").find_children("Tree_*", "Node3D", true, false):
		obstacle_nodes.append(value as Node3D)
	var conflicts: Array[String] = []
	var free_camera := city.get_node("EstablishingCamera") as Camera3D
	for camera_value in get_nodes_in_group("traffic_cameras"):
		var device := camera_value as Node3D
		if not bool(device.get_meta("camera_site_spatially_safe", false)):
			conflicts.append("%s did not resolve a safe placement" % device.name)
		var device_bounds := CitySpatialSafety.world_bounds(device)
		for obstacle in obstacle_nodes:
			if not obstacle.visible:
				continue
			var obstacle_bounds := CitySpatialSafety.world_bounds(obstacle)
			if device_bounds.intersection(obstacle_bounds).get_volume() > 0.015:
				conflicts.append("%s intersects %s" % [device.name, obstacle.name])
		var feed := device.get_node("FeedCamera") as Camera3D
		feed.current = true
		free_camera.call("_exit_street_camera_view", feed)
		var centre := device_bounds.get_center()
		var distance := free_camera.global_position.distance_to(centre)
		if not free_camera.is_current() or distance < 10.0 or distance > 30.0:
			conflicts.append("%s Escape release did not produce a bounded exterior overview" % device.name)
		elif int(free_camera.call("_street_exit_occluder_count", free_camera.global_position, centre, device)) > 0:
			conflicts.append("%s Escape release view is obstructed by a static asset" % device.name)
	if not conflicts.is_empty():
		printerr("TRAFFIC CAMERA SPATIAL SAFETY FAILED: %s" % "; ".join(conflicts))
		quit(1)
		return
	print("TRAFFIC_CAMERA_SPATIAL_SAFETY_OK devices=%d obstacle_assets=%d" % [get_nodes_in_group("traffic_cameras").size(), obstacle_nodes.size()])
	quit(0)
