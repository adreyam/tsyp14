extends Control

var samples: Array[Vector2] = []
var metric_name := "Select a live reading"
var unit := ""
var line_color := Color("65cde8")
var last_plotted_points := PackedVector2Array()


func _ready() -> void:
	# Graph primitives must never bleed into the device selector or panel rails.
	clip_contents = true


func set_series(next_name: String, next_unit: String, next_samples: Array[Vector2]) -> void:
	metric_name = next_name if not next_name.contains("_") else next_name.replace("_", " ").capitalize()
	unit = next_unit
	samples = next_samples.slice(maxi(0, next_samples.size() - 120))
	queue_redraw()


func _draw() -> void:
	last_plotted_points = PackedVector2Array()
	draw_rect(Rect2(Vector2.ZERO, size), Color("20242b"), true)
	var current_text := ""
	if not samples.is_empty():
		current_text = "  %.1f %s" % [samples[-1].y, unit]
	draw_string(ThemeDB.fallback_font, Vector2(14, 23), metric_name + current_text, HORIZONTAL_ALIGNMENT_LEFT, size.x - 28, 15, Color("eef6fb"))
	draw_string(ThemeDB.fallback_font, Vector2(14, 42), "%d live samples  ·  updates with simulation state" % samples.size(), HORIZONTAL_ALIGNMENT_LEFT, size.x - 28, 10, Color("93a3b2"))
	var area := Rect2(Vector2(58, 58), Vector2(maxf(20.0, size.x - 76.0), maxf(20.0, size.y - 92.0)))
	draw_line(area.position, Vector2(area.position.x, area.end.y), Color("65717e"), 1.0)
	draw_line(Vector2(area.position.x, area.end.y), area.end, Color("65717e"), 1.0)
	if samples.is_empty():
		draw_string(ThemeDB.fallback_font, area.get_center() - Vector2(88, 0), "Click a reading to begin its history", HORIZONTAL_ALIGNMENT_LEFT, -1, 11, Color("9ba8b4"))
		return
	var minimum := samples[0].y
	var maximum := samples[0].y
	for sample in samples:
		minimum = minf(minimum, sample.y)
		maximum = maxf(maximum, sample.y)
	var padding := maxf((maximum - minimum) * 0.16, maxf(absf(maximum) * 0.015, 0.25))
	minimum -= padding
	maximum += padding
	for grid_index in 5:
		var y := area.position.y + float(grid_index) / 4.0 * area.size.y
		draw_line(Vector2(area.position.x, y), Vector2(area.end.x, y), Color(0.35, 0.40, 0.46, 0.24), 1.0)
	var first_time := samples[0].x
	var last_time := samples[-1].x
	var points := PackedVector2Array()
	for index in samples.size():
		# History is an ordered stream, while operators may pause or set the city
		# clock backward/forward. Index spacing keeps every point monotonic and
		# prevents clock changes from producing off-canvas diagonals or stacked
		# full-width lines.
		var x_ratio := float(index) / maxf(1.0, float(samples.size() - 1))
		var x := area.position.x + x_ratio * area.size.x if samples.size() > 1 else area.get_center().x
		var y := area.end.y - (samples[index].y - minimum) / (maximum - minimum) * area.size.y
		points.append(Vector2(x, y))
	last_plotted_points = points
	if points.size() >= 2:
		var fill := PackedVector2Array([Vector2(points[0].x, area.end.y)])
		fill.append_array(points)
		fill.append(Vector2(points[-1].x, area.end.y))
		draw_colored_polygon(fill, Color(line_color, 0.10))
		draw_polyline(points, line_color, 2.4, true)
	for index in points.size():
		if index == points.size() - 1 or index % maxi(1, int(points.size() / 12)) == 0:
			draw_circle(points[index], 2.4 if index < points.size() - 1 else 4.0, line_color)
	var label_colour := Color("aebbc7")
	draw_string(ThemeDB.fallback_font, Vector2(8, area.position.y + 5), "%.1f" % maximum, HORIZONTAL_ALIGNMENT_LEFT, 46, 10, label_colour)
	draw_string(ThemeDB.fallback_font, Vector2(8, area.end.y), "%.1f" % minimum, HORIZONTAL_ALIGNMENT_LEFT, 46, 10, label_colour)
	draw_string(ThemeDB.fallback_font, Vector2(area.position.x, size.y - 10), _clock_text(first_time), HORIZONTAL_ALIGNMENT_LEFT, -1, 10, label_colour)
	draw_string(ThemeDB.fallback_font, Vector2(area.end.x - 42, size.y - 10), _clock_text(last_time), HORIZONTAL_ALIGNMENT_LEFT, -1, 10, label_colour)


func _clock_text(absolute_minute: float) -> String:
	var minute := int(fposmod(absolute_minute, 1440.0))
	return "%02d:%02d" % [int(minute / 60), minute % 60]
