shader_type spatial;
render_mode blend_mix, unshaded, depth_draw_never, cull_disabled, shadows_disabled;

#include "res://shaders/incident_noise.gdshaderinc"

// Velocity-aligned water streak. The particle system aligns local +Y to the
// particle velocity; the quad is turned about that axis to face the camera,
// so a jet reads as a continuous arc of water at any viewing angle.
uniform vec3 water_colour : source_color = vec3(0.86, 0.92, 0.98);
uniform float opacity = 0.75;
varying float age;
varying float seed;

void vertex() {
	vec3 origin = MODEL_MATRIX[3].xyz;
	vec3 axis_y = MODEL_MATRIX[1].xyz;
	float streak_length = length(axis_y);
	axis_y = axis_y / max(streak_length, 0.0001);
	float width = length(MODEL_MATRIX[0].xyz);
	vec3 to_camera = normalize(CAMERA_POSITION_WORLD - origin);
	vec3 axis_x = cross(axis_y, to_camera);
	axis_x = length(axis_x) > 0.001 ? normalize(axis_x) : vec3(1.0, 0.0, 0.0);
	// VERTEX still holds the quad's local corner (its own width/length);
	// rebuild it in world space and draw with the view matrix alone.
	VERTEX = origin + axis_x * VERTEX.x * width + axis_y * VERTEX.y * streak_length;
	MODELVIEW_MATRIX = VIEW_MATRIX;
	age = INSTANCE_CUSTOM.y;
	seed = fract(sin(float(INSTANCE_ID) * 17.13) * 43758.5453);
}

void fragment() {
	vec2 uv = UV * 2.0 - 1.0;
	float core = 1.0 - smoothstep(0.15, 1.0, abs(uv.x));
	float ends = 1.0 - smoothstep(0.55, 1.0, abs(uv.y));
	float breakup = incident_fbm(vec2(uv.x * 2.0 + seed * 13.0, uv.y * 5.0 - TIME * 6.0));
	float body = core * ends * smoothstep(0.25, 0.6, breakup + core * 0.3);
	float glint = pow(core, 6.0) * 0.35;
	ALBEDO = water_colour + vec3(glint);
	ALPHA = clamp(body * opacity * COLOR.a * (1.0 - smoothstep(0.75, 1.0, age)), 0.0, 0.9);
}
