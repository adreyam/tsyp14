extends Node

## Runtime control of every lit window in the city.
##
## Each building owns one window-state texture (one texel per window: lit level,
## tint, flicker, brightness variation) read by building_interior_window.gdshader,
## so any window can be lit, dimmed, tinted or switched off on its own. By
## default the windows follow the time of day: more are lit in the evening,
## few in the small hours, each at its own deterministic threshold, so they come
## on and go off one by one rather than all together. Anything else (a citizen
## coming home, a power cut, an alarm) can take over a window, a building or the
## whole city through the methods below.
##
## A lit window is not just a brighter colour: the lit windows nearest the camera
## each get a real, soft warm light placed just outside the pane, taken from a
## small pool that follows the camera, so the facade, the pavement and anything
## standing below are actually lit by them. The pool keeps the cost constant
## however many thousands of windows the city has.
##
## Public API
##   set_window_level(building, window_index, level)  -1 returns it to the schedule
##   set_window_tint(building, window_index, tint)    0 warm .. 1 cool (television)
##   set_building_fraction(building, fraction)        share lit; -1 = schedule
##   set_building_powered(building, powered)
##   set_global_fraction(fraction)                    -1 = schedule
##   get_windows(building) -> Array of {index, position, normal, size, level, overridden}
##   find_windows_near(point, radius) -> Array of {building, index, position, level}

signal windows_changed(building: Node3D)

const STATE_WIDTH := 64
const PATCH_GRID := 64
const STATE_REFRESH_SECONDS := 0.5
const LEVEL_FOLLOW_PER_SECOND := 2.4
const LIGHT_POOL_SIZE := 40
const LIGHT_RADIUS := 62.0
const LIGHT_REFRESH_SECONDS := 0.3
const LIGHT_FADE_PER_SECOND := 3.0
const NIGHT_ACTIVE := 0.1
## Share of the city's windows lit through the day, as [minute of day, share];
## linear between entries (the shader only shows them when it is dark).
const SCHEDULE := [
	[0, 0.30], [90, 0.13], [240, 0.06], [330, 0.16], [390, 0.42], [450, 0.18],
	[600, 0.10], [1020, 0.28], [1110, 0.58], [1200, 0.82], [1320, 0.66], [1410, 0.42], [1440, 0.30],
]

var _entries: Array[Dictionary] = []
var _by_node := {}
var environment: Node
var _night := 0.0
var _minute := 540.0
var _global_fraction := -1.0
var _state_clock := 0.0
var _light_clock := 0.0
var _lights: Array[Dictionary] = []
var _total_windows := 0


func _ready() -> void:
	name = "BuildingWindowController"
	for slot in LIGHT_POOL_SIZE:
		var light := OmniLight3D.new()
		light.name = "WindowLight_%02d" % slot
		light.top_level = true
		light.light_energy = 0.0
		light.visible = false
		light.shadow_enabled = false
		light.omni_attenuation = 1.5
		light.light_specular = 0.25
		light.distance_fade_enabled = false
		add_child(light)
		_lights.append({"light": light, "entry": -1, "window": -1, "energy": 0.0, "target": 0.0})


# --- Building registration (used by SmartCityAssets while it builds the overlays) -------------

func create_building(building: Node3D, building_index: int, occupancy: float, painted: bool) -> Dictionary:
	var entry := {
		"node": building, "index": building_index, "occupancy": occupancy, "painted": painted,
		"windows": [], "materials": [], "image": null, "texture": null, "dirty": true,
		"powered": true, "fraction": -1.0, "centre": building.global_position, "radius": 20.0,
	}
	_entries.append(entry)
	_by_node[building.get_instance_id()] = _entries.size() - 1
	return entry


