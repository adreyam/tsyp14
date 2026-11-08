shader_type spatial;
render_mode cull_back, depth_draw_opaque;

// Skinned garment overlay for responders. UV2.x carries the vertex's normalized
// position along its body chain (waist→neck, shoulder→wrist, hip→ankle) and
// UV2.y the chain id, so reflective bands sit where real uniforms have them.
uniform vec4 base_colour : source_color = vec4(0.86, 0.95, 0.12, 1.0);
uniform vec4 stripe_colour : source_color = vec4(0.82, 0.84, 0.86, 1.0);
uniform vec4 torso_bands = vec4(0.30, 0.37, 0.50, 0.57);
uniform vec4 limb_bands = vec4(0.70, 0.78, 2.0, 2.0);
uniform float base_emission = 0.10;
uniform float stripe_emission = 0.55;
uniform float roughness_value = 0.62;

float band(float t, vec2 range) {
	return smoothstep(range.x - 0.006, range.x + 0.006, t) * (1.0 - smoothstep(range.y - 0.006, range.y + 0.006, t));
}

void fragment() {
	float t = UV2.x;
	float chain = UV2.y;
	float stripe = 0.0;
	if (chain < 0.5) {
		stripe = max(band(t, torso_bands.xy), band(t, torso_bands.zw));
	} else {
		stripe = max(band(t, limb_bands.xy), band(t, limb_bands.zw));
	}
	// Subtle fabric breakup so large areas are not flat colour.
	float weave = 0.94 + 0.06 * sin(UV.x * 900.0) * sin(UV.y * 900.0);
	vec3 colour = mix(base_colour.rgb * weave, stripe_colour.rgb, stripe);
	ALBEDO = colour;
	ROUGHNESS = mix(roughness_value, 0.25, stripe);
	METALLIC = stripe * 0.35;
	EMISSION = colour * mix(base_emission, stripe_emission, stripe);
}
