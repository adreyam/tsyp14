shader_type spatial;
render_mode diffuse_burley, specular_schlick_ggx, cull_back;

// Instance slots are fixed across vehicle_body/lamp/wheel shaders: one
// MeshInstance carries body and lamp materials together.
// One shader for every non-lamp body finish of the procedural fleet, so all
// cars share four materials and differ only by per-instance parameters.
// finish: 0 painted panels, 1 glazing, 2 black trim/rubber, 3 bright metal.
uniform int finish = 0;

// Body colour, livery accent (painted where UV.x = 1) and soot
// (0..1) when a vehicle burns.
instance uniform vec3 paint_color : source_color, instance_index(0) = vec3(0.8, 0.8, 0.82);
instance uniform vec3 accent_color : source_color, instance_index(1) = vec3(0.95, 0.95, 0.95);
instance uniform float paint_metallic : instance_index(2) = 0.0;
instance uniform float soot : instance_index(3) = 0.0;
// Bits of the side doors that exist as hinged panels; the body's own panels
// of those doors (flagged in UV.y) render as the cabin behind the opening.
instance uniform float door_cutout : instance_index(10) = 0.0;

const vec3 SOOT = vec3(0.045, 0.04, 0.035);

void fragment() {
	int door_bit = int(round(UV.y));
	bool cabin = door_bit > 0 && (int(round(door_cutout)) & door_bit) != 0;
	// Handles and seams travel with the door; panels become the cabin.
	if (cabin && finish > 1) {
		discard;
	}
	vec3 albedo;
	float metallic = 0.0;
	float roughness = 0.5;
	float specular = 0.5;
	if (cabin) {
		albedo = vec3(0.045, 0.045, 0.05);
		roughness = 0.85;
		specular = 0.2;
	} else if (finish == 0) {
		albedo = mix(paint_color, accent_color, UV.x);
		// Accent livery panels are always solid (non-metallic) paint.
		metallic = paint_metallic * (1.0 - UV.x);
		roughness = mix(0.34, 0.42, UV.x);
		CLEARCOAT = 1.0 - soot;
		CLEARCOAT_ROUGHNESS = 0.12;
	} else if (finish == 1) {
		albedo = vec3(0.035, 0.045, 0.055);
		roughness = 0.04;
		specular = 0.75;
	} else if (finish == 2) {
		albedo = vec3(0.028, 0.029, 0.031);
		roughness = 0.62;
		specular = 0.32;
	} else {
		albedo = vec3(0.78, 0.79, 0.8);
		metallic = 1.0;
		roughness = 0.16;
	}
	float burnt = clamp(soot, 0.0, 0.92);
	ALBEDO = mix(albedo, SOOT, burnt);
	METALLIC = metallic * (1.0 - burnt);
	ROUGHNESS = mix(roughness, 1.0, burnt);
	SPECULAR = specular;
}
