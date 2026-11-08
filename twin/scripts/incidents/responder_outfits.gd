extends RefCounted

## Role identity gear for responders, built from simple shaded meshes and fixed
## rigidly to Bip01 bones. Each piece is positioned from the live skeleton at a
## calibrated neutral pose, then parented to a BoneAttachment3D, so it moves
## with the authored mocap instead of floating near the body.

const MODELS_BY_ROLE := {
	"police": ["Business_Male_02", "Business_Male_03", "Business_Female_02", "Business_Male_01"],
	"paramedic": ["Medical_Male_01", "Medical_Female_01", "Medical_Female_02"],
	"firefighter": ["Construction_Male_02", "Construction_Male_03", "Construction_Male_01"],
	"tow_operator": ["Construction_Female_01", "Construction_Male_04"],
	"utility_worker": ["Construction_Male_01", "Construction_Male_04", "Construction_Female_01"],
	"sanitation_worker": ["Construction_Male_04", "Construction_Male_01"],
}

const GARMENT_SHADER := preload("res://shaders/responder_garment.gdshader")
const TORSO_BONES := ["Bip01 Spine", "Bip01 Spine1", "Bip01 Spine2", "Bip01 L Clavicle", "Bip01 R Clavicle"]
const ARM_BONES := ["Bip01 L UpperArm", "Bip01 L Forearm", "Bip01 R UpperArm", "Bip01 R Forearm"]
const LEG_BONES := ["Bip01 Pelvis", "Bip01 L Thigh", "Bip01 L Calf", "Bip01 R Thigh", "Bip01 R Calf"]
## Chain used to place reflective bands: 0 torso (waist→neck), 1 arm
## (shoulder→wrist), 2 leg (hip→ankle).
const BONE_CHAIN := {
	"Bip01 Pelvis": [2, "Bip01 Pelvis", "Bip01 L Foot"], "Bip01 Spine": [0, "Bip01 Pelvis", "Bip01 Neck"],
	"Bip01 Spine1": [0, "Bip01 Pelvis", "Bip01 Neck"], "Bip01 Spine2": [0, "Bip01 Pelvis", "Bip01 Neck"],
	"Bip01 L Clavicle": [0, "Bip01 Pelvis", "Bip01 Neck"], "Bip01 R Clavicle": [0, "Bip01 Pelvis", "Bip01 Neck"],
	"Bip01 L UpperArm": [1, "Bip01 L UpperArm", "Bip01 L Hand"], "Bip01 L Forearm": [1, "Bip01 L UpperArm", "Bip01 L Hand"],
	"Bip01 R UpperArm": [1, "Bip01 R UpperArm", "Bip01 R Hand"], "Bip01 R Forearm": [1, "Bip01 R UpperArm", "Bip01 R Hand"],
	"Bip01 L Thigh": [2, "Bip01 L Thigh", "Bip01 L Foot"], "Bip01 L Calf": [2, "Bip01 L Thigh", "Bip01 L Foot"],
	"Bip01 R Thigh": [2, "Bip01 R Thigh", "Bip01 R Foot"], "Bip01 R Calf": [2, "Bip01 R Thigh", "Bip01 R Foot"],
}

static var _materials: Dictionary = {}
static var _garment_cache: Dictionary = {}


static func model_path(role: String, index: int) -> String:
	var names := MODELS_BY_ROLE.get(role, MODELS_BY_ROLE["utility_worker"]) as Array
	var name := str(names[index % names.size()])
	return "res://assets/rocketbox/%s/Export/%s.fbx" % [name, name]


static func _material(key: String, colour: Color, roughness := 0.6, metallic := 0.0, emission := 0.0) -> StandardMaterial3D:
	if _materials.has(key):
		return _materials[key] as StandardMaterial3D
	var material := StandardMaterial3D.new()
	material.albedo_color = colour
	material.roughness = roughness
	material.metallic = metallic
	if emission > 0.0:
		material.emission_enabled = true
		material.emission = colour
		material.emission_energy_multiplier = emission
	_materials[key] = material
	return material


static func bone_world(skeleton: Skeleton3D, bone: String) -> Transform3D:
	var index := skeleton.find_bone(bone)
	return skeleton.global_transform * skeleton.get_bone_global_pose(index) if index >= 0 else skeleton.global_transform


