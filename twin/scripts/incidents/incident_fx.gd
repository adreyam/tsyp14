extends RefCounted

## Particle effects for incidents, built from the shared noise shaders.
## Every effect is a Node3D whose `set_intensity(0..1)` scales emission
## smoothly, so the bridge's fire/water state drives it without popping.

const FLAME_SHADER := preload("res://shaders/incident_flame.gdshader")
const SMOKE_SHADER := preload("res://shaders/incident_smoke.gdshader")
const WATER_SHADER := preload("res://shaders/incident_water.gdshader")
const STREAK_SHADER := preload("res://shaders/incident_water_streak.gdshader")
const FX_LAYER := 8

static var _materials: Dictionary = {}


class Effect extends Node3D:
	var emitters: Array[GPUParticles3D] = []
	var base_amounts: Array[float] = []
	var lights: Array[OmniLight3D] = []
	var light_energy := 0.0
	var intensity := 0.0
	var target := 0.0
	var flicker := 0.0

	func add_emitter(emitter: GPUParticles3D) -> void:
		emitters.append(emitter)
		base_amounts.append(float(emitter.amount))
		emitter.layers = FX_LAYER
		add_child(emitter)

	func set_intensity(value: float) -> void:
		target = clampf(value, 0.0, 1.0)

	func _process(delta: float) -> void:
		intensity = move_toward(intensity, target, delta * 0.6)
		for index in emitters.size():
			var emitter := emitters[index]
			emitter.emitting = intensity > 0.02
			emitter.amount_ratio = clampf(intensity, 0.0, 1.0)
		flicker += delta
		for light in lights:
			var wobble := 0.78 + 0.22 * sin(flicker * 11.0 + light.get_instance_id() % 7) * sin(flicker * 7.3)
			light.light_energy = light_energy * intensity * wobble


static func _streak(width: float, length: float, parameters := {}) -> QuadMesh:
	var quad := _quad(1.0, STREAK_SHADER, parameters)
	quad.size = Vector2(width, length)
	return quad


static func _quad(size: float, shader: Shader, parameters: Dictionary) -> QuadMesh:
	var key := "%s|%s" % [shader.resource_path, str(parameters)]
	var material: ShaderMaterial = _materials.get(key)
	if material == null:
		material = ShaderMaterial.new()
		material.shader = shader
		for parameter in parameters:
			material.set_shader_parameter(str(parameter), parameters[parameter])
		_materials[key] = material
	var quad := QuadMesh.new()
	quad.size = Vector2(size, size)
	quad.material = material
	return quad


static func _ramp(colours: Array, offsets: Array) -> GradientTexture1D:
	var gradient := Gradient.new()
	gradient.offsets = PackedFloat32Array(offsets)
	gradient.colors = PackedColorArray(colours)
	var texture := GradientTexture1D.new()
	texture.gradient = gradient
	return texture


static func _curve(points: Array) -> CurveTexture:
	var curve := Curve.new()
	for point in points:
		curve.add_point(point as Vector2)
	var texture := CurveTexture.new()
	texture.curve = curve
	return texture


static func _emitter(amount: int, lifetime: float, mesh: Mesh, process: ParticleProcessMaterial, bounds: AABB) -> GPUParticles3D:
	var particles := GPUParticles3D.new()
	particles.amount = amount
	particles.lifetime = lifetime
	particles.randomness = 0.6
	particles.process_material = process
	particles.draw_pass_1 = mesh
	particles.visibility_aabb = bounds
	particles.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	particles.emitting = false
	return particles


static func ground_collider(half_size: float) -> GPUParticlesCollisionBox3D:
	## Road/sidewalk surface (y = 0.17) for particle collisions.
	var collider := GPUParticlesCollisionBox3D.new()
	collider.name = "GroundCollider"
	collider.size = Vector3(half_size * 2.0, 1.0, half_size * 2.0)
	collider.position = Vector3(0.0, -0.5, 0.0)
	return collider


