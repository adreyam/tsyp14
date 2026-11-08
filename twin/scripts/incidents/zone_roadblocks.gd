extends Node3D

## Zone lockdowns (earthquake, flood): the bridge closes the four streets into the zone (road_blocks.py: red lights
## into them, cars sent another way, police cars and a fire engine parked across them) and lists them in its state
## ("lockdowns": zone, reason, entrances with position and direction). This node draws a road block at each
## entrance: a red-and-white barrier across the lane, flashing amber lamps, cones and a "ROAD CLOSED" sign.
##
## It also closes the zone itself when an earthquake hits one zone (and reopens it when the damage is cleaned up),
## so a quake triggered from the control-room panel or the device API is locked down even without the Brain.

const GROUND_Y := 0.12
const BARRIER_WIDTH := 3.8
const STRIPES := 8

var manager: Node
var _blocks: Dictionary = {}          # "zone:edge" -> Node3D
var _lamps: Array = []
var _clock := 0.0
var _materials: Dictionary = {}
var _quakes_connected := false


func initialize(incident_manager: Node) -> void:
	manager = incident_manager
	name = "ZoneRoadblocks"
	_connect_earthquakes()


func _connect_earthquakes() -> void:
	## The earthquake system lives beside the incident manager in the city scene (it may be added after us).
	if _quakes_connected or manager == null or manager.get_parent() == null:
		return
	var quakes := manager.get_parent().get_node_or_null("Earthquakes")
	if quakes == null:
		return
	if quakes.has_signal("earthquake_started"):
		quakes.connect("earthquake_started", _on_earthquake)
	if quakes.has_signal("earthquake_cleared"):
		quakes.connect("earthquake_cleared", _on_earthquake_cleared)
	_quakes_connected = true


func lockdown(zone: String, reason: String) -> String:
	if manager == null:
		return ""
	return str(manager.link.call("send", {"action": "lockdown", "zone": zone.to_upper(), "reason": reason}))


func reopen(zone: String, reason: String = "") -> String:
	if manager == null:
		return ""
	return str(manager.link.call("send", {"action": "reopen", "zone": zone.to_upper(), "reason": reason}))


func _on_earthquake(event: Dictionary) -> void:
	var zone := str(event.get("zone", ""))
	if zone.length() == 1:
		lockdown(zone, "earthquake")


func _on_earthquake_cleared(_removed: int, _restored: int) -> void:
	reopen("", "earthquake")


func on_state(state: Dictionary) -> void:
	_connect_earthquakes()
	var wanted := {}
	for record_value in state.get("lockdowns", []):
		var record := record_value as Dictionary
		for entrance_value in record.get("entrances", []):
			var entrance := entrance_value as Dictionary
			var key := "%s:%s" % [str(record.get("zone", "")), str(entrance.get("edge", ""))]
			wanted[key] = true
			if not _blocks.has(key):
				_blocks[key] = _build_block(entrance, str(record.get("zone", "")), str(record.get("reason", "")))
	for key in _blocks.keys():
		if not wanted.has(key):
			var node := _blocks[key] as Node3D
			_blocks.erase(key)
			for lamp in node.find_children("Lamp*", "OmniLight3D", true, false):
				_lamps.erase(lamp)
			node.queue_free()


func _process(delta: float) -> void:
	if _lamps.is_empty():
		return
	_clock += delta
	var on := fmod(_clock, 0.9) < 0.45
	for index in _lamps.size():
		var lamp := _lamps[index] as OmniLight3D
		if is_instance_valid(lamp):
			# the two lamps of a barrier flash in turn
			lamp.light_energy = 2.6 if on == (index % 2 == 0) else 0.0


