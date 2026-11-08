extends RefCounted
## One consistent, procedurally modelled vehicle family for every SUMO class.
##
## Each body is a loft of smooth cross-sections along the car (bumpers, hood,
## glasshouse, roof and real wheel arches), closed by front and rear faces.
## Every polygon is assigned to one of five shared finishes: paint, glass,
## trim, bright metal and lamps. Head-, tail-, brake-, indicator-, reversing
## and beacon lenses are polygon sections of the body itself, cut on the
## loft's own vertex rows so their edges are crisp; they are lit by
## per-instance shader parameters (see set_lamps), never by extra nodes.
##
## Models are authored at the exact SUMO class length and width (mirrors
## included), so the class fit leaves them at scale 1 and the wheels stay
## round while rolling. Wheels are separate meshes whose vertices sit at their
## car-space hub, the layout CityLife's wheel-pivot rig expects.

const BODY_SHADER := preload("res://shaders/vehicle_body.gdshader")
const LAMP_SHADER := preload("res://shaders/vehicle_lamp.gdshader")
const WHEEL_SHADER := preload("res://shaders/vehicle_wheel.gdshader")

enum { PAINT, GLASS, TRIM, CHROME, LAMP }
const SURFACE_COUNT := 5

const LAMP_HEAD := 0
const LAMP_TAIL := 1
# Indicator lenses by the car's own frame: L = +X side, R = -X side.
const LAMP_INDICATOR_FL := 2
const LAMP_INDICATOR_FR := 3
const LAMP_INDICATOR_RL := 4
const LAMP_INDICATOR_RR := 5
const LAMP_REVERSE := 6
const LAMP_BEACON_A := 7
const LAMP_BEACON_B := 8
const LAMP_BEACON_AMBER := 9
const LAMP_PLATE := 10
const LAMP_TAXI_SIGN := 11

# Warning-light bits shared with the bridge and VehicleLightRig.
const LIGHT_BAR := 1
const LIGHT_HAZARD := 2
const LIGHT_AMBER := 8

# SUMO vehicle signal bits (TraCI getSignals).
const SIGNAL_BLINKER_RIGHT := 1
const SIGNAL_BLINKER_LEFT := 2
const SIGNAL_BLINKER_EMERGENCY := 4
const SIGNAL_BRAKELIGHT := 8
const SIGNAL_BACKDRIVE := 128

const MIRROR_REACH := 0.14
# Section rows a side door spans: bumper-line sill up to the window top.
const DOOR_ROW_FIRST := 2
const DOOR_ROW_LAST := 8
const DOOR_OPEN_DEGREES := 64.0
const DOOR_SECONDS := 0.55
# A seated occupant's root sits this far below the road-level stance.
const SEATED_DROP := 0.5

# Realistic paint mix for private cars: [colour, metallic].
const CIVILIAN_PAINTS := [
	[Color(0.92, 0.92, 0.9), 0.0], [Color(0.93, 0.93, 0.92), 0.0], [Color(0.9, 0.9, 0.88), 0.0],
	[Color(0.035, 0.037, 0.04), 0.6], [Color(0.03, 0.03, 0.032), 0.0],
	[Color(0.28, 0.29, 0.3), 0.75], [Color(0.42, 0.43, 0.45), 0.8],
	[Color(0.66, 0.67, 0.69), 0.85], [Color(0.6, 0.61, 0.62), 0.85],
	[Color(0.06, 0.13, 0.3), 0.7], [Color(0.13, 0.22, 0.38), 0.6],
	[Color(0.5, 0.04, 0.04), 0.55], [Color(0.62, 0.08, 0.05), 0.0],
	[Color(0.09, 0.17, 0.13), 0.6], [Color(0.44, 0.38, 0.3), 0.6],
]
const CIVILIAN_STYLES := ["sedan", "hatchback", "suv", "wagon", "sedan", "coupe", "suv", "hatchback"]

## Lamp flash clock shared by the lamp shader and the warning-light rig.
static var lamp_clock := 0.0
static var _materials: Array[Material] = []
static var _wheel_material: ShaderMaterial
static var _cache: Dictionary = {}


# --- Styles -----------------------------------------------------------------