static func fire(width: float, depth: float, height: float, with_smoke := true) -> Effect:
	## Flames over a width×depth burning area, embers and a smoke column.
	var effect := Effect.new()
	effect.name = "FireEffect"
	var flames := ParticleProcessMaterial.new()
	flames.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_BOX
	flames.emission_box_extents = Vector3(width * 0.5, 0.1, depth * 0.5)
	flames.direction = Vector3.UP
	flames.spread = 12.0
	flames.gravity = Vector3(0.0, 1.6, 0.0)
	flames.initial_velocity_min = 0.6 * height
	flames.initial_velocity_max = 1.2 * height
	flames.scale_min = 0.7
	flames.scale_max = 1.3
	flames.scale_curve = _curve([Vector2(0.0, 0.55), Vector2(0.35, 1.0), Vector2(1.0, 0.25)])
	flames.color_ramp = _ramp([Color(1, 1, 1, 0.0), Color(1, 1, 1, 1.0), Color(1, 1, 1, 0.55), Color(1, 1, 1, 0.0)], [0.0, 0.12, 0.6, 1.0])
	flames.angle_min = -12.0
	flames.angle_max = 12.0
	var flame_size := clampf(minf(width, depth) * 0.9, 0.9, 3.2) * maxf(height * 0.45, 1.0)
	effect.add_emitter(_emitter(int(clampf(width * depth * 6.0, 24.0, 90.0)), 0.9, _quad(flame_size, FLAME_SHADER, {}), flames, AABB(Vector3(-width - 2, -1, -depth - 2), Vector3(width * 2 + 4, height * 3 + 6, depth * 2 + 4))))
	var embers := ParticleProcessMaterial.new()
	embers.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_BOX
	embers.emission_box_extents = Vector3(width * 0.5, 0.3, depth * 0.5)
	embers.direction = Vector3.UP
	embers.spread = 35.0
	embers.gravity = Vector3(0.4, 1.2, 0.2)
	embers.initial_velocity_min = 1.5
	embers.initial_velocity_max = 4.0
	embers.turbulence_enabled = true
	embers.turbulence_noise_strength = 1.4
	embers.scale_min = 0.04
	embers.scale_max = 0.09
	embers.color_ramp = _ramp([Color(1, 0.8, 0.3, 1), Color(1, 0.35, 0.05, 0.8), Color(0.4, 0.05, 0.0, 0.0)], [0.0, 0.6, 1.0])
	var ember_mesh := SphereMesh.new()
	ember_mesh.radius = 0.5
	ember_mesh.height = 1.0
	ember_mesh.radial_segments = 6
	ember_mesh.rings = 3
	var ember_material := StandardMaterial3D.new()
	ember_material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	ember_material.vertex_color_use_as_albedo = true
	ember_material.emission_enabled = true
	ember_material.emission = Color(1.0, 0.45, 0.1)
	ember_material.emission_energy_multiplier = 4.0
	ember_mesh.material = ember_material
	effect.add_emitter(_emitter(28, 2.2, ember_mesh, embers, AABB(Vector3(-8, -1, -8), Vector3(16, 18, 16))))
	if with_smoke:
		effect.add_child(_smoke_column(effect, width, depth, height))
	var light := OmniLight3D.new()
	light.light_color = Color(1.0, 0.55, 0.2)
	light.omni_range = maxf(8.0, width * 3.0)
	light.position = Vector3(0, height * 0.8, 0)
	light.shadow_enabled = false
	effect.add_child(light)
	effect.lights.append(light)
	effect.light_energy = 3.2
	return effect


