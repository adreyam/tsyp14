extends SceneTree

const MODELS := [
	"res://assets/poly_pizza/city_props/Car.glb",
	"res://assets/poly_pizza/city_props/Police Car.glb",
	"res://assets/poly_pizza/city_props/SUV.glb",
	"res://assets/poly_pizza/city_props/Sports Car.glb",
	"res://assets/poly_pizza/city_pack/Car-unqqkULtRU.glb",
	"res://assets/poly_pizza/city_pack/Nissan GTR.glb",
	"res://assets/poly_pizza/city_pack/Convertible.glb",
	"res://assets/poly_pizza/city_props/Bus.glb",
	"res://assets/poly_pizza/city_props/Van.glb",
	"res://assets/poly_pizza/city_props/Pickup Truck.glb",
]


func _init() -> void:
	call_deferred("_audit")


func _audit() -> void:
	for source_path in MODELS:
		var vehicle := (load(source_path) as PackedScene).instantiate() as Node3D
		root.add_child(vehicle)
		await process_frame
		var bounds := _get_bounds(vehicle)
		var wheels: Array[Node3D] = []
		for node in vehicle.find_children("*", "Node3D", true, false):
			if "wheel" in node.name.to_lower():
				wheels.append(node as Node3D)
		var wheel_report: Array[String] = []
		for wheel in wheels:
			var before := wheel.global_position
			wheel.rotate_object_local(Vector3.RIGHT, 0.4)
			var stable := wheel.global_position.distance_to(before) < 0.00001
			var wheel_aabb := (wheel as MeshInstance3D).mesh.get_aabb() if wheel is MeshInstance3D and (wheel as MeshInstance3D).mesh != null else AABB()
			wheel_report.append("%s@%s stable=%s aabb=%s meshes=%s" % [wheel.name, wheel.global_position, stable, wheel_aabb, _wheel_mesh_report(wheel)])
		var mesh_report: Array[String] = []
		for mesh_node in vehicle.find_children("*", "MeshInstance3D", true, false):
			var mesh_instance := mesh_node as MeshInstance3D
			mesh_report.append("%s@%s" % [vehicle.get_path_to(mesh_instance), vehicle.to_local(mesh_instance.global_position)])
		print("VEHICLE_AUDIT path=%s bounds=%s wheels=%d [%s] mesh_nodes=[%s]" % [source_path.get_file(), bounds, wheels.size(), "; ".join(wheel_report), "; ".join(mesh_report)])
		vehicle.queue_free()
	await process_frame
	quit(0)


func _get_bounds(node: Node3D) -> AABB:
	var result := AABB()
	var has_bounds := false
	for mesh_node in node.find_children("*", "MeshInstance3D", true, false):
		var mesh_instance := mesh_node as MeshInstance3D
		if mesh_instance.mesh == null:
			continue
		var aabb := mesh_instance.mesh.get_aabb()
		for corner in [
			aabb.position,
			aabb.position + Vector3(aabb.size.x, 0, 0),
			aabb.position + Vector3(0, aabb.size.y, 0),
			aabb.position + Vector3(0, 0, aabb.size.z),
			aabb.end,
		]:
			var point := node.to_local(mesh_instance.to_global(corner))
			result = AABB(point, Vector3.ZERO) if not has_bounds else result.expand(point)
			has_bounds = true
	return result


func _wheel_mesh_report(wheel: Node3D) -> String:
	var pieces: Array[String] = []
	for mesh_node in wheel.find_children("*", "MeshInstance3D", true, false):
		var mesh_instance := mesh_node as MeshInstance3D
		pieces.append("%s:%s" % [mesh_instance.name, wheel.to_local(mesh_instance.global_position)])
	return ",".join(pieces)
