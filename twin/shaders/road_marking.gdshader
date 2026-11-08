shader_type spatial;
render_mode blend_mix, depth_draw_opaque, cull_back, shadows_disabled;

// Painted road line. A 16 cm line is narrower than a pixel from the air, and
// thin opaque geometry then shimmers frame to frame. The line instead takes
// its true pixel coverage as opacity: solid up close, a steady faint stripe
// far away.
uniform vec4 paint : source_color = vec4(0.95, 0.79, 0.25, 1.0);
uniform float line_width = 0.16;

varying vec3 world_position;

void vertex() {
	world_position = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}

void fragment() {
	// World metres covered by one pixel here (screen-space derivative).
	float pixel = max(length(dFdx(world_position)), length(dFdy(world_position)));
	ALBEDO = paint.rgb;
	ROUGHNESS = 0.66;
	ALPHA = clamp(line_width / max(pixel * 1.5, 0.0001), 0.0, 1.0);
}
