extends RefCounted

## Props that no CC0 scan exists for (post box, dumpster, ATM, bus stop, bicycle ...)
## are built here at their real-world dimensions with physically based materials.
## Origin = footprint centre on the ground (after PropFactory grounding), +Z = front.
## Hinged parts and contact anchors are registered on the model and picked up by
## PropFactory so a procedural prop behaves exactly like a scanned one.

static var _materials: Dictionary = {}

# --- materials ---------------------------------------------------------------

static func mat(key: String, colour: Color, rough := 0.6, metal := 0.0, emission := 0.0) -> StandardMaterial3D:
	if _materials.has(key):
		return _materials[key] as StandardMaterial3D
	var m := StandardMaterial3D.new()
	m.albedo_color = colour
	m.roughness = rough
	m.metallic = metal
	if emission > 0.0:
		m.emission_enabled = true
		m.emission = colour
		m.emission_energy_multiplier = emission
	_materials[key] = m
	return m

static func _poster_texture(seed_value: int, size := Vector2i(256, 384)) -> ImageTexture:
	var image := Image.create(size.x, size.y, false, Image.FORMAT_RGB8)
	var rng := RandomNumberGenerator.new()
	rng.seed = seed_value
	var background := Color.from_hsv(rng.randf(), 0.25, 0.95)
	image.fill(background)
	var accent := Color.from_hsv(fposmod(background.h + 0.5, 1.0), 0.7, 0.55)
	image.fill_rect(Rect2i(0, 0, size.x, int(size.y * 0.28)), accent)
	for line in 7:
		var y := int(size.y * (0.36 + 0.075 * line))
		var width := int(size.x * rng.randf_range(0.45, 0.88))
		image.fill_rect(Rect2i(int(size.x * 0.08), y, width, int(size.y * 0.03)), Color(0.15, 0.15, 0.18))
	image.fill_rect(Rect2i(int(size.x * 0.08), int(size.y * 0.9), int(size.x * 0.3), int(size.y * 0.05)), accent)
	return ImageTexture.create_from_image(image)

# --- mesh helpers --------------------------------------------------------------

static func box(parent: Node3D, node_name: String, size: Vector3, pos: Vector3, material: Material) -> MeshInstance3D:
	var m := MeshInstance3D.new()
	var mesh := BoxMesh.new()
	mesh.size = size
	m.mesh = mesh
	m.name = node_name
	m.position = pos
	m.material_override = material
	parent.add_child(m)
	return m

static func cyl(parent: Node3D, node_name: String, radius: float, height: float, pos: Vector3, material: Material, top_radius := -1.0, rotation_deg := Vector3.ZERO, sides := 24) -> MeshInstance3D:
	var m := MeshInstance3D.new()
	var mesh := CylinderMesh.new()
	mesh.bottom_radius = radius
	mesh.top_radius = radius if top_radius < 0.0 else top_radius
	mesh.height = height
	mesh.radial_segments = sides
	m.mesh = mesh
	m.name = node_name
	m.position = pos
	m.rotation_degrees = rotation_deg
	m.material_override = material
	parent.add_child(m)
	return m

static func quad(parent: Node3D, node_name: String, size: Vector2, pos: Vector3, material: Material, rotation_deg := Vector3.ZERO) -> MeshInstance3D:
	var m := MeshInstance3D.new()
	var mesh := QuadMesh.new()
	mesh.size = size
	m.mesh = mesh
	m.name = node_name
	m.position = pos
	m.rotation_degrees = rotation_deg
	m.material_override = material
	parent.add_child(m)
	return m

static func tube(parent: Node3D, node_name: String, from: Vector3, to: Vector3, radius: float, material: Material) -> MeshInstance3D:
	var length := from.distance_to(to)
	var m := cyl(parent, node_name, radius, length, (from + to) * 0.5, material, -1.0, Vector3.ZERO, 10)
	var up := (to - from).normalized()
	var axis := Vector3.UP.cross(up)
	if axis.length() > 0.0001:
		m.basis = Basis(axis.normalized(), Vector3.UP.angle_to(up))
	elif up.y < 0.0:
		m.basis = Basis(Vector3.RIGHT, PI)
	return m

static func hinge(model: Node3D, part_name: String, pivot_pos: Vector3, axis: Vector3, open_deg: float) -> Node3D:
	var pivot := Node3D.new()
	pivot.name = "Pivot_%s" % part_name
	pivot.position = pivot_pos
	model.add_child(pivot)
	var parts := model.get_meta("proc_parts", {}) as Dictionary
	parts[part_name] = {"pivot": pivot, "axis": axis, "open": open_deg, "lift": 0.0, "slide": Vector3.ZERO}
	model.set_meta("proc_parts", parts)
	return pivot

static func anchor(model: Node3D, anchor_name: String, pos: Vector3, parent: Node3D = null) -> Marker3D:
	var marker := Marker3D.new()
	marker.name = "Anchor_%s" % anchor_name
	(parent if parent != null else model).add_child(marker)
	marker.position = pos
	var anchors := model.get_meta("proc_anchors", {}) as Dictionary
	anchors[anchor_name] = marker
	model.set_meta("proc_anchors", anchors)
	return marker

## Called by PropFactory after grounding: promotes parts/anchors made by a builder.
static func register_anchors(root: Node3D) -> void:
	var model := root.get_node_or_null("Model") as Node3D
	if model == null:
		return
	var parts := root.get_meta("parts", {}) as Dictionary
	for part_name in (model.get_meta("proc_parts", {}) as Dictionary):
		var spec := (model.get_meta("proc_parts") as Dictionary)[part_name] as Dictionary
		var pivot := spec.pivot as Node3D
		spec["base"] = pivot.transform
		parts[part_name] = spec
	root.set_meta("parts", parts)
	var anchors := root.get_meta("anchors", {}) as Dictionary
	for anchor_name in (model.get_meta("proc_anchors", {}) as Dictionary):
		anchors[anchor_name] = (model.get_meta("proc_anchors") as Dictionary)[anchor_name]
	root.set_meta("anchors", anchors)

# --- dispatch -------------------------------------------------------------------

