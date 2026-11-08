extends Node3D

## Sixteen physical roadside cameras.  The unified control room selects one of
## these Camera3D nodes directly; there is no image capture, file transfer, or
## second renderer between the city and the control-room viewport.

const CAMERA_SITES := [
	# Two opposing approaches at each busy central junction. The physical pole
	# sits beside its traffic light and its lens frames the junction centre, so
	# every selection shows incoming traffic, cross traffic, and signal state.
	{"id": "CAM-01", "position": Vector3(-303.0, 0.17, -303.0), "target": Vector3(-285.0, 1.5, -285.0)},
	{"id": "CAM-02", "position": Vector3(-267.0, 0.17, -267.0), "target": Vector3(-285.0, 1.5, -285.0)},
	{"id": "CAM-03", "position": Vector3(113.0, 0.17, -113.0), "target": Vector3(95.0, 1.5, -95.0)},
	{"id": "CAM-04", "position": Vector3(77.0, 0.17, -77.0), "target": Vector3(95.0, 1.5, -95.0)},
	{"id": "CAM-05", "position": Vector3(-113.0, 0.17, 77.0), "target": Vector3(-95.0, 1.5, 95.0)},
	{"id": "CAM-06", "position": Vector3(-77.0, 0.17, 113.0), "target": Vector3(-95.0, 1.5, 95.0)},
	{"id": "CAM-07", "position": Vector3(113.0, 0.17, 77.0), "target": Vector3(95.0, 1.5, 95.0)},
	{"id": "CAM-08", "position": Vector3(77.0, 0.17, 113.0), "target": Vector3(95.0, 1.5, 95.0)},
	{"id": "CAM-09", "position": Vector3(303.0, 0.17, 267.0), "target": Vector3(285.0, 1.5, 285.0)},
	{"id": "CAM-10", "position": Vector3(267.0, 0.17, 303.0), "target": Vector3(285.0, 1.5, 285.0)},
	{"id": "CAM-11", "position": Vector3(-303.0, 0.17, 267.0), "target": Vector3(-285.0, 1.5, 285.0)},
	{"id": "CAM-12", "position": Vector3(-267.0, 0.17, 303.0), "target": Vector3(-285.0, 1.5, 285.0)},
	{"id": "CAM-13", "position": Vector3(303.0, 0.17, -303.0), "target": Vector3(285.0, 1.5, -285.0)},
	{"id": "CAM-14", "position": Vector3(267.0, 0.17, -267.0), "target": Vector3(285.0, 1.5, -285.0)},
	{"id": "CAM-15", "position": Vector3(-113.0, 0.17, -113.0), "target": Vector3(-95.0, 1.5, -95.0)},
	{"id": "CAM-16", "position": Vector3(-77.0, 0.17, -77.0), "target": Vector3(-95.0, 1.5, -95.0)},
]

const CitySpatialSafety = preload("res://scripts/city_spatial_safety.gd")

var _camera_by_id: Dictionary = {}


func _ready() -> void:
	var static_obstacles := _camera_static_obstacles()
	for site_value in CAMERA_SITES:
		var site := site_value as Dictionary
		var camera_id := str(site.get("id", "CAM"))
		var device := _make_camera_device(camera_id)
		device.position = site.get("position", Vector3.ZERO) as Vector3
		device.set_meta("traffic_target", site.get("target", Vector3.FORWARD) as Vector3)
		add_child(device)
		# Camera3D looks down local -Z. Passing use_model_front=true had every
		# lens looking away from its intended traffic junction.
		device.look_at(site.get("target", Vector3.FORWARD) as Vector3, Vector3.UP)
		_place_at_nearest_safe_site(device, site.get("position", Vector3.ZERO) as Vector3, site.get("target", Vector3.FORWARD) as Vector3, static_obstacles)
		_camera_by_id[camera_id] = device


func _camera_static_obstacles() -> Array[Dictionary]:
	var obstacles: Array[Dictionary] = []
	var candidates := []
	for group_name in ["buildings", "backdrop_buildings", "city_trees", "urban_props", "power_poles", "traffic_signals", "npc_activity_props"]:
		candidates.append_array(get_tree().get_nodes_in_group(group_name))
	# Central-block trees were authored before the city_trees safety group was
	# introduced. Include them explicitly so camera rigs cannot occupy canopies.
	var central_buildings := get_parent().get_node_or_null("Buildings")
	if central_buildings != null:
		candidates.append_array(central_buildings.find_children("Tree_*", "Node3D", true, false))
	var seen := {}
	for value in candidates:
		if not value is Node3D:
			continue
		var obstacle := value as Node3D
		if not obstacle.visible or seen.has(obstacle.get_instance_id()):
			continue
		seen[obstacle.get_instance_id()] = true
		var bounds := CitySpatialSafety.world_bounds(obstacle)
		if bounds.size != Vector3.ZERO:
			obstacles.append({"node":obstacle, "bounds":bounds})
	return obstacles


