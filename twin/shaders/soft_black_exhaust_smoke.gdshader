shader_type spatial;
render_mode unshaded, cull_disabled, depth_prepass_alpha;

uniform vec4 smoke_tint : source_color = vec4(0.055, 0.06, 0.065, 0.82);

float smoke_noise(vec2 point) {
	return fract(sin(dot(point, vec2(127.1, 311.7))) * 43758.5453);
}

void vertex() {
	// Particle quads keep their world size but always face the active city camera.
	mat4 world = MODEL_MATRIX;
	float sx = length(world[0].xyz);
	float sy = length(world[1].xyz);
	MODELVIEW_MATRIX = VIEW_MATRIX * mat4(
		vec4(sx, 0.0, 0.0, 0.0),
		vec4(0.0, sy, 0.0, 0.0),
		vec4(0.0, 0.0, 1.0, 0.0),
		world[3]
	);
}

void fragment() {
	vec2 centered = UV * 2.0 - 1.0;
	float radius = length(centered);
	float breakup = smoke_noise(floor(UV * 7.0)) * 0.13;
	float soft_edge = 1.0 - smoothstep(0.48 + breakup, 1.0, radius);
	float cloudy_core = 0.72 + smoke_noise(floor(UV * 11.0 + vec2(2.0, 7.0))) * 0.28;
	ALBEDO = smoke_tint.rgb;
	ALPHA = smoke_tint.a * COLOR.a * soft_edge * cloudy_core;
}