static func _style(name: String, length: float, width: float) -> Dictionary:
	## Profiles are [u, value] control points along the car, u = -1 at the rear
	## bumper and +1 at the front; heights are metres above the road.
	var s := {
		"name": name, "length": length, "width": width,
		"wheel_radius": 0.335, "wheel_width": 0.225, "axles": [0.58, -0.58],
		"bottom": [[-1.0, 0.34], [-0.86, 0.23], [0.86, 0.23], [1.0, 0.32]],
		"plan": [[-1.0, 0.84], [-0.97, 0.95], [-0.9, 0.99], [-0.75, 1.0], [0.75, 1.0], [0.9, 0.985], [0.97, 0.94], [1.0, 0.82]],
		"belt": [], "roof": [],
		"crown": 0.03, "glass_inset": 0.07, "tumble": 0.22, "roof_round": 0.07,
		"windshield": [0.07, 0.385], "rear_window": [-0.64, -0.39],
		"side_glass": [[-0.36, -0.03], [0.0, 0.3]], "blackout_pillars": true,
		"head_band": [0.6, 0.72], "head_wrap": 0.05, "head_x": 0.5, "grille_x": 0.36,
		"tail_band": [0.76, 0.9], "tail_wrap": 0.04, "tail_x": 0.55,
		"front_glass_cap": false, "rear_glass_cap": false, "cladding": false,
		"doors": [0.33, 0.0, -0.37], "handles": [0.27, -0.06], "mirror_u": 0.3,
		"deck_u": -2.0, "livery": "", "livery_doors": [-0.37, 0.33],
		"accessories": [], "bus_doors": [],
	}
	match name:
		"sedan", "police", "taxi":
			s.belt = [[-1.0, 0.94], [-0.975, 0.99], [-0.88, 1.02], [-0.6, 1.03], [0.0, 0.99], [0.36, 0.94], [0.85, 0.85], [0.97, 0.8], [1.0, 0.75]]
			s.roof = [[-1.0, 0.96], [-0.975, 1.01], [-0.88, 1.04], [-0.66, 1.06], [-0.4, 1.4], [-0.25, 1.44], [0.0, 1.45], [0.08, 1.42], [0.385, 0.98], [0.85, 0.87], [0.97, 0.82], [1.0, 0.77]]
		"hatchback":
			s.belt = [[-1.0, 0.8], [-0.97, 0.96], [-0.9, 1.0], [-0.5, 1.0], [0.0, 0.97], [0.36, 0.92], [0.85, 0.83], [0.97, 0.76], [1.0, 0.66]]
			s.roof = [[-1.0, 0.82], [-0.97, 1.0], [-0.92, 1.12], [-0.82, 1.42], [-0.7, 1.48], [0.0, 1.49], [0.08, 1.46], [0.385, 0.96], [0.85, 0.85], [0.97, 0.78], [1.0, 0.68]]
			s.rear_window = [-0.92, -0.71]
			s.side_glass = [[-0.62, -0.03], [0.0, 0.3]]
			s.doors = [0.33, 0.0, -0.4]
			s.tail_band = [0.86, 0.98]
		"wagon":
			s.belt = [[-1.0, 0.8], [-0.97, 0.96], [-0.9, 1.01], [-0.5, 1.02], [0.0, 0.98], [0.36, 0.93], [0.85, 0.83], [0.97, 0.76], [1.0, 0.66]]
			s.roof = [[-1.0, 0.82], [-0.975, 1.05], [-0.95, 1.38], [-0.88, 1.46], [0.0, 1.47], [0.08, 1.44], [0.385, 0.97], [0.85, 0.85], [0.97, 0.78], [1.0, 0.68]]
			s.rear_window = [-0.975, -0.9]
			s.side_glass = [[-0.86, -0.4], [-0.37, -0.03], [0.0, 0.3]]
			s.tail_band = [0.84, 0.96]
		"coupe":
			s.belt = [[-1.0, 0.76], [-0.97, 0.9], [-0.88, 0.97], [-0.6, 0.99], [0.0, 0.95], [0.3, 0.9], [0.85, 0.8], [0.97, 0.73], [1.0, 0.63]]
			s.roof = [[-1.0, 0.78], [-0.97, 0.92], [-0.88, 0.99], [-0.7, 1.02], [-0.42, 1.3], [-0.15, 1.36], [0.0, 1.36], [0.05, 1.33], [0.34, 0.93], [0.85, 0.82], [0.97, 0.75], [1.0, 0.65]]
			s.windshield = [0.04, 0.345]
			s.rear_window = [-0.7, -0.4]
			s.side_glass = [[-0.38, 0.27]]
			s.doors = [0.31, -0.2]
			s.handles = [-0.26]
			s.mirror_u = 0.27
			s.bottom = [[-1.0, 0.3], [-0.86, 0.2], [0.86, 0.2], [1.0, 0.28]]
			s.head_band = [0.61, 0.72]
			s.tail_band = [0.76, 0.88]
		"suv":
			s.wheel_radius = 0.38
			s.wheel_width = 0.245
			s.bottom = [[-1.0, 0.44], [-0.86, 0.33], [0.86, 0.33], [1.0, 0.42]]
			s.belt = [[-1.0, 0.92], [-0.97, 1.08], [-0.9, 1.12], [-0.5, 1.13], [0.0, 1.1], [0.36, 1.06], [0.85, 0.99], [0.97, 0.92], [1.0, 0.8]]
			s.roof = [[-1.0, 0.94], [-0.975, 1.16], [-0.95, 1.6], [-0.88, 1.69], [0.0, 1.71], [0.08, 1.68], [0.37, 1.1], [0.85, 1.01], [0.97, 0.94], [1.0, 0.82]]
			s.windshield = [0.07, 0.37]
			s.rear_window = [-0.975, -0.9]
			s.side_glass = [[-0.86, -0.42], [-0.39, -0.03], [0.0, 0.3]]
			s.head_band = [0.8, 0.93]
			s.tail_band = [0.98, 1.1]
			s.cladding = true
			s.tumble = 0.16
		"ambulance":
			s.wheel_radius = 0.4
			s.wheel_width = 0.25
			s.axles = [0.56, -0.5]
			s.bottom = [[-1.0, 0.5], [-0.9, 0.4], [0.88, 0.4], [1.0, 0.48]]
			s.belt = [[-1.0, 1.0], [-0.985, 1.15], [0.5, 1.15], [0.62, 1.12], [0.9, 1.06], [0.98, 0.98], [1.0, 0.9]]
			s.roof = [[-1.0, 1.02], [-0.99, 2.55], [-0.97, 2.62], [0.33, 2.62], [0.35, 2.36], [0.42, 2.33], [0.66, 1.2], [0.9, 1.1], [0.98, 1.0], [1.0, 0.92]]
			s.plan = [[-1.0, 0.96], [-0.985, 1.0], [0.9, 1.0], [0.97, 0.96], [1.0, 0.86]]
			s.windshield = [0.43, 0.66]
			s.rear_window = [-2.0, -1.9]
			s.side_glass = [[0.36, 0.6]]
			s.blackout_pillars = false
			s.tumble = 0.03
			s.glass_inset = 0.04
			s.roof_round = 0.05
			s.head_band = [0.86, 0.98]
			s.tail_band = [1.2, 1.5]
			s.tail_wrap = 0.02
			s.doors = [0.62, 0.35, -0.2]
			s.handles = [0.4]
			s.mirror_u = 0.6
			s.livery = "ambulance"
			s.accessories = ["lightbar"]
			s.lightbar_u = 0.22
		"fire":
			s.wheel_radius = 0.5
			s.wheel_width = 0.32
			s.axles = [0.62, -0.5]
			s.bottom = [[-1.0, 0.62], [-0.92, 0.5], [0.92, 0.5], [1.0, 0.6]]
			s.belt = [[-1.0, 1.2], [-0.99, 1.4], [0.99, 1.4], [1.0, 1.3]]
			s.roof = [[-1.0, 1.25], [-0.995, 3.05], [-0.98, 3.1], [0.47, 3.1], [0.5, 2.92], [0.97, 2.9], [0.995, 2.82], [1.0, 2.6]]
			s.plan = [[-1.0, 0.97], [-0.99, 1.0], [0.97, 1.0], [0.995, 0.97], [1.0, 0.9]]
			s.windshield = [2.0, 2.1]
			s.rear_window = [-2.0, -1.9]
			s.side_glass = [[0.62, 0.95]]
			s.front_glass_cap = true
			s.blackout_pillars = false
			s.tumble = 0.02
			s.glass_inset = 0.03
			s.roof_round = 0.06
			s.head_band = [0.78, 0.92]
			s.head_x = 0.62
			s.grille_x = 0.5
			s.tail_band = [1.0, 1.25]
			s.tail_wrap = 0.015
			s.doors = [0.97, 0.6, 0.45, 0.25, 0.05, -0.15, -0.35, -0.75]
			s.handles = [0.65]
			s.mirror_u = 0.95
			s.livery = "fire"
			s.livery_doors = [-2.0, -1.9]
			s.accessories = ["lightbar", "ladder"]
			s.lightbar_u = 0.82
		"tow":
			s.wheel_radius = 0.46
			s.wheel_width = 0.3
			s.axles = [0.62, -0.42]
			s.bottom = [[-1.0, 0.6], [-0.9, 0.5], [0.92, 0.5], [1.0, 0.58]]
			s.belt = [[-1.0, 1.1], [-0.99, 1.22], [0.36, 1.22], [0.37, 1.45], [0.97, 1.45], [0.995, 1.36], [1.0, 1.25]]
			s.roof = [[-1.0, 1.12], [-0.99, 1.25], [0.33, 1.25], [0.345, 2.52], [0.37, 2.58], [0.6, 2.58], [0.66, 2.48], [0.8, 1.6], [0.97, 1.52], [0.995, 1.42], [1.0, 1.27]]
			s.plan = [[-1.0, 0.97], [-0.99, 1.0], [0.97, 1.0], [0.995, 0.96], [1.0, 0.88]]
			s.windshield = [0.62, 0.8]
			s.rear_window = [0.34, 0.37]
			s.side_glass = [[0.38, 0.62]]
			s.blackout_pillars = false
			s.tumble = 0.05
			s.glass_inset = 0.04
			s.roof_round = 0.06
			s.head_band = [1.06, 1.2]
			s.tail_band = [0.82, 1.0]
			s.tail_wrap = 0.015
			s.doors = [0.63, 0.38]
			s.handles = [0.43]
			s.mirror_u = 0.63
			s.deck_u = 0.34
			s.livery = "service"
			s.accessories = ["amber_beacon", "tow_boom"]
			s.beacon_u = 0.47
		"utility":
			s.wheel_radius = 0.42
			s.wheel_width = 0.27
			s.axles = [0.6, -0.5]
			s.bottom = [[-1.0, 0.56], [-0.9, 0.46], [0.9, 0.46], [1.0, 0.54]]
			s.belt = [[-1.0, 1.05], [-0.99, 1.2], [0.36, 1.2], [0.4, 1.3], [0.9, 1.25], [0.98, 1.15], [1.0, 1.05]]
			s.roof = [[-1.0, 1.07], [-0.99, 2.72], [-0.97, 2.78], [0.34, 2.78], [0.36, 2.42], [0.42, 2.4], [0.66, 1.32], [0.9, 1.27], [0.98, 1.17], [1.0, 1.07]]
			s.plan = [[-1.0, 0.97], [-0.99, 1.0], [0.92, 1.0], [0.98, 0.96], [1.0, 0.87]]
			s.windshield = [0.43, 0.66]
			s.rear_window = [-2.0, -1.9]
			s.side_glass = [[0.42, 0.62]]
			s.blackout_pillars = false
			s.tumble = 0.03
			s.glass_inset = 0.04
			s.roof_round = 0.05
			s.head_band = [0.96, 1.08]
			s.tail_band = [1.2, 1.5]
			s.tail_wrap = 0.015
			s.doors = [0.66, 0.38, 0.0, -0.45]
			s.handles = [0.44]
			s.mirror_u = 0.64
			s.livery = "service"
			s.accessories = ["amber_beacon"]
			s.beacon_u = 0.2
		"garbage":
			# Rear-loader refuse truck: cab-forward crew cab, tall packer body,
			# sloped tailgate hopper at the back where the bins are tipped.
			s.wheel_radius = 0.5
			s.wheel_width = 0.32
			s.axles = [0.64, -0.46]
			s.bottom = [[-1.0, 0.72], [-0.94, 0.52], [0.92, 0.5], [1.0, 0.6]]
			s.belt = [[-1.0, 1.15], [-0.99, 1.35], [0.99, 1.35], [1.0, 1.25]]
			# Vertical tailgate up to the hopper lip, then sloping forward so a
			# tipped container clears it.
			s.roof = [[-1.0, 1.0], [-0.997, 1.82], [-0.84, 3.12], [0.52, 3.12], [0.535, 2.62], [0.6, 2.6], [0.63, 2.72], [0.96, 2.72], [0.99, 2.62], [1.0, 2.4]]
			s.plan = [[-1.0, 0.95], [-0.98, 1.0], [0.97, 1.0], [0.995, 0.97], [1.0, 0.9]]
			s.windshield = [2.0, 2.1]
			s.rear_window = [-2.0, -1.9]
			s.side_glass = [[0.66, 0.95]]
			s.front_glass_cap = true
			s.blackout_pillars = false
			s.tumble = 0.02
			s.glass_inset = 0.03
			s.roof_round = 0.05
			s.head_band = [0.78, 0.92]
			s.head_x = 0.62
			s.grille_x = 0.5
			s.tail_band = [1.05, 1.3]
			s.tail_wrap = 0.015
			s.doors = [0.97, 0.8, 0.79, 0.62]
			s.handles = [0.66]
			s.mirror_u = 0.95
			s.livery = "refuse"
			s.accessories = ["amber_beacon", "rear_hopper"]
			s.beacon_u = 0.8
		"bus":
			s.wheel_radius = 0.5
			s.wheel_width = 0.3
			s.axles = [0.62, -0.5]
			s.bottom = [[-1.0, 0.5], [-0.97, 0.4], [0.97, 0.4], [1.0, 0.48]]
			s.belt = [[-1.0, 1.0], [-0.995, 1.16], [0.995, 1.16], [1.0, 1.0]]
			s.roof = [[-1.0, 1.02], [-0.995, 2.9], [-0.985, 2.98], [0.985, 2.98], [0.995, 2.9], [1.0, 1.02]]
			s.plan = [[-1.0, 0.96], [-0.995, 1.0], [0.995, 1.0], [1.0, 0.96]]
			s.windshield = [2.0, 2.1]
			s.rear_window = [-2.0, -1.9]
			s.side_glass = [[-0.96, -0.66], [-0.63, -0.33], [-0.3, 0.0], [0.03, 0.33], [0.36, 0.7], [0.73, 0.97]]
			s.front_glass_cap = true
			s.rear_glass_cap = true
			s.blackout_pillars = true
			s.tumble = 0.01
			s.glass_inset = 0.02
			s.roof_round = 0.1
			s.head_band = [0.68, 0.82]
			s.head_x = 0.62
			s.grille_x = 0.45
			s.tail_band = [0.75, 0.95]
			s.tail_wrap = 0.01
			s.doors = []
			s.handles = []
			s.mirror_u = 0.96
			s.bus_doors = [[0.76, 0.9], [-0.12, 0.02]]
			s.livery = "bus"
	if name == "police":
		s.livery = "police"
		s.accessories = ["lightbar"]
		s.lightbar_u = -0.1
	elif name == "taxi":
		s.accessories = ["taxi_sign"]
	return s


