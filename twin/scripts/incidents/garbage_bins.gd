extends Node3D

## The city's large green four-wheel refuse containers (1100 l), sited by
## tools/compute_garbage_bins.gd at the kerb of their streets. Each container
## is one node whose origin sits on the pavement under its centre, so a crew
## member can wheel it (move the node along the ground) and the truck's lifter
## can tip it (rotate the body about the hopper edge). The lid hangs on a
## hinge at the back and falls open as the body tips past horizontal.

const BINS_PATH := "res://sumo/garbage_bins.json"
const GROUND_Y := 0.17
## Footprint along the kerb x depth, body height above the casters.
const LENGTH := 1.36
const DEPTH := 1.06
const BODY_HEIGHT := 1.12
const CASTER_HEIGHT := 0.2
const BODY_GREEN := Color(0.07, 0.3, 0.13)
const LID_GREEN := Color(0.05, 0.24, 0.1)
## Fully tipped: the open top faces into the truck's hopper mouth, the body
## stays behind the tailgate (which slopes forward above the hopper lip).
const TIP_DEGREES := 115.0
## Height of the hopper lip the lifter raises the container's rim to.
const HOPPER_LIP := 1.82

var bins: Dictionary = {}
static var _shared: Dictionary = {}


func _ready() -> void:
	name = "GarbageBins"
	var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(BINS_PATH)) if FileAccess.file_exists(BINS_PATH) else null
	if not parsed is Dictionary:
		return
	for entry_value in (parsed as Dictionary).get("bins", []):
		var entry := entry_value as Dictionary
		var node := build_bin()
		node.name = str(entry.id).replace("-", "_")
		add_child(node)
		# The container's long side runs along the kerb; its lid hinge (back)
		# is on the pavement side, its handles and lifter comb (+Z) face the road.
		var yaw := float(entry.yaw) - PI * 0.5
		node.global_transform = Transform3D(Basis(Vector3.UP, yaw), Vector3(float(entry.x), GROUND_Y, float(entry.z)))
		bins[str(entry.id)] = {"node": node, "home": node.global_transform, "entry": entry}


func bin_node(bin_id: String) -> Node3D:
	return (bins[bin_id] as Dictionary).node as Node3D if bins.has(bin_id) else null


func home_of(bin_id: String) -> Transform3D:
	return (bins[bin_id] as Dictionary).home as Transform3D if bins.has(bin_id) else Transform3D.IDENTITY


static func set_tip(node: Node3D, amount: float) -> void:
	## amount 0 upright … 1 fully tipped over the hopper (the "Body" pivots
	## about its front-top edge, toward local +Z; the lid swings open).
	var body := node.get_node("Body") as Node3D
	body.rotation.x = deg_to_rad(TIP_DEGREES) * amount
	var lid := body.get_node("LidHinge") as Node3D
	# Gravity opens the lid once the body leans past ~70°.
	lid.rotation.x = -deg_to_rad(150.0) * clampf((amount - 0.5) / 0.4, 0.0, 1.0)


static func build_bin() -> Node3D:
	if _shared.is_empty():
		_shared = _build_shared()
	var root := Node3D.new()
	root.add_to_group("garbage_bins")
	# The whole container (casters included) tips with the "Body" pivot: the
	# front-top edge (+Z face, rim height), where the lifter comb holds it
	# against the hopper.
	var pivot := Node3D.new()
	pivot.name = "Body"
	pivot.position = Vector3(0.0, CASTER_HEIGHT + BODY_HEIGHT, DEPTH * 0.5)
	root.add_child(pivot)
	var base := -pivot.position
	for corner in [Vector2(-1, -1), Vector2(1, -1), Vector2(-1, 1), Vector2(1, 1)]:
		var caster := MeshInstance3D.new()
		caster.mesh = _shared.caster
		caster.material_override = _shared.black
		caster.position = base + Vector3(corner.x * (LENGTH * 0.5 - 0.14), 0.065, corner.y * (DEPTH * 0.5 - 0.14))
		caster.rotation.z = PI * 0.5
		pivot.add_child(caster)
		var fork := MeshInstance3D.new()
		fork.mesh = _shared.fork
		fork.material_override = _shared.steel
		fork.position = base + Vector3(corner.x * (LENGTH * 0.5 - 0.14), 0.15, corner.y * (DEPTH * 0.5 - 0.14))
		pivot.add_child(fork)
	var shell := MeshInstance3D.new()
	shell.name = "Shell"
	shell.mesh = _shared.shell
	shell.material_override = _shared.green
	shell.position = Vector3(0.0, -BODY_HEIGHT, -DEPTH * 0.5)
	pivot.add_child(shell)
	var rim := MeshInstance3D.new()
	rim.mesh = _shared.rim
	rim.material_override = _shared.green
	rim.position = Vector3(0.0, -0.03, -DEPTH * 0.5)
	pivot.add_child(rim)
	for side in [-1.0, 1.0]:
		# Push handles on the road face and the lifter's comb bar.
		var handle := MeshInstance3D.new()
		handle.mesh = _shared.handle
		handle.material_override = _shared.black
		handle.position = Vector3(side * (LENGTH * 0.5 - 0.28), -0.16, 0.05)
		pivot.add_child(handle)
	var comb := MeshInstance3D.new()
	comb.mesh = _shared.comb
	comb.material_override = _shared.steel
	comb.position = Vector3(0.0, -0.34, 0.03)
	pivot.add_child(comb)
	var hinge := Node3D.new()
	hinge.name = "LidHinge"
	hinge.position = Vector3(0.0, 0.0, -DEPTH - 0.02)
	pivot.add_child(hinge)
	var lid := MeshInstance3D.new()
	lid.mesh = _shared.lid
	lid.material_override = _shared.lid_material
	lid.position = Vector3(0.0, 0.035, DEPTH * 0.5 + 0.03)
	hinge.add_child(lid)
	return root


