extends RefCounted

## Crash crumple and fire scorch for pooled vehicle models. The deformation is
## computed once per impact on duplicated meshes (the shared imported meshes
## are never modified) and every original is restored when the model returns
## to the pool, so a damaged look never leaks onto an unrelated car.

const META_ORIGINALS := "damage_original_meshes"
const META_MATERIALS := "damage_original_materials"
const META_SOOT := "damage_soot"
const ProceduralVehicle := preload("res://scripts/vehicles/procedural_vehicle.gd")


static func apply_crumple(vehicle: Node3D, zone: String, level: float, seed_value: int) -> void:
	if level <= 0.02:
		return
	restore(vehicle)
	var meshes := vehicle.find_children("*", "MeshInstance3D", true, false)
	var originals := {}
	# Bounds in the vehicle frame tell us where the zone's surface is.
	var bounds := _local_bounds(vehicle)
	if bounds.size == Vector3.ZERO:
		return
	var centre := bounds.get_center()
	var half := bounds.size * 0.5
	# Unit direction (vehicle local) pointing at the struck side; nose is +Z.
	var direction := {"front": Vector3(0, 0, 1), "rear": Vector3(0, 0, -1), "left": Vector3(1, 0, 0), "right": Vector3(-1, 0, 0)}.get(zone, Vector3(0, 0, 1)) as Vector3
	var depth := clampf(level, 0.0, 1.0) * (0.55 if zone in ["front", "rear"] else 0.32)
	var reach := (half.z if zone in ["front", "rear"] else half.x) * 0.85
	var noise := FastNoiseLite.new()
	noise.seed = seed_value
	noise.frequency = 1.6
	for value in meshes:
		var mesh_instance := value as MeshInstance3D
		if mesh_instance.mesh == null or not mesh_instance.mesh is ArrayMesh or mesh_instance.get_parent() is BoneAttachment3D:
			continue
		if mesh_instance.name.begins_with("VehicleLightRig") or mesh_instance.get_parent().name == "VehicleLightRig":
			continue
		var source := mesh_instance.mesh as ArrayMesh
		# Paths are relative to the vehicle: a duplicated ghost then restores
		# its own meshes, never the live car's.
		originals[vehicle.get_path_to(mesh_instance)] = source
		var to_vehicle := _metric_frame(vehicle).affine_inverse() * mesh_instance.global_transform
		var to_mesh := to_vehicle.affine_inverse()
		var deformed := ArrayMesh.new()
		for surface in source.get_surface_count():
			var arrays := source.surface_get_arrays(surface)
			var vertices: PackedVector3Array = arrays[Mesh.ARRAY_VERTEX]
			for index in vertices.size():
				var local := to_vehicle * vertices[index]
				var offset := local - centre
				# Normalized penetration of this vertex into the struck side.
				var along := offset.dot(direction) / maxf(reach if zone in ["front", "rear"] else half.x, 0.01)
				var influence := smoothstep(0.35, 1.05, along)
				if influence <= 0.0:
					continue
				var jitter := 0.75 + 0.5 * noise.get_noise_3d(local.x, local.y, local.z)
				# Push inward and slightly crumple downward/outward.
				var push := -direction * depth * influence * jitter
				push.y -= depth * 0.18 * influence * absf(noise.get_noise_3d(local.z, local.x, local.y))
				vertices[index] = to_mesh * (local + push)
			arrays[Mesh.ARRAY_VERTEX] = vertices
			# Normals stay as authored; the dent is read through the silhouette.
			deformed.add_surface_from_arrays(source.surface_get_primitive_type(surface), arrays)
			deformed.surface_set_material(surface, source.surface_get_material(surface))
		mesh_instance.mesh = deformed
	vehicle.set_meta(META_ORIGINALS, originals)
	vehicle.set_meta("damage_zone", zone)
	vehicle.set_meta("damage_level", level)


static func scorch(vehicle: Node3D, amount: float) -> void:
	## Darkens paint toward soot as a vehicle burns (amount 0..1).
	if vehicle.has_meta("procedural_vehicle"):
		# Procedural finishes, lenses and tyres take soot as a shader input.
		ProceduralVehicle.set_soot(vehicle, amount)
		vehicle.set_meta(META_SOOT, amount)
		return
	var originals: Dictionary = vehicle.get_meta(META_MATERIALS, {})
	for value in vehicle.find_children("*", "MeshInstance3D", true, false):
		var mesh_instance := value as MeshInstance3D
		if mesh_instance.mesh == null or mesh_instance.get_parent().name == "VehicleLightRig":
			continue
		var path := vehicle.get_path_to(mesh_instance)
		if not originals.has(path):
			originals[path] = mesh_instance.material_override
		var base := mesh_instance.get_active_material(0)
		if not base is StandardMaterial3D:
			continue
		var burnt := (originals[path] if originals[path] is StandardMaterial3D else base).duplicate() as StandardMaterial3D
		burnt.albedo_color = burnt.albedo_color.lerp(Color(0.05, 0.045, 0.04), clampf(amount, 0.0, 0.92))
		burnt.roughness = lerpf(burnt.roughness, 1.0, amount)
		mesh_instance.material_override = burnt
	vehicle.set_meta(META_MATERIALS, originals)


static func restore(vehicle: Node3D) -> void:
	if vehicle.has_meta(META_SOOT):
		ProceduralVehicle.set_soot(vehicle, 0.0)
	var originals: Dictionary = vehicle.get_meta(META_ORIGINALS, {})
	for path in originals:
		var mesh_instance := vehicle.get_node_or_null(path) as MeshInstance3D
		if mesh_instance != null:
			mesh_instance.mesh = originals[path]
	var materials: Dictionary = vehicle.get_meta(META_MATERIALS, {})
	for path in materials:
		var mesh_instance := vehicle.get_node_or_null(path) as MeshInstance3D
		if mesh_instance != null:
			mesh_instance.material_override = materials[path]
	for key in [META_ORIGINALS, META_MATERIALS, META_SOOT, "damage_zone", "damage_level"]:
		if vehicle.has_meta(key):
			vehicle.remove_meta(key)


static func is_damaged(vehicle: Node3D) -> bool:
	return vehicle.has_meta(META_ORIGINALS) or vehicle.has_meta(META_MATERIALS) or vehicle.has_meta(META_SOOT)


static func _metric_frame(vehicle: Node3D) -> Transform3D:
	## Vehicle yaw frame in metres (model scale removed), so dent depth is
	## physical regardless of how each model was imported.
	return Transform3D(vehicle.global_transform.basis.orthonormalized(), vehicle.global_transform.origin)


static func _local_bounds(vehicle: Node3D) -> AABB:
	var result := AABB()
	var found := false
	for value in vehicle.find_children("*", "MeshInstance3D", true, false):
		var mesh_instance := value as MeshInstance3D
		if mesh_instance.mesh == null or mesh_instance.get_parent().name == "VehicleLightRig":
			continue
		var transform := _metric_frame(vehicle).affine_inverse() * mesh_instance.global_transform
		var bounds := transform * mesh_instance.mesh.get_aabb()
		result = bounds if not found else result.merge(bounds)
		found = true
	return result