static func _smoke_column(effect: Effect, width: float, depth: float, height: float) -> Node3D:
	## Dense billow at the source feeding a continuous rising plume: overlapping
	## puffs, buoyancy and a light wind, so it reads as one body of smoke.
	var holder := Node3D.new()
	holder.name = "SmokeColumn"
	var billow := ParticleProcessMaterial.new()
	billow.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_BOX
	billow.emission_box_extents = Vector3(width * 0.4, 0.2, depth * 0.4)
	billow.direction = Vector3.UP
	billow.spread = 20.0
	billow.gravity = Vector3(0.12, 0.9, 0.05)
	billow.initial_velocity_min = 0.8
	billow.initial_velocity_max = 1.4
	billow.damping_min = 0.2
	billow.damping_max = 0.4
	billow.scale_min = 0.8
	billow.scale_max = 1.2
	billow.scale_curve = _curve([Vector2(0.0, 0.3), Vector2(1.0, 1.0)])
	billow.angle_min = -180.0
	billow.angle_max = 180.0
	billow.angular_velocity_min = -12.0
	billow.angular_velocity_max = 12.0
	billow.color_ramp = _ramp([Color(0.05, 0.05, 0.05, 0.0), Color(0.05, 0.05, 0.05, 0.9), Color(0.09, 0.09, 0.09, 0.85)], [0.0, 0.15, 1.0])
	var source_size := clampf(maxf(width, depth) * 1.4, 2.0, 6.0)
	var source := _emitter(int(clampf(width * depth * 6.0, 30.0, 70.0)), 3.2, _quad(source_size, SMOKE_SHADER, {"density": 1.1, "billow_speed": 0.08}), billow, AABB(Vector3(-12, -2, -12), Vector3(24, 16, 24)))
	source.position = Vector3(0, height * 0.6, 0)
	var plume := ParticleProcessMaterial.new()
	plume.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_SPHERE
	plume.emission_sphere_radius = maxf(width, depth) * 0.45
	plume.direction = Vector3.UP
	plume.spread = 8.0
	plume.gravity = Vector3(0.28, 0.75, 0.12)
	plume.initial_velocity_min = 1.8
	plume.initial_velocity_max = 2.6
	plume.damping_min = 0.08
	plume.damping_max = 0.15
	plume.scale_min = 0.9
	plume.scale_max = 1.2
	plume.scale_curve = _curve([Vector2(0.0, 0.45), Vector2(0.6, 0.85), Vector2(1.0, 1.0)])
	plume.angle_min = -180.0
	plume.angle_max = 180.0
	plume.angular_velocity_min = -8.0
	plume.angular_velocity_max = 8.0
	plume.color_ramp = _ramp([Color(0.08, 0.08, 0.08, 0.0), Color(0.09, 0.09, 0.09, 0.8), Color(0.22, 0.22, 0.23, 0.5), Color(0.36, 0.37, 0.39, 0.0)], [0.0, 0.08, 0.6, 1.0])
	var plume_size := clampf(maxf(width, depth) * 2.4, 4.0, 12.0)
	var rising := _emitter(int(clampf(width * depth * 8.0, 50.0, 110.0)), 10.0, _quad(plume_size, SMOKE_SHADER, {"density": 0.95, "billow_speed": 0.05}), plume, AABB(Vector3(-30, -2, -30), Vector3(80, 60 + height * 2.0, 70)))
	rising.position = Vector3(0, height * 0.6 + 2.0, 0)
	for emitter in [source, rising]:
		effect.emitters.append(emitter)
		effect.base_amounts.append(float(emitter.amount))
		emitter.layers = FX_LAYER
		holder.add_child(emitter)
	return holder


static func engine_smoke(steam := false) -> Effect:
	## Light smoke (or steam) leaking from a damaged/overheated engine bay.
	var effect := Effect.new()
	effect.name = "EngineSmoke"
	var process := ParticleProcessMaterial.new()
	process.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_BOX
	process.emission_box_extents = Vector3(0.6, 0.05, 0.5)
	process.direction = Vector3.UP
	process.spread = 25.0
	process.gravity = Vector3(0.3, 0.9, 0.1)
	process.initial_velocity_min = 0.5
	process.initial_velocity_max = 1.2
	process.scale_min = 0.6
	process.scale_max = 1.1
	process.scale_curve = _curve([Vector2(0.0, 0.3), Vector2(1.0, 1.0)])
	process.angle_min = -180.0
	process.angle_max = 180.0
	var colour := Color(0.86, 0.88, 0.9) if steam else Color(0.42, 0.43, 0.45)
	process.color_ramp = _ramp([Color(colour, 0.0), Color(colour, 0.5), Color(colour, 0.0)], [0.0, 0.2, 1.0])
	effect.add_emitter(_emitter(22, 3.5, _quad(1.6, SMOKE_SHADER, {"density": 0.8}), process, AABB(Vector3(-6, -1, -6), Vector3(12, 12, 12))))
	return effect