static func build_model(builder: String) -> Node3D:
	match builder:
		"mailbox": return _mailbox()
		"dumpster": return _dumpster()
		"atm": return _atm()
		"bus_stop": return _bus_stop()
		"bicycle": return _bicycle()
		"cone": return _cone()
		"fire_point": return _fire_point()
		"info_board": return _info_board()
		"poster_column": return _poster_column()
		"meter_pedestal": return _meter_pedestal()
		"phone": return _phone()
		"meter_reader": return _meter_reader()
		"cup": return _cup()
		"envelope": return _envelope()
		"notice_sheet": return _notice_sheet()
		"park_bench": return _park_bench()
		"vending_machine": return _vending_machine()
		"parking_meter": return _parking_meter()
		"drinking_fountain": return _drinking_fountain()
		"payphone": return _payphone()
		"market_counter": return _market_counter()
		"soda_can": return _soda_can()
		"handset": return _handset()
		"coin": return _coin()
		"newspaper": return _newspaper()
	return null

static func decorate(builder: String, root: Node3D, model: Node3D) -> void:
	if builder == "meter_chamber":
		_meter_chamber(root, model)
	elif builder == "aircon_pad":
		_aircon_pad(root, model)

# --- street furniture ---------------------------------------------------------------

static func _mailbox() -> Node3D:
	var m := Node3D.new()
	var blue := mat("post_blue", Color(0.07, 0.17, 0.45), 0.35, 0.55)
	var dark := mat("post_dark", Color(0.05, 0.05, 0.06), 0.5, 0.4)
	for x in [-0.17, 0.17]:
		for z in [-0.19, 0.19]:
			box(m, "Leg", Vector3(0.045, 0.14, 0.045), Vector3(x, 0.07, z), dark)
	box(m, "Body", Vector3(0.46, 0.70, 0.56), Vector3(0, 0.49, 0), blue)
	cyl(m, "ArchedTop", 0.23, 0.56, Vector3(0, 0.84, 0), blue, -1.0, Vector3(90, 0, 0), 28)
	box(m, "Eagle", Vector3(0.22, 0.12, 0.004), Vector3(0, 0.62, 0.282), mat("post_white", Color(0.9, 0.9, 0.92), 0.5))
	# Pull-down collection door on the lower front, hinged at its bottom edge.
	var door_pivot := hinge(m, "door", Vector3(0, 0.22, 0.282), Vector3.RIGHT, 55.0)
	box(door_pivot, "Door", Vector3(0.40, 0.34, 0.018), Vector3(0, 0.17, 0.0), blue)
	box(door_pivot, "DoorHandle", Vector3(0.12, 0.03, 0.03), Vector3(0, 0.30, 0.02), dark)
	anchor(m, "door_handle", Vector3(0, 0.30, 0.03), door_pivot)
	# Top-hinged letter flap above the slot.
	var flap_pivot := hinge(m, "flap", Vector3(0, 0.97, 0.286), Vector3.RIGHT, -55.0)
	box(flap_pivot, "Flap", Vector3(0.32, 0.10, 0.012), Vector3(0, -0.05, 0.0), mat("post_flap", Color(0.1, 0.1, 0.12), 0.4, 0.6))
	anchor(m, "flap_edge", Vector3(0, -0.10, 0.012), flap_pivot)
	anchor(m, "slot", Vector3(0, 0.90, 0.286))
	return m

static func _dumpster() -> Node3D:
	var m := Node3D.new()
	var green := mat("dump_green", Color(0.09, 0.27, 0.16), 0.55, 0.45)
	var plastic := mat("dump_lid", Color(0.07, 0.08, 0.09), 0.7)
	var steel := mat("dump_steel", Color(0.13, 0.13, 0.14), 0.45, 0.8)
	box(m, "Body", Vector3(1.80, 0.95, 0.95), Vector3(0, 0.655, 0), green)
	for x in [-0.78, 0.78]:
		box(m, "Skid", Vector3(0.10, 0.18, 0.80), Vector3(x, 0.09, 0), steel)
	for z in [-0.5, 0.5]:
		box(m, "RimLip", Vector3(1.84, 0.05, 0.05), Vector3(0, 1.16, z), steel)
	for i in 2:
		var x_centre := -0.45 if i == 0 else 0.45
		var pivot := hinge(m, "lid_%d" % i, Vector3(x_centre, 1.18, -0.48), Vector3.RIGHT, -34.0)
		box(pivot, "Lid", Vector3(0.88, 0.04, 0.98), Vector3(0, 0.02, 0.49), plastic)
		anchor(m, "lid_edge_%d" % i, Vector3(0, 0.04, 0.98), pivot)
	anchor(m, "rim", Vector3(0, 1.17, 0.5))
	anchor(m, "drop", Vector3(0, 1.1, 0.0))
	return m

