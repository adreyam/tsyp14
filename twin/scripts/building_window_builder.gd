extends RefCounted

## Finds the window planes of an imported building model.
##
## A building's windows are polygons of its own mesh: either whole glass
## surfaces (a surface whose material is the glazing) or, on models that keep
## every part in one atlas-textured surface, the individual quads whose atlas
## colour is the colour of a pane. Both are selected triangle by triangle,
## nothing is generated, and the selected triangles are grouped into windows
## (triangles that touch, or sit edge to edge in the same plane, are one window).
## Each window gets a number that the overlay mesh carries in UV2.x; the shader
## looks that number up in the building's window-state texture, so each window
## can be lit, dimmed or tinted on its own.
##
## Results are cached per mesh: all instances of one model share the overlay
## mesh and the list of windows (in the mesh's local space).

const MERGE_GAP := 0.22
const MERGE_NORMAL_DOT := 0.96

static var _cache: Dictionary = {}
static var _atlas_images: Dictionary = {}


static func windows_for_surface(mesh: Mesh, surface_index: int, material: StandardMaterial3D, rule: Dictionary) -> Dictionary:
	## rule:
	##   select "glass"      every triangle of the surface is a window
	##   select "atlas_pane" triangles whose atlas colour matches rule.pane and
	##                       whose shape matches rule.max_area / rule.max_normal_y
	## Returns {"mesh": ArrayMesh (UV2.x = window number), "windows": Array of
	## {centre, normal, size, triangles} in mesh-local space}; mesh is null when
	## the surface has no window.
	var key := "%d:%d:%s" % [mesh.get_rid().get_id(), surface_index, str(rule.hash())]
	if _cache.has(key):
		return _cache[key] as Dictionary
	var result := {"mesh": null, "windows": []}
	var arrays := mesh.surface_get_arrays(surface_index)
	var vertices := arrays[Mesh.ARRAY_VERTEX] as PackedVector3Array
	var normals: Variant = arrays[Mesh.ARRAY_NORMAL]
	var uvs: Variant = arrays[Mesh.ARRAY_TEX_UV]
	var source_indices: Variant = arrays[Mesh.ARRAY_INDEX]
	var indices := PackedInt32Array()
	if source_indices == null or (source_indices as PackedInt32Array).is_empty():
		for vertex_index in vertices.size():
			indices.append(vertex_index)
	else:
		indices = source_indices as PackedInt32Array
	var triangle_count := int(indices.size() / 3)
	var selected: Array[int] = []
	var select := str(rule.get("select", "glass"))
	## World metres per mesh unit: imported models come in at very different scales.
	var unit := maxf(float(rule.get("scale", 1.0)), 0.000001)
	var image: Image = null
	if select == "atlas_pane":
		image = _atlas_image(material)
		if image == null or uvs == null:
			_cache[key] = result
			return result
	for triangle in triangle_count:
		var a := vertices[indices[triangle * 3]]
		var b := vertices[indices[triangle * 3 + 1]]
		var c := vertices[indices[triangle * 3 + 2]]
		var cross := (b - a).cross(c - a)
		if cross.length_squared() < 1.0e-14:
			continue
		if select == "glass":
			selected.append(triangle)
			continue
		var normal := cross.normalized()
		if absf(normal.y) > float(rule.get("max_normal_y", 0.35)) or cross.length() * 0.5 * unit * unit > float(rule.get("max_area", 3.0)):
			continue
		var uv_list := uvs as PackedVector2Array
		var centre_uv := (uv_list[indices[triangle * 3]] + uv_list[indices[triangle * 3 + 1]] + uv_list[indices[triangle * 3 + 2]]) / 3.0
		var pixel := image.get_pixel(clampi(int(centre_uv.x * float(image.get_width())), 0, image.get_width() - 1), clampi(int(centre_uv.y * float(image.get_height())), 0, image.get_height() - 1))
		if _matches_pane(pixel, rule.get("pane", {}) as Dictionary):
			selected.append(triangle)
	if selected.is_empty():
		_cache[key] = result
		return result
	var groups := _group_triangles(vertices, indices, selected, unit)
	var window_of_triangle := {}
	var windows: Array = []
	for group_index in groups.size():
		var window := _describe_window(vertices, indices, normals, groups[group_index] as Array)
		windows.append(window)
		for triangle in groups[group_index] as Array:
			window_of_triangle[int(triangle)] = group_index
	# Unshared vertices: one window can never bleed into another through a shared
	# corner, and UV2.x is exactly that window's number everywhere on it.
	var out_vertices := PackedVector3Array()
	var out_normals := PackedVector3Array()
	var out_uv := PackedVector2Array()
	var out_uv2 := PackedVector2Array()
	for triangle in selected:
		for corner in 3:
			var source_index := indices[triangle * 3 + corner]
			out_vertices.append(vertices[source_index])
			if normals != null:
				out_normals.append((normals as PackedVector3Array)[source_index])
			out_uv.append((uvs as PackedVector2Array)[source_index] if uvs != null else Vector2.ZERO)
			out_uv2.append(Vector2(float(window_of_triangle[triangle]), 0.0))
	var out_arrays: Array = []
	out_arrays.resize(Mesh.ARRAY_MAX)
	out_arrays[Mesh.ARRAY_VERTEX] = out_vertices
	if normals != null:
		out_arrays[Mesh.ARRAY_NORMAL] = out_normals
	out_arrays[Mesh.ARRAY_TEX_UV] = out_uv
	out_arrays[Mesh.ARRAY_TEX_UV2] = out_uv2
	var overlay := ArrayMesh.new()
	overlay.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, out_arrays)
	result.mesh = overlay
	result.windows = windows
	_cache[key] = result
	return result


