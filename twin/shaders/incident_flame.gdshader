shader_type spatial;
render_mode blend_add, unshaded, depth_draw_never, cull_disabled, shadows_disabled;

#include "res://shaders/incident_noise.gdshaderinc"

// Fire tongue billboard. COLOR.a (from the particle colour ramp) is the
// remaining heat; INSTANCE_CUSTOM.y is the normalized particle age.
uniform sampler2D depth_texture : hint_depth_texture, filter_linear_mipmap;
uniform float intensity = 1.0;
uniform float soft_distance = 0.9;
uniform vec3 hot_colour : source_color = vec3(1.0, 0.92, 0.55);
uniform vec3 mid_colour : source_color = vec3(1.0, 0.42, 0.05);
uniform vec3 cool_colour : source_color = vec3(0.55, 0.06, 0.01);
varying float age;
varying float seed;

void vertex() {
	MODELVIEW_MATRIX = incident_billboard(VIEW_MATRIX, INV_VIEW_MATRIX, MODEL_MATRIX);
	age = INSTANCE_CUSTOM.y;
	seed = fract(sin(float(INSTANCE_ID) * 12.9898) * 43758.5453);
}

void fragment() {
	vec2 uv = UV * 2.0 - 1.0;
	// Upward scrolling, warped turbulence gives licking tongues.
	vec2 flow = vec2(uv.x * 1.6, uv.y * 1.2 + TIME * 2.4 + seed * 9.0);
	float warp = incident_fbm(flow * 1.3 + vec2(seed * 3.1, 0.0));
	float turbulence = incident_fbm(flow * 2.2 + warp * 1.6);
	// Teardrop: wide at the base (uv.y = 1), narrow and broken at the tip.
	float height = clamp((1.0 - uv.y) * 0.5, 0.0, 1.0);
	float width = mix(0.95, 0.18, height);
	float body = 1.0 - smoothstep(width * 0.55, width, abs(uv.x) + (turbulence - 0.5) * 0.55);
	float tip = 1.0 - smoothstep(0.55, 1.0, height + (turbulence - 0.5) * 0.6);
	float heat = body * tip * COLOR.a * intensity;
	heat *= smoothstep(0.0, 0.08, age) * (1.0 - smoothstep(0.75, 1.0, age));
	vec3 colour = mix(cool_colour, mid_colour, smoothstep(0.15, 0.5, heat));
	colour = mix(colour, hot_colour, smoothstep(0.55, 0.95, heat));
	// Soft particles: fade where the quad meets geometry.
	float scene_depth = texture(depth_texture, SCREEN_UV).r;
	vec4 scene_view = INV_PROJECTION_MATRIX * vec4(SCREEN_UV * 2.0 - 1.0, scene_depth, 1.0);
	scene_view.xyz /= scene_view.w;
	// View space looks down -Z: positive when scene geometry lies behind.
	float soft = clamp((VERTEX.z - scene_view.z) / soft_distance, 0.0, 1.0);
	ALBEDO = colour * heat * 2.6 * soft;
	ALPHA = clamp(heat * soft, 0.0, 1.0);
}