static func _atm() -> Node3D:
	var m := Node3D.new()
	var body := mat("atm_body", Color(0.14, 0.15, 0.18), 0.35, 0.6)
	var trim := mat("atm_trim", Color(0.7, 0.72, 0.75), 0.3, 0.9)
	var screen_off := mat("atm_screen", Color(0.1, 0.45, 0.85), 0.2, 0.0, 1.6)
	var key := mat("atm_key", Color(0.82, 0.84, 0.87), 0.4, 0.7)
	box(m, "Plinth", Vector3(0.70, 0.10, 0.58), Vector3(0, 0.05, 0), mat("atm_plinth", Color(0.1, 0.1, 0.11), 0.7))
	box(m, "Cabinet", Vector3(0.66, 1.50, 0.54), Vector3(0, 0.85, -0.02), body)
	box(m, "Hood", Vector3(0.72, 0.16, 0.62), Vector3(0, 1.67, 0), mat("atm_hood", Color(0.05, 0.28, 0.5), 0.4, 0.4))
	box(m, "FacePanel", Vector3(0.60, 1.0, 0.03), Vector3(0, 1.06, 0.265), mat("atm_face", Color(0.2, 0.21, 0.24), 0.3, 0.7))
	quad(m, "Screen", Vector2(0.34, 0.26), Vector3(0, 1.35, 0.283), screen_off)
	box(m, "ScreenBezel", Vector3(0.40, 0.32, 0.01), Vector3(0, 1.35, 0.276), mat("atm_bezel", Color(0.03, 0.03, 0.04), 0.3))
	var keypad := box(m, "Keypad", Vector3(0.20, 0.15, 0.03), Vector3(0, 1.08, 0.295), trim)
	keypad.rotation_degrees = Vector3(-20, 0, 0)
	for r in 4:
		for c in 3:
			box(keypad, "Key", Vector3(0.04, 0.025, 0.012), Vector3(-0.058 + c * 0.058, 0.052 - r * 0.036, 0.02), key)
	box(m, "CardSlot", Vector3(0.11, 0.025, 0.012), Vector3(0.20, 1.25, 0.28), mat("atm_slot", Color(0.01, 0.01, 0.01), 0.3))
	var tray := hinge(m, "cash_flap", Vector3(0, 0.90, 0.28), Vector3.RIGHT, -55.0)
	box(tray, "CashFlap", Vector3(0.34, 0.075, 0.012), Vector3(0, -0.04, 0), trim)
	box(m, "ReceiptSlot", Vector3(0.12, 0.02, 0.012), Vector3(-0.2, 1.25, 0.28), mat("atm_slot", Color(0.01, 0.01, 0.01), 0.3))
	anchor(m, "keypad", Vector3(0, 1.08, 0.31))
	anchor(m, "screen", Vector3(0, 1.35, 0.30))
	anchor(m, "card_slot", Vector3(0.20, 1.25, 0.29))
	anchor(m, "cash_flap", Vector3(0, -0.075, 0.015), tray)
	anchor(m, "cash_slot", Vector3(0, 0.88, 0.30))
	return m

static func _bus_stop() -> Node3D:
	var m := Node3D.new()
	var steel := mat("stop_steel", Color(0.35, 0.37, 0.4), 0.4, 0.85)
	var yellow := mat("stop_yellow", Color(0.95, 0.72, 0.05), 0.4, 0.2)
	box(m, "Foot", Vector3(0.34, 0.03, 0.34), Vector3(0, 0.015, 0), mat("stop_concrete", Color(0.5, 0.5, 0.5), 0.9))
	cyl(m, "Pole", 0.04, 2.6, Vector3(0, 1.33, 0), steel)
	cyl(m, "SignDisc", 0.20, 0.015, Vector3(0, 2.45, 0.05), yellow, -1.0, Vector3(90, 0, 0), 32)
	box(m, "SignBack", Vector3(0.30, 0.30, 0.01), Vector3(0, 2.45, 0.035), yellow)
	box(m, "CaseBody", Vector3(0.44, 0.62, 0.055), Vector3(0, 1.62, 0.085), mat("stop_case", Color(0.12, 0.13, 0.15), 0.4, 0.5))
	box(m, "Timetable", Vector3(0.38, 0.54, 0.004), Vector3(0, 1.62, 0.114), mat("stop_paper", Color(0.93, 0.93, 0.9), 0.8))
	var pivot := hinge(m, "glass", Vector3(-0.22, 1.62, 0.116), Vector3.UP, -110.0)
	var glass := mat("stop_glass", Color(0.7, 0.85, 0.95, 0.18), 0.05)
	glass.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	box(pivot, "GlassDoor", Vector3(0.43, 0.60, 0.008), Vector3(0.215, 0, 0.004), glass)
	box(pivot, "DoorFrame", Vector3(0.45, 0.025, 0.014), Vector3(0.225, 0.305, 0.004), steel)
	anchor(m, "case_handle", Vector3(0.40, 0, 0.012), pivot)
	anchor(m, "timetable", Vector3(0, 1.62, 0.13))
	anchor(m, "case_front", Vector3(0, 1.62, 0.13))
	return m

static func _bicycle() -> Node3D:
	var m := Node3D.new()
	var frame := mat("bike_frame", Color(0.08, 0.2, 0.55), 0.35, 0.8)
	var black := mat("bike_black", Color(0.03, 0.03, 0.035), 0.6, 0.2)
	var chrome := mat("bike_chrome", Color(0.75, 0.77, 0.8), 0.25, 1.0)
	var r := 0.34
	var wheel_x := [-0.54, 0.54]
	for x in wheel_x:
		var tyre := MeshInstance3D.new()
		var torus := TorusMesh.new()
		torus.inner_radius = r - 0.026
		torus.outer_radius = r
		torus.rings = 36
		torus.ring_segments = 8
		tyre.mesh = torus
		tyre.name = "Tyre"
		tyre.position = Vector3(x, r - 0.013, 0)
		tyre.material_override = black
		# TorusMesh lies in the XZ plane; stand it up so the wheel rolls along X.
		tyre.basis = Basis(Vector3.RIGHT, PI * 0.5)
		m.add_child(tyre)
		for i in 12:
			var angle := TAU * float(i) / 12.0
			var spoke_to := Vector3(x + cos(angle) * (r - 0.03), r - 0.013 + sin(angle) * (r - 0.03), 0)
			tube(m, "Spoke", Vector3(x, r - 0.013, 0), spoke_to, 0.0013, chrome)
		cyl(m, "Hub", 0.02, 0.06, Vector3(x, r - 0.013, 0), chrome, -1.0, Vector3(90, 0, 0), 12)
	var bottom_bracket := Vector3(0.0, 0.30, 0)
	var seat_top := Vector3(-0.17, 0.92, 0)
	var head_top := Vector3(0.40, 0.86, 0)
	var head_bottom := Vector3(0.45, 0.65, 0)
	var rear_hub := Vector3(-0.54, r - 0.013, 0)
	var front_hub := Vector3(0.54, r - 0.013, 0)
	tube(m, "SeatTube", bottom_bracket, seat_top, 0.017, frame)
	tube(m, "TopTube", seat_top + Vector3(0, -0.03, 0), head_top, 0.016, frame)
	tube(m, "DownTube", bottom_bracket, head_bottom, 0.019, frame)
	tube(m, "HeadTube", head_top + Vector3(0.01, 0.03, 0), head_bottom, 0.021, frame)
	for z in [-0.045, 0.045]:
		tube(m, "ChainStay", bottom_bracket + Vector3(0, 0, z), rear_hub + Vector3(0, 0, z), 0.01, frame)
		tube(m, "SeatStay", seat_top + Vector3(0, -0.06, z), rear_hub + Vector3(0, 0, z), 0.009, frame)
		tube(m, "Fork", head_top + Vector3(0.02, -0.02, z * 0.8), front_hub + Vector3(0, 0, z), 0.012, frame)
	tube(m, "SeatPost", seat_top, seat_top + Vector3(-0.02, 0.1, 0), 0.014, chrome)
	box(m, "Saddle", Vector3(0.26, 0.05, 0.15), Vector3(-0.07 - 0.14, 1.04, 0) + Vector3(0.14, 0, 0), black)
	tube(m, "Stem", head_top + Vector3(0.02, 0.03, 0), head_top + Vector3(0.05, 0.11, 0), 0.014, chrome)
	tube(m, "Handlebar", Vector3(0.06, 0.97, -0.21), Vector3(0.06, 0.97, 0.21), 0.012, chrome)
	for z in [-0.21, 0.21]:
		cyl(m, "Grip", 0.017, 0.10, Vector3(0.06, 0.97, z * 1.12), black, -1.0, Vector3(90, 0, 0), 10)
	cyl(m, "Chainring", 0.085, 0.01, bottom_bracket + Vector3(0, 0, 0.05), chrome, -1.0, Vector3(90, 0, 0), 24)
	tube(m, "Crank", bottom_bracket + Vector3(0, 0, 0.07), bottom_bracket + Vector3(0.12, -0.08, 0.07), 0.008, chrome)
	box(m, "Pedal", Vector3(0.09, 0.015, 0.05), bottom_bracket + Vector3(0.12, -0.08, 0.1), black)
	# Inverted-U rack the bicycle is locked to.
	for z in [-0.34, 0.34]:
		tube(m, "RackPost", Vector3(0.05, 0.0, z), Vector3(0.05, 0.78, z), 0.018, chrome)
	tube(m, "RackBar", Vector3(0.05, 0.78, -0.34), Vector3(0.05, 0.78, 0.34), 0.018, chrome)
	anchor(m, "saddle", Vector3(-0.07, 1.07, 0))
	anchor(m, "grip_right", Vector3(0.06, 0.98, 0.25))
	anchor(m, "lock_point", Vector3(0.05, 0.80, 0.0))
	anchor(m, "rack_bar", Vector3(0.05, 0.78, 0.34))
	return m