static func style_for(visual_class: String, variant: int) -> String:
	match visual_class:
		"car":
			return str(CIVILIAN_STYLES[posmod(variant, CIVILIAN_STYLES.size())])
		"police", "taxi", "ambulance", "fire", "tow", "utility", "garbage", "bus":
			return visual_class
	return "sedan"


static func paint_for(visual_class: String, variant: int) -> Dictionary:
	match visual_class:
		"police":
			return {"paint": Color(0.03, 0.032, 0.036), "accent": Color(0.93, 0.93, 0.92), "metallic": 0.3}
		"taxi":
			return {"paint": Color(0.98, 0.72, 0.05), "accent": Color(0.03, 0.03, 0.03), "metallic": 0.0}
		"ambulance":
			return {"paint": Color(0.94, 0.94, 0.92), "accent": Color(0.78, 0.05, 0.05), "metallic": 0.0}
		"fire":
			return {"paint": Color(0.68, 0.04, 0.03), "accent": Color(0.94, 0.94, 0.9), "metallic": 0.15}
		"tow", "utility":
			return {"paint": Color(0.93, 0.93, 0.9), "accent": Color(0.98, 0.45, 0.05), "metallic": 0.0}
		"garbage":
			return {"paint": Color(0.92, 0.93, 0.9), "accent": Color(0.13, 0.4, 0.19), "metallic": 0.0}
		"bus":
			return {"paint": Color(0.1, 0.34, 0.62), "accent": Color(0.94, 0.94, 0.92), "metallic": 0.2}
	# A hash spreads neighbouring pool indices across the whole palette.
	var pick := CIVILIAN_PAINTS[posmod(variant * 7 + 3, CIVILIAN_PAINTS.size())] as Array
	return {"paint": pick[0], "accent": pick[0], "metallic": float(pick[1])}


# --- Public API -------------------------------------------------------------

static func build(visual_class: String, class_spec: Dictionary, variant: int) -> Node3D:
	## A complete vehicle (Body + four wheels) at the class's exact size.
	var style_name := style_for(visual_class, variant)
	var length := float(class_spec.get("length", 4.7))
	var width := float(class_spec.get("width", 2.1))
	# Private cars keep a realistic body width; service classes fill their
	# SUMO footprint (mirrors included) so the class fit stays at scale 1.
	var body_width := 1.84 if style_name in ["sedan", "taxi", "hatchback", "coupe"] else (1.94 if style_name in ["suv", "wagon"] else width - MIRROR_REACH * 2.0)
	if style_name == "wagon":
		body_width = 1.86
	var key := "%s|%.2f|%.2f|%.2f" % [style_name, length, body_width, width]
	if not _cache.has(key):
		var style := _style(style_name, length, body_width)
		# Mirrors end exactly at the SUMO footprint, never beyond it.
		style.mirror_half = minf(width * 0.5, body_width * 0.5 + MIRROR_REACH)
		_cache[key] = _build_meshes(style)
	var meshes := _cache[key] as Dictionary
	var root := Node3D.new()
	root.name = "ProceduralVehicle"
	root.set_meta("procedural_key", key)
	var body := MeshInstance3D.new()
	body.name = "Body"
	body.mesh = meshes.body
	root.add_child(body)
	for index in (meshes.wheels as Array).size():
		var wheel := MeshInstance3D.new()
		wheel.name = "Wheel_%s" % ["FL", "FR", "RL", "RR", "ML", "MR"][index]
		wheel.mesh = (meshes.wheels as Array)[index]
		root.add_child(wheel)
	var paint := paint_for(visual_class, variant)
	body.set_instance_shader_parameter(&"paint_color", paint.paint)
	body.set_instance_shader_parameter(&"accent_color", paint.accent)
	body.set_instance_shader_parameter(&"paint_metallic", paint.metallic)
	body.set_instance_shader_parameter(&"lamp_phase", float(variant % 97) * 0.137)
	match visual_class:
		"ambulance":
			body.set_instance_shader_parameter(&"beacon_pattern", 1.0)
			body.set_instance_shader_parameter(&"beacon_b", Color(0.95, 0.95, 1.0))
		"fire":
			body.set_instance_shader_parameter(&"beacon_pattern", 2.0)
			body.set_instance_shader_parameter(&"beacon_b", Color(1.0, 0.25, 0.05))
	root.set_meta("procedural_style", style_name)
	return root


static func install(vehicle: Node3D, visual_class: String, class_spec: Dictionary, variant: int) -> void:
	## Replaces an authored vehicle's imported model with the procedural one,
	## keeping the node itself (name, groups, metadata, transform).
	for child in vehicle.get_children():
		if child is Node3D:
			vehicle.remove_child(child)
			child.queue_free()
	var model := build(visual_class, class_spec, variant)
	for part in model.get_children():
		model.remove_child(part)
		vehicle.add_child(part)
	vehicle.set_meta("procedural_style", model.get_meta("procedural_style"))
	vehicle.set_meta("procedural_key", model.get_meta("procedural_key"))
	vehicle.set_meta("procedural_vehicle", true)
	model.free()


static func body_of(vehicle: Node3D) -> MeshInstance3D:
	return vehicle.get_node_or_null("Body") as MeshInstance3D if vehicle.has_meta("procedural_vehicle") else null


static func set_lamps(vehicle: Node3D, warning_bits: int, sumo_signals: int) -> void:
	## Applies warning-light bits and SUMO signal bits to the integrated lamps.
	## Parameters are only written when they change.
	var body := body_of(vehicle)
	if body == null:
		return
	var key := warning_bits | (sumo_signals << 8)
	if int(vehicle.get_meta("lamp_key", -1)) == key:
		return
	vehicle.set_meta("lamp_key", key)
	var brake := 1.0 if sumo_signals & SIGNAL_BRAKELIGHT else 0.0
	# SUMO's y axis maps to Godot +Z, which mirrors the network: a SUMO left
	# turn is rendered as a turn toward the car's -X side (the driver's right
	# when facing +Z). Indicators flash on the side the car visibly turns to.
	var plus_x := 1.0 if sumo_signals & SIGNAL_BLINKER_RIGHT else 0.0
	var minus_x := 1.0 if sumo_signals & SIGNAL_BLINKER_LEFT else 0.0
	var hazard := 1.0 if (warning_bits & LIGHT_HAZARD) or (sumo_signals & SIGNAL_BLINKER_EMERGENCY) else 0.0
	var beacons := 1.0 if warning_bits & LIGHT_BAR else 0.0
	var amber := 1.0 if warning_bits & LIGHT_AMBER else 0.0
	var reverse := 1.0 if sumo_signals & SIGNAL_BACKDRIVE else 0.0
	# Units on a call run dipped beams; everything else daytime running lamps.
	body.set_instance_shader_parameter(&"lamp_state", Vector4(beacons, brake, plus_x, minus_x))
	body.set_instance_shader_parameter(&"lamp_extra", Vector4(beacons, hazard, amber, reverse))


static func advance_lamp_clock(delta: float) -> void:
	lamp_clock = fposmod(lamp_clock + delta, 3600.0)
	RenderingServer.global_shader_parameter_set(&"vehicle_lamp_clock", lamp_clock)


static func set_soot(vehicle: Node3D, amount: float) -> void:
	for part in vehicle.get_children():
		if part is MeshInstance3D and (part.name == "Body" or str(part.name).begins_with("Wheel_")):
			(part as MeshInstance3D).set_instance_shader_parameter(&"soot", amount)
		elif part is Node3D:
			# Rolling wheels live under their pivots.
			for nested in part.get_children():
				if nested is MeshInstance3D and str(nested.name).begins_with("Wheel_"):
					(nested as MeshInstance3D).set_instance_shader_parameter(&"soot", amount)


