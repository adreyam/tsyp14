extends Node3D

## A building coming down. The original (batched, static) building is hidden and replaced by a stack of slabs: copies of its own
## meshes, each clipped to one band of its height. Floors fail from the bottom: every slab is crushed in turn while the ones above
## it drop onto the debris, drift and tilt, with dust and flying debris, and what is left is a low pile of rubble. restore() puts
## the building back exactly as it was, as if nothing had happened.

const SlabShader := preload("res://shaders/building_slab.gdshader")
const CRUSH_STAGGER := 0.42
const CRUSH_SECONDS := 1.5
const FINAL_SQUASH := 0.07
const TOTAL_SECONDS := 7.5
const DUST_COLOUR := Color(0.64, 0.60, 0.54)

signal landed(position: Vector3, strength: float)

var building: Node3D
var finished := false
var started := false
var seed_value := 0
var base_y := 0.0
var height := 10.0
var centre := Vector3.ZERO
var footprint := Vector2(10.0, 10.0)
var _slabs: Array[Dictionary] = []
var _clock := 0.0
var _rng := RandomNumberGenerator.new()
var _dust: GPUParticles3D
var _debris: GPUParticles3D
var _haze: GPUParticles3D
var _rubble: Node3D
var _bursts_done := 0
var _dir := Vector3.RIGHT

func setup(target: Node3D, seed_number: int) -> void:
	building = target
	seed_value = seed_number
	_rng.seed = seed_number
	name = "Collapse_%s" % target.name
	_measure()
	_dir = Vector3(_rng.randf_range(-1.0, 1.0), 0.0, _rng.randf_range(-1.0, 1.0)).normalized()
	_build_slabs()

func _measure() -> void:
	var low := Vector3(INF, INF, INF)
	var high := Vector3(-INF, -INF, -INF)
	for mesh in _body_meshes():
		var box := mesh.global_transform * mesh.get_aabb()
		low = low.min(box.position)
		high = high.max(box.end)
	if low.x == INF:
		low = building.global_position - Vector3(5, 0, 5)
		high = building.global_position + Vector3(5, 10, 5)
	base_y = low.y
	height = maxf(high.y - low.y, 3.0)
	centre = Vector3((low.x + high.x) * 0.5, base_y, (low.z + high.z) * 0.5)
	footprint = Vector2(maxf(high.x - low.x, 4.0), maxf(high.z - low.z, 4.0))

## The building's own body meshes (not the window glow overlays, lamps or signs, which fall with the original).
func _body_meshes() -> Array[MeshInstance3D]:
	var out: Array[MeshInstance3D] = []
	for value in building.find_children("*", "MeshInstance3D", true, false):
		var mesh := value as MeshInstance3D
		if mesh == null or mesh.mesh == null or mesh.is_in_group("building_window_lights") or mesh.is_in_group("building_sign_lights"):
			continue
		var ignored := false
		var node: Node = mesh.get_parent()
		while node != null and node != building:
			if "Lantern" in str(node.name) or "Sign" in str(node.name) or "Glow" in str(node.name):
				ignored = true
			node = node.get_parent()
		if ignored or "Glow" in str(mesh.name):
			continue
		var standard := true
		for surface in mesh.mesh.get_surface_count():
			if not mesh.get_active_material(surface) is StandardMaterial3D:
				standard = false
		var size := (mesh.global_transform * mesh.get_aabb()).size
		if standard and maxf(size.x, maxf(size.y, size.z)) > 1.5:
			out.append(mesh)
	return out