func add_part(entry: Dictionary, mesh_node: MeshInstance3D, overlay: MeshInstance3D, local_windows: Array, material: ShaderMaterial) -> void:
	## One overlay mesh of the building and the windows it carries (in the source
	## mesh's local space; the overlay is a sibling with the same transform).
	if not (entry.materials as Array).has(material):
		(entry.materials as Array).append(material)
	var offset := (entry.windows as Array).size()
	overlay.set_instance_shader_parameter("id_offset", offset)
	var bounds_point := mesh_node.global_position
	for local_index in local_windows.size():
		var local := local_windows[local_index] as Dictionary
		var world_normal := (mesh_node.global_basis * (local.normal as Vector3)).normalized()
		var world_centre := mesh_node.global_transform * (local.centre as Vector3)
		var scale := mesh_node.global_basis.get_scale()
		var size := local.size as Vector2
		(entry.windows as Array).append({
			"position": world_centre, "normal": world_normal,
			"size": Vector2(size.x * scale.x, size.y * scale.y),
			"random": _hash01(int(entry.index) * 7919 + offset + local_index),
			"level": 0.0, "target": 0.0, "override": -1.0, "tint": 0.0, "flicker": 0.0,
			"variation": _hash01(int(entry.index) * 104729 + offset + local_index * 3 + 11),
		})
	entry.radius = maxf(float(entry.radius), bounds_point.distance_to(entry.centre) + 6.0)


func finish_building(entry: Dictionary) -> void:
	var windows := entry.windows as Array
	_total_windows += windows.size()
	var rows := PATCH_GRID if bool(entry.painted) else maxi(1, int(ceil(float(maxi(windows.size(), 1)) / float(STATE_WIDTH))))
	var image := Image.create(STATE_WIDTH, rows, false, Image.FORMAT_RGBA8)
	var texture := ImageTexture.create_from_image(image)
	entry.image = image
	entry.texture = texture
	for material in entry.materials as Array:
		(material as ShaderMaterial).set_shader_parameter("window_state", texture)
		(material as ShaderMaterial).set_shader_parameter("window_state_width", STATE_WIDTH)
	if bool(entry.painted):
		_seed_patches(entry)
	entry.dirty = true


func window_count() -> int:
	return _total_windows


# --- Control API -------------------------------------------------------------------------------

func set_window_level(building: Node3D, window_index: int, level: float) -> void:
	var window := _window(building, window_index)
	if window.is_empty():
		return
	window.override = -1.0 if level < 0.0 else clampf(level, 0.0, 1.0)
	_mark_dirty(building)


func set_window_tint(building: Node3D, window_index: int, tint: float) -> void:
	var window := _window(building, window_index)
	if window.is_empty():
		return
	window.tint = clampf(tint, 0.0, 1.0)
	window.flicker = 1.0 if tint > 0.5 else 0.0
	_mark_dirty(building)


func set_building_fraction(building: Node3D, fraction: float) -> void:
	var entry := _entry(building)
	if entry.is_empty():
		return
	entry.fraction = fraction
	entry.dirty = true
	windows_changed.emit(building)


func set_building_powered(building: Node3D, powered: bool) -> void:
	var entry := _entry(building)
	if entry.is_empty():
		return
	entry.powered = powered
	entry.dirty = true
	windows_changed.emit(building)


func set_global_fraction(fraction: float) -> void:
	_global_fraction = fraction
	for entry in _entries:
		entry.dirty = true


func get_windows(building: Node3D) -> Array[Dictionary]:
	var result: Array[Dictionary] = []
	var entry := _entry(building)
	if entry.is_empty():
		return result
	var windows := entry.windows as Array
	for index in windows.size():
		var window := windows[index] as Dictionary
		result.append({"index": index, "position": window.position, "normal": window.normal, "size": window.size, "level": window.level, "overridden": float(window.override) >= 0.0})
	return result


func find_windows_near(point: Vector3, radius: float) -> Array[Dictionary]:
	var result: Array[Dictionary] = []
	for entry in _entries:
		if (entry.centre as Vector3).distance_to(point) > radius + float(entry.radius):
			continue
		var windows := entry.windows as Array
		for index in windows.size():
			var window := windows[index] as Dictionary
			if (window.position as Vector3).distance_to(point) <= radius:
				result.append({"building": entry.node, "index": index, "position": window.position, "level": window.level})
	return result


func lit_window_count() -> int:
	var count := 0
	for entry in _entries:
		for window in entry.windows as Array:
			if float((window as Dictionary).level) > 0.5:
				count += 1
	return count


func pool_lights_active() -> int:
	var count := 0
	for slot in _lights:
		if (slot.light as Light3D).visible:
			count += 1
	return count


# --- Simulation -------------------------------------------------------------------------------