# --- Doors --------------------------------------------------------------------

static func _door_slots(s: Dictionary) -> Array:
	## Hinged side doors: [u0, u1] along the car (hinge at the front edge u1),
	## one front pair, plus a rear pair on four-door cars.
	var seams := s.doors as Array
	var slots: Array = []
	if seams.size() < 2:
		return slots
	var ranges := [[float(seams[1]), float(seams[0])]]
	if seams.size() >= 3 and str(s.name) in ["sedan", "police", "taxi", "hatchback", "wagon", "suv"]:
		ranges.append([float(seams[2]), float(seams[1])])
	for index in ranges.size():
		for side: float in [1.0, -1.0]:
			var bit := 1 << (index * 2 + (0 if side > 0.0 else 1))
			slots.append({"u0": float(ranges[index][0]), "u1": float(ranges[index][1]), "side": side, "bit": bit, "rear": index == 1})
	return slots


static func _door_bit_at(s: Dictionary, u: float, side: float) -> int:
	for slot in _door_slots(s):
		if float(slot.side) == side and u > float(slot.u0) and u < float(slot.u1):
			return int(slot.bit)
	return 0


static func door_slots(vehicle: Node3D) -> Array:
	## World-space boarding geometry of every hinged door: the seat inside
	## the cabin, the standing spot just outside the opening, and its side.
	var key := str(vehicle.get_meta("procedural_key", ""))
	if not _cache.has(key):
		return []
	var s := (_cache[key] as Dictionary).style as Dictionary
	var output: Array = []
	var half := float(s.get("mirror_half", float(s.width) * 0.5 + MIRROR_REACH))
	var basis := vehicle.global_transform.basis.orthonormalized()
	var origin := vehicle.global_transform.origin
	for slot in _door_slots(s):
		var mid_u := (float(slot.u0) + float(slot.u1)) * 0.5
		var z := mid_u * float(s.length) * 0.5
		var side := float(slot.side)
		var body_half := float(s.width) * 0.5 * _curve(s.plan, mid_u)
		var seat := origin + basis * Vector3(side * (body_half - 0.42), 0.0, z)
		var outside := origin + basis * Vector3(side * (half + 0.55), 0.0, z)
		var outward := (basis * Vector3(side, 0, 0)).normalized()
		output.append({"bit": int(slot.bit), "side": side, "rear": bool(slot.rear), "seat": seat, "outside": outside, "yaw_out": atan2(outward.x, outward.z)})
	return output


static func ensure_doors(vehicle: Node3D) -> bool:
	## Creates the hinged door panels on first use. The body's door panels
	## then render as the dark cabin behind them, so an open door shows an
	## opening, while a closed door is indistinguishable from the body.
	var body := body_of(vehicle)
	if body == null:
		return false
	if vehicle.has_meta("door_hinges"):
		return true
	var key := str(vehicle.get_meta("procedural_key", ""))
	var entry := _cache.get(key, {}) as Dictionary
	if entry.is_empty():
		return false
	var s := entry.style as Dictionary
	var doors := entry.doors as Dictionary
	var hinges := {}
	var mask := 0
	for slot in _door_slots(s):
		var bit := int(slot.bit)
		if not doors.has(bit):
			doors[bit] = _door_mesh(s, slot)
		var hinge_info := doors[bit] as Dictionary
		var hinge := Node3D.new()
		hinge.name = "DoorHinge_%d" % bit
		hinge.position = hinge_info.hinge
		var door := MeshInstance3D.new()
		door.name = "Door_%d" % bit
		door.mesh = hinge_info.mesh
		for parameter in [&"paint_color", &"accent_color", &"paint_metallic", &"soot"]:
			door.set_instance_shader_parameter(parameter, body.get_instance_shader_parameter(parameter))
		door.layers = body.layers
		hinge.add_child(door)
		vehicle.add_child(hinge)
		hinges[bit] = hinge
		mask |= bit
	body.set_instance_shader_parameter(&"door_cutout", float(mask))
	vehicle.set_meta("door_hinges", hinges)
	return true


static func swing_door(vehicle: Node3D, bit: int, open: bool) -> Tween:
	## Swings one door open or shut (eased, about its front hinge).
	if not ensure_doors(vehicle):
		return null
	var hinge := (vehicle.get_meta("door_hinges") as Dictionary).get(bit) as Node3D
	if hinge == null:
		return null
	var side := 1.0 if bit in [1, 4] else -1.0
	var target := -side * deg_to_rad(DOOR_OPEN_DEGREES) if open else 0.0
	var tween := vehicle.create_tween()
	tween.tween_property(hinge, "rotation:y", target, DOOR_SECONDS).set_trans(Tween.TRANS_SINE).set_ease(Tween.EASE_OUT if open else Tween.EASE_IN_OUT)
	return tween


static func _door_mesh(s: Dictionary, slot: Dictionary) -> Dictionary:
	## The door skin: the body's own panels between the door seams (sill to
	## window top) 4 mm proud, an inner trim panel 6 cm in, closing edges and
	## the handle, all relative to the front hinge.
	var builder := Builder.new(SURFACE_COUNT)
	var side := float(slot.side)
	var u0 := float(slot.u0)
	var u1 := float(slot.u1)
	var stations: Array[float] = [u0]
	for u in _stations(s):
		if u > u0 + 0.002 and u < u1 - 0.002:
			stations.append(u)
	stations.append(u1)
	var outer: Array = []
	var inner: Array = []
	for u in stations:
		var section := _section(s, u)
		var points := section.points as PackedVector2Array
		var row_out := []
		var row_in := []
		for j in range(DOOR_ROW_FIRST, DOOR_ROW_LAST + 2):
			var p := points[j]
			var q := points[mini(j + 1, points.size() - 1)]
			var r := points[maxi(j - 1, 0)]
			var tangent := (q - r).normalized()
			var normal := Vector2(tangent.y, -tangent.x)
			row_out.append(Vector3((p.x + normal.x * 0.004) * side, p.y + normal.y * 0.004, float(section.z)))
			row_in.append(Vector3((p.x - normal.x * 0.06) * side, p.y - normal.y * 0.06, float(section.z)))
		outer.append(row_out)
		inner.append(row_in)
	var front_section := _section(s, u1)
	var hinge := Vector3((front_section.points as PackedVector2Array)[5].x * side, 0.0, u1 * float(s.length) * 0.5)
	var zones := _section(s, (u0 + u1) * 0.5).zones as Array[String]
	for i in stations.size() - 1:
		var u := (stations[i] + stations[i + 1]) * 0.5
		for k in DOOR_ROW_LAST - DOOR_ROW_FIRST + 1:
			var j := k + DOOR_ROW_FIRST
			var a := (outer[i] as Array)[k] as Vector3
			var b := (outer[i + 1] as Array)[k] as Vector3
			var c := (outer[i + 1] as Array)[k + 1] as Vector3
			var d := (outer[i] as Array)[k + 1] as Vector3
			if a.distance_to(d) < 0.0005 and b.distance_to(c) < 0.0005:
				continue
			var info := _classify(s, zones[j + 1], u, side)
			builder.quad(int(info[0]), a - hinge, b - hinge, c - hinge, d - hinge, Vector3(side, 0, 0), Color.WHITE, 0, float(info[1]))
			var ia := (inner[i] as Array)[k] as Vector3
			var ib := (inner[i + 1] as Array)[k] as Vector3
			var ic := (inner[i + 1] as Array)[k + 1] as Vector3
			var id := (inner[i] as Array)[k + 1] as Vector3
			builder.quad(TRIM, ia - hinge, ib - hinge, ic - hinge, id - hinge, Vector3(-side, 0, 0), Color.WHITE, -1)
	# Closing edges: rear and front columns, sill and window top.
	var last := stations.size() - 1
	for k in DOOR_ROW_LAST - DOOR_ROW_FIRST + 1:
		for column in [[0, -1.0], [last, 1.0]]:
			var o0 := (outer[column[0]] as Array)[k] as Vector3
			var o1 := (outer[column[0]] as Array)[k + 1] as Vector3
			var i0 := (inner[column[0]] as Array)[k] as Vector3
			var i1 := (inner[column[0]] as Array)[k + 1] as Vector3
			builder.quad(TRIM, o0 - hinge, o1 - hinge, i1 - hinge, i0 - hinge, Vector3(0, 0, float(column[1])), Color.WHITE, -1)
	for i in last:
		for row in [[0, -1.0], [DOOR_ROW_LAST - DOOR_ROW_FIRST + 1, 1.0]]:
			var o0 := (outer[i] as Array)[row[0]] as Vector3
			var o1 := (outer[i + 1] as Array)[row[0]] as Vector3
			var i0 := (inner[i] as Array)[row[0]] as Vector3
			var i1 := (inner[i + 1] as Array)[row[0]] as Vector3
			builder.quad(TRIM, o0 - hinge, o1 - hinge, i1 - hinge, i0 - hinge, Vector3(0, float(row[1]), 0), Color.WHITE, -1)
	for u in s.handles:
		if float(u) > u0 and float(u) < u1:
			var p := _surface_point(s, float(u), 6, 0.016)
			builder.box(CHROME, Vector3(p.x * side, p.y - 0.02, p.z) - hinge, Vector3(0.024, 0.028, 0.15), Color())
	var materials: Array = []
	for surface in SURFACE_COUNT:
		materials.append(_surface_material(surface))
	return {"mesh": builder.commit(materials), "hinge": hinge}