static func attach(skeleton: Skeleton3D, bone: String, node: Node3D, world_transform: Transform3D) -> void:
	## Parents node to a bone so its current world transform is preserved.
	var attachment := BoneAttachment3D.new()
	attachment.name = "Gear_%s_%s" % [bone.replace(" ", "_"), node.name]
	attachment.bone_name = bone
	skeleton.add_child(attachment)
	attachment.global_transform = bone_world(skeleton, bone)
	attachment.add_child(node)
	node.global_transform = world_transform


static func _mesh(name: String, mesh: Mesh, material: Material) -> MeshInstance3D:
	var instance := MeshInstance3D.new()
	instance.name = name
	instance.mesh = mesh
	instance.material_override = material
	instance.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
	return instance


static func apply(person: Node3D, skeleton: Skeleton3D, role: String) -> void:
	var root_basis := Basis(Vector3.UP, person.global_rotation.y)
	var forward := root_basis * Vector3.BACK
	var up := Vector3.UP
	var head := bone_world(skeleton, "Bip01 Head").origin
	var neck := bone_world(skeleton, "Bip01 Neck").origin
	var spine := bone_world(skeleton, "Bip01 Spine2").origin
	var left_arm := bone_world(skeleton, "Bip01 L UpperArm").origin
	var right_arm := bone_world(skeleton, "Bip01 R UpperArm").origin
	var shoulder_width := left_arm.distance_to(right_arm)
	# The crown sits roughly one head-length above the head joint.
	var crown := head + up * 0.17
	match role:
		"police":
			_police_cap(skeleton, crown, forward, root_basis)
			_garment(person, "vest", TORSO_BONES, 0.016, {"base_colour": Color(0.80, 0.93, 0.10), "stripe_colour": Color(0.80, 0.82, 0.86), "torso_bands": Vector4(0.42, 0.49, 0.62, 0.69)})
			_back_label(skeleton, spine, forward, root_basis, "POLICE", Color(0.04, 0.06, 0.16))
		"firefighter":
			_fire_helmet(skeleton, crown, forward, root_basis)
			# Turnout coat and trousers: tan bunker gear with yellow/silver bands.
			_garment(person, "coat", TORSO_BONES + ARM_BONES, 0.022, {"base_colour": Color(0.55, 0.45, 0.28), "stripe_colour": Color(0.95, 0.9, 0.3), "torso_bands": Vector4(0.14, 0.22, 0.58, 0.65), "limb_bands": Vector4(0.72, 0.80, 2.0, 2.0), "base_emission": 0.0, "roughness_value": 0.85})
			_garment(person, "trousers", LEG_BONES, 0.018, {"base_colour": Color(0.52, 0.42, 0.26), "stripe_colour": Color(0.95, 0.9, 0.3), "limb_bands": Vector4(0.70, 0.77, 2.0, 2.0), "torso_bands": Vector4(2.0, 2.0, 2.0, 2.0), "base_emission": 0.0, "roughness_value": 0.85})
			_air_tank(skeleton, spine, forward, root_basis)
		"paramedic":
			_garment(person, "vest", TORSO_BONES, 0.016, {"base_colour": Color(0.16, 0.70, 0.30), "stripe_colour": Color(0.85, 0.87, 0.9), "torso_bands": Vector4(0.42, 0.49, 0.62, 0.69)})
			_back_label(skeleton, spine, forward, root_basis, "PARAMEDIC", Color(0.97, 0.97, 0.97))
		"tow_operator", "utility_worker":
			if not person.scene_file_path.contains("Construction_Female_01"):
				_garment(person, "vest", TORSO_BONES, 0.016, {"base_colour": Color(1.0, 0.48, 0.05), "stripe_colour": Color(0.85, 0.87, 0.9), "torso_bands": Vector4(0.42, 0.49, 0.62, 0.69)})
		"sanitation_worker":
			# Municipal collection crew: green hi-vis jacket with silver bands.
			_garment(person, "vest", TORSO_BONES + ARM_BONES, 0.018, {"base_colour": Color(0.18, 0.56, 0.22), "stripe_colour": Color(0.85, 0.87, 0.9), "torso_bands": Vector4(0.42, 0.49, 0.62, 0.69), "limb_bands": Vector4(0.72, 0.79, 2.0, 2.0)})