func _process(delta: float) -> void:
	_state_clock += delta
	_light_clock += delta
	if _state_clock >= STATE_REFRESH_SECONDS:
		var step := _state_clock
		_state_clock = 0.0
		_refresh_conditions()
		if _night >= NIGHT_ACTIVE or _any_dirty():
			_update_states(step)
	if _light_clock >= LIGHT_REFRESH_SECONDS:
		_light_clock = 0.0
		_assign_lights()
	_fade_lights(delta)


func _refresh_conditions() -> void:
	if environment != null and environment.has_method("get_conditions"):
		var conditions := environment.call("get_conditions") as Dictionary
		_night = float(conditions.get("night_factor", 0.0))
		_minute = float(conditions.get("minute_of_day", 0)) + (float(conditions.get("simulation_minute", 0.0)) - floorf(float(conditions.get("simulation_minute", 0.0))))


func _any_dirty() -> bool:
	for entry in _entries:
		if bool(entry.dirty):
			return true
	return false


func activity_at(minute_of_day: float) -> float:
	## The scheduled share of windows lit at a time of day.
	var previous := SCHEDULE[0] as Array
	for point in SCHEDULE:
		var current := point as Array
		if minute_of_day <= float(current[0]):
			var span := maxf(float(current[0]) - float(previous[0]), 0.001)
			return lerpf(float(previous[1]), float(current[1]), (minute_of_day - float(previous[0])) / span)
		previous = current
	return float((SCHEDULE[SCHEDULE.size() - 1] as Array)[1])


func _update_states(step: float) -> void:
	var base := activity_at(_minute) if _global_fraction < 0.0 else _global_fraction
	var follow := minf(1.0, LEVEL_FOLLOW_PER_SECOND * step)
	for entry in _entries:
		var changed := bool(entry.dirty)
		var share := base * float(entry.occupancy) if float(entry.fraction) < 0.0 else float(entry.fraction)
		share = clampf(share, 0.0, 1.0)
		if not bool(entry.powered):
			share = 0.0
		if bool(entry.painted):
			changed = _update_patches(entry, share, follow) or changed
		else:
			var image := entry.image as Image
			for index in (entry.windows as Array).size():
				var window := (entry.windows as Array)[index] as Dictionary
				var target := 0.0
				if not bool(entry.powered):
					target = 0.0
				elif float(window.override) >= 0.0:
					target = float(window.override)
				else:
					target = 1.0 if float(window.random) < share else 0.0
				window.target = target
				var level := float(window.level)
				if not is_equal_approx(level, target):
					level = move_toward(level, target, follow)
					window.level = level
					changed = true
					image.set_pixel(index % STATE_WIDTH, index / STATE_WIDTH, Color(level, float(window.tint), float(window.flicker), float(window.variation)))
				elif bool(entry.dirty):
					image.set_pixel(index % STATE_WIDTH, index / STATE_WIDTH, Color(level, float(window.tint), float(window.flicker), float(window.variation)))
		if changed:
			(entry.texture as ImageTexture).update(entry.image as Image)
		entry.dirty = false


func _seed_patches(entry: Dictionary) -> void:
	## Painted-window buildings: a 64x64 patch map over the atlas UV space.
	var image := entry.image as Image
	var randoms := PackedFloat32Array()
	randoms.resize(PATCH_GRID * PATCH_GRID)
	for index in randoms.size():
		randoms[index] = _hash01(int(entry.index) * 65537 + index)
		image.set_pixel(index % PATCH_GRID, index / PATCH_GRID, Color(0.0, 0.0, 0.0, _hash01(int(entry.index) * 131 + index * 7)))
	var levels := PackedFloat32Array()
	levels.resize(PATCH_GRID * PATCH_GRID)
	entry.windows_patch_random = randoms
	entry.patch_levels = levels


func _update_patches(entry: Dictionary, share: float, follow: float) -> bool:
	var image := entry.image as Image
	var randoms := entry.windows_patch_random as PackedFloat32Array
	var levels := entry.patch_levels as PackedFloat32Array
	var changed := false
	for index in randoms.size():
		var target := 1.0 if randoms[index] < share else 0.0
		var level := levels[index]
		if not is_equal_approx(level, target):
			level = move_toward(level, target, follow)
			levels[index] = level
			var x := index % PATCH_GRID
			var y := index / PATCH_GRID
			var current := image.get_pixel(x, y)
			image.set_pixel(x, y, Color(level, current.g, current.b, current.a))
			changed = true
	entry.patch_levels = levels
	return changed


