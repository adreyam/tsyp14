extends Node3D

## Warning lights for any rendered vehicle, driven by the parent's
## "vehicle_lights" bitmask (published by the bridge, or set by a presenter
## while a unit is in Godot custody). On the procedural fleet the lenses are
## polygon sections of the body (lit by its lamp shader); this rig only adds
## the light they cast on the street, flashing in step with those lenses.
## Other models get lamp boxes built from their real bounds.

const ProceduralVehicle := preload("res://scripts/vehicles/procedural_vehicle.gd")

const LIGHT_BAR := 1
const LIGHT_HAZARD := 2
const LIGHT_SIREN := 4
const LIGHT_AMBER := 8
const LIGHT_WORK := 16

const PALETTES := {
	"police": [Color(1.0, 0.06, 0.04), Color(0.05, 0.25, 1.0)],
	"ambulance": [Color(1.0, 0.06, 0.04), Color(0.95, 0.95, 1.0)],
	"fire": [Color(1.0, 0.06, 0.04), Color(1.0, 0.25, 0.05)],
}

var _vehicle: Node3D
var _class := "car"
var _bar_lamps: Array[MeshInstance3D] = []
var _bar_lights: Array[OmniLight3D] = []
var _amber_lamp: MeshInstance3D
var _amber_light: OmniLight3D
var _hazard_lamps: Array[MeshInstance3D] = []
var _work_lights: Array[SpotLight3D] = []
var _time := 0.0
var _phase_offset := 0.0
var _integrated := false
static var _lamp_materials: Dictionary = {}


func setup(vehicle: Node3D, visual_class: String, local_bounds: AABB) -> void:
	_vehicle = vehicle
	_class = visual_class
	name = "VehicleLightRig"
	_phase_offset = float(vehicle.get_instance_id() % 997) * 0.013
	var body := ProceduralVehicle.body_of(vehicle)
	_integrated = body != null
	if _integrated:
		_phase_offset = float(body.get_instance_shader_parameter(&"lamp_phase"))
	var scale_value := vehicle.scale.abs()
	# Work in metres in the vehicle's yaw frame, independent of model scale.
	scale = Vector3(1.0 / maxf(scale_value.x, 0.001), 1.0 / maxf(scale_value.y, 0.001), 1.0 / maxf(scale_value.z, 0.001))
	var size := local_bounds.size * scale_value
	var centre := local_bounds.get_center() * scale_value
	var roof := local_bounds.end.y * scale_value.y
	var front := local_bounds.end.z * scale_value.z
	var rear := local_bounds.position.z * scale_value.z
	var half_width := size.x * 0.5
	var palette := PALETTES.get(visual_class, PALETTES["police"]) as Array
	if visual_class in ["police", "ambulance", "fire"]:
		# Lightbar across the roof, a little forward of centre.
		var bar_z := centre.z + size.z * (0.12 if visual_class == "police" else 0.30)
		for side in [-1.0, 1.0]:
			if not _integrated:
				_bar_lamps.append(_lamp(Vector3(0.36, 0.10, 0.18), Vector3(side * minf(half_width * 0.42, 0.5), roof + 0.05, bar_z), palette[0 if side < 0.0 else 1]))
			var light := OmniLight3D.new()
			light.light_color = palette[0 if side < 0.0 else 1]
			light.omni_range = 11.0
			light.light_energy = 0.0
			light.shadow_enabled = false
			light.position = Vector3(side * half_width * 0.8, roof + 0.2, bar_z)
			add_child(light)
			_bar_lights.append(light)
	if visual_class in ["tow", "utility", "garbage"]:
		if not _integrated:
			_amber_lamp = _lamp(Vector3(0.26, 0.16, 0.26), Vector3(0.0, roof + 0.08, centre.z + size.z * 0.28), Color(1.0, 0.55, 0.02))
		_amber_light = OmniLight3D.new()
		_amber_light.light_color = Color(1.0, 0.55, 0.05)
		_amber_light.omni_range = 9.0
		_amber_light.light_energy = 0.0
		_amber_light.position = Vector3(0.0, roof + 0.3, centre.z + size.z * 0.28)
		add_child(_amber_light)
	# Hazard lamps at the four corners, at lamp height.
	var lamp_y := local_bounds.position.y * scale_value.y + size.y * 0.42
	for corner in [Vector3(-1, 0, 1), Vector3(1, 0, 1), Vector3(-1, 0, -1), Vector3(1, 0, -1)]:
		if _integrated:
			break
		var z := front - 0.02 if corner.z > 0.0 else rear + 0.02
		_hazard_lamps.append(_lamp(Vector3(0.16, 0.08, 0.05), Vector3(corner.x * (half_width - 0.12), lamp_y, z), Color(1.0, 0.5, 0.0)))
	if visual_class in ["fire", "utility", "tow", "garbage"]:
		for side in [-1.0, 1.0]:
			var spot := SpotLight3D.new()
			spot.light_color = Color(1.0, 0.96, 0.88)
			spot.spot_range = 18.0
			spot.spot_angle = 48.0
			spot.light_energy = 0.0
			spot.shadow_enabled = false
			spot.position = Vector3(side * half_width, roof, centre.z)
			spot.rotation = Vector3(deg_to_rad(-38.0), deg_to_rad(90.0 * side), 0.0)
			add_child(spot)
			_work_lights.append(spot)
	_apply(0)


