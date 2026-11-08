extends Node
## Static batching for simple scenery that never moves or changes material.
##
## Thousands of individual building, tree and road meshes cost one draw call
## each (and one more per shadow cascade). After the city has finished
## placing its scenery, every static surface under the target containers is
## merged per (map chunk, material) into a single mesh, and each original
## MeshInstance3D is hidden on the rendering server only: it is drawn by no
## camera and no light, yet the node keeps its groups, transform, bounds and
## visible flag for every system that queries the scene (cameras, spatial
## safety, obstacle indexes, selection). When a batched original is later
## hidden or shown (stations clearing ambient props, spatial safety), only the
## batch that holds it is rebuilt from the originals that are still visible.

## Scenery containers (paths relative to the City root) that are batched.
@export var targets: Array[NodePath] = [
	NodePath("CityBackdrop/OuterDistricts"),
	NodePath("CityBackdrop/OuterRoadNetwork"),
	NodePath("Roads"),
	NodePath("Buildings"),
	NodePath("UrbanProps"),
	NodePath("PowerGrid"),
]
## Chunk edge in metres (one city block): small enough for frustum culling,
## large enough to collapse thousands of instances.
@export var chunk_size := 190.0

# Power lines take a highlight overlay from the control room, so they keep
# their own instances.
const EXCLUDED_GROUPS := ["vehicles", "backdrop_vehicles", "pedestrians", "backdrop_pedestrians", "traffic_signals", "power_lines"]

var batched_instances := 0
var batch_count := 0
var _groups := {}  # key -> {instances, surfaces, material, shadow, batch}
var _dirty := {}
var _root: Node3D


func _ready() -> void:
	# Other systems still place and replace scenery during their own _ready
	# (reservoir parcels replace park trees): batch once that has settled.
	await get_tree().process_frame
	await get_tree().process_frame
	_batch()


func _batch() -> void:
	var city := get_parent() as Node3D
	var started := Time.get_ticks_msec()
	for path in targets:
		var container := city.get_node_or_null(path)
		if container == null:
			continue
		for value in container.find_children("*", "MeshInstance3D", true, false):
			var instance := value as MeshInstance3D
			if not _batchable(instance):
				continue
			var origin := city.global_transform.affine_inverse() * instance.global_position
			var chunk := Vector2i(floori(origin.x / chunk_size), floori(origin.z / chunk_size))
			var keys: Array[String] = []
			for surface in instance.mesh.get_surface_count():
				var material := instance.get_active_material(surface)
				var key := "%d,%d|%d|%d" % [chunk.x, chunk.y, material.get_instance_id() if material else 0, instance.cast_shadow]
				if not _groups.has(key):
					_groups[key] = {"instances": [], "surfaces": [], "material": material, "shadow": instance.cast_shadow, "batch": null}
				var group := _groups[key] as Dictionary
				(group.instances as Array).append(instance)
				(group.surfaces as Array).append(surface)
				if not keys.has(key):
					keys.append(key)
			instance.set_meta(&"static_batched", true)
			instance.visibility_changed.connect(_on_original_visibility_changed.bind(keys))
			batched_instances += 1
	_root = Node3D.new()
	_root.name = "StaticBatches"
	city.add_child(_root)
	for key in _groups:
		_rebuild(key)
	print("STATIC_BATCHER instances=%d batches=%d ms=%d" % [batched_instances, batch_count, Time.get_ticks_msec() - started])


func _rebuild(key: String) -> void:
	var city := get_parent() as Node3D
	var group := _groups[key] as Dictionary
	var to_city := city.global_transform.affine_inverse()
	var tool := SurfaceTool.new()
	var empty := true
	var instances := group.instances as Array
	for index in instances.size():
		var instance := instances[index] as MeshInstance3D
		if not is_instance_valid(instance) or not instance.is_visible_in_tree():
			continue
		tool.append_from(instance.mesh, int(group.surfaces[index]), to_city * instance.global_transform)
		# The node itself re-enables its server instance whenever it is shown.
		RenderingServer.instance_set_visible(instance.get_instance(), false)
		empty = false
	var batch := group.batch as MeshInstance3D
	if empty:
		if batch != null:
			batch.queue_free()
			group.batch = null
			batch_count -= 1
		return
	if batch == null:
		batch = MeshInstance3D.new()
		batch.material_override = group.material
		batch.cast_shadow = group.shadow
		_root.add_child(batch)
		group.batch = batch
		batch_count += 1
	batch.mesh = tool.commit()


func _on_original_visibility_changed(keys: Array[String]) -> void:
	if _dirty.is_empty():
		_flush_dirty.call_deferred()
	for key in keys:
		_dirty[key] = true


func _flush_dirty() -> void:
	for key in _dirty:
		_rebuild(key)
	_dirty.clear()


func _batchable(instance: MeshInstance3D) -> bool:
	if instance.mesh == null or not instance.is_visible_in_tree() or instance.layers == 0 or instance.skin != null or instance.has_meta(&"static_batched"):
		return false
	if instance.mesh is ArrayMesh == false or instance.visibility_range_end > 0.0:
		return false
	# Detailed models carry imported distance LODs that a merged mesh would
	# lose (drawing full detail at any range): they keep their own instances.
	for surface in instance.mesh.get_surface_count():
		if not (RenderingServer.mesh_get_surface(instance.mesh.get_rid(), surface).get("lods", []) as Array).is_empty():
			return false
	var node: Node = instance
	while node != null and node != get_parent():
		for group in EXCLUDED_GROUPS:
			if node.is_in_group(group):
				return false
		node = node.get_parent()
	return true