static func _police_cap(skeleton: Skeleton3D, crown: Vector3, forward: Vector3, root_basis: Basis) -> void:
	var cap := Node3D.new()
	cap.name = "PoliceCap"
	var crown_mesh := CylinderMesh.new()
	crown_mesh.top_radius = 0.118
	crown_mesh.bottom_radius = 0.104
	crown_mesh.height = 0.075
	cap.add_child(_mesh("Crown", crown_mesh, _material("police_navy", Color(0.05, 0.07, 0.16), 0.55)))
	var band := CylinderMesh.new()
	band.top_radius = 0.106
	band.bottom_radius = 0.106
	band.height = 0.026
	var band_node := _mesh("Band", band, _material("police_band", Color(0.02, 0.02, 0.02), 0.4))
	band_node.position = Vector3(0, -0.025, 0)
	cap.add_child(band_node)
	var visor := BoxMesh.new()
	visor.size = Vector3(0.17, 0.012, 0.075)
	var visor_node := _mesh("Visor", visor, _material("police_visor", Color(0.01, 0.01, 0.01), 0.2))
	visor_node.position = Vector3(0, -0.035, 0.115)
	visor_node.rotation_degrees = Vector3(14, 0, 0)
	cap.add_child(visor_node)
	var badge := BoxMesh.new()
	badge.size = Vector3(0.035, 0.035, 0.008)
	var badge_node := _mesh("Badge", badge, _material("police_gold", Color(0.95, 0.74, 0.22), 0.25, 0.8))
	badge_node.position = Vector3(0, 0.0, 0.112)
	cap.add_child(badge_node)
	attach(skeleton, "Bip01 Head", cap, Transform3D(root_basis, crown - Vector3.UP * 0.035 - forward * 0.01))


static func _fire_helmet(skeleton: Skeleton3D, crown: Vector3, forward: Vector3, root_basis: Basis) -> void:
	var helmet := Node3D.new()
	helmet.name = "FireHelmet"
	var shell := SphereMesh.new()
	shell.radius = 0.155
	shell.height = 0.31
	shell.is_hemisphere = true
	var shell_node := _mesh("Shell", shell, _material("fire_helmet", Color(0.78, 0.06, 0.04), 0.3, 0.1))
	shell_node.scale = Vector3(1.0, 0.82, 1.12)
	helmet.add_child(shell_node)
	var brim := CylinderMesh.new()
	brim.top_radius = 0.2
	brim.bottom_radius = 0.215
	brim.height = 0.014
	var brim_node := _mesh("Brim", brim, _material("fire_helmet", Color(0.78, 0.06, 0.04), 0.3, 0.1))
	brim_node.scale = Vector3(1.0, 1.0, 1.25)
	brim_node.position = Vector3(0, 0.0, -0.03)
	helmet.add_child(brim_node)
	var shield := BoxMesh.new()
	shield.size = Vector3(0.07, 0.06, 0.012)
	var shield_node := _mesh("Shield", shield, _material("fire_gold", Color(0.95, 0.76, 0.2), 0.3, 0.4))
	shield_node.position = Vector3(0, 0.07, 0.16)
	helmet.add_child(shield_node)
	attach(skeleton, "Bip01 Head", helmet, Transform3D(root_basis, crown - Vector3.UP * 0.07 - forward * 0.005))


static func _air_tank(skeleton: Skeleton3D, spine: Vector3, forward: Vector3, root_basis: Basis) -> void:
	var tank := Node3D.new()
	tank.name = "AirTank"
	var cylinder := CylinderMesh.new()
	cylinder.top_radius = 0.075
	cylinder.bottom_radius = 0.075
	cylinder.height = 0.52
	tank.add_child(_mesh("Cylinder", cylinder, _material("fire_tank", Color(0.75, 0.76, 0.78), 0.35, 0.6)))
	var plate := BoxMesh.new()
	plate.size = Vector3(0.24, 0.48, 0.03)
	var plate_node := _mesh("Harness", plate, _material("fire_harness", Color(0.08, 0.08, 0.08), 0.7))
	plate_node.position = Vector3(0, 0.0, 0.085)
	tank.add_child(plate_node)
	var reflective := BoxMesh.new()
	reflective.size = Vector3(0.16, 0.035, 0.155)
	var stripe := _mesh("Stripe", reflective, _material("reflective_yellow", Color(0.92, 0.95, 0.35), 0.35, 1.2))
	stripe.position = Vector3(0, 0.12, 0)
	tank.add_child(stripe)
	attach(skeleton, "Bip01 Spine2", tank, Transform3D(root_basis, spine - forward * 0.24 - Vector3.UP * 0.12))


