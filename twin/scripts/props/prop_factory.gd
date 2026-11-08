extends RefCounted

## Builds a catalogued prop: picks the right variant out of a glTF "variant sheet",
## puts loose parts back, recentres the footprint, sets the lowest visible point
## on y = 0, re-pivots movable parts onto their hinges and creates named contact
## anchors. Everything is derived from the model's own geometry, never guessed.

const Catalog := preload("res://scripts/props/prop_catalog.gd")
const Procedural := preload("res://scripts/props/procedural_props.gd")

static var _scene_cache: Dictionary = {}

static func build(kind: String) -> Node3D:
	var entry := Catalog.entry(kind)
	if entry.is_empty():
		push_error("PropFactory: unknown prop kind '%s'" % kind)
		return null
	var root := Node3D.new()
	root.name = "Prop_%s" % kind
	root.set_meta("prop_kind", kind)
	var model: Node3D = null
	var model_id := str(entry.get("model", ""))
	if model_id != "":
		model = _load_model(model_id)
		if model == null:
			return null
		model.rotation.y = deg_to_rad(float(entry.get("yaw", 0.0)))
		model.scale = Vector3.ONE * float(entry.get("unit", 1.0))
		root.add_child(model)
		_hide_variants(model, entry.get("hide", []) as Array)
		_apply_rest(model, entry.get("rest", []) as Array)
	else:
		model = Procedural.build_model(str(entry.get("builder", "")))
		if model == null:
			push_error("PropFactory: no builder for '%s'" % kind)
			return null
		root.add_child(model)
	model.name = "Model"
	_ground(root, model, bool(entry.get("flush", false)))
	var parts := {}
	for part_name in (entry.get("parts", {}) as Dictionary):
		var part := _make_part(root, model, str(part_name), (entry.parts as Dictionary)[part_name] as Dictionary)
		if not part.is_empty():
			parts[str(part_name)] = part
	root.set_meta("parts", parts)
	var anchors := {}
	for anchor_name in (entry.get("anchors", {}) as Dictionary):
		var marker := _make_anchor(root, model, parts, str(anchor_name), (entry.anchors as Dictionary)[anchor_name] as Dictionary)
		if marker != null:
			anchors[str(anchor_name)] = marker
	root.set_meta("anchors", anchors)
	if entry.has("builder") and model_id != "":
		Procedural.decorate(str(entry.builder), root, model)
	Procedural.register_anchors(root)
	bake_static(root)
	var bounds := visible_bounds(root)
	root.set_meta("prop_size", bounds.size)
	root.set_meta("prop_height", bounds.size.y)
	return root

static func _load_model(id: String) -> Node3D:
	if not _scene_cache.has(id):
		_scene_cache[id] = load(Catalog.polyhaven(id)) as PackedScene
	var scene := _scene_cache[id] as PackedScene
	if scene == null:
		push_error("PropFactory: cannot load Poly Haven model '%s'" % id)
		return null
	return scene.instantiate() as Node3D

# --- geometry helpers -------------------------------------------------------

## Bounds of the visible meshes below `node`, in the space of `space_root` (the
## transform chain is walked by hand so this also works before entering the tree).
static func visible_bounds(node: Node3D, skip_named: Array = []) -> AABB:
	var result := {"box": AABB(), "found": false}
	_collect_bounds(node, Transform3D.IDENTITY, result, skip_named)
	return result.box as AABB if bool(result.found) else AABB()

static func node_bounds(root: Node3D, node: Node3D) -> AABB:
	var result := {"box": AABB(), "found": false}
	_collect_bounds(node, relative_transform(root, node), result, [])
	return result.box as AABB if bool(result.found) else AABB()

static func relative_transform(root: Node3D, node: Node3D) -> Transform3D:
	var xf := Transform3D.IDENTITY
	var current := node
	while current != null and current != root:
		xf = current.transform * xf
		current = current.get_parent() as Node3D
	return xf

## `here` is the transform of `node` in the target space (its own transform included).
static func _collect_bounds(node: Node3D, here: Transform3D, result: Dictionary, skip_named: Array) -> void:
	if not node.visible:
		return
	if node is MeshInstance3D and (node as MeshInstance3D).mesh != null and not str(node.name) in skip_named:
		var box := here * (node as MeshInstance3D).mesh.get_aabb()
		result.box = box if not bool(result.found) else (result.box as AABB).merge(box)
		result.found = true
	for child in node.get_children():
		if child is Node3D:
			_collect_bounds(child as Node3D, here * (child as Node3D).transform, result, skip_named)