static func _cone() -> Node3D:
	var m := Node3D.new()
	var orange := mat("cone_orange", Color(1.0, 0.33, 0.04), 0.5)
	var white := mat("cone_white", Color(0.92, 0.92, 0.9), 0.35, 0.0, 0.25)
	box(m, "Base", Vector3(0.36, 0.03, 0.36), Vector3(0, 0.015, 0), mat("cone_base", Color(0.06, 0.06, 0.06), 0.8))
	cyl(m, "Body", 0.145, 0.67, Vector3(0, 0.365, 0), orange, 0.03, Vector3.ZERO, 28)
	cyl(m, "BandLow", 0.10, 0.09, Vector3(0, 0.37, 0), white, 0.085, Vector3.ZERO, 28)
	cyl(m, "BandHigh", 0.075, 0.07, Vector3(0, 0.52, 0), white, 0.062, Vector3.ZERO, 28)
	anchor(m, "top", Vector3(0, 0.68, 0))
	anchor(m, "grip", Vector3(0, 0.60, 0))
	return m

static func _fire_point() -> Node3D:
	var m := Node3D.new()
	var red := mat("fire_red", Color(0.72, 0.04, 0.03), 0.4, 0.3)
	var grey := mat("fire_grey", Color(0.32, 0.34, 0.37), 0.5, 0.7)
	box(m, "Foot", Vector3(0.5, 0.03, 0.34), Vector3(0, 0.015, 0), mat("stop_concrete", Color(0.5, 0.5, 0.5), 0.9))
	box(m, "Post", Vector3(0.11, 1.0, 0.11), Vector3(0, 0.53, -0.05), grey)
	box(m, "Cabinet", Vector3(0.50, 0.70, 0.22), Vector3(0, 0.95, 0.02), red)
	var door := hinge(m, "door", Vector3(-0.25, 0.95, 0.135), Vector3.UP, -60.0)
	var glass := mat("stop_glass", Color(0.7, 0.85, 0.95, 0.2), 0.05)
	glass.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	box(door, "DoorFrame", Vector3(0.50, 0.70, 0.014), Vector3(0.25, 0, 0.0), red)
	box(door, "DoorGlass", Vector3(0.38, 0.54, 0.016), Vector3(0.25, 0, 0.0), glass)
	box(door, "Handle", Vector3(0.03, 0.12, 0.03), Vector3(0.44, 0.0, 0.02), grey)
	cyl(m, "Extinguisher", 0.07, 0.46, Vector3(0, 0.95, 0.00), mat("fire_bottle", Color(0.8, 0.05, 0.04), 0.35, 0.4), -1.0, Vector3.ZERO, 20)
	cyl(m, "Head", 0.03, 0.08, Vector3(0, 1.21, 0.0), grey)
	box(m, "Sign", Vector3(0.30, 0.20, 0.01), Vector3(0, 1.45, -0.04), mat("fire_sign", Color(0.78, 0.04, 0.03), 0.4))
	anchor(m, "door_handle", Vector3(0.44, 0, 0.02), door)
	anchor(m, "panel", Vector3(0, 0.95, 0.15))
	anchor(m, "label", Vector3(0, 1.45, -0.02))
	return m

