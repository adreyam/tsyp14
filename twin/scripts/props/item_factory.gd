extends RefCounted

## Builds the small things people carry: cups, phones, parcels, clipboards, bags.
## Each item is grounded on its own bottom and carries a `GripPoint` marker (where
## the palm closes) so a hand can be placed on it exactly instead of on its centre.

const Catalog := preload("res://scripts/props/prop_catalog.gd")
const Procedural := preload("res://scripts/props/procedural_props.gd")
const PropFactory := preload("res://scripts/props/prop_factory.gd")

static func build(kind: String) -> Node3D:
	var spec := Catalog.handheld(kind)
	if spec.is_empty():
		push_error("ItemFactory: unknown item '%s'" % kind)
		return null
	var root := Node3D.new()
	root.name = "Item_%s" % kind
	root.set_meta("item_kind", kind)
	var model: Node3D = null
	if spec.has("model"):
		var packed := load(Catalog.polyhaven(str(spec.model))) as PackedScene
		if packed == null:
			return null
		model = packed.instantiate() as Node3D
	else:
		model = Procedural.build_model(str(spec.builder))
	if model == null:
		return null
	model.name = "Model"
	root.add_child(model)
	# Bottom on y = 0, footprint centre at the origin (same rule as every other prop).
	var box := PropFactory.visible_bounds(root)
	if box.size != Vector3.ZERO:
		var centre := box.get_center()
		model.position += Vector3(-centre.x, -box.position.y, -centre.z)
	var grip := Marker3D.new()
	grip.name = "GripPoint"
	grip.position = spec.grip_point as Vector3
	root.add_child(grip)
	root.set_meta("grip_point", spec.grip_point)
	root.set_meta("grip_axis", spec.grip_axis)
	root.set_meta("grip_radius", float(spec.radius))
	root.set_meta("grip_pose", str(spec.pose))
	root.set_meta("grip_roll", float(spec.roll))
	var bounds := PropFactory.visible_bounds(root)
	root.set_meta("item_size", bounds.size)
	return root
