shader_type spatial;
render_mode blend_mix, depth_draw_never, cull_disabled, shadows_disabled, diffuse_lambert_wrap, specular_disabled;

#include "res://shaders/incident_noise.gdshaderinc"

// Billowing smoke / steam puff. COLOR carries tint and opacity over life;
// lit with a wrap-around diffuse so the plume reads as volume, not cards.
uniform sampler2D depth_texture : hint_depth_texture, filter_linear_mipmap;
uniform float density = 1.0;
uniform float soft_distance = 1.6;
uniform float billow_speed = 0.12;
varying float age;
varying float seed;

void vertex() {
	MODELVIEW_MATRIX = incident_billboard(VIEW_MATRIX, INV_VIEW_MATRIX, MODEL_MATRIX);
	age = INSTANCE_CUSTOM.y;
	seed = fract(sin(float(INSTANCE_ID) * 78.233) * 43758.5453);
	// Normal facing the camera, bent outward toward the rim for roundness.
	NORMAL = vec3(0.0, 0.0, 1.0);
}

void fragment() {
	vec2 uv = UV * 2.0 - 1.0;
	float radius = length(uv);
	vec2 drift = uv * (1.15 - age * 0.25) + vec2(seed * 7.0, seed * 3.0 - TIME * billow_speed);
	float warp = incident_fbm(drift * 1.4);
	float cloud = incident_fbm(drift * 2.3 + vec2(warp * 1.8, -warp));
	float edge = 1.0 - smoothstep(0.35 + cloud * 0.35, 1.0, radius);
	float opacity = edge * smoothstep(0.25, 0.75, cloud + 0.25) * COLOR.a * density;
	opacity *= smoothstep(0.0, 0.12, age) * (1.0 - smoothstep(0.6, 1.0, age));
	float scene_depth = texture(depth_texture, SCREEN_UV).r;
	vec4 scene_view = INV_PROJECTION_MATRIX * vec4(SCREEN_UV * 2.0 - 1.0, scene_depth, 1.0);
	scene_view.xyz /= scene_view.w;
	// View space looks down -Z: positive when scene geometry lies behind.
	float soft = clamp((VERTEX.z - scene_view.z) / soft_distance, 0.0, 1.0);
	// Pseudo-volumetric normal: puffs bulge toward the viewer.
	NORMAL = normalize(vec3(uv * 0.85 + vec2(cloud - 0.5) * 0.6, 1.0 - radius * 0.4));
	ALBEDO = COLOR.rgb * mix(0.72, 1.08, cloud);
	ALPHA = clamp(opacity * soft, 0.0, 1.0);
	ROUGHNESS = 1.0;
}