static func _info_board() -> Node3D:
	var m := Node3D.new()
	var steel := mat("board_steel", Color(0.16, 0.17, 0.19), 0.45, 0.8)
	for x in [-1.1, 1.1]:
		box(m, "Post", Vector3(0.10, 2.3, 0.10), Vector3(x, 1.15, -0.04), steel)
		box(m, "Footing", Vector3(0.36, 0.05, 0.36), Vector3(x, 0.025, -0.04), mat("stop_concrete", Color(0.5, 0.5, 0.5), 0.9))
	box(m, "Frame", Vector3(2.5, 1.25, 0.08), Vector3(0, 1.55, 0), steel)
	var face := StandardMaterial3D.new()
	face.albedo_texture = _poster_texture(7, Vector2i(512, 256))
	face.roughness = 0.5
	quad(m, "Face", Vector2(2.36, 1.11), Vector3(0, 1.55, 0.042), face)
	box(m, "Lamp", Vector3(1.6, 0.04, 0.12), Vector3(0, 2.23, 0.1), steel)
	anchor(m, "board_centre", Vector3(0, 1.55, 0.05))
	anchor(m, "board_left", Vector3(-1.0, 1.4, 0.05))
	anchor(m, "board_right", Vector3(1.0, 1.4, 0.05))
	anchor(m, "mount_left", Vector3(-1.1, 1.1, 0.02))
	anchor(m, "label", Vector3(0.9, 1.05, 0.05))
	return m

static func _poster_column() -> Node3D:
	var m := Node3D.new()
	var iron := mat("column_iron", Color(0.05, 0.14, 0.1), 0.45, 0.7)
	cyl(m, "Plinth", 0.58, 0.18, Vector3(0, 0.09, 0), iron, 0.5, Vector3.ZERO, 32)
	var wrap := StandardMaterial3D.new()
	wrap.albedo_texture = _poster_texture(11, Vector2i(512, 256))
	wrap.roughness = 0.55
	cyl(m, "Drum", 0.46, 1.9, Vector3(0, 1.13, 0), wrap, -1.0, Vector3.ZERO, 40)
	cyl(m, "Cap", 0.52, 0.12, Vector3(0, 2.14, 0), iron, 0.42, Vector3.ZERO, 32)
	var dome := MeshInstance3D.new()
	var sphere := SphereMesh.new()
	sphere.radius = 0.42
	sphere.height = 0.5
	dome.mesh = sphere
	dome.name = "Dome"
	dome.position = Vector3(0, 2.34, 0)
	dome.material_override = iron
	m.add_child(dome)
	var sheet := quad(m, "NoticeSheet", Vector2(0.42, 0.594), Vector3(0.0, 1.35, 0.4635), mat("notice_paper", Color(0.97, 0.96, 0.9), 0.8))
	sheet.visible = false
	anchor(m, "sheet_centre", Vector3(0, 1.35, 0.47))
	anchor(m, "sheet_top", Vector3(0, 1.62, 0.47))
	anchor(m, "sheet_bottom", Vector3(0, 1.08, 0.47))
	return m

static func _meter_pedestal() -> Node3D:
	var m := Node3D.new()
	var grey := mat("ped_grey", Color(0.52, 0.55, 0.58), 0.5, 0.6)
	var dark := mat("ped_dark", Color(0.1, 0.1, 0.11), 0.5, 0.5)
	box(m, "Pad", Vector3(0.62, 0.08, 0.48), Vector3(0, 0.04, 0), mat("stop_concrete", Color(0.5, 0.5, 0.5), 0.9))
	box(m, "Post", Vector3(0.10, 1.05, 0.10), Vector3(0, 0.605, -0.06), dark)
	box(m, "Cabinet", Vector3(0.46, 0.56, 0.20), Vector3(0, 1.28, 0), grey)
	var screen := mat("ped_display", Color(0.2, 1.0, 0.45), 0.2, 0.0, 1.6)
	box(m, "DisplayWell", Vector3(0.20, 0.09, 0.012), Vector3(0, 1.38, 0.104), dark)
	quad(m, "Display", Vector2(0.17, 0.06), Vector3(0, 1.38, 0.112), screen)
	var door := hinge(m, "door", Vector3(-0.23, 1.28, 0.10), Vector3.UP, -110.0)
	box(door, "Door", Vector3(0.46, 0.56, 0.016), Vector3(0.23, 0, 0.0), grey)
	box(door, "Handle", Vector3(0.025, 0.1, 0.03), Vector3(0.40, 0, 0.022), dark)
	anchor(m, "door_handle", Vector3(0.40, 0, 0.03), door)
	anchor(m, "panel", Vector3(0, 1.28, 0.12))
	anchor(m, "display", Vector3(0, 1.38, 0.13))
	anchor(m, "label", Vector3(0.0, 1.17, 0.12))
	return m

static func _meter_chamber(root: Node3D, model: Node3D) -> void:
	## Dark chamber below the lid with a brass water meter, only visible once the
	## cover has been slid off the frame.
	var pit := Node3D.new()
	pit.name = "Chamber"
	model.add_child(pit)
	var dark := mat("pit_dark", Color(0.02, 0.02, 0.02), 0.95)
	var floor_m := cyl(pit, "PitFloor", 0.30, 0.02, Vector3(0, -0.41, 0), dark, -1.0, Vector3.ZERO, 24)
	var wall := cyl(pit, "PitWall", 0.30, 0.41, Vector3(0, -0.21, 0), dark, -1.0, Vector3.ZERO, 24)
	var wall_mat := dark.duplicate() as StandardMaterial3D
	wall_mat.cull_mode = BaseMaterial3D.CULL_FRONT
	wall.material_override = wall_mat
	var brass := mat("meter_brass", Color(0.72, 0.5, 0.2), 0.35, 0.9)
	cyl(pit, "MeterBody", 0.075, 0.09, Vector3(0, -0.16, 0), brass, -1.0, Vector3.ZERO, 20)
	cyl(pit, "MeterDial", 0.062, 0.006, Vector3(0, -0.112, 0), mat("meter_dial", Color(0.92, 0.92, 0.88), 0.4), -1.0, Vector3.ZERO, 20)
	cyl(pit, "PipeIn", 0.022, 0.30, Vector3(-0.17, -0.18, 0), brass, -1.0, Vector3(0, 0, 90), 12)
	cyl(pit, "PipeOut", 0.022, 0.30, Vector3(0.17, -0.18, 0), brass, -1.0, Vector3(0, 0, 90), 12)
	var marker := Marker3D.new()
	marker.name = "Anchor_dial"
	pit.add_child(marker)
	marker.position = Vector3(0, -0.10, 0)
	var anchors := root.get_meta("anchors", {}) as Dictionary
	anchors["dial"] = marker
	root.set_meta("anchors", anchors)

# --- hand-held items (origin = bottom centre, lying/standing as normally carried) ---