# --- Materials ----------------------------------------------------------------

static func _surface_material(surface: int) -> Material:
	if _materials.is_empty():
		for finish in 4:
			var material := ShaderMaterial.new()
			material.shader = BODY_SHADER
			material.set_shader_parameter(&"finish", finish)
			_materials.append(material)
		var lamp := ShaderMaterial.new()
		lamp.shader = LAMP_SHADER
		_materials.append(lamp)
	return _materials[surface]


static func _get_wheel_material() -> ShaderMaterial:
	if _wheel_material == null:
		_wheel_material = ShaderMaterial.new()
		_wheel_material.shader = WHEEL_SHADER
	return _wheel_material


# --- Profiles -----------------------------------------------------------------

static func _curve(points: Array, u: float) -> float:
	## Monotone cubic (Fritsch–Carlson) through [u, value] control points:
	## smooth, and never overshoots a hood line or roof into a bump.
	var count := points.size()
	if u <= float(points[0][0]):
		return float(points[0][1])
	if u >= float(points[count - 1][0]):
		return float(points[count - 1][1])
	var index := 0
	while index < count - 2 and u > float(points[index + 1][0]):
		index += 1
	var slopes: Array[float] = []
	for k in count - 1:
		slopes.append((float(points[k + 1][1]) - float(points[k][1])) / maxf(float(points[k + 1][0]) - float(points[k][0]), 0.00001))
	var tangent := func(k: int) -> float:
		if k == 0:
			return slopes[0]
		if k == count - 1:
			return slopes[count - 2]
		if slopes[k - 1] * slopes[k] <= 0.0:
			return 0.0
		return 2.0 / (1.0 / slopes[k - 1] + 1.0 / slopes[k])
	var u0 := float(points[index][0])
	var u1 := float(points[index + 1][0])
	var h := u1 - u0
	var t := (u - u0) / h
	var t2 := t * t
	var t3 := t2 * t
	return (2.0 * t3 - 3.0 * t2 + 1.0) * float(points[index][1]) + (t3 - 2.0 * t2 + t) * h * float(tangent.call(index)) \
		+ (-2.0 * t3 + 3.0 * t2) * float(points[index + 1][1]) + (t3 - t2) * h * float(tangent.call(index + 1))


static func _in_ranges(ranges: Array, u: float) -> bool:
	for range_value in ranges:
		if u >= float(range_value[0]) and u <= float(range_value[1]):
			return true
	return false


static func _section(s: Dictionary, u: float) -> Dictionary:
	## Half cross-section (x >= 0) from the underbody centre to the roof
	## centre, with the zone of each segment.
	var half := float(s.width) * 0.5 * _curve(s.plan, u)
	var z := u * float(s.length) * 0.5
	var bottom := _curve(s.bottom, u)
	var arch_radius := float(s.wheel_radius) + 0.055
	for axle in s.axles:
		var dz := z - float(axle) * float(s.length) * 0.5
		if absf(dz) < arch_radius:
			bottom = maxf(bottom, float(s.wheel_radius) + sqrt(arch_radius * arch_radius - dz * dz))
	var belt := _curve(s.belt, u)
	var top := maxf(_curve(s.roof, u), belt + 0.015)
	var blend := smoothstep(-0.25, 0.25, u)
	var band_hi := lerpf(float(s.tail_band[1]), float(s.head_band[1]), blend)
	var band_lo := lerpf(float(s.tail_band[0]), float(s.head_band[0]), blend)
	# The lamp band always fits under the hood/boot line, keeping its height,
	# so lenses never collapse where the body drops toward a bumper.
	var band_height := band_hi - band_lo
	band_hi = minf(band_hi, belt - 0.035)
	band_lo = minf(band_lo, band_hi - band_height)
	var glass_half := half - float(s.glass_inset)
	var roof_half := maxf(glass_half - float(s.tumble) * (top - belt), half * 0.35)
	var round_value := minf(float(s.roof_round), (top - belt) * 0.5)
	var crown := float(s.crown) * clampf(roof_half / 0.7, 0.0, 1.0)
	var raw := [
		[0.0, bottom, "under"],
		[half - 0.09, bottom, "under"],
		[half - 0.015, bottom + 0.05, "lower"],
		[half, lerpf(bottom + 0.05, band_lo, 0.5), "lower"],
		[half + 0.004, band_lo, "lower"],
		[half + 0.004, band_hi, "band"],
		[half - 0.008, lerpf(band_hi, belt, 0.6), "upper"],
		[half - 0.03, belt - 0.012, "upper"],
		[glass_half, belt + 0.012, "shoulder"],
		[roof_half, top - round_value, "glass"],
		[roof_half - round_value * 0.8, top - round_value * 0.12, "roofedge"],
		[roof_half * 0.5, top + crown * 0.75, "roof"],
		[0.0, top + crown, "roof"],
	]
	var points := PackedVector2Array()
	var zones: Array[String] = []
	var previous_y := -INF
	for index in raw.size():
		var entry := raw[index] as Array
		var y := float(entry[1])
		# Rows stay ordered bottom to top (arches can lift the sill above the
		# lamp band); collapsed rows become zero-height quads that are skipped.
		if index > 1:
			y = maxf(y, previous_y)
		if index >= 2 and index <= 7:
			y = minf(y, belt - 0.012)
		previous_y = y
		points.append(Vector2(float(entry[0]), y))
		zones.append(str(entry[2]))
	return {"points": points, "zones": zones, "z": z, "u": u}


static func _stations(s: Dictionary) -> PackedFloat32Array:
	var values: Array[float] = []
	for index in 61:
		values.append(-1.0 + 2.0 * float(index) / 60.0)
	for k in range(1, 7):
		values.append(1.0 - float(k) * 0.006)
		values.append(-1.0 + float(k) * 0.006)
	for profile in [s.belt, s.roof, s.plan, s.bottom]:
		for point in profile:
			values.append(float(point[0]))
	for range_value in [s.windshield, s.rear_window, s.livery_doors] + Array(s.side_glass) + Array(s.bus_doors):
		values.append(float(range_value[0]))
		values.append(float(range_value[1]))
	for seam in s.doors:
		values.append(float(seam))
	values.append(1.0 - float(s.head_wrap))
	values.append(1.0 - float(s.head_wrap) * 0.45)
	values.append(-1.0 + float(s.tail_wrap))
	values.append(float(s.deck_u))
	var arch_u := (float(s.wheel_radius) + 0.055) * 2.0 / float(s.length)
	for axle in s.axles:
		for k in range(-8, 9):
			values.append(float(axle) + arch_u * float(k) / 8.0)
	values.sort()
	var output := PackedFloat32Array()
	for value in values:
		if value < -1.0 or value > 1.0:
			continue
		if output.is_empty() or value - output[output.size() - 1] > 0.004:
			output.append(value)
	output[0] = -1.0
	output[output.size() - 1] = 1.0
	return output


# --- Mesh assembly --------------------------------------------------------------

