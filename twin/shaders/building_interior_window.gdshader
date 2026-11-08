shader_type spatial;
render_mode unshaded, blend_add, depth_draw_never, cull_disabled;

// Interior light shown through a building's own window polygons.
//
// Every window has a state texel written by BuildingWindowController:
//   R  lit level 0..1 (0 = dark window)
//   G  tint 0 = warm tungsten .. 1 = cool white (television / LED)
//   B  flicker amount (television)
//   A  per-window brightness variation
//
// Geometry windows (overlay meshes built from the model's own window quads)
// carry their window number in UV2.x. Models whose windows are only painted
// into a texture atlas have no window polygons; their state texture is a 64x64
// map laid over the atlas UV space (uv_patches), so lit and dark patches of
// painted windows can still be controlled.

uniform sampler2D source_texture : source_color, filter_linear_mipmap;
uniform int mask_mode = 0;
uniform vec3 interior_color : source_color = vec3(1.0, 0.63, 0.28);
uniform vec3 cool_color : source_color = vec3(0.62, 0.80, 1.0);
uniform float emission_strength = 2.6;
uniform sampler2D window_state : filter_nearest, repeat_disable;
uniform int window_state_width = 64;
uniform bool controlled = false;
uniform bool uv_patches = false;
instance uniform float night_strength = 0.0;
instance uniform int id_offset = 0;

int index_hash(float x) {
	return int(fract(sin(x * 12.9898) * 43758.5453) * 100.0);
}

void vertex() {
	// Keep the glow on the authored glass while avoiding coplanar flicker: a
	// fixed 2 cm in the world whatever scale the imported model was authored at.
	float model_scale = max(length(MODEL_MATRIX[0].xyz), 0.000001);
	VERTEX += NORMAL * (0.02 / model_scale);
}

void fragment() {
	float mask = 1.0;
	if (mask_mode > 0) {
		vec3 source = texture(source_texture, UV).rgb;
		float maximum = max(source.r, max(source.g, source.b));
		float minimum = min(source.r, min(source.g, source.b));
		float saturation = maximum - minimum;
		float luminance = dot(source, vec3(0.2126, 0.7152, 0.0722));
		if (mask_mode == 1) {
			// White/neutral panes in the apartment and house atlases.
			mask = smoothstep(0.72, 0.91, luminance) * (1.0 - smoothstep(0.20, 0.52, saturation));
		} else if (mask_mode == 2) {
			// Mid-value blue/cyan glazing in commercial atlases.
			float cool_bias = source.b - source.r;
			mask = smoothstep(0.015, 0.16, cool_bias) * smoothstep(0.10, 0.48, luminance);
		} else if (mask_mode == 3) {
			// The tower atlas encodes windows as small dark-navy pixels.
			float cool_bias = source.b - source.r;
			mask = smoothstep(0.025, 0.11, cool_bias) * (1.0 - smoothstep(0.16, 0.34, luminance));
		} else if (mask_mode == 4) {
			// The bar atlas encodes storefront glass as saturated bright cyan.
			float cool_bias = source.b - source.r;
			mask = smoothstep(0.16, 0.34, saturation) * smoothstep(0.06, 0.18, cool_bias) * smoothstep(0.32, 0.58, luminance);
		} else if (mask_mode == 5) {
			// Apartment windows use pale blinds below the earlier white threshold.
			mask = smoothstep(0.48, 0.70, luminance) * (1.0 - smoothstep(0.16, 0.34, saturation));
		} else {
			// House panes are the dark neutral pixels inside exact window components.
			mask = (1.0 - smoothstep(0.16, 0.34, luminance)) * (1.0 - smoothstep(0.08, 0.22, saturation));
		}
	}

	float level = 1.0;
	float tint = 0.0;
	float flicker = 0.0;
	float variation = 1.0;
	if (controlled || uv_patches) {
		int index;
		int width = window_state_width;
		if (uv_patches) {
			index = int(clamp(UV.y, 0.0, 0.999) * 64.0) * width + int(clamp(UV.x, 0.0, 0.999) * 64.0);
		} else {
			index = id_offset + int(UV2.x + 0.5);
		}
		vec4 state = texelFetch(window_state, ivec2(index % width, index / width), 0);
		level = state.r;
		tint = state.g;
		flicker = state.b;
		variation = mix(0.72, 1.0, state.a);
	}
	if (mask < 0.025 || level < 0.02 || night_strength < 0.025) {
		discard;
	}
	float active = smoothstep(0.05, 0.75, night_strength);
	float pulse = 1.0 - flicker * (0.5 + 0.5 * sin(TIME * 17.0 + float(index_hash(UV2.x))) ) * 0.35;
	vec3 colour = mix(interior_color, cool_color, tint);
	vec3 warm_glass = mix(colour * 0.55, colour, mask) * variation * pulse;
	float lit = level * mask * active;
	ALBEDO = warm_glass;
	EMISSION = warm_glass * emission_strength * lit;
	ALPHA = clamp(lit * 0.68, 0.0, 0.68);
}