static func _phone() -> Node3D:
	var m := Node3D.new()
	box(m, "Body", Vector3(0.072, 0.008, 0.150), Vector3(0, 0.004, 0), mat("phone_body", Color(0.04, 0.04, 0.05), 0.25, 0.6))
	quad(m, "Screen", Vector2(0.066, 0.140), Vector3(0, 0.0082, 0), mat("phone_screen", Color(0.35, 0.65, 1.0), 0.1, 0.0, 1.4), Vector3(-90, 0, 0))
	return m

static func _meter_reader() -> Node3D:
	var m := Node3D.new()
	box(m, "Body", Vector3(0.09, 0.03, 0.19), Vector3(0, 0.015, 0), mat("reader_body", Color(0.95, 0.75, 0.05), 0.5, 0.1))
	quad(m, "Screen", Vector2(0.07, 0.075), Vector3(0, 0.0305, -0.04), mat("reader_screen", Color(0.25, 0.9, 0.5), 0.2, 0.0, 1.3), Vector3(-90, 0, 0))
	box(m, "Keys", Vector3(0.07, 0.006, 0.06), Vector3(0, 0.033, 0.045), mat("reader_keys", Color(0.1, 0.1, 0.11), 0.5))
	return m

static func _cup() -> Node3D:
	var m := Node3D.new()
	cyl(m, "Cup", 0.032, 0.105, Vector3(0, 0.0525, 0), mat("cup_paper", Color(0.95, 0.94, 0.9), 0.7), 0.04, Vector3.ZERO, 24)
	cyl(m, "Sleeve", 0.0345, 0.045, Vector3(0, 0.05, 0), mat("cup_sleeve", Color(0.55, 0.36, 0.2), 0.8), 0.0375, Vector3.ZERO, 24)
	cyl(m, "Lid", 0.042, 0.012, Vector3(0, 0.111, 0), mat("cup_lid", Color(0.12, 0.12, 0.12), 0.5), -1.0, Vector3.ZERO, 24)
	return m

static func _envelope() -> Node3D:
	var m := Node3D.new()
	box(m, "Envelope", Vector3(0.22, 0.003, 0.11), Vector3(0, 0.0015, 0), mat("env_white", Color(0.96, 0.95, 0.9), 0.85))
	return m

static func _notice_sheet() -> Node3D:
	var m := Node3D.new()
	var sheet := StandardMaterial3D.new()
	sheet.albedo_texture = _poster_texture(3, Vector2i(210, 297))
	sheet.roughness = 0.8
	var q := quad(m, "Sheet", Vector2(0.21, 0.297), Vector3(0, 0.0015, 0), sheet, Vector3(-90, 0, 0))
	q.mesh.set("flip_faces", false)
	return m

static func _aircon_pad(root: Node3D, model: Node3D) -> void:
	## The scanned condenser is a bracket-mounted unit. Stood on the ground it gets two
	## concrete plinths under its frame, so its body sits on them at the bracket height.
	var concrete := mat("pad_concrete", Color(0.55, 0.55, 0.53), 0.92)
	var lifted := 0.32
	for x in [-0.28, 0.28]:
		box(root, "Plinth", Vector3(0.12, lifted, 0.34), Vector3(x, lifted * 0.5, 0.0), concrete)

# --- more street furniture ------------------------------------------------------------------

static func _park_bench() -> Node3D:
	## 1.8 m slatted bench: cast-iron ends, seat 45 cm above the ground, back rest to 85 cm.
	var m := Node3D.new()
	var wood := mat("bench_wood", Color(0.46, 0.27, 0.12), 0.7)
	var iron := mat("bench_iron", Color(0.07, 0.08, 0.09), 0.45, 0.8)
	for i in 5:
		box(m, "SeatSlat", Vector3(1.80, 0.04, 0.07), Vector3(0, 0.45, -0.17 + i * 0.085), wood)
	for i in 3:
		var slat := box(m, "BackSlat", Vector3(1.80, 0.09, 0.03), Vector3(0, 0.60 + i * 0.12, -0.235 - i * 0.012), wood)
		slat.rotation_degrees = Vector3(-8.0, 0, 0)
	for x in [-0.82, 0.82]:
		box(m, "LegFront", Vector3(0.05, 0.43, 0.05), Vector3(x, 0.215, 0.17), iron)
		box(m, "LegBack", Vector3(0.05, 0.43, 0.05), Vector3(x, 0.215, -0.22), iron)
		var back_post := box(m, "BackPost", Vector3(0.05, 0.46, 0.04), Vector3(x, 0.66, -0.25), iron)
		back_post.rotation_degrees = Vector3(-8.0, 0, 0)
		box(m, "SeatRail", Vector3(0.04, 0.04, 0.46), Vector3(x, 0.43, -0.03), iron)
		box(m, "ArmRest", Vector3(0.05, 0.03, 0.42), Vector3(x, 0.68, -0.02), iron)
		box(m, "ArmPost", Vector3(0.04, 0.22, 0.04), Vector3(x, 0.56, 0.17), iron)
		box(m, "Foot", Vector3(0.07, 0.015, 0.50), Vector3(x, 0.008, -0.03), iron)
	anchor(m, "seat_front", Vector3(0, 0.45, 0.20))
	anchor(m, "seat_centre", Vector3(0, 0.47, -0.05))
	anchor(m, "back_top", Vector3(0, 0.88, -0.28))
	anchor(m, "arm_rest", Vector3(0.82, 0.70, 0.0))
	anchor(m, "slats", Vector3(0, 0.47, 0.0))
	return m