class Builder:
	## Triangles per finish; normals are averaged per smoothing group (group
	## -1 is flat-shaded), so material boundaries always stay crisp.
	var surfaces: Array = []

	func _init(count: int) -> void:
		for index in count:
			# Plain Arrays: they are shared references, whereas packed arrays
			# read out of a container are copies that would drop appends.
			surfaces.append({"positions": [], "colors": [], "codes": [], "groups": []})

	func tri(surface: int, a: Vector3, b: Vector3, c: Vector3, outward: Vector3, color: Color, group: int, code := 0.0, door := 0.0) -> void:
		var normal := (b - a).cross(c - a)
		if normal.length_squared() < 1e-12:
			return
		if normal.dot(outward) < 0.0:
			var swap := b
			b = c
			c = swap
		var data := surfaces[surface] as Dictionary
		# Godot treats clockwise triangles as front faces.
		(data.positions as Array).append_array([a, c, b])
		(data.colors as Array).append_array([color, color, color])
		var codes := Vector2(code, door)
		(data.codes as Array).append_array([codes, codes, codes])
		(data.groups as Array).append(group)

	func quad(surface: int, a: Vector3, b: Vector3, c: Vector3, d: Vector3, outward: Vector3, color: Color, group: int, code := 0.0, door := 0.0) -> void:
		tri(surface, a, b, c, outward, color, group, code, door)
		tri(surface, a, c, d, outward, color, group, code, door)

	func box(surface: int, centre: Vector3, size: Vector3, color: Color, code := 0.0, door := 0.0) -> void:
		var h := size * 0.5
		for axis in 3:
			for sign_value: float in [-1.0, 1.0]:
				var n := Vector3.ZERO
				n[axis] = sign_value
				var u_axis := Vector3.ZERO
				u_axis[(axis + 1) % 3] = h[(axis + 1) % 3]
				var v_axis := Vector3.ZERO
				v_axis[(axis + 2) % 3] = h[(axis + 2) % 3]
				var c := centre + n * h[axis]
				quad(surface, c - u_axis - v_axis, c + u_axis - v_axis, c + u_axis + v_axis, c - u_axis + v_axis, n, color, -1, code, door)

	func commit(materials: Array, lods := false) -> ArrayMesh:
		## Distance LODs (meshoptimizer) are generated from the arrays, so the
		## same path works with every renderer, headless included.
		var importer := ImporterMesh.new()
		for surface in surfaces.size():
			var data := surfaces[surface] as Dictionary
			var positions := PackedVector3Array(data.positions as Array)
			if positions.is_empty():
				continue
			var groups := PackedInt32Array(data.groups as Array)
			var sums := {}
			var face_normals := PackedVector3Array()
			for tri_index in groups.size():
				var a := positions[tri_index * 3]
				var b := positions[tri_index * 3 + 1]
				var c := positions[tri_index * 3 + 2]
				var face := (c - a).cross(b - a)
				face_normals.append(face.normalized())
				if groups[tri_index] < 0:
					continue
				for corner in 3:
					var key := _key(positions[tri_index * 3 + corner], groups[tri_index])
					sums[key] = (sums.get(key, Vector3.ZERO) as Vector3) + face
			var normals := PackedVector3Array()
			normals.resize(positions.size())
			for tri_index in groups.size():
				for corner in 3:
					var vertex := tri_index * 3 + corner
					if groups[tri_index] < 0:
						normals[vertex] = face_normals[tri_index]
					else:
						var smooth := (sums[_key(positions[vertex], groups[tri_index])] as Vector3).normalized()
						# Keep creases sharper than ~50 degrees faceted.
						normals[vertex] = smooth if smooth.dot(face_normals[tri_index]) > 0.64 else face_normals[tri_index]
			var tool := SurfaceTool.new()
			tool.begin(Mesh.PRIMITIVE_TRIANGLES)
			var colors := PackedColorArray(data.colors as Array)
			var codes := data.codes as Array
			for vertex in positions.size():
				tool.set_color(colors[vertex])
				# UV.x carries the lamp kind or livery mask and UV.y the door
				# panel bit, exactly (vertex colours are not bit-exact).
				tool.set_uv(codes[vertex] as Vector2)
				tool.set_normal(normals[vertex])
				tool.add_vertex(positions[vertex])
			tool.index()
			importer.add_surface(Mesh.PRIMITIVE_TRIANGLES, tool.commit_to_arrays(), [], {}, materials[surface])
		if lods:
			importer.generate_lods(25.0, 60.0, [])
		return importer.get_mesh()

	static func _key(p: Vector3, group: int) -> String:
		return "%d|%d|%d|%d" % [group, roundi(p.x * 4000.0), roundi(p.y * 4000.0), roundi(p.z * 4000.0)]


static func _build_meshes(s: Dictionary) -> Dictionary:
	var builder := Builder.new(SURFACE_COUNT)
	var stations := _stations(s)
	var sections: Array[Dictionary] = []
	for u in stations:
		sections.append(_section(s, u))
	_build_loft(builder, s, sections)
	_build_cap(builder, s, sections[sections.size() - 1], true)
	_build_cap(builder, s, sections[0], false)
	_build_details(builder, s)
	var materials: Array = []
	for surface in SURFACE_COUNT:
		materials.append(_surface_material(surface))
	var wheels: Array[ArrayMesh] = []
	var body_half := float(s.width) * 0.5
	for axle in s.axles:
		for side: float in [1.0, -1.0]:
			var hub := Vector3(side * (body_half * _curve(s.plan, float(axle)) - float(s.wheel_width) * 0.5 - 0.02), float(s.wheel_radius), float(axle) * float(s.length) * 0.5)
			wheels.append(_wheel_mesh(s, hub, side))
	var body := builder.commit(materials, true)
	# Lens kinds present in the body, for validation without mesh readback.
	var kinds := {}
	for code in ((builder.surfaces[LAMP] as Dictionary).codes as Array):
		kinds[int((code as Vector2).x)] = true
	var kind_list := kinds.keys()
	kind_list.sort()
	body.set_meta("lamp_kinds", kind_list)
	return {"body": body, "wheels": wheels, "style": s, "doors": {}}


static func _accent(s: Dictionary, zone: String, u: float, side: float) -> float:
	match str(s.livery):
		"police":
			if zone in ["lower", "band", "upper"] and _in_ranges([s.livery_doors], u):
				return 1.0
			return 1.0 if zone in ["roof", "roofedge"] and absf(u) < 0.4 else 0.0
		"ambulance", "fire":
			return 1.0 if zone == "band" and u < 0.97 else 0.0
		"service":
			return 1.0 if zone == "band" else 0.0
		"refuse":
			# Green packer body and tailgate behind a white crew cab.
			return 1.0 if u < 0.545 and zone not in ["roof", "roofedge"] else (1.0 if zone == "band" else 0.0)
		"bus":
			return 1.0 if zone in ["lower"] else 0.0
	return 0.0


static func _classify(s: Dictionary, zone: String, u: float, side: float) -> Array:
	## [surface, lamp kind or accent] for a loft quad at station u.
	var head_start := 1.0 - float(s.head_wrap)
	var tail_end := -1.0 + float(s.tail_wrap)
	if absf(u) > 1.5:
		return [PAINT, 0.0]
	match zone:
		"under":
			return [TRIM, 0.0]
		"lower":
			if bool(s.cladding) or (u > 0.94 or u < -0.94) and str(s.name) in ["suv", "tow", "utility", "garbage", "fire", "bus"]:
				return [TRIM, 0.0]
			if _in_ranges(s.bus_doors, u) and side < 0.0:
				return [GLASS, 0.0]
		"band":
			if u > head_start:
				var kind := LAMP_HEAD if u > 1.0 - float(s.head_wrap) * 0.45 else (LAMP_INDICATOR_FL if side > 0.0 else LAMP_INDICATOR_FR)
				return [LAMP, float(kind)]
			if u < tail_end:
				return [LAMP, float(LAMP_TAIL)]
			if _in_ranges(s.bus_doors, u) and side < 0.0:
				return [GLASS, 0.0]
		"upper":
			if _in_ranges(s.bus_doors, u) and side < 0.0:
				return [GLASS, 0.0]
		"shoulder":
			if u < float(s.deck_u):
				return [TRIM, 0.0]
			if _in_ranges(s.side_glass, u) or (_in_ranges(s.bus_doors, u) and side < 0.0):
				return [TRIM, 0.0]
		"glass":
			if u < float(s.deck_u):
				return [TRIM, 0.0]
			if _in_ranges(s.side_glass, u) or (_in_ranges(s.bus_doors, u) and side < 0.0):
				return [GLASS, 0.0]
			if bool(s.blackout_pillars) and u > float((s.side_glass as Array)[0][0]) and u < float((s.side_glass as Array)[-1][1]):
				return [TRIM, 0.0]
		"roofedge", "roof":
			if u < float(s.deck_u):
				return [TRIM, 0.0]
			if _in_ranges([s.windshield, s.rear_window], u):
				return [GLASS, 0.0]
	return [PAINT, _accent(s, zone, u, side)]


static func _build_loft(builder: Builder, s: Dictionary, sections: Array[Dictionary]) -> void:
	var slots := _door_slots(s)
	for i in sections.size() - 1:
		var a := sections[i]
		var b := sections[i + 1]
		var u := (float(a.u) + float(b.u)) * 0.5
		var pa := a.points as PackedVector2Array
		var pb := b.points as PackedVector2Array
		var zones := a.zones as Array[String]
		for j in pa.size() - 1:
			var zone := zones[j + 1]
			if pa[j].distance_to(pa[j + 1]) < 0.0005 and pb[j].distance_to(pb[j + 1]) < 0.0005:
				continue
			var segment := pa[j + 1] - pa[j]
			var outward_2d := Vector2(segment.y, -segment.x)
			for side: float in [1.0, -1.0]:
				var info := _classify(s, zone, u, side)
				var surface := int(info[0])
				var outward := Vector3(outward_2d.x * side, outward_2d.y, 0.0)
				var door := 0.0
				if j >= DOOR_ROW_FIRST and j <= DOOR_ROW_LAST:
					for slot in slots:
						if float(slot.side) == side and u > float(slot.u0) and u < float(slot.u1):
							door = float(slot.bit)
				builder.quad(surface,
					Vector3(pa[j].x * side, pa[j].y, float(a.z)),
					Vector3(pb[j].x * side, pb[j].y, float(b.z)),
					Vector3(pb[j + 1].x * side, pb[j + 1].y, float(b.z)),
					Vector3(pa[j + 1].x * side, pa[j + 1].y, float(a.z)),
					outward, Color.WHITE, 0, float(info[1]), door)