func _lamp(size: Vector3, position_value: Vector3, colour: Color) -> MeshInstance3D:
	var mesh := BoxMesh.new()
	mesh.size = size
	var lamp := MeshInstance3D.new()
	lamp.mesh = mesh
	lamp.position = position_value
	lamp.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	lamp.material_override = _material(colour, false)
	lamp.set_meta("lamp_colour", colour)
	add_child(lamp)
	return lamp


static func _material(colour: Color, lit: bool) -> StandardMaterial3D:
	var key := "%s|%s" % [colour.to_html(), lit]
	if _lamp_materials.has(key):
		return _lamp_materials[key] as StandardMaterial3D
	var material := StandardMaterial3D.new()
	material.albedo_color = colour.darkened(0.55) if not lit else colour
	material.roughness = 0.25
	if lit:
		material.emission_enabled = true
		material.emission = colour
		material.emission_energy_multiplier = 9.0
	_lamp_materials[key] = material
	return material


func _process(delta: float) -> void:
	if not is_instance_valid(_vehicle):
		return
	_time += delta
	var bits := int(_vehicle.get_meta("vehicle_lights", 0)) if _vehicle.visible else 0
	if _integrated:
		# Presenters change the bits while a unit is in Godot custody.
		ProceduralVehicle.set_lamps(_vehicle, bits, int(_vehicle.get_meta("vehicle_signals", 0)))
	_apply(bits)


func _apply(bits: int) -> void:
	# Integrated lenses flash on the shader clock; cast light follows it.
	var t := (ProceduralVehicle.lamp_clock if _integrated else _time) + _phase_offset
	var bar_on := (bits & LIGHT_BAR) != 0
	for index in _bar_lights.size():
		var lit := false
		if bar_on:
			match _class:
				"police":
					# Alternating left/right double flash.
					var cycle := fposmod(t, 0.8)
					var side_window := cycle < 0.4 if index == 0 else cycle >= 0.4
					lit = side_window and fposmod(cycle, 0.4) < 0.28 and fposmod(cycle, 0.14) < 0.09
				"ambulance":
					lit = fposmod(t + float(index) * 0.25, 0.5) < 0.22
				_:
					lit = fposmod(t + float(index) * 0.35, 0.7) < 0.35
		if index < _bar_lamps.size():
			var lamp := _bar_lamps[index]
			lamp.material_override = _material(lamp.get_meta("lamp_colour") as Color, lit)
		_bar_lights[index].light_energy = 2.6 if lit else 0.0
	if _amber_light != null:
		var amber := (bits & LIGHT_AMBER) != 0 and fposmod(t, 0.9) < 0.45
		if _amber_lamp != null:
			_amber_lamp.material_override = _material(Color(1.0, 0.55, 0.02), amber)
		_amber_light.light_energy = 2.0 if amber else 0.0
	var hazard := (bits & LIGHT_HAZARD) != 0 and fposmod(t, 0.8) < 0.4
	for lamp in _hazard_lamps:
		lamp.material_override = _material(Color(1.0, 0.5, 0.0), hazard)
	var work := (bits & LIGHT_WORK) != 0
	for spot in _work_lights:
		spot.light_energy = 2.2 if work else 0.0
