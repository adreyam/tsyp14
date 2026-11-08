shader_type spatial;
render_mode blend_mix, depth_draw_never, cull_disabled, shadows_disabled, specular_schlick_ggx;

#include "res://shaders/incident_noise.gdshaderinc"

// Water jet / spray droplet cluster: stretched along its velocity by the
// particle system, broken into droplets by noise, with a bright specular rim.
uniform sampler2D depth_texture : hint_depth_texture, filter_linear_mipmap;
uniform vec3 water_colour : source_color = vec3(0.78, 0.88, 0.96);
uniform float soft_distance = 0.5;
varying float age;
varying float seed;

void vertex() {
	MODELVIEW_MATRIX = incident_billboard(VIEW_MATRIX, INV_VIEW_MATRIX, MODEL_MATRIX);
	age = INSTANCE_CUSTOM.y;
	seed = fract(sin(float(INSTANCE_ID) * 39.346) * 43758.5453);
}

void fragment() {
	vec2 uv = UV * 2.0 - 1.0;
	float droplets = incident_fbm(uv * vec2(3.0, 6.0) + vec2(seed * 11.0, TIME * 3.0));
	float shape = 1.0 - smoothstep(0.25, 1.0, length(uv * vec2(1.0, 0.7)));
	float body = shape * smoothstep(0.35, 0.65, droplets + shape * 0.35);
	float scene_depth = texture(depth_texture, SCREEN_UV).r;
	vec4 scene_view = INV_PROJECTION_MATRIX * vec4(SCREEN_UV * 2.0 - 1.0, scene_depth, 1.0);
	scene_view.xyz /= scene_view.w;
	// View space looks down -Z: positive when scene geometry lies behind.
	float soft = clamp((VERTEX.z - scene_view.z) / soft_distance, 0.0, 1.0);
	ALBEDO = water_colour;
	ROUGHNESS = 0.08;
	METALLIC = 0.0;
	SPECULAR = 0.9;
	ALPHA = clamp(body * COLOR.a * soft * (1.0 - smoothstep(0.7, 1.0, age)), 0.0, 0.85);
}