static func _back_label(skeleton: Skeleton3D, spine: Vector3, forward: Vector3, root_basis: Basis, text: String, colour: Color) -> void:
	var label := Label3D.new()
	label.name = "BackLabel"
	label.text = text
	label.font_size = 44
	label.pixel_size = 0.0016
	label.modulate = colour
	label.outline_size = 0
	label.double_sided = false
	# Just behind the garment's back surface, facing backwards.
	attach(skeleton, "Bip01 Spine2", label, Transform3D(root_basis.rotated(Vector3.UP, PI), spine - forward * 0.19 + Vector3.UP * 0.02))


static func _garment(person: Node3D, kind: String, bones: Array, inflate_world: float, parameters: Dictionary) -> void:
	## Builds a skinned overlay from the avatar's own mesh: triangles weighted
	## to the given bones, offset along their normals and shaded as the garment.
	## It shares the body's Skin, so it deforms exactly with the mocap.
	for value in person.find_children("*", "MeshInstance3D", true, false):
		var body_mesh := value as MeshInstance3D
		if body_mesh.skin == null or body_mesh.mesh == null or body_mesh.has_meta("responder_garment"):
			continue
		var cache_key := "%s|%s|%.4f" % [person.scene_file_path, kind, inflate_world]
		var garment_mesh: ArrayMesh = _garment_cache.get(cache_key)
		if garment_mesh == null:
			garment_mesh = _build_garment_mesh(body_mesh, bones, inflate_world)
			_garment_cache[cache_key] = garment_mesh
		if garment_mesh == null or garment_mesh.get_surface_count() == 0:
			continue
		var overlay := MeshInstance3D.new()
		overlay.name = "Garment_%s" % kind
		overlay.set_meta("responder_garment", kind)
		overlay.mesh = garment_mesh
		overlay.skin = body_mesh.skin
		var material := ShaderMaterial.new()
		material.shader = GARMENT_SHADER
		for key in parameters:
			material.set_shader_parameter(str(key), parameters[key])
		overlay.material_override = material
		body_mesh.get_parent().add_child(overlay)
		overlay.transform = body_mesh.transform
		overlay.skeleton = overlay.get_path_to(body_mesh.get_node(body_mesh.skeleton))