static func find_node(model: Node, node_name: String) -> Node3D:
	return model.find_child(node_name, true, false) as Node3D

# --- variant sheets & loose parts -------------------------------------------

static func _hide_variants(model: Node3D, fragments: Array) -> void:
	for fragment in fragments:
		for value in model.find_children("*", "Node3D", true, false):
			if str(fragment).to_lower() in str(value.name).to_lower():
				(value as Node3D).visible = false

static func _apply_rest(model: Node3D, rules: Array) -> void:
	for rule_value in rules:
		var rule := rule_value as Dictionary
		var node := find_node(model, str(rule.node))
		if node == null:
			continue
		if rule.has("on_top_of"):
			var base := find_node(model, str(rule.on_top_of))
			if base == null:
				continue
			# The sheet ships the lid propped open; closed means its authored upright pose.
			node.transform.basis = Basis.IDENTITY
			# Both boxes in the model's frame: put the lid's footprint centre over the
			# base's centre and its underside on the base's top rim.
			var base_box := node_bounds(model, base)
			var lid_box := node_bounds(model, node)
			var delta := Vector3(base_box.get_center().x - lid_box.get_center().x, base_box.end.y - lid_box.position.y, base_box.get_center().z - lid_box.get_center().z)
			node.position += delta
		elif rule.has("rotation_deg"):
			node.rotation_degrees = rule.rotation_deg as Vector3

# --- grounding ---------------------------------------------------------------

static func _ground(root: Node3D, model: Node3D, flush: bool) -> void:
	var box := visible_bounds(root)
	if box.size == Vector3.ZERO:
		return
	var centre := box.get_center()
	# Footprint centre to the origin, lowest visible point to y = 0. A flush prop
	# (a drain cover) has its top surface at y = 0 instead, so it sits in the paving.
	var lift := -box.position.y
	if flush:
		lift = -box.end.y + 0.004
	model.position += Vector3(-centre.x, lift, -centre.z)

# --- movable parts -------------------------------------------------------------

static func _make_part(root: Node3D, model: Node3D, part_name: String, spec: Dictionary) -> Dictionary:
	var node := find_node(model, str(spec.node))
	if node == null:
		return {}
	var box := node_bounds(root, node)
	var pivot_point := box.position + box.size * (spec.pivot as Vector3)
	var node_xf := relative_transform(root, node)
	var pivot := Node3D.new()
	pivot.name = "Pivot_%s" % part_name
	pivot.position = pivot_point
	root.add_child(pivot)
	node.get_parent().remove_child(node)
	node.owner = null
	pivot.add_child(node)
	node.transform = pivot.transform.affine_inverse() * node_xf
	return {"pivot": pivot, "base": pivot.transform, "axis": spec.get("axis", Vector3.RIGHT) as Vector3,
		"open": float(spec.get("open", 0.0)), "lift": float(spec.get("lift", 0.0)), "slide": spec.get("slide", Vector3.ZERO) as Vector3}

## 0 = closed / rest, 1 = fully open. The motion is a hinge rotation about the
## pivot (plus an optional lift along the axis for threaded caps and a slide).
static func set_part(root: Node3D, part_name: String, amount: float) -> void:
	var parts := root.get_meta("parts", {}) as Dictionary
	if not parts.has(part_name):
		return
	var part := parts[part_name] as Dictionary
	var pivot := part.pivot as Node3D
	if not is_instance_valid(pivot):
		return
	var t := clampf(amount, 0.0, 1.0)
	var base := part.base as Transform3D
	var axis := part.axis as Vector3
	var turned := base.basis * Basis(axis.normalized(), deg_to_rad(float(part.open)) * t)
	var offset := axis.normalized() * float(part.lift) * t + (part.slide as Vector3) * t
	pivot.transform = Transform3D(turned, base.origin + offset)

static func part_amount(root: Node3D, part_name: String) -> float:
	var parts := root.get_meta("parts", {}) as Dictionary
	if not parts.has(part_name):
		return 0.0
	return float((parts[part_name] as Dictionary).get("t", 0.0))