func _build_slabs() -> void:
	var count := clampi(int(ceil(height / 4.2)), 3, 8)
	var slab_height := height / float(count)
	var meshes := _body_meshes()
	for index in count:
		var low_y := base_y + slab_height * float(index)
		var pivot := Node3D.new()
		pivot.name = "Slab_%d" % index
		add_child(pivot)
		pivot.global_position = Vector3(centre.x, low_y + slab_height * 0.5, centre.z)
		for source in meshes:
			var copy := MeshInstance3D.new()
			copy.mesh = source.mesh
			copy.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON
			pivot.add_child(copy)
			copy.global_transform = source.global_transform
			var inverse := source.global_transform.affine_inverse()
			var lower := (inverse * Vector3(source.global_position.x, low_y - 0.01, source.global_position.z)).y
			var upper := (inverse * Vector3(source.global_position.x, low_y + slab_height + 0.01, source.global_position.z)).y
			if index == 0:
				lower = -100000.0
			if index == count - 1:
				upper = 100000.0
			for surface in source.mesh.get_surface_count():
				var original := source.get_active_material(surface) as StandardMaterial3D
				var material := ShaderMaterial.new()
				material.shader = SlabShader
				material.set_shader_parameter("albedo_color", original.albedo_color)
				material.set_shader_parameter("use_texture", original.albedo_texture != null)
				if original.albedo_texture != null:
					material.set_shader_parameter("albedo_texture", original.albedo_texture)
				material.set_shader_parameter("use_vertex_color", original.vertex_color_use_as_albedo)
				material.set_shader_parameter("band_min", minf(lower, upper))
				material.set_shader_parameter("band_max", maxf(lower, upper))
				copy.set_surface_override_material(surface, material)
		_slabs.append({
			"node": pivot, "index": index, "height": slab_height, "rest": pivot.global_position,
			"drift": _dir.rotated(Vector3.UP, _rng.randf_range(-0.9, 0.9)) * _rng.randf_range(1.2, 4.2) * (0.35 + float(index) / float(count)),
			"tilt_axis": Vector3(_rng.randf_range(-1, 1), 0.0, _rng.randf_range(-1, 1)).normalized(),
			"tilt": deg_to_rad(_rng.randf_range(3.0, 13.0)) * (0.35 + float(index) / float(count)),
			"spin": deg_to_rad(_rng.randf_range(-9.0, 9.0)),
			"start": 0.35 + CRUSH_STAGGER * float(index) + _rng.randf_range(0.0, 0.2),
		})

# --- playback ---------------------------------------------------------------------------------

func play() -> void:
	if started:
		return
	started = true
	building.visible = false
	_make_effects()

func _process(delta: float) -> void:
	if not started or finished:
		return
	_clock += delta
	var shake := clampf(1.0 - _clock / 2.2, 0.0, 1.0) * 0.12
	var stack := 0.0
	for slab in _slabs:
		var node := slab.node as Node3D
		var crush := smoothstep(float(slab.start), float(slab.start) + CRUSH_SECONDS, _clock)
		var squash := lerpf(1.0, FINAL_SQUASH, crush)
		var slab_height := float(slab.height)
		var rest := slab.rest as Vector3
		var fall := smoothstep(float(slab.start) + 0.3, float(slab.start) + CRUSH_SECONDS + 1.0, _clock)
		node.scale = Vector3(1.0 + 0.04 * crush, squash, 1.0 + 0.04 * crush)
		var y := base_y + stack + slab_height * squash * 0.5
		var drift := (slab.drift as Vector3) * fall
		var jitter := Vector3(sin(_clock * 53.0 + float(slab.index) * 1.7), 0.0, cos(_clock * 47.0 + float(slab.index) * 2.3)) * shake
		node.global_position = Vector3(rest.x, y, rest.z) + drift + jitter
		node.basis = Basis(slab.tilt_axis as Vector3, float(slab.tilt) * fall) * Basis(Vector3.UP, float(slab.spin) * fall) * Basis.from_scale(node.scale)
		stack += slab_height * squash
	_effects_step()
	if _clock >= TOTAL_SECONDS:
		_finish()

func _effects_step() -> void:
	var due := int(_clock / 0.9)
	while _bursts_done < due and _bursts_done < 8:
		_bursts_done += 1
		if _debris != null:
			_debris.restart()
		landed.emit(centre, clampf(0.4 + float(_bursts_done) * 0.1, 0.0, 1.0))

func _finish() -> void:
	finished = true
	_build_rubble()
	if _dust != null:
		_dust.emitting = false
	if _haze != null:
		_haze.emitting = true
		var timer := get_tree().create_timer(28.0)
		timer.timeout.connect(func() -> void:
			if is_instance_valid(_haze):
				_haze.emitting = false)

func _make_effects() -> void:
	_dust = _particles("Dust", 260, 7.0, Vector3(footprint.x * 0.5, 0.4, footprint.y * 0.5), Vector3(0, 1, 0), 4.0, 0.45, 0.16, Vector2(2.2, 6.5), true)
	_dust.emitting = true
	_dust.one_shot = false
	_debris = _particles("Debris", 90, 2.6, Vector3(footprint.x * 0.4, height * 0.3, footprint.y * 0.4), Vector3(0, 1, 0), 9.0, 0.5, 0.55, Vector2(0.25, 0.7), false)
	_debris.one_shot = true
	_debris.explosiveness = 0.9
	_debris.emitting = false
	_haze = _particles("Haze", 70, 9.0, Vector3(footprint.x * 0.5, 0.3, footprint.y * 0.5), Vector3(0, 1, 0), 1.2, 0.2, 0.09, Vector2(4.0, 9.0), true)
	_haze.emitting = false