static func impact_burst(normal: Vector3, energy: float) -> Node3D:
	## One-shot glass, plastic shards and dust at the contact point.
	var holder := Node3D.new()
	holder.name = "ImpactBurst"
	var shards := ParticleProcessMaterial.new()
	shards.direction = Vector3(normal.x, 0.6, normal.z).normalized()
	shards.spread = 70.0
	shards.gravity = Vector3(0, -9.8, 0)
	shards.initial_velocity_min = 1.5 + energy * 0.3
	shards.initial_velocity_max = 3.5 + energy * 0.5
	shards.angular_velocity_min = -400.0
	shards.angular_velocity_max = 400.0
	shards.scale_min = 0.03
	shards.scale_max = 0.09
	shards.collision_mode = ParticleProcessMaterial.COLLISION_RIGID
	shards.collision_friction = 0.8
	shards.collision_bounce = 0.15
	var shard_mesh := BoxMesh.new()
	shard_mesh.size = Vector3(1.0, 0.15, 0.7)
	var glass := StandardMaterial3D.new()
	glass.albedo_color = Color(0.82, 0.9, 0.95, 0.7)
	glass.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	glass.roughness = 0.05
	glass.metallic_specular = 1.0
	shard_mesh.material = glass
	var shard_emitter := _emitter(int(clampf(energy * 10.0, 20.0, 70.0)), 6.0, shard_mesh, shards, AABB(Vector3(-6, -1, -6), Vector3(12, 5, 12)))
	shard_emitter.one_shot = true
	shard_emitter.explosiveness = 0.95
	shard_emitter.layers = FX_LAYER
	holder.add_child(shard_emitter)
	var dust := ParticleProcessMaterial.new()
	dust.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_SPHERE
	dust.emission_sphere_radius = 0.4
	dust.direction = Vector3(normal.x, 0.3, normal.z).normalized()
	dust.spread = 80.0
	dust.gravity = Vector3(0, 0.25, 0)
	dust.initial_velocity_min = 0.8
	dust.initial_velocity_max = 2.2
	dust.damping_min = 1.5
	dust.damping_max = 2.5
	dust.scale_min = 0.8
	dust.scale_max = 1.5
	dust.angle_min = -180.0
	dust.angle_max = 180.0
	dust.color_ramp = _ramp([Color(0.55, 0.53, 0.5, 0.0), Color(0.55, 0.53, 0.5, 0.5), Color(0.6, 0.58, 0.55, 0.0)], [0.0, 0.1, 1.0])
	var dust_emitter := _emitter(16, 2.4, _quad(1.5, SMOKE_SHADER, {"density": 0.7}), dust, AABB(Vector3(-6, -1, -6), Vector3(12, 6, 12)))
	dust_emitter.one_shot = true
	dust_emitter.explosiveness = 0.9
	dust_emitter.layers = FX_LAYER
	holder.add_child(dust_emitter)
	holder.add_child(ground_collider(8.0))
	shard_emitter.emitting = true
	dust_emitter.emitting = true
	return holder