static func painted_surface(mesh: Mesh, surface_index: int, max_normal_y: float) -> ArrayMesh:
	## For models whose windows exist only as pixels of the atlas: the surface's
	## upright triangles (roofs and ground excluded), original UVs retained.
	var arrays := mesh.surface_get_arrays(surface_index)
	var vertices := arrays[Mesh.ARRAY_VERTEX] as PackedVector3Array
	var source_indices: Variant = arrays[Mesh.ARRAY_INDEX]
	var indices := PackedInt32Array()
	if source_indices == null or (source_indices as PackedInt32Array).is_empty():
		for vertex_index in vertices.size():
			indices.append(vertex_index)
	else:
		indices = source_indices as PackedInt32Array
	var kept := PackedInt32Array()
	for triangle in int(indices.size() / 3):
		var a := vertices[indices[triangle * 3]]
		var b := vertices[indices[triangle * 3 + 1]]
		var c := vertices[indices[triangle * 3 + 2]]
		var cross := (b - a).cross(c - a)
		if cross.length_squared() < 1.0e-14 or absf(cross.normalized().y) > max_normal_y:
			continue
		kept.append(indices[triangle * 3])
		kept.append(indices[triangle * 3 + 1])
		kept.append(indices[triangle * 3 + 2])
	var result := ArrayMesh.new()
	if kept.is_empty():
		return result
	arrays[Mesh.ARRAY_INDEX] = kept
	result.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arrays)
	return result


static func _atlas_image(material: StandardMaterial3D) -> Image:
	if material == null or material.albedo_texture == null:
		return null
	var texture := material.albedo_texture
	var key := texture.get_rid().get_id()
	if not _atlas_images.has(key):
		var image := texture.get_image()
		if image != null and image.is_compressed():
			image.decompress()
		_atlas_images[key] = image
	return _atlas_images[key] as Image


static func _matches_pane(pixel: Color, pane: Dictionary) -> bool:
	var luminance := pixel.r * 0.2126 + pixel.g * 0.7152 + pixel.b * 0.0722
	var saturation := maxf(pixel.r, maxf(pixel.g, pixel.b)) - minf(pixel.r, minf(pixel.g, pixel.b))
	var lum_range := pane.get("lum", [0.0, 1.0]) as Array
	var sat_range := pane.get("sat", [0.0, 1.0]) as Array
	if luminance < float(lum_range[0]) or luminance > float(lum_range[1]):
		return false
	if saturation < float(sat_range[0]) or saturation > float(sat_range[1]):
		return false
	if pane.has("blue_over_red") and pixel.b - pixel.r < float(pane.blue_over_red):
		return false
	if pane.has("red_over_blue") and pixel.r - pixel.b < float(pane.red_over_blue):
		return false
	return true


