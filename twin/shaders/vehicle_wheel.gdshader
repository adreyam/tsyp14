shader_type spatial;
render_mode diffuse_burley, specular_schlick_ggx, cull_back;

// Tyre and wheel of the procedural fleet in one surface: vertex COLOR.rgb is
// the albedo and COLOR.a selects machined alloy (1) or rubber (0).
instance uniform float soot : instance_index(3) = 0.0;

void fragment() {
	float alloy = step(0.5, COLOR.a);
	float burnt = clamp(soot, 0.0, 0.92);
	ALBEDO = mix(COLOR.rgb, vec3(0.04), burnt);
	METALLIC = alloy * 0.9 * (1.0 - burnt);
	ROUGHNESS = mix(mix(0.82, 0.24, alloy), 1.0, burnt);
	SPECULAR = mix(0.3, 0.5, alloy);
}