static func _cap_cell(s: Dictionary, front: bool, row: int, zone: String, x_mid: float, half: float) -> Array:
	## Finish of one end-face cell. Rows follow the section: 0 underbody,
	## 1 bumper lip, 2 lower bumper, 3 plate row, 4 lamp band, 5-6 upper
	## panel, 7 shoulder, 8+ glasshouse/roof.
	var ax := absf(x_mid) / maxf(half, 0.01)
	var side := 1.0 if x_mid >= 0.0 else -1.0
	var plate := 0.26 / maxf(half, 0.01)
	var truck := str(s.name) in ["suv", "tow", "utility", "garbage", "fire", "bus"]
	if row <= 1:
		return [TRIM, 0.0]
	if zone in ["glass", "roofedge", "roof"]:
		if (front and bool(s.front_glass_cap)) or (not front and bool(s.rear_glass_cap)):
			return [GLASS, 0.0] if ax < 0.94 else [PAINT, 0.0]
		if not front and float(s.deck_u) > -1.5:
			return [TRIM, 0.0]
		return [PAINT, 0.0]
	if front:
		match row:
			2:
				return [TRIM, 0.0] if ax < float(s.grille_x) * 1.15 or truck else [PAINT, 0.0]
			3:
				return [LAMP, float(LAMP_PLATE)] if ax < plate else [PAINT, 0.0]
			4:
				if ax < float(s.grille_x):
					return [TRIM, 0.0]
				if ax > 0.9:
					return [LAMP, float(LAMP_INDICATOR_FL if side > 0.0 else LAMP_INDICATOR_FR)]
				if ax > float(s.head_x):
					return [LAMP, float(LAMP_HEAD)]
			5:
				if ax < float(s.grille_x):
					return [TRIM, 0.0]
		return [PAINT, 0.0]
	match row:
		2:
			return [TRIM, 0.0] if truck else [PAINT, 0.0]
		3:
			return [LAMP, float(LAMP_PLATE)] if ax < plate else [PAINT, 0.0]
		4:
			var tail_x := float(s.tail_x)
			if ax > tail_x + 0.14:
				return [LAMP, float(LAMP_TAIL)]
			if ax > tail_x + 0.04:
				return [LAMP, float(LAMP_INDICATOR_RL if side > 0.0 else LAMP_INDICATOR_RR)]
			if ax > tail_x - 0.06:
				return [LAMP, float(LAMP_REVERSE)]
	return [PAINT, _accent(s, zone, -0.99, side) if zone == "band" else 0.0]


static func _build_cap(builder: Builder, s: Dictionary, section: Dictionary, front: bool) -> void:
	## End face as stacked trapezoids between the section's rows, split at the
	## lamp/grille x thresholds, so every lens and grille edge is a real edge.
	var points := section.points as PackedVector2Array
	var zones := section.zones as Array[String]
	var z := float(section.z)
	var outward := Vector3(0, 0, 1.0 if front else -1.0)
	var half := points[3].x
	var thresholds: Array[float] = []
	if front:
		thresholds.assign([float(s.grille_x), float(s.head_x), 0.9, float(s.grille_x) * 1.15])
	else:
		thresholds.assign([float(s.tail_x) - 0.06, float(s.tail_x) + 0.04, float(s.tail_x) + 0.14])
	thresholds.append(0.26 / maxf(half, 0.01))
	thresholds.append(0.94)
	var group := 1 if front else 2
	for j in points.size() - 1:
		var lo := points[j]
		var hi := points[j + 1]
		if hi.y - lo.y < 0.0005:
			continue
		var zone := zones[j + 1]
		# Cut positions as fractions of each row's half width.
		var cuts: Array[float] = [0.0, 1.0]
		for threshold in thresholds:
			if threshold > 0.0 and threshold < 1.0:
				cuts.append(threshold)
		cuts.sort()
		for k in cuts.size() - 1:
			var f0 := cuts[k]
			var f1 := cuts[k + 1]
			if f1 - f0 < 0.001:
				continue
			for side: float in [1.0, -1.0]:
				var x_mid := (lo.x + hi.x) * 0.25 * (f0 + f1) * side
				var info := _cap_cell(s, front, j, zone, x_mid, (lo.x + hi.x) * 0.5)
				var surface := int(info[0])
				builder.quad(surface,
					Vector3(lo.x * f0 * side, lo.y, z), Vector3(lo.x * f1 * side, lo.y, z),
					Vector3(hi.x * f1 * side, hi.y, z), Vector3(hi.x * f0 * side, hi.y, z),
					outward, Color.WHITE, group, float(info[1]))


static func _surface_point(s: Dictionary, u: float, row: int, offset: float) -> Vector3:
	## A point on the body side (row of the section at u), pushed outward.
	var section := _section(s, u)
	var points := section.points as PackedVector2Array
	var p := points[row]
	var q := points[mini(row + 1, points.size() - 1)]
	var r := points[maxi(row - 1, 0)]
	var tangent := (q - r).normalized()
	var normal := Vector2(tangent.y, -tangent.x)
	return Vector3(p.x + normal.x * offset, p.y + normal.y * offset, float(section.z))


static func _build_details(builder: Builder, s: Dictionary) -> void:
	var length := float(s.length)
	# Door shut lines: thin dark strips following the side from sill to belt.
	for u in s.doors:
		for side: float in [1.0, -1.0]:
			for row in range(2, 7):
				var a := _surface_point(s, float(u), row, 0.0015)
				var b := _surface_point(s, float(u), row + 1, 0.0015)
				if b.y - a.y < 0.002:
					continue
				var dz := Vector3(0, 0, 0.004)
				a.x *= side
				b.x *= side
				builder.quad(TRIM, a - dz, a + dz, b + dz, b - dz, Vector3(side, 0, 0), Color(), -1)
	# Door handles (each also lives on its hinged door once doors exist).
	for u in s.handles:
		for side: float in [1.0, -1.0]:
			var p := _surface_point(s, float(u), 6, 0.012)
			builder.box(CHROME, Vector3(p.x * side, p.y - 0.02, p.z), Vector3(0.024, 0.028, 0.15), Color(), 0.0, float(_door_bit_at(s, float(u), side)))
	# Mirrors on short arms at the base of the A-pillar.
	var mirror_u := float(s.mirror_u)
	var base := _surface_point(s, mirror_u, 8, 0.0)
	var reach := float(s.get("mirror_half", float(s.width) * 0.5 + MIRROR_REACH)) - base.x
	var tall := 0.3 if str(s.name) in ["ambulance", "fire", "tow", "utility", "garbage", "bus"] else 0.13
	for side: float in [1.0, -1.0]:
		# The housing's outer face is exactly the class width (body + mirrors).
		var centre := Vector3((base.x + reach * 0.69) * side, base.y + 0.06 + tall * 0.5, base.z - 0.04)
		builder.box(PAINT, centre, Vector3(reach * 0.62, tall, 0.09), Color())
		builder.box(GLASS, centre + Vector3(0, 0, -0.047), Vector3(reach * 0.55, tall * 0.85, 0.006), Color())
		builder.box(TRIM, Vector3((base.x + reach * 0.18) * side, base.y + 0.06, base.z - 0.04), Vector3(reach * 0.36, 0.035, 0.05), Color())
	var accessories := s.accessories as Array
	if "lightbar" in accessories:
		var u := float(s.get("lightbar_u", 0.0))
		var top := _section(s, u)
		var roof_y := (top.points as PackedVector2Array)[12].y
		var bar_half := minf((top.points as PackedVector2Array)[9].x * 0.82, 0.68)
		var z := u * length * 0.5
		builder.box(TRIM, Vector3(0, roof_y + 0.03, z), Vector3(bar_half * 2.0, 0.06, 0.26), Color())
		# Beacon A on the vehicle's right (-X), like the rig's first light.
		builder.box(LAMP, Vector3(-bar_half * 0.5, roof_y + 0.105, z), Vector3(bar_half * 0.94, 0.09, 0.22), Color(), float(LAMP_BEACON_A))
		builder.box(LAMP, Vector3(bar_half * 0.5, roof_y + 0.105, z), Vector3(bar_half * 0.94, 0.09, 0.22), Color(), float(LAMP_BEACON_B))
		if str(s.name) in ["ambulance", "fire"]:
			# Rear corner beacons on tall bodies.
			var rear := _section(s, -0.97)
			var rear_y := (rear.points as PackedVector2Array)[12].y
			var rear_x := (rear.points as PackedVector2Array)[9].x - 0.12
			builder.box(LAMP, Vector3(-rear_x, rear_y + 0.05, -0.97 * length * 0.5), Vector3(0.16, 0.1, 0.12), Color(), float(LAMP_BEACON_A))
			builder.box(LAMP, Vector3(rear_x, rear_y + 0.05, -0.97 * length * 0.5), Vector3(0.16, 0.1, 0.12), Color(), float(LAMP_BEACON_B))
	if "amber_beacon" in accessories:
		var u := float(s.get("beacon_u", 0.4))
		var roof_y := (_section(s, u).points as PackedVector2Array)[12].y
		builder.box(TRIM, Vector3(0, roof_y + 0.025, u * length * 0.5), Vector3(0.9, 0.05, 0.2), Color())
		for side: float in [1.0, -1.0]:
			builder.box(LAMP, Vector3(side * 0.32, roof_y + 0.11, u * length * 0.5), Vector3(0.18, 0.13, 0.16), Color(), float(LAMP_BEACON_AMBER))
	if "taxi_sign" in accessories:
		var roof_y := (_section(s, -0.12).points as PackedVector2Array)[12].y
		builder.box(LAMP, Vector3(0, roof_y + 0.085, -0.12 * length * 0.5), Vector3(0.52, 0.15, 0.2), Color(), float(LAMP_TAXI_SIGN))
	if "ladder" in accessories:
		var roof_y := (_section(s, -0.3).points as PackedVector2Array)[12].y
		var z0 := -0.96 * length * 0.5
		var z1 := 0.44 * length * 0.5
		for side: float in [1.0, -1.0]:
			builder.box(CHROME, Vector3(side * 0.34, roof_y + 0.16, (z0 + z1) * 0.5), Vector3(0.06, 0.1, z1 - z0), Color())
			builder.box(TRIM, Vector3(side * 0.34, roof_y + 0.05, z0 + 0.4), Vector3(0.08, 0.1, 0.08), Color())
			builder.box(TRIM, Vector3(side * 0.34, roof_y + 0.05, z1 - 0.4), Vector3(0.08, 0.1, 0.08), Color())
		var rung := z0 + 0.15
		while rung < z1:
			builder.box(CHROME, Vector3(0, roof_y + 0.16, rung), Vector3(0.64, 0.035, 0.035), Color())
			rung += 0.36
	if "rear_hopper" in accessories:
		# Rear-loader tail: the dark hopper mouth below the lip, the lifter
		# rails either side and the comb bar the container hooks onto.
		# Every part sits flush with or inside the tail: the truck keeps its
		# exact SUMO length.
		var tail := -length * 0.5
		var half := float(s.width) * 0.5 - 0.32
		builder.box(TRIM, Vector3(0, 1.4, tail + 0.015), Vector3(half * 2.0, 0.7, 0.03), Color())
		builder.box(CHROME, Vector3(0, 1.8, tail + 0.04), Vector3(half * 2.0 + 0.1, 0.06, 0.08), Color())
		for side: float in [1.0, -1.0]:
			builder.box(CHROME, Vector3(side * (half + 0.08), 1.25, tail + 0.04), Vector3(0.07, 1.1, 0.08), Color())
		builder.box(CHROME, Vector3(0, 0.98, tail + 0.035), Vector3(half * 1.4, 0.07, 0.07), Color())
	if "tow_boom" in accessories:
		var deck_y := _curve(s.belt, -0.5)
		var cab_rear := float(s.deck_u) * length * 0.5
		builder.box(TRIM, Vector3(0, deck_y + 0.45, cab_rear - 0.08), Vector3(float(s.width) - 0.2, 0.9, 0.08), Color())
		builder.box(TRIM, Vector3(0, deck_y + 0.25, -length * 0.5 + 0.2), Vector3(0.5, 0.5, 0.36), Color())
		builder.box(CHROME, Vector3(0, deck_y + 0.3, -length * 0.5 + 0.06), Vector3(0.14, 0.14, 0.1), Color())


