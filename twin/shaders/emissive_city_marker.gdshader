shader_type spatial;
render_mode diffuse_burley, specular_schlick_ggx, cull_back;

uniform vec4 base_color : source_color = vec4(0.9, 0.08, 0.025, 1.0);
uniform vec4 emission_color : source_color = vec4(1.0, 0.04, 0.005, 1.0);
uniform float emission_energy : hint_range(0.0, 8.0) = 2.4;

void fragment() {
	ALBEDO = base_color.rgb;
	METALLIC = 0.12;
	ROUGHNESS = 0.26;
	SPECULAR = 0.5;
	EMISSION = emission_color.rgb * emission_energy;
}