func _particles(label: String, amount: int, lifetime: float, extents: Vector3, direction: Vector3, speed: float, spread: float, alpha: float, size: Vector2, soft: bool) -> GPUParticles3D:
	var particles := GPUParticles3D.new()
	particles.name = label
	particles.amount = amount
	particles.lifetime = lifetime
	particles.explosiveness = 0.0
	particles.randomness = 0.6
	particles.visibility_aabb = AABB(Vector3(-60, -2, -60), Vector3(120, 90, 120))
	particles.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	var process := ParticleProcessMaterial.new()
	process.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_BOX
	process.emission_box_extents = extents
	process.direction = direction
	process.spread = 180.0 * spread
	process.initial_velocity_min = speed * 0.35
	process.initial_velocity_max = speed
	process.gravity = Vector3(0, 0.6 if soft else -9.8, 0)
	process.damping_min = 1.5 if soft else 0.0
	process.damping_max = 3.0 if soft else 0.0
	process.scale_min = size.x
	process.scale_max = size.y
	var ramp := Gradient.new()
	ramp.set_color(0, Color(DUST_COLOUR.r, DUST_COLOUR.g, DUST_COLOUR.b, 0.0))
	ramp.add_point(0.12, Color(DUST_COLOUR.r, DUST_COLOUR.g, DUST_COLOUR.b, alpha))
	ramp.set_color(ramp.get_point_count() - 1, Color(DUST_COLOUR.r * 0.9, DUST_COLOUR.g * 0.9, DUST_COLOUR.b * 0.9, 0.0))
	var texture := GradientTexture1D.new()
	texture.gradient = ramp
	if soft:
		process.color_ramp = texture
		var curve := Curve.new()
		curve.add_point(Vector2(0.0, 0.35))
		curve.add_point(Vector2(1.0, 1.4))
		var curve_texture := CurveTexture.new()
		curve_texture.curve = curve
		process.scale_curve = curve_texture
	particles.process_material = process
	var quad: Mesh
	var material := StandardMaterial3D.new()
	if soft:
		var sphere := SphereMesh.new()
		sphere.radius = 0.5
		sphere.height = 1.0
		sphere.radial_segments = 10
		sphere.rings = 6
		quad = sphere
		material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
		material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
		material.vertex_color_use_as_albedo = true
		material.albedo_color = Color(1, 1, 1, 1)
		material.disable_receive_shadows = true
	else:
		var chunk := BoxMesh.new()
		chunk.size = Vector3(0.5, 0.35, 0.4)
		quad = chunk
		material.albedo_color = Color(0.46, 0.43, 0.40)
		material.roughness = 0.95
	particles.draw_pass_1 = quad
	particles.material_override = material
	add_child(particles)
	particles.global_position = Vector3(centre.x, base_y + (0.5 if soft else height * 0.35), centre.z)
	return particles

func _build_rubble() -> void:
	_rubble = Node3D.new()
	_rubble.name = "Rubble"
	add_child(_rubble)
	var material := StandardMaterial3D.new()
	material.albedo_color = Color(0.43, 0.41, 0.39)
	material.roughness = 0.96
	var chunks := clampi(int(footprint.x * footprint.y / 9.0), 14, 60)
	for index in chunks:
		var chunk := MeshInstance3D.new()
		var box := BoxMesh.new()
		box.size = Vector3(_rng.randf_range(0.9, 3.2), _rng.randf_range(0.4, 1.5), _rng.randf_range(0.9, 3.0))
		chunk.mesh = box
		chunk.material_override = material
		_rubble.add_child(chunk)
		var spread := Vector3(_rng.randf_range(-0.5, 0.5) * footprint.x, 0.0, _rng.randf_range(-0.5, 0.5) * footprint.y)
		chunk.global_position = Vector3(centre.x + spread.x, base_y + box.size.y * 0.5 * _rng.randf_range(0.4, 1.4) + _rng.randf_range(0.0, 0.9), centre.z + spread.z)
		chunk.rotation = Vector3(_rng.randf_range(-0.4, 0.4), _rng.randf_range(0.0, TAU), _rng.randf_range(-0.4, 0.4))

## Back to a standing building, as if it had never fallen.
func restore() -> void:
	if is_instance_valid(building):
		building.visible = true
		building.remove_meta("collapsed")
	queue_free()