static func _build_garment_mesh(body_mesh: MeshInstance3D, bones: Array, inflate_world: float) -> ArrayMesh:
	var skin := body_mesh.skin
	var skeleton := body_mesh.get_node_or_null(body_mesh.skeleton) as Skeleton3D
	var bind_names: Array[String] = []
	var bind_positions: Dictionary = {}
	for bind_index in skin.get_bind_count():
		var bone_name := str(skin.get_bind_name(bind_index))
		if bone_name.is_empty() and skeleton != null:
			bone_name = skeleton.get_bone_name(skin.get_bind_bone(bind_index))
		bind_names.append(bone_name)
		bind_positions[bone_name] = skin.get_bind_pose(bind_index).affine_inverse().origin
	var source := body_mesh.mesh
	# Mesh units → metres: the avatar is ~1.8 m tall along its longest bind axis.
	var extent := source.get_aabb().size
	var metres_per_unit := 1.80 / maxf(extent.x, maxf(extent.y, extent.z))
	var inflate := inflate_world / metres_per_unit
	var output := ArrayMesh.new()
	for surface in source.get_surface_count():
		var arrays := source.surface_get_arrays(surface)
		var vertices: PackedVector3Array = arrays[Mesh.ARRAY_VERTEX]
		var normals: PackedVector3Array = arrays[Mesh.ARRAY_NORMAL]
		var vertex_bones: PackedInt32Array = arrays[Mesh.ARRAY_BONES]
		var weights: PackedFloat32Array = arrays[Mesh.ARRAY_WEIGHTS]
		var indices: PackedInt32Array = arrays[Mesh.ARRAY_INDEX]
		var uvs: PackedVector2Array = arrays[Mesh.ARRAY_TEX_UV] if arrays[Mesh.ARRAY_TEX_UV] != null else PackedVector2Array()
		if vertices.is_empty() or vertex_bones.is_empty() or indices.is_empty():
			continue
		var influences := vertex_bones.size() / vertices.size()
		var region := PackedFloat32Array()
		region.resize(vertices.size())
		var dominant: Array[String] = []
		dominant.resize(vertices.size())
		for vertex in vertices.size():
			var best_weight := -1.0
			var total := 0.0
			for slot in influences:
				var weight := weights[vertex * influences + slot]
				var bind := vertex_bones[vertex * influences + slot]
				if bind < 0 or bind >= bind_names.size() or weight <= 0.0:
					continue
				var bone_name := bind_names[bind]
				if bones.has(bone_name):
					total += weight
					if weight > best_weight:
						best_weight = weight
						dominant[vertex] = bone_name
			region[vertex] = total
		var remap: Dictionary = {}
		var new_indices := PackedInt32Array()
		for triangle in range(0, indices.size(), 3):
			var keep := true
			for corner in 3:
				if region[indices[triangle + corner]] < 0.6:
					keep = false
					break
			if not keep:
				continue
			for corner in 3:
				var original := indices[triangle + corner]
				if not remap.has(original):
					remap[original] = remap.size()
				new_indices.append(int(remap[original]))
		if new_indices.is_empty():
			continue
		# Inflate along position-welded normals: split vertices at UV/normal
		# seams must move together or the garment tears into spikes.
		var welded_normals: Dictionary = {}
		for original_value in remap:
			var original := int(original_value)
			var key := (vertices[original] * 2000.0).round()
			var normal := normals[original] if original < normals.size() else Vector3.UP
			welded_normals[key] = (welded_normals.get(key, Vector3.ZERO) as Vector3) + normal
		var count := remap.size()
		var out_vertices := PackedVector3Array(); out_vertices.resize(count)
		var out_normals := PackedVector3Array(); out_normals.resize(count)
		var out_uv := PackedVector2Array(); out_uv.resize(count)
		var out_uv2 := PackedVector2Array(); out_uv2.resize(count)
		var out_bones := PackedInt32Array(); out_bones.resize(count * influences)
		var out_weights := PackedFloat32Array(); out_weights.resize(count * influences)
		for original_value in remap:
			var original := int(original_value)
			var target := int(remap[original_value])
			var normal := normals[original] if original < normals.size() else Vector3.UP
			var welded := (welded_normals.get((vertices[original] * 2000.0).round(), normal) as Vector3).normalized()
			out_vertices[target] = vertices[original] + welded * inflate
			out_normals[target] = normal
			out_uv[target] = uvs[original] if original < uvs.size() else Vector2.ZERO
			var chain := BONE_CHAIN.get(dominant[original], [0, "Bip01 Pelvis", "Bip01 Neck"]) as Array
			var chain_start := bind_positions.get(str(chain[1]), Vector3.ZERO) as Vector3
			var chain_end := bind_positions.get(str(chain[2]), Vector3.ONE) as Vector3
			var axis := chain_end - chain_start
			var along := clampf((vertices[original] - chain_start).dot(axis) / maxf(axis.length_squared(), 0.000001), 0.0, 1.0)
			out_uv2[target] = Vector2(along, float(chain[0]))
			for slot in influences:
				out_bones[target * influences + slot] = vertex_bones[original * influences + slot]
				out_weights[target * influences + slot] = weights[original * influences + slot]
		var out_arrays := []
		out_arrays.resize(Mesh.ARRAY_MAX)
		out_arrays[Mesh.ARRAY_VERTEX] = out_vertices
		out_arrays[Mesh.ARRAY_NORMAL] = out_normals
		out_arrays[Mesh.ARRAY_TEX_UV] = out_uv
		out_arrays[Mesh.ARRAY_TEX_UV2] = out_uv2
		out_arrays[Mesh.ARRAY_BONES] = out_bones
		out_arrays[Mesh.ARRAY_WEIGHTS] = out_weights
		out_arrays[Mesh.ARRAY_INDEX] = new_indices
		output.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, out_arrays, [], {}, Mesh.ARRAY_FLAG_USE_8_BONE_WEIGHTS if influences == 8 else 0)
	return output