static func _wheel_mesh(s: Dictionary, hub: Vector3, side: float) -> ArrayMesh:
	## Tyre with rounded shoulders, a recessed five-spoke alloy and a dark
	## brake disc, revolved around the axle (local X).
	var builder := Builder.new(1)
	var r := float(s.wheel_radius)
	var w := float(s.wheel_width) * 0.5
	var rim := r * 0.66
	var rubber := Color(0.035, 0.035, 0.037, 0.0)
	var wall := Color(0.05, 0.05, 0.052, 0.0)
	var alloy := Color(0.66, 0.67, 0.69, 1.0)
	var disc := Color(0.12, 0.12, 0.12, 1.0)
	var heavy := r >= 0.42
	if heavy:
		alloy = Color(0.58, 0.59, 0.6, 1.0)
	# Outward is +X for the left side (side = 1).
	var profile := [
		[rim, -w * 0.92, wall], [r - 0.035, -w, wall], [r - 0.006, -w * 0.82, rubber], [r, -w * 0.45, rubber],
		[r, w * 0.45, rubber], [r - 0.006, w * 0.82, rubber], [r - 0.035, w, wall], [rim, w * 0.92, wall],
		[rim * 0.97, w * 0.86, alloy],
	]
	var segments := 28
	for i in segments:
		var a0 := TAU * float(i) / float(segments)
		var a1 := TAU * float(i + 1) / float(segments)
		for j in profile.size() - 1:
			var p0 := profile[j] as Array
			var p1 := profile[j + 1] as Array
			var v00 := hub + Vector3(float(p0[1]) * side, cos(a0) * float(p0[0]), sin(a0) * float(p0[0]))
			var v01 := hub + Vector3(float(p1[1]) * side, cos(a0) * float(p1[0]), sin(a0) * float(p1[0]))
			var v11 := hub + Vector3(float(p1[1]) * side, cos(a1) * float(p1[0]), sin(a1) * float(p1[0]))
			var v10 := hub + Vector3(float(p0[1]) * side, cos(a1) * float(p0[0]), sin(a1) * float(p0[0]))
			var mid := (v00 + v01 + v11 + v10) * 0.25
			var radial := Vector3(0, mid.y - hub.y, mid.z - hub.z)
			var outward := radial if j >= 2 and j <= 4 else (radial + Vector3(side * (1.0 if float(p0[1]) > 0.0 else -1.0) * radial.length(), 0, 0))
			builder.quad(0, v00, v01, v11, v10, outward, p0[2] as Color if j < profile.size() - 2 else alloy, 0)
		# Recessed brake disc face and inner face.
		var dish_x := w * 0.55 * side
		var c0 := hub + Vector3(dish_x, 0, 0)
		builder.tri(0, c0, hub + Vector3(dish_x, cos(a0) * rim * 0.97, sin(a0) * rim * 0.97), hub + Vector3(dish_x, cos(a1) * rim * 0.97, sin(a1) * rim * 0.97), Vector3(side, 0, 0), disc, -1)
		var inner_x := -w * 0.92 * side
		builder.tri(0, hub + Vector3(inner_x, 0, 0), hub + Vector3(inner_x, cos(a0) * rim, sin(a0) * rim), hub + Vector3(inner_x, cos(a1) * rim, sin(a1) * rim), Vector3(-side, 0, 0), disc, -1)
		# Rim barrel between the lip and the dish.
		builder.quad(0, hub + Vector3(w * 0.86 * side, cos(a0) * rim * 0.97, sin(a0) * rim * 0.97), hub + Vector3(dish_x, cos(a0) * rim * 0.97, sin(a0) * rim * 0.97),
			hub + Vector3(dish_x, cos(a1) * rim * 0.97, sin(a1) * rim * 0.97), hub + Vector3(w * 0.86 * side, cos(a1) * rim * 0.97, sin(a1) * rim * 0.97),
			-Vector3(0, cos((a0 + a1) * 0.5), sin((a0 + a1) * 0.5)), Color(0.45, 0.46, 0.47, 1.0), -1)
	# Spokes from the hub cap to the rim lip, standing proud of the dish.
	var spoke_count := 8 if heavy else 5
	for k in spoke_count:
		var angle := TAU * float(k) / float(spoke_count)
		var dir := Vector3(0, cos(angle), sin(angle))
		var perp := Vector3(0, -sin(angle), cos(angle))
		var hub_r := rim * 0.22
		var x_face := w * 0.8 * side
		var x_back := w * 0.58 * side
		var width_in := rim * 0.16
		var width_out := rim * 0.11
		var a := hub + Vector3(x_face, 0, 0) + dir * hub_r - perp * width_in
		var b := hub + Vector3(x_face, 0, 0) + dir * rim * 0.95 - perp * width_out
		var c := hub + Vector3(x_face, 0, 0) + dir * rim * 0.95 + perp * width_out
		var d := hub + Vector3(x_face, 0, 0) + dir * hub_r + perp * width_in
		builder.quad(0, a, b, c, d, Vector3(side, 0, 0), alloy, -1)
		for edge in [[a, b, -perp], [d, c, perp]]:
			var e0 := edge[0] as Vector3
			var e1 := edge[1] as Vector3
			var back := Vector3(x_back - x_face, 0, 0)
			builder.quad(0, e0, e1, e1 + back, e0 + back, edge[2] as Vector3, Color(0.5, 0.51, 0.52, 1.0), -1)
	# Hub cap.
	for i in 10:
		var a0 := TAU * float(i) / 10.0
		var a1 := TAU * float(i + 1) / 10.0
		var x := w * 0.84 * side
		builder.tri(0, hub + Vector3(x, 0, 0), hub + Vector3(x, cos(a0) * rim * 0.24, sin(a0) * rim * 0.24), hub + Vector3(x, cos(a1) * rim * 0.24, sin(a1) * rim * 0.24), Vector3(side, 0, 0), Color(0.72, 0.73, 0.75, 1.0), -1)
	return builder.commit([_get_wheel_material()])