static func hose_stream() -> Effect:
	## A water jet of velocity-aligned streaks plus fine mist; aim with aim_hose.
	var effect := Effect.new()
	effect.name = "HoseStream"
	var jet := ParticleProcessMaterial.new()
	jet.direction = Vector3.FORWARD
	jet.spread = 1.8
	jet.gravity = Vector3(0, -9.8, 0)
	jet.initial_velocity_min = 13.2
	jet.initial_velocity_max = 13.8
	jet.particle_flag_align_y = true
	jet.scale_min = 0.8
	jet.scale_max = 1.2
	jet.scale_curve = _curve([Vector2(0.0, 0.6), Vector2(1.0, 1.4)])
	jet.color_ramp = _ramp([Color(1, 1, 1, 0.95), Color(1, 1, 1, 0.6)], [0.0, 1.0])
	jet.collision_mode = ParticleProcessMaterial.COLLISION_HIDE_ON_CONTACT
	effect.add_emitter(_emitter(220, 1.5, _streak(0.11, 0.95), jet, AABB(Vector3(-25, -10, -25), Vector3(50, 30, 50))))
	var mist := ParticleProcessMaterial.new()
	mist.direction = Vector3.FORWARD
	mist.spread = 7.0
	mist.gravity = Vector3(0, -2.5, 0)
	mist.initial_velocity_min = 8.0
	mist.initial_velocity_max = 11.5
	mist.damping_min = 2.0
	mist.damping_max = 4.0
	mist.scale_min = 0.5
	mist.scale_max = 1.0
	mist.angle_min = -180.0
	mist.angle_max = 180.0
	mist.color_ramp = _ramp([Color(0.92, 0.95, 0.98, 0.0), Color(0.92, 0.95, 0.98, 0.22), Color(0.95, 0.96, 0.98, 0.0)], [0.0, 0.35, 1.0])
	effect.add_emitter(_emitter(50, 1.8, _quad(1.1, SMOKE_SHADER, {"density": 0.5}), mist, AABB(Vector3(-25, -10, -25), Vector3(50, 30, 50))))
	return effect


static func attach_ground(effect: Node3D, ground_y: float, half_size: float) -> void:
	## Collider placed in world space at the walking surface under an effect.
	var collider := ground_collider(half_size)
	effect.add_child(collider)
	collider.top_level = true
	collider.global_position = Vector3(effect.global_position.x, ground_y - 0.5, effect.global_position.z)


static func aim_hose(effect: Node3D, nozzle: Vector3, target: Vector3) -> void:
	## Points the jet so its ballistic arc (13.5 m/s muzzle) lands on target,
	## ends the streaks there and places the impact splash at the target.
	effect.global_position = nozzle
	var flat := Vector3(target.x - nozzle.x, 0.0, target.z - nozzle.z)
	var distance := maxf(flat.length(), 0.5)
	var rise := target.y - nozzle.y
	var speed := 13.5
	var gravity := 9.8
	var term := speed ** 4 - gravity * (gravity * distance * distance + 2.0 * rise * speed * speed)
	var angle := atan2(speed * speed - sqrt(maxf(term, 0.0)), gravity * distance) if term >= 0.0 else deg_to_rad(42.0)
	var yaw := atan2(flat.x, flat.z)
	effect.global_rotation = Vector3(0.0, yaw + PI, 0.0)
	effect.rotate_object_local(Vector3.RIGHT, angle)
	var flight := distance / maxf(speed * cos(angle), 0.1)
	var emitters := (effect as Effect).emitters
	if emitters.size() >= 2:
		emitters[0].lifetime = flight + 0.05
		emitters[1].lifetime = flight * 1.1 + 0.1
	var splash := effect.get_node_or_null("TargetSplash") as GPUParticles3D
	if splash == null:
		var process := ParticleProcessMaterial.new()
		process.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_SPHERE
		process.emission_sphere_radius = 0.35
		process.direction = Vector3.UP
		process.spread = 70.0
		process.gravity = Vector3(0, -5.0, 0)
		process.initial_velocity_min = 1.2
		process.initial_velocity_max = 3.0
		process.damping_min = 1.5
		process.damping_max = 3.0
		process.scale_min = 0.5
		process.scale_max = 1.2
		process.angle_min = -180.0
		process.angle_max = 180.0
		process.color_ramp = _ramp([Color(0.92, 0.94, 0.97, 0.0), Color(0.92, 0.94, 0.97, 0.35), Color(0.95, 0.96, 0.98, 0.0)], [0.0, 0.2, 1.0])
		splash = _emitter(36, 1.2, _quad(1.0, SMOKE_SHADER, {"density": 0.55}), process, AABB(Vector3(-6, -2, -6), Vector3(12, 8, 12)))
		splash.name = "TargetSplash"
		splash.top_level = true
		splash.layers = FX_LAYER
		effect.add_child(splash)
		(effect as Effect).emitters.append(splash)
		(effect as Effect).base_amounts.append(36.0)
	splash.global_position = target