static func _build_shared() -> Dictionary:
	var green := StandardMaterial3D.new()
	green.albedo_color = BODY_GREEN
	green.roughness = 0.55
	green.metallic_specular = 0.35
	var lid_material := StandardMaterial3D.new()
	lid_material.albedo_color = LID_GREEN
	lid_material.roughness = 0.75
	var black := StandardMaterial3D.new()
	black.albedo_color = Color(0.03, 0.03, 0.03)
	black.roughness = 0.8
	var steel := StandardMaterial3D.new()
	steel.albedo_color = Color(0.46, 0.47, 0.48)
	steel.metallic = 0.7
	steel.roughness = 0.45
	var caster := CylinderMesh.new()
	caster.top_radius = 0.065
	caster.bottom_radius = 0.065
	caster.height = 0.05
	var fork := BoxMesh.new()
	fork.size = Vector3(0.09, 0.07, 0.09)
	var handle := CapsuleMesh.new()
	handle.radius = 0.022
	handle.height = 0.34
	var comb := BoxMesh.new()
	comb.size = Vector3(LENGTH * 0.8, 0.06, 0.05)
	var rim := BoxMesh.new()
	rim.size = Vector3(LENGTH + 0.06, 0.07, DEPTH + 0.06)
	var lid := BoxMesh.new()
	lid.size = Vector3(LENGTH + 0.08, 0.05, DEPTH + 0.1)
	return {
		"green": green, "lid_material": lid_material, "black": black, "steel": steel,
		"caster": caster, "fork": fork, "handle": handle, "comb": comb, "rim": rim, "lid": lid,
		"shell": _tapered_shell(),
	}


static func _tapered_shell() -> ArrayMesh:
	## Open-topped tub, narrower at the base (the real container's draft), with
	## a slightly sloped front so it reads as moulded plastic, not a crate.
	var surface := SurfaceTool.new()
	surface.begin(Mesh.PRIMITIVE_TRIANGLES)
	var bottom_half := Vector2(LENGTH * 0.5 - 0.07, DEPTH * 0.5 - 0.08)
	var top_half := Vector2(LENGTH * 0.5, DEPTH * 0.5)
	var b := [Vector3(-bottom_half.x, 0, -bottom_half.y), Vector3(bottom_half.x, 0, -bottom_half.y), Vector3(bottom_half.x, 0, bottom_half.y), Vector3(-bottom_half.x, 0, bottom_half.y)]
	var t := [Vector3(-top_half.x, BODY_HEIGHT, -top_half.y), Vector3(top_half.x, BODY_HEIGHT, -top_half.y), Vector3(top_half.x, BODY_HEIGHT, top_half.y), Vector3(-top_half.x, BODY_HEIGHT, top_half.y)]
	for index in 4:
		var next := (index + 1) % 4
		_quad(surface, b[index], b[next], t[next], t[index])
	_quad(surface, b[3], b[2], b[1], b[0])
	surface.generate_normals()
	return surface.commit()


static func _quad(surface: SurfaceTool, a: Vector3, b: Vector3, c: Vector3, d: Vector3) -> void:
	# Clockwise seen from outside: Godot's front faces, so the outer walls
	# are the ones drawn (the open tub never shows its inside through them).
	for point in [a, b, c, a, c, d]:
		surface.add_vertex(point)
