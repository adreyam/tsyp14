shader_type spatial;
render_mode cull_disabled;

// One horizontal slice of a building (mesh-local height band) drawn with the building's own texture or colours.
// Used while a building collapses: each slab is a copy of the whole mesh that discards everything outside its band.
// Faces seen from behind (the cut interior) are drawn as dusty concrete so the slabs read as solid.

uniform sampler2D albedo_texture : source_color, filter_linear_mipmap, repeat_enable;
uniform vec4 albedo_color : source_color = vec4(1.0);
uniform bool use_texture = false;
uniform bool use_vertex_color = false;
uniform float band_min = -100000.0;
uniform float band_max = 100000.0;
uniform float dust : hint_range(0.0, 1.0) = 0.0;
uniform vec3 dust_color : source_color = vec3(0.62, 0.58, 0.52);
uniform vec3 concrete_color : source_color = vec3(0.42, 0.41, 0.40);

varying float mesh_height;

void vertex() {
	mesh_height = VERTEX.y;
}

void fragment() {
	if (mesh_height < band_min || mesh_height > band_max) {
		discard;
	}
	vec4 colour = albedo_color;
	if (use_texture) {
		colour *= texture(albedo_texture, UV);
	}
	if (use_vertex_color) {
		colour *= COLOR;
	}
	vec3 rgb = colour.rgb;
	if (!FRONT_FACING) {
		rgb = concrete_color;
		NORMAL = -NORMAL;
	}
	ALBEDO = mix(rgb, dust_color, dust);
	ROUGHNESS = 0.92;
	METALLIC = 0.0;
}
