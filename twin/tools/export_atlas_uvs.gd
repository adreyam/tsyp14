extends SceneTree
## Exports, per atlas texture, the UV triangles of every model surface that
## samples it (res://tools/atlas_uv_islands.json). tools/pad_atlas_textures.py
## uses them to grow each UV island's colours into the unused gutters so
## mipmaps never average the black background into a surface.
## Run: godot --headless --path . --script tools/export_atlas_uvs.gd

const ROOTS := ["res://assets/poly_pizza"]


func _initialize() -> void:
	var islands := {}
	for root in ROOTS:
		for path in _glb_files(root):
			var scene := (load(path) as PackedScene).instantiate()
			for value in scene.find_children("*", "MeshInstance3D", true, false):
				var instance := value as MeshInstance3D
				if instance.mesh == null:
					continue
				for surface in instance.mesh.get_surface_count():
					var material := instance.get_active_material(surface)
					if not material is StandardMaterial3D or (material as StandardMaterial3D).albedo_texture == null:
						continue
					var texture_path := (material as StandardMaterial3D).albedo_texture.resource_path
					if not texture_path.ends_with(".png"):
						continue
					var arrays := instance.mesh.surface_get_arrays(surface)
					var uvs: PackedVector2Array = arrays[Mesh.ARRAY_TEX_UV]
					var indices: PackedInt32Array = arrays[Mesh.ARRAY_INDEX] if arrays[Mesh.ARRAY_INDEX] != null else PackedInt32Array(range(uvs.size()))
					var triangles: Array = islands.get(texture_path, [])
					for i in range(0, indices.size() - 2, 3):
						var a := uvs[indices[i]]
						var b := uvs[indices[i + 1]]
						var c := uvs[indices[i + 2]]
						# Tiled UVs: bring the triangle into the 0..1 tile it sits in.
						var shift := ((a + b + c) / 3.0).floor()
						a -= shift
						b -= shift
						c -= shift
						triangles.append([snappedf(a.x, 0.00001), snappedf(a.y, 0.00001), snappedf(b.x, 0.00001), snappedf(b.y, 0.00001), snappedf(c.x, 0.00001), snappedf(c.y, 0.00001)])
					islands[texture_path] = triangles
			scene.free()
	var file := FileAccess.open("res://tools/atlas_uv_islands.json", FileAccess.WRITE)
	file.store_string(JSON.stringify(islands))
	file.close()
	for texture_path in islands:
		print("ATLAS %s triangles=%d" % [texture_path, (islands[texture_path] as Array).size()])
	quit(0)


func _glb_files(root: String) -> Array[String]:
	var output: Array[String] = []
	var directory := DirAccess.open(root)
	for sub in directory.get_directories():
		output.append_array(_glb_files(root.path_join(sub)))
	for file_name in directory.get_files():
		if file_name.ends_with(".glb"):
			output.append(root.path_join(file_name))
	return output