static func _group_triangles(vertices: PackedVector3Array, indices: PackedInt32Array, selected: Array[int], unit: float) -> Array:
	## Union-find over the selected triangles: those sharing a corner are one
	## window; then windows that sit edge to edge in the same plane (the panes of
	## one framed opening) are merged.
	var parent := {}
	for triangle in selected:
		parent[triangle] = triangle
	var by_corner := {}
	for triangle in selected:
		for corner in 3:
			var point := vertices[indices[triangle * 3 + corner]]
			var key := Vector3i(roundi(point.x * unit * 20.0), roundi(point.y * unit * 20.0), roundi(point.z * unit * 20.0))
			if by_corner.has(key):
				_union(parent, int(by_corner[key]), triangle)
			else:
				by_corner[key] = triangle
	var members := {}
	for triangle in selected:
		var root := _find(parent, triangle)
		if not members.has(root):
			members[root] = []
		(members[root] as Array).append(triangle)
	var groups: Array = members.values()
	# Merge groups that touch in the same plane.
	var boxes: Array = []
	var normals: Array = []
	for group in groups:
		var box := AABB()
		var started := false
		var normal := Vector3.ZERO
		for triangle in group as Array:
			var a := vertices[indices[int(triangle) * 3]]
			var b := vertices[indices[int(triangle) * 3 + 1]]
			var c := vertices[indices[int(triangle) * 3 + 2]]
			normal += (b - a).cross(c - a)
			for point in [a, b, c]:
				box = AABB(point, Vector3.ZERO) if not started else box.expand(point as Vector3)
				started = true
		boxes.append(box)
		normals.append(normal.normalized())
	var merged := true
	while merged:
		merged = false
		for i in groups.size():
			for j in range(i + 1, groups.size()):
				if (normals[i] as Vector3).dot(normals[j] as Vector3) < MERGE_NORMAL_DOT:
					continue
				if (boxes[i] as AABB).grow(MERGE_GAP / unit).intersects(boxes[j] as AABB):
					# Same plane only: the distance along the normal must be tiny.
					var offset := ((boxes[i] as AABB).get_center() - (boxes[j] as AABB).get_center()).dot(normals[i] as Vector3)
					if absf(offset) * unit > 0.12:
						continue
					(groups[i] as Array).append_array(groups[j] as Array)
					boxes[i] = (boxes[i] as AABB).merge(boxes[j] as AABB)
					groups.remove_at(j)
					boxes.remove_at(j)
					normals.remove_at(j)
					merged = true
					break
			if merged:
				break
	return groups


static func _describe_window(vertices: PackedVector3Array, indices: PackedInt32Array, vertex_normals: Variant, group: Array) -> Dictionary:
	var normal := Vector3.ZERO
	var box := AABB()
	var started := false
	for triangle in group:
		var a := vertices[indices[int(triangle) * 3]]
		var b := vertices[indices[int(triangle) * 3 + 1]]
		var c := vertices[indices[int(triangle) * 3 + 2]]
		if vertex_normals != null:
			# The model's own outward normals (the winding convention differs
			# between importers, the normals do not).
			for corner in 3:
				normal += (vertex_normals as PackedVector3Array)[indices[int(triangle) * 3 + corner]]
		else:
			normal -= (b - a).cross(c - a)
		for point in [a, b, c]:
			box = AABB(point, Vector3.ZERO) if not started else box.expand(point as Vector3)
			started = true
	normal = normal.normalized()
	var tangent := Vector3.UP.cross(normal)
	if tangent.length_squared() < 0.0001:
		tangent = Vector3.RIGHT
	tangent = tangent.normalized()
	var minimum := Vector2(INF, INF)
	var maximum := Vector2(-INF, -INF)
	for triangle in group:
		for corner in 3:
			var point := vertices[indices[int(triangle) * 3 + corner]]
			var plane := Vector2(point.dot(tangent), point.y)
			minimum = Vector2(minf(minimum.x, plane.x), minf(minimum.y, plane.y))
			maximum = Vector2(maxf(maximum.x, plane.x), maxf(maximum.y, plane.y))
	return {"centre": box.get_center(), "normal": normal, "size": maximum - minimum, "triangles": group.size()}


static func _find(parent: Dictionary, node: int) -> int:
	while int(parent[node]) != node:
		parent[node] = parent[int(parent[node])]
		node = int(parent[node])
	return node


static func _union(parent: Dictionary, a: int, b: int) -> void:
	var root_a := _find(parent, a)
	var root_b := _find(parent, b)
	if root_a != root_b:
		parent[root_b] = root_a
