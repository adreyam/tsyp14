// Smooth value noise / fBm shared by incident effects. Bilinear-interpolated
// lattice noise with quintic fade: no grid-aligned blocks at any scale.
float incident_hash(vec2 p) {
	p = fract(p * vec2(123.34, 456.21));
	p += dot(p, p + 45.32);
	return fract(p.x * p.y);
}

float incident_value_noise(vec2 p) {
	vec2 i = floor(p);
	vec2 f = fract(p);
	vec2 u = f * f * f * (f * (f * 6.0 - 15.0) + 10.0);
	float a = incident_hash(i);
	float b = incident_hash(i + vec2(1.0, 0.0));
	float c = incident_hash(i + vec2(0.0, 1.0));
	float d = incident_hash(i + vec2(1.0, 1.0));
	return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}

float incident_fbm(vec2 p) {
	float value = 0.0;
	float amplitude = 0.5;
	mat2 rotation = mat2(vec2(0.8, 0.6), vec2(-0.6, 0.8));
	for (int octave = 0; octave < 5; octave++) {
		value += amplitude * incident_value_noise(p);
		p = rotation * p * 2.03 + vec2(1.7, 9.2);
		amplitude *= 0.5;
	}
	return value;
}

// Camera-facing quad that keeps the particle's scale and spin.
mat4 incident_billboard(mat4 view, mat4 inv_view, mat4 model) {
	float scale_x = length(model[0].xyz);
	float scale_y = length(model[1].xyz);
	mat4 facing = mat4(inv_view[0] * scale_x, inv_view[1] * scale_y, inv_view[2], model[3]);
	return view * facing;
}