# --- Real light from the windows nearest the camera ---------------------------------------------

func _assign_lights() -> void:
	var camera := get_viewport().get_camera_3d() if get_viewport() != null else null
	if camera == null or _night < NIGHT_ACTIVE:
		for slot in _lights:
			slot.target = 0.0
		return
	var eye := camera.global_position
	var candidates: Array = []
	for entry_index in _entries.size():
		var entry := _entries[entry_index]
		if bool(entry.painted) or (entry.centre as Vector3).distance_to(eye) > LIGHT_RADIUS + float(entry.radius):
			continue
		var windows := entry.windows as Array
		for window_index in windows.size():
			var window := windows[window_index] as Dictionary
			if float(window.level) < 0.3:
				continue
			var distance := (window.position as Vector3).distance_to(eye)
			if distance <= LIGHT_RADIUS:
				candidates.append([distance, entry_index, window_index])
	candidates.sort_custom(func(a: Array, b: Array) -> bool: return float(a[0]) < float(b[0]))
	var wanted := {}
	for pick in candidates.slice(0, LIGHT_POOL_SIZE):
		wanted[Vector2i(int((pick as Array)[1]), int((pick as Array)[2]))] = true
	# Keep lights that still belong to a wanted window; release the rest.
	for slot in _lights:
		var key := Vector2i(int(slot.entry), int(slot.window))
		if int(slot.entry) >= 0 and wanted.has(key):
			wanted.erase(key)
			slot.target = _light_energy(_entries[int(slot.entry)], int(slot.window), eye)
		else:
			slot.target = 0.0
	# Hand waiting windows to lights that have faded out.
	for slot in _lights:
		if wanted.is_empty():
			break
		if float(slot.target) > 0.0 or float(slot.energy) > 0.02:
			continue
		var key: Vector2i = wanted.keys()[0]
		wanted.erase(key)
		slot.entry = key.x
		slot.window = key.y
		var entry := _entries[key.x]
		var window := (entry.windows as Array)[key.y] as Dictionary
		var light := slot.light as OmniLight3D
		light.global_position = (window.position as Vector3) + (window.normal as Vector3) * 0.7
		var size := window.size as Vector2
		light.omni_range = clampf(3.4 + maxf(size.x, size.y) * 1.3, 4.0, 8.0)
		light.light_color = Color(1.0, 0.66, 0.34).lerp(Color(0.66, 0.82, 1.0), float(window.tint))
		slot.target = _light_energy(entry, key.y, eye)


func _light_energy(entry: Dictionary, window_index: int, _eye: Vector3) -> float:
	var window := (entry.windows as Array)[window_index] as Dictionary
	var size := window.size as Vector2
	var area := clampf(size.x * size.y, 0.4, 6.0)
	return float(window.level) * (0.55 + 0.32 * sqrt(area)) * lerpf(0.8, 1.15, float(window.variation))


func _fade_lights(delta: float) -> void:
	for slot in _lights:
		var light := slot.light as OmniLight3D
		var energy := move_toward(float(slot.energy), float(slot.target), LIGHT_FADE_PER_SECOND * delta)
		slot.energy = energy
		light.light_energy = energy
		light.visible = energy > 0.01
		if energy <= 0.01 and float(slot.target) <= 0.0:
			slot.entry = -1
			slot.window = -1


# --- Helpers -----------------------------------------------------------------------------------

func _entry(building: Node3D) -> Dictionary:
	if building == null or not _by_node.has(building.get_instance_id()):
		return {}
	return _entries[int(_by_node[building.get_instance_id()])]


func _window(building: Node3D, window_index: int) -> Dictionary:
	var entry := _entry(building)
	if entry.is_empty() or window_index < 0 or window_index >= (entry.windows as Array).size():
		return {}
	return (entry.windows as Array)[window_index] as Dictionary


func _mark_dirty(building: Node3D) -> void:
	var entry := _entry(building)
	if not entry.is_empty():
		entry.dirty = true
		windows_changed.emit(building)


static func _hash01(seed_value: int) -> float:
	var x := seed_value * 374761393 + 668265263
	x = (x ^ (x >> 13)) * 1274126177
	x = x ^ (x >> 16)
	return float(x & 0xFFFFFF) / float(0x1000000)
