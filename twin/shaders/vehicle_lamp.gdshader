shader_type spatial;
render_mode diffuse_burley, specular_schlick_ggx, cull_back;

// Every lamp lens of a procedural vehicle is a polygon section of its body
// mesh in this one surface. UV.x holds the lens kind; the lamp states are
// per-instance parameters, and every flash pattern is evaluated here from
// the shared lamp clock, so no lamp needs a node, a material swap or a
// per-frame script update.
//
// kinds: 0 headlamp, 1 tail/brake, 2..5 indicators (front-left, front-right,
// rear-left, rear-right), 6 reversing lamp, 7/8 roof beacons A/B, 9 amber
// beacon, 10 number plate, 11 taxi roof sign.

// x headlamps (0 daytime running, 1 dipped beam), y brake, z indicators on
// the car's +X side, w indicators on its -X side.
instance uniform vec4 lamp_state : instance_index(4) = vec4(0.0);
// x roof beacons, y hazard flashers, z amber beacon, w reversing.
instance uniform vec4 lamp_extra : instance_index(5) = vec4(0.0);
instance uniform float lamp_phase : instance_index(6) = 0.0;
// 0 police, 1 ambulance, 2 fire (matches the warning light rig).
instance uniform float beacon_pattern : instance_index(7) = 0.0;
instance uniform vec3 beacon_a : source_color, instance_index(8) = vec3(1.0, 0.06, 0.04);
instance uniform vec3 beacon_b : source_color, instance_index(9) = vec3(0.05, 0.25, 1.0);
instance uniform float soot : instance_index(3) = 0.0;

// Advanced once per frame by CityLife; the warning-light rig flashes the
// light it casts on the same clock.
global uniform float vehicle_lamp_clock;
global uniform float city_night_factor;

const vec3 AMBER = vec3(1.0, 0.42, 0.02);

float beacon_lit(float t, float index) {
	if (beacon_pattern < 0.5) {
		// Alternating left/right double flash.
		float cycle = mod(t, 0.8);
		bool window = index < 0.5 ? cycle < 0.4 : cycle >= 0.4;
		return (window && mod(cycle, 0.4) < 0.28 && mod(cycle, 0.14) < 0.09) ? 1.0 : 0.0;
	} else if (beacon_pattern < 1.5) {
		return mod(t + index * 0.25, 0.5) < 0.22 ? 1.0 : 0.0;
	}
	return mod(t + index * 0.35, 0.7) < 0.35 ? 1.0 : 0.0;
}

void fragment() {
	int kind = int(round(UV.x));
	float t = vehicle_lamp_clock + lamp_phase;
	float blink = mod(t, 0.8) < 0.4 ? 1.0 : 0.0;
	vec3 lens = vec3(0.0);
	vec3 glow = vec3(0.0);
	float metallic = 0.0;
	float roughness = 0.08;
	if (kind == 0) {
		lens = vec3(0.82, 0.84, 0.86);
		metallic = 0.55;
		glow = vec3(1.0, 0.96, 0.88) * mix(0.9, 5.8, max(lamp_state.x, city_night_factor));
	} else if (kind == 1) {
		lens = vec3(0.42, 0.02, 0.02);
		glow = vec3(1.0, 0.03, 0.02) * (0.35 + lamp_state.x * 0.9 + lamp_state.y * 6.0);
	} else if (kind >= 2 && kind <= 5) {
		lens = vec3(0.55, 0.26, 0.02);
		bool left = kind == 2 || kind == 4;
		float on = max(left ? lamp_state.z : lamp_state.w, lamp_extra.y);
		glow = AMBER * 7.0 * on * blink;
	} else if (kind == 6) {
		lens = vec3(0.8, 0.8, 0.82);
		glow = vec3(1.0) * 4.0 * lamp_extra.w;
	} else if (kind == 7 || kind == 8) {
		vec3 colour = kind == 7 ? beacon_a : beacon_b;
		lens = colour * 0.35;
		glow = colour * 10.0 * lamp_extra.x * beacon_lit(t, float(kind - 7));
	} else if (kind == 9) {
		lens = AMBER * 0.45;
		glow = AMBER * 9.0 * lamp_extra.z * (mod(t, 0.9) < 0.45 ? 1.0 : 0.0);
	} else if (kind == 10) {
		lens = vec3(0.86, 0.86, 0.82);
		roughness = 0.55;
	} else {
		lens = vec3(0.95, 0.88, 0.55);
		roughness = 0.4;
		glow = vec3(1.0, 0.86, 0.4) * 1.6;
	}
	float burnt = clamp(soot, 0.0, 0.92);
	ALBEDO = mix(lens, vec3(0.04), burnt);
	METALLIC = metallic * (1.0 - burnt);
	ROUGHNESS = mix(roughness, 1.0, burnt);
	SPECULAR = 0.8;
	EMISSION = glow * (1.0 - burnt);
}