static func _vending_machine() -> Node3D:
	var m := Node3D.new()
	var red := mat("vend_red", Color(0.72, 0.05, 0.06), 0.35, 0.3)
	var black := mat("vend_black", Color(0.03, 0.03, 0.035), 0.5, 0.3)
	var glass := mat("vend_glass", Color(0.45, 0.6, 0.7, 0.35), 0.05)
	glass.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	box(m, "Plinth", Vector3(0.92, 0.08, 0.78), Vector3(0, 0.04, 0), black)
	box(m, "Cabinet", Vector3(0.94, 1.74, 0.80), Vector3(0, 0.95, 0), red)
	box(m, "Header", Vector3(0.90, 0.20, 0.02), Vector3(0, 1.62, 0.405), mat("vend_header", Color(0.95, 0.95, 0.95), 0.4, 0.0, 0.8))
	box(m, "WindowRecess", Vector3(0.58, 1.20, 0.02), Vector3(-0.12, 1.0, 0.405), black)
	var colours := [Color(0.9, 0.1, 0.1), Color(0.1, 0.4, 0.9), Color(0.95, 0.7, 0.1), Color(0.1, 0.7, 0.3), Color(0.9, 0.4, 0.1)]
	for row in 5:
		for col in 6:
			cyl(m, "Can", 0.032, 0.115, Vector3(-0.32 + col * 0.1, 0.52 + row * 0.22, 0.36), mat("vend_can_%d" % ((row + col) % 5), colours[(row + col) % 5], 0.3, 0.7), -1.0, Vector3.ZERO, 12)
	box(m, "Glass", Vector3(0.58, 1.20, 0.01), Vector3(-0.12, 1.0, 0.418), glass)
	box(m, "Panel", Vector3(0.20, 1.0, 0.02), Vector3(0.34, 1.05, 0.405), mat("vend_panel", Color(0.15, 0.15, 0.17), 0.4, 0.6))
	for r in 4:
		for c in 3:
			box(m, "Button", Vector3(0.035, 0.03, 0.012), Vector3(0.28 + c * 0.055, 1.28 - r * 0.06, 0.42), mat("vend_button", Color(0.85, 0.86, 0.88), 0.4, 0.8))
	box(m, "CoinSlot", Vector3(0.05, 0.1, 0.012), Vector3(0.34, 0.95, 0.42), mat("vend_slot", Color(0.01, 0.01, 0.01), 0.3))
	box(m, "Display", Vector3(0.14, 0.05, 0.012), Vector3(0.34, 1.45, 0.42), mat("vend_display", Color(0.9, 0.2, 0.1), 0.2, 0.0, 1.6))
	box(m, "DispenseWell", Vector3(0.52, 0.22, 0.02), Vector3(-0.12, 0.22, 0.405), black)
	var flap := hinge(m, "flap", Vector3(-0.12, 0.33, 0.415), Vector3.RIGHT, -55.0)
	box(flap, "Flap", Vector3(0.46, 0.20, 0.012), Vector3(0, -0.10, 0), mat("vend_flap", Color(0.12, 0.12, 0.13), 0.4, 0.5))
	anchor(m, "coin_slot", Vector3(0.34, 0.95, 0.43))
	anchor(m, "buttons", Vector3(0.335, 1.16, 0.43))
	anchor(m, "window", Vector3(-0.12, 1.1, 0.43))
	anchor(m, "flap_edge", Vector3(0, -0.20, 0.012), flap)
	anchor(m, "dispense", Vector3(-0.12, 0.22, 0.34))
	return m

static func _parking_meter() -> Node3D:
	var m := Node3D.new()
	var steel := mat("meter_steel", Color(0.28, 0.30, 0.33), 0.4, 0.85)
	box(m, "Base", Vector3(0.20, 0.04, 0.20), Vector3(0, 0.02, 0), steel)
	cyl(m, "Pole", 0.04, 1.15, Vector3(0, 0.6, 0), steel)
	box(m, "Head", Vector3(0.22, 0.34, 0.15), Vector3(0, 1.32, 0), mat("meter_head", Color(0.05, 0.22, 0.45), 0.4, 0.5))
	box(m, "Screen", Vector3(0.14, 0.06, 0.01), Vector3(0, 1.43, 0.08), mat("meter_screen", Color(0.3, 0.9, 0.45), 0.2, 0.0, 1.4))
	box(m, "CoinSlot", Vector3(0.05, 0.012, 0.01), Vector3(0.0, 1.35, 0.08), mat("meter_slot", Color(0.01, 0.01, 0.01), 0.3))
	cyl(m, "Button", 0.018, 0.012, Vector3(0.05, 1.26, 0.08), mat("meter_button", Color(0.1, 0.8, 0.2), 0.3), -1.0, Vector3(90, 0, 0), 14)
	box(m, "TicketTray", Vector3(0.12, 0.03, 0.03), Vector3(0, 1.18, 0.08), mat("meter_tray", Color(0.02, 0.02, 0.02), 0.5))
	anchor(m, "coin_slot", Vector3(0, 1.35, 0.09))
	anchor(m, "button", Vector3(0.05, 1.26, 0.09))
	anchor(m, "screen", Vector3(0, 1.43, 0.09))
	anchor(m, "ticket", Vector3(0, 1.18, 0.10))
	return m

static func _drinking_fountain() -> Node3D:
	var m := Node3D.new()
	var steel := mat("fountain_steel", Color(0.7, 0.72, 0.75), 0.25, 0.95)
	var stone := mat("fountain_stone", Color(0.55, 0.55, 0.52), 0.9)
	box(m, "Pedestal", Vector3(0.30, 0.80, 0.30), Vector3(0, 0.40, 0), stone)
	box(m, "Basin", Vector3(0.46, 0.08, 0.34), Vector3(0, 0.92, 0.02), steel)
	box(m, "Rim", Vector3(0.50, 0.03, 0.38), Vector3(0, 0.97, 0.02), steel)
	tube(m, "Spout", Vector3(0, 1.0, -0.08), Vector3(0, 1.08, 0.04), 0.012, steel)
	cyl(m, "Button", 0.025, 0.02, Vector3(0.20, 0.86, 0.0), mat("fountain_button", Color(0.1, 0.1, 0.12), 0.4, 0.7), -1.0, Vector3(0, 0, 90), 14)
	anchor(m, "button", Vector3(0.215, 0.86, 0.0))
	anchor(m, "spout", Vector3(0, 1.10, 0.05))
	anchor(m, "basin", Vector3(0, 0.98, 0.04))
	return m