static func geyser() -> Effect:
	## Burst water main at the kerb: a streaked column, foam crown, falling
	## spray and base splash.
	var effect := Effect.new()
	effect.name = "WaterGeyser"
	var column := ParticleProcessMaterial.new()
	column.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_SPHERE
	column.emission_sphere_radius = 0.18
	column.direction = Vector3.UP
	column.spread = 6.0
	column.gravity = Vector3(0, -9.8, 0)
	column.initial_velocity_min = 8.0
	column.initial_velocity_max = 10.0
	column.particle_flag_align_y = true
	column.scale_min = 0.8
	column.scale_max = 1.3
	column.color_ramp = _ramp([Color(1, 1, 1, 0.95), Color(1, 1, 1, 0.65)], [0.0, 1.0])
	column.collision_mode = ParticleProcessMaterial.COLLISION_HIDE_ON_CONTACT
	effect.add_emitter(_emitter(460, 2.1, _streak(0.13, 1.15, {"opacity": 0.7}), column, AABB(Vector3(-8, -1, -8), Vector3(16, 9, 16))))
	var crown := ParticleProcessMaterial.new()
	crown.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_SPHERE
	crown.emission_sphere_radius = 0.3
	crown.direction = Vector3.UP
	crown.spread = 35.0
	crown.gravity = Vector3(0, -6.0, 0)
	crown.initial_velocity_min = 6.5
	crown.initial_velocity_max = 9.0
	crown.damping_min = 0.5
	crown.damping_max = 1.2
	crown.scale_min = 0.4
	crown.scale_max = 0.9
	crown.angle_min = -180.0
	crown.angle_max = 180.0
	crown.color_ramp = _ramp([Color(0.95, 0.97, 1.0, 0.0), Color(0.95, 0.97, 1.0, 0.22), Color(0.95, 0.97, 1.0, 0.0)], [0.0, 0.4, 1.0])
	effect.add_emitter(_emitter(40, 2.2, _quad(0.85, SMOKE_SHADER, {"density": 0.45}), crown, AABB(Vector3(-8, -1, -8), Vector3(16, 9, 16))))
	var splash := ParticleProcessMaterial.new()
	splash.emission_shape = ParticleProcessMaterial.EMISSION_SHAPE_RING
	splash.emission_ring_radius = 0.9
	splash.emission_ring_inner_radius = 0.3
	splash.emission_ring_height = 0.05
	splash.emission_ring_axis = Vector3.UP
	splash.direction = Vector3.UP
	splash.spread = 50.0
	splash.gravity = Vector3(0, -9.8, 0)
	splash.initial_velocity_min = 1.0
	splash.initial_velocity_max = 2.6
	splash.particle_flag_align_y = true
	splash.scale_min = 0.5
	splash.scale_max = 0.9
	splash.color_ramp = _ramp([Color(1, 1, 1, 0.8), Color(1, 1, 1, 0.4)], [0.0, 1.0])
	effect.add_emitter(_emitter(90, 0.7, _streak(0.07, 0.35), splash, AABB(Vector3(-8, -1, -8), Vector3(16, 9, 16))))
	effect.add_child(ground_collider(8.0))
	return effect


static func puddle(radius: float) -> MeshInstance3D:
	## Standing water on the asphalt: a thin, very smooth reflective disc.
	var disc := MeshInstance3D.new()
	disc.name = "Puddle"
	var mesh := CylinderMesh.new()
	mesh.top_radius = radius
	mesh.bottom_radius = radius
	mesh.height = 0.01
	mesh.radial_segments = 32
	disc.mesh = mesh
	var material := StandardMaterial3D.new()
	material.albedo_color = Color(0.12, 0.15, 0.18, 0.62)
	material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	material.roughness = 0.04
	material.metallic = 0.25
	disc.material_override = material
	disc.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	disc.layers = FX_LAYER
	return disc