func _place_at_nearest_safe_site(device: Node3D, requested: Vector3, target: Vector3, obstacles: Array[Dictionary]) -> void:
	var outward := requested - target
	outward.y = 0.0
	if outward.length_squared() < 0.01:
		outward = Vector3(1.0, 0.0, 1.0)
	outward = outward.normalized()
	var tangent := Vector3(-outward.z, 0.0, outward.x)
	var chosen := requested
	var found := false
	# Ordered by displacement: retain the authored site whenever it is safe,
	# then search along the sidewalk before moving farther into the block.
	for outward_distance in [0.0, 3.0, 6.0, 9.0, 12.0, 15.0, 18.0, 22.0]:
		for side_distance in [0.0, 3.0, -3.0, 6.0, -6.0, 9.0, -9.0, 12.0, -12.0, 16.0, -16.0]:
			var candidate := requested + outward * float(outward_distance) + tangent * float(side_distance)
			if CitySpatialSafety.overlaps_road(AABB(candidate - Vector3(0.38, 0.0, 0.38), Vector3(0.76, 0.6, 0.76)), 0.10):
				continue
			device.global_position = candidate
			device.look_at(target, Vector3.UP)
			device.force_update_transform()
			var device_bounds := CitySpatialSafety.world_bounds(device)
			if _camera_bounds_are_clear(device_bounds, obstacles):
				chosen = candidate
				found = true
				break
		if found:
			break
	device.global_position = chosen
	device.look_at(target, Vector3.UP)
	device.force_update_transform()
	device.set_meta("requested_camera_position", requested)
	device.set_meta("camera_site_adjustment", chosen - requested)
	device.set_meta("camera_site_spatially_safe", found)
	if not found:
		push_warning("No collision-free placement found for %s; retaining requested site" % device.name)


func _camera_bounds_are_clear(device_bounds: AABB, obstacles: Array[Dictionary]) -> bool:
	for obstacle in obstacles:
		var obstacle_bounds := obstacle.bounds as AABB
		if device_bounds.intersection(obstacle_bounds).get_volume() > 0.015:
			return false
	return true


func _make_camera_device(camera_id: String) -> Node3D:
	var device := Node3D.new()
	device.name = "TrafficCamera_%s" % camera_id.replace("-", "_")
	device.add_to_group("traffic_cameras")
	device.add_to_group("sensors")
	device.set_meta("camera_id", camera_id)
	device.set_meta("sensor_id", camera_id)
	device.set_meta("asset_type", "traffic_camera_sensor")
	device.set_meta("sensed_metrics", ["vehicle_count", "speed", "occupancy"])
	device.set_meta("highlightable", true)
	device.set_meta("type", "traffic_camera")
	device.set_meta("feed", "live_3d")
	var metal := _material(Color(0.075, 0.10, 0.13), 0.38)
	var housing := _material(Color(0.68, 0.72, 0.74), 0.48)
	var glass := _material(Color(0.015, 0.08, 0.12), 0.14)
	var glow := _material(Color(0.04, 0.95, 0.42), 0.25, Color(0.02, 1.0, 0.30), 3.8)
	_add_mesh(device, "Pole", _cylinder(0.12, 7.4), Vector3(0, 3.7, 0), metal)
	_add_mesh(device, "Base", _cylinder(0.24, 0.30), Vector3(0, 0.15, 0), housing)
	_add_mesh(device, "Arm", _box(Vector3(0.18, 0.18, 2.2)), Vector3(0, 6.45, -0.9), metal)
	_add_mesh(device, "CameraHousing", _box(Vector3(0.82, 0.58, 1.15)), Vector3(0, 6.25, -2.0), housing)
	var lens := _add_mesh(device, "Lens", _cylinder(0.21, 0.20), Vector3(0, 6.25, -2.65), glass)
	lens.rotation.x = PI * 0.5
	_add_mesh(device, "StatusLED", _sphere(0.07), Vector3(0.28, 6.43, -2.58), glow)
	var feed_camera := Camera3D.new()
	feed_camera.name = "FeedCamera"
	feed_camera.position = Vector3(0, 6.25, -2.72)
	feed_camera.fov = 68.0
	feed_camera.near = 0.12
	feed_camera.far = 850.0
	device.add_child(feed_camera)
	return device


func _add_mesh(parent: Node3D, mesh_name: String, mesh: PrimitiveMesh, local_position: Vector3, material: StandardMaterial3D) -> MeshInstance3D:
	var instance := MeshInstance3D.new()
	instance.name = mesh_name
	instance.mesh = mesh
	instance.position = local_position
	instance.material_override = material
	instance.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
	parent.add_child(instance)
	return instance


func _box(size_value: Vector3) -> BoxMesh:
	var mesh := BoxMesh.new()
	mesh.size = size_value
	return mesh


func _cylinder(radius: float, height_value: float) -> CylinderMesh:
	var mesh := CylinderMesh.new()
	mesh.top_radius = radius
	mesh.bottom_radius = radius
	mesh.height = height_value
	return mesh


func _sphere(radius: float) -> SphereMesh:
	var mesh := SphereMesh.new()
	mesh.radius = radius
	mesh.height = radius * 2.0
	return mesh


func _material(color: Color, roughness_value: float, emission_color := Color.TRANSPARENT, emission_energy := 0.0) -> StandardMaterial3D:
	var material := StandardMaterial3D.new()
	material.albedo_color = color
	material.metallic = 0.55 if emission_energy == 0.0 else 0.1
	material.roughness = roughness_value
	if emission_energy > 0.0:
		material.emission_enabled = true
		material.emission = emission_color
		material.emission_energy_multiplier = emission_energy
	return material