# --- anchors -------------------------------------------------------------------

static func _make_anchor(root: Node3D, model: Node3D, parts: Dictionary, anchor_name: String, spec: Dictionary) -> Marker3D:
	# Parts were moved under their pivots (children of the root), so look through the whole prop.
	var node := find_node(root, str(spec.get("node", ""))) if str(spec.get("node", "")) != "" else model
	if node == null:
		return null
	# A part's anchor is measured in the part's closed pose, then parented to its pivot
	# so it travels with the part when the part opens.
	var box := node_bounds(root, node)
	if box.size == Vector3.ZERO:
		box = visible_bounds(root)
	var fraction := spec.f as Vector3
	var point := box.position + box.size * fraction
	var marker := Marker3D.new()
	marker.name = "Anchor_%s" % anchor_name
	var part_name := str(spec.get("part", ""))
	if part_name != "" and parts.has(part_name):
		var pivot := (parts[part_name] as Dictionary).pivot as Node3D
		pivot.add_child(marker)
		marker.position = pivot.transform.affine_inverse() * point
	else:
		root.add_child(marker)
		marker.position = point
	return marker

static func anchor(root: Node3D, anchor_name: String) -> Node3D:
	var anchors := root.get_meta("anchors", {}) as Dictionary
	return anchors.get(anchor_name) as Node3D

# --- grounding audit -------------------------------------------------------------

## Distance of the prop's lowest visible point from `ground_y`, evaluated on what
## would actually be rendered. 0 means sitting exactly on the ground.
static func ground_gap(root: Node3D, ground_y: float) -> float:
	var box := visible_bounds(root)
	var xf := root.global_transform if root.is_inside_tree() else Transform3D.IDENTITY
	var world_min := (xf * box).position.y
	return world_min - ground_y

# --- baking -----------------------------------------------------------------------------------

const KEEP_LIVE_NODES := ["CradleHandset", "NoticeSheet", "Chamber"]

## Merges every static mesh of a prop into one mesh per material. A bicycle with 24 spokes or a
## vending machine with 30 cans becomes a handful of draw calls instead of hundreds of nodes. Hinged
## parts (anything under a Pivot_*) and nodes the interactions toggle stay as they are.
static func bake_static(root: Node3D) -> void:
	var by_material := {}
	var doomed: Array[Node] = []
	for value in root.find_children("*", "MeshInstance3D", true, false):
		var mesh_node := value as MeshInstance3D
		if mesh_node.mesh == null:
			continue
		if not _bakeable(root, mesh_node):
			continue
		doomed.append(mesh_node)
		if not _is_visible_chain(root, mesh_node):
			continue
		var xf := relative_transform(root, mesh_node)
		for surface in mesh_node.mesh.get_surface_count():
			var material := mesh_node.get_active_material(surface)
			var key := material.get_instance_id() if material != null else 0
			if not by_material.has(key):
				var tool := SurfaceTool.new()
				tool.begin(Mesh.PRIMITIVE_TRIANGLES)
				by_material[key] = {"tool": tool, "material": material}
			((by_material[key] as Dictionary).tool as SurfaceTool).append_from(mesh_node.mesh, surface, xf)
	if doomed.is_empty():
		return
	var baked := ArrayMesh.new()
	for key in by_material:
		var entry := by_material[key] as Dictionary
		var tool := entry.tool as SurfaceTool
		if entry.material != null:
			tool.set_material(entry.material as Material)
		tool.commit(baked)
	for node in doomed:
		node.get_parent().remove_child(node)
		node.queue_free()
	var merged := MeshInstance3D.new()
	merged.name = "Baked"
	merged.mesh = baked
	root.add_child(merged)

static func _bakeable(root: Node3D, node: Node3D) -> bool:
	var current: Node = node
	while current != null and current != root:
		if str(current.name).begins_with("Pivot_") or str(current.name) in KEEP_LIVE_NODES:
			return false
		current = current.get_parent()
	return true

static func _is_visible_chain(root: Node3D, node: Node3D) -> bool:
	var current: Node = node
	while current != null and current != root:
		if current is Node3D and not (current as Node3D).visible:
			return false
		current = current.get_parent()
	return true