static func _payphone() -> Node3D:
	var m := Node3D.new()
	var body := mat("phone_body2", Color(0.12, 0.14, 0.2), 0.4, 0.6)
	var steel := mat("phone_steel", Color(0.7, 0.72, 0.75), 0.3, 0.9)
	box(m, "Base", Vector3(0.40, 0.04, 0.36), Vector3(0, 0.02, 0), mat("stop_concrete", Color(0.5, 0.5, 0.5), 0.9))
	box(m, "Post", Vector3(0.10, 1.0, 0.10), Vector3(0, 0.54, -0.1), body)
	box(m, "Housing", Vector3(0.30, 0.62, 0.20), Vector3(0, 1.25, -0.02), body)
	box(m, "Hood", Vector3(0.34, 0.06, 0.26), Vector3(0, 1.58, -0.02), steel)
	box(m, "Screen", Vector3(0.18, 0.08, 0.01), Vector3(0, 1.42, 0.082), mat("pp_screen", Color(0.2, 0.9, 0.5), 0.2, 0.0, 1.3))
	for r in 4:
		for c in 3:
			box(m, "Key", Vector3(0.035, 0.03, 0.012), Vector3(-0.05 + c * 0.05, 1.28 - r * 0.045, 0.092), steel)
	box(m, "CoinSlot", Vector3(0.05, 0.02, 0.012), Vector3(0.1, 1.40, 0.092), mat("pp_slot", Color(0.01, 0.01, 0.01), 0.3))
	box(m, "Cradle", Vector3(0.26, 0.05, 0.06), Vector3(0, 1.12, 0.09), steel)
	var cradle_handset := _handset()
	cradle_handset.name = "CradleHandset"
	cradle_handset.position = Vector3(0, 1.165, 0.09)
	m.add_child(cradle_handset)
	anchor(m, "keypad", Vector3(0, 1.20, 0.10))
	anchor(m, "coin_slot", Vector3(0.1, 1.40, 0.10))
	anchor(m, "handset", Vector3(0, 1.17, 0.12))
	anchor(m, "screen", Vector3(0, 1.42, 0.10))
	return m

static func _market_counter() -> Node3D:
	var m := Node3D.new()
	var wood := mat("counter_wood", Color(0.42, 0.28, 0.16), 0.6)
	var top := mat("counter_top", Color(0.78, 0.76, 0.72), 0.35)
	box(m, "Body", Vector3(1.40, 0.90, 0.62), Vector3(0, 0.45, 0), wood)
	box(m, "Top", Vector3(1.50, 0.05, 0.70), Vector3(0, 0.925, 0.02), top)
	box(m, "Kick", Vector3(1.36, 0.08, 0.58), Vector3(0, 0.04, 0), mat("counter_kick", Color(0.08, 0.08, 0.09), 0.7))
	var packed := load("res://assets/props/polyhaven/CashRegister_01/CashRegister_01.gltf") as PackedScene
	if packed != null:
		var register := packed.instantiate() as Node3D
		register.name = "Register"
		register.position = Vector3(-0.35, 0.95, -0.02)
		register.rotation.y = PI
		m.add_child(register)
		var drawer := register.find_child("CashRegister_011_drawer", true, false) as Node3D
		if drawer != null:
			var pivot := Node3D.new()
			pivot.name = "Pivot_drawer"
			pivot.position = drawer.position
			register.add_child(pivot)
			var xf := drawer.transform
			drawer.get_parent().remove_child(drawer)
			drawer.owner = null
			pivot.add_child(drawer)
			drawer.transform = Transform3D.IDENTITY
			var parts := m.get_meta("proc_parts", {}) as Dictionary
			parts["drawer"] = {"pivot": pivot, "axis": Vector3.RIGHT, "open": 0.0, "lift": 0.0, "slide": Vector3(0, 0, -0.28)}
			m.set_meta("proc_parts", parts)
	# Goods laid out on the customer side of the counter.
	var crate_m := mat("counter_crate", Color(0.55, 0.4, 0.2), 0.8)
	box(m, "FruitCrate", Vector3(0.40, 0.12, 0.30), Vector3(0.45, 0.99, 0.12), crate_m)
	anchor(m, "register_keys", Vector3(-0.35, 1.12, 0.12))
	anchor(m, "drawer_edge", Vector3(-0.35, 1.0, 0.30))
	anchor(m, "counter_top", Vector3(0.1, 0.96, 0.18))
	anchor(m, "goods", Vector3(0.45, 1.06, 0.12))
	anchor(m, "handover", Vector3(0.1, 1.02, 0.34))
	return m

# --- items --------------------------------------------------------------------------------

static func _soda_can() -> Node3D:
	var m := Node3D.new()
	cyl(m, "Can", 0.033, 0.122, Vector3(0, 0.061, 0), mat("can_red", Color(0.8, 0.08, 0.08), 0.25, 0.8), -1.0, Vector3.ZERO, 20)
	cyl(m, "Top", 0.028, 0.004, Vector3(0, 0.124, 0), mat("can_top", Color(0.75, 0.77, 0.8), 0.25, 0.95), -1.0, Vector3.ZERO, 20)
	return m

static func _handset() -> Node3D:
	var m := Node3D.new()
	var black := mat("handset_black", Color(0.03, 0.03, 0.035), 0.4, 0.3)
	box(m, "Grip", Vector3(0.20, 0.028, 0.030), Vector3(0, 0.014, 0), black)
	box(m, "Ear", Vector3(0.05, 0.04, 0.05), Vector3(-0.10, 0.02, 0), black)
	box(m, "Mouth", Vector3(0.05, 0.04, 0.05), Vector3(0.10, 0.02, 0), black)
	return m

static func _coin() -> Node3D:
	var m := Node3D.new()
	cyl(m, "Coin", 0.012, 0.002, Vector3(0, 0.001, 0), mat("coin_gold", Color(0.85, 0.65, 0.2), 0.3, 0.9), -1.0, Vector3.ZERO, 16)
	return m

static func _newspaper() -> Node3D:
	var m := Node3D.new()
	var sheet := StandardMaterial3D.new()
	sheet.albedo_texture = _poster_texture(21, Vector2i(420, 300))
	sheet.roughness = 0.9
	sheet.cull_mode = BaseMaterial3D.CULL_DISABLED
	quad(m, "Paper", Vector2(0.38, 0.28), Vector3(0, 0.002, 0), sheet, Vector3(-90, 0, 0))
	return m
