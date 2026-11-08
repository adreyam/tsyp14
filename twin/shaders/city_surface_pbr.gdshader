shader_type spatial;
render_mode diffuse_burley, specular_schlick_ggx, cull_back;

uniform sampler2D albedo_texture : source_color, filter_linear_mipmap_anisotropic, repeat_enable;
uniform sampler2D normal_texture : hint_normal, filter_linear_mipmap_anisotropic, repeat_enable;
uniform sampler2D roughness_texture : hint_roughness_r, filter_linear_mipmap_anisotropic, repeat_enable;
uniform vec4 tint : source_color = vec4(1.0);
uniform vec2 uv_scale = vec2(10.0);
uniform float normal_strength : hint_range(0.0, 2.0) = 0.6;
uniform float roughness_strength : hint_range(0.25, 1.5) = 1.0;
uniform float macro_variation : hint_range(0.0, 0.2) = 0.04;

varying vec3 world_position;

void vertex() {
	world_position = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}

void fragment() {
	vec2 tiled_uv = UV * uv_scale;
	vec3 base_color = texture(albedo_texture, tiled_uv).rgb * tint.rgb;
	float large_detail = sin(world_position.x * 0.031) * cos(world_position.z * 0.027);
	base_color *= 1.0 + large_detail * macro_variation;

	ALBEDO = base_color;
	NORMAL_MAP = texture(normal_texture, tiled_uv).rgb;
	NORMAL_MAP_DEPTH = normal_strength;
	ROUGHNESS = clamp(texture(roughness_texture, tiled_uv).r * roughness_strength, 0.3, 1.0);
	METALLIC = 0.0;
	SPECULAR = 0.35;
}
