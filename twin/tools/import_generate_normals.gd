@tool
extends EditorScenePostImport
## Import post-process for models exported without vertex normals (several
## Poly Pizza assets). Without normals the renderer cannot light a surface and
## the whole model renders black (the bar) or uniformly flat. Each surface
## missing normals gets flat, faceted normals (the low-poly look these models
## are authored for) and tangents for normal-mapped materials.


func _post_import(scene: Node) -> Object:
	for value in scene.find_children("*", "MeshInstance3D", true, false):
		var instance := value as MeshInstance3D
		var mesh := instance.mesh as ArrayMesh
		if mesh == null:
			continue
		var rebuilt := ArrayMesh.new()
		var changed := false
		for surface in mesh.get_surface_count():
			var arrays := mesh.surface_get_arrays(surface)
			var tool := SurfaceTool.new()
			tool.create_from(mesh, surface)
			if arrays[Mesh.ARRAY_NORMAL] == null:
				# One plane normal per triangle (SurfaceTool.generate_normals
				# would smooth across shared positions and shade flat walls
				# with diagonal gradients).
				tool.deindex()
				var flat := tool.commit_to_arrays()
				var vertices: PackedVector3Array = flat[Mesh.ARRAY_VERTEX]
				var normals := PackedVector3Array()
				normals.resize(vertices.size())
				for index in range(0, vertices.size() - 2, 3):
					var normal := Plane(vertices[index], vertices[index + 1], vertices[index + 2]).normal
					normals[index] = normal
					normals[index + 1] = normal
					normals[index + 2] = normal
				flat[Mesh.ARRAY_NORMAL] = normals
				tool = SurfaceTool.new()
				tool.create_from_arrays(flat)
				if flat[Mesh.ARRAY_TEX_UV] != null:
					tool.generate_tangents()
				changed = true
			tool.commit(rebuilt)
			rebuilt.surface_set_material(rebuilt.get_surface_count() - 1, mesh.surface_get_material(surface))
		if changed:
			instance.mesh = rebuilt
	return scene