func _build_block(entrance: Dictionary, zone: String, reason: String) -> Node3D:
	var root := Node3D.new()
	root.name = "RoadBlock_%s_%s" % [zone, str(entrance.get("edge", ""))]
	add_child(root)
	var forward := Vector3(float(entrance.get("dx", 0.0)), 0.0, float(entrance.get("dz", 1.0)))
	if forward.length() < 0.01:
		forward = Vector3.FORWARD
	forward = forward.normalized()
	root.global_transform = Transform3D(Basis.looking_at(forward, Vector3.UP), Vector3(float(entrance.get("x", 0.0)), GROUND_Y, float(entrance.get("z", 0.0))))
	# Barrier beam across the lane, in red and white stripes, on two posts.
	var stripe_width := BARRIER_WIDTH / float(STRIPES)
	for index in STRIPES:
		var colour := Color(0.85, 0.08, 0.06) if index % 2 == 0 else Color(0.95, 0.95, 0.93)
		_box(root, Vector3(stripe_width, 0.22, 0.1), Vector3(-BARRIER_WIDTH / 2.0 + stripe_width * (index + 0.5), 1.0, 0.0), colour, colour.r > 0.9)
	for side in [-1.0, 1.0]:
		_box(root, Vector3(0.12, 1.1, 0.12), Vector3(side * BARRIER_WIDTH / 2.0, 0.55, 0.0), Color(0.2, 0.2, 0.22), false)
		_box(root, Vector3(0.5, 0.06, 0.5), Vector3(side * BARRIER_WIDTH / 2.0, 0.03, 0.0), Color(0.2, 0.2, 0.22), false)
		# flashing amber lamp on top of each post
		var bulb := MeshInstance3D.new()
		var sphere := SphereMesh.new()
		sphere.radius = 0.1
		sphere.height = 0.2
		bulb.mesh = sphere
		bulb.material_override = _material(Color(1.0, 0.62, 0.05), true)
		bulb.position = Vector3(side * BARRIER_WIDTH / 2.0, 1.2, 0.0)
		root.add_child(bulb)
		var lamp := OmniLight3D.new()
		lamp.name = "Lamp%d" % (_lamps.size())
		lamp.light_color = Color(1.0, 0.6, 0.1)
		lamp.omni_range = 7.0
		lamp.light_energy = 2.6
		lamp.position = bulb.position + Vector3(0, 0.15, 0)
		root.add_child(lamp)
		_lamps.append(lamp)
	# Cones in a line on the approach (outside the zone, behind the barrier as seen from the zone).
	for index in 3:
		var cone := Node3D.new()
		root.add_child(cone)
		cone.position = Vector3(-1.2 + index * 1.2, 0.0, 2.2)
		var body := MeshInstance3D.new()
		var mesh := CylinderMesh.new()
		mesh.top_radius = 0.03
		mesh.bottom_radius = 0.2
		mesh.height = 0.6
		body.mesh = mesh
		body.material_override = _material(Color(1.0, 0.33, 0.04), false)
		body.position = Vector3(0, 0.3, 0)
		cone.add_child(body)
		_box(cone, Vector3(0.42, 0.04, 0.42), Vector3(0, 0.02, 0), Color(0.06, 0.06, 0.06), false)
	# Sign
	var label := Label3D.new()
	label.text = "ROAD CLOSED\nZone %s - %s" % [zone, reason.capitalize() if not reason.is_empty() else "Emergency"]
	label.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	label.font_size = 56
	label.pixel_size = 0.01
	label.outline_size = 12
	label.modulate = Color(1.0, 0.92, 0.3)
	label.position = Vector3(0, 2.1, 0)
	root.add_child(label)
	return root


func _box(parent: Node3D, size: Vector3, position_value: Vector3, colour: Color, glow: bool) -> void:
	var box := MeshInstance3D.new()
	var mesh := BoxMesh.new()
	mesh.size = size
	box.mesh = mesh
	box.material_override = _material(colour, glow)
	box.position = position_value
	parent.add_child(box)


func _material(colour: Color, glow: bool) -> StandardMaterial3D:
	var key := "%s:%s" % [colour.to_html(), glow]
	if _materials.has(key):
		return _materials[key] as StandardMaterial3D
	var material := StandardMaterial3D.new()
	material.albedo_color = colour
	material.roughness = 0.55
	if glow:
		material.emission_enabled = true
		material.emission = colour
		material.emission_energy_multiplier = 0.6 if colour.g > 0.9 else 1.6
	_materials[key] = material
	return material
