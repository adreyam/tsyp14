extends Control

signal time_selected(minute_of_day: int)
signal expanded_changed(expanded: bool)

var conditions := {"minute_of_day":540, "time_text":"09:00", "day":1, "period":"Morning", "weather_label":"Clear"}
var expanded := false
var _ignore_dial_until_left_release := false


func _ready() -> void:
	mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
	mouse_filter = Control.MOUSE_FILTER_STOP
	_update_minimum_size()


func set_conditions(next_conditions: Dictionary) -> void:
	conditions = next_conditions.duplicate(true)
	queue_redraw()


func set_expanded(next_expanded: bool) -> void:
	if expanded == next_expanded:
		return
	expanded = next_expanded
	_update_minimum_size()
	expanded_changed.emit(expanded)
	queue_redraw()


func _update_minimum_size() -> void:
	custom_minimum_size = Vector2(310, 222) if expanded else Vector2(174, 70)


func _gui_input(event: InputEvent) -> void:
	if event is InputEventMouseButton and (event as InputEventMouseButton).button_index == MOUSE_BUTTON_LEFT:
		var mouse_button := event as InputEventMouseButton
		if not mouse_button.pressed and _ignore_dial_until_left_release:
			_ignore_dial_until_left_release = false
			accept_event()
			return
		if not mouse_button.pressed:
			return
		if not expanded:
			# Resizing the compact control moves the dial underneath the same held
			# pointer. Ignore that entire press/drag gesture until it is released;
			# the first click opens the clock and can never also change the time.
			_ignore_dial_until_left_release = true
			set_expanded(true)
			accept_event()
			return
		if _ignore_dial_until_left_release:
			accept_event()
			return
		if mouse_button.position.x >= size.x - 34.0 and mouse_button.position.y <= 34.0:
			set_expanded(false)
			accept_event()
			return
		_select_time(mouse_button.position)
	elif expanded and not _ignore_dial_until_left_release and event is InputEventMouseMotion and (event as InputEventMouseMotion).button_mask & MOUSE_BUTTON_MASK_LEFT:
		_select_time((event as InputEventMouseMotion).position)


func _select_time(position_value: Vector2) -> void:
	var centre := Vector2(size.x * 0.5, size.y * 0.48)
	var offset := position_value - centre
	if offset.length() < 38.0:
		return
	var angle := fposmod(atan2(offset.y, offset.x) + PI * 0.5, TAU)
	var minute := int(round(angle / TAU * 1440.0 / 5.0) * 5.0) % 1440
	time_selected.emit(minute)


func _draw() -> void:
	if not expanded:
		_draw_compact()
		return
	var centre := Vector2(size.x * 0.5, size.y * 0.48)
	var radius := minf(72.0, minf(size.x * 0.31, size.y * 0.40))
	draw_circle(centre, radius + 14.0, Color("171c24"))
	draw_arc(centre, radius + 8.0, 0.0, TAU, 96, Color("566170"), 1.2, true)
	# Civil twilight/day and night are distinct arcs on a true 24-hour dial.
	draw_arc(centre, radius + 8.0, _hour_angle(6.0), _hour_angle(19.0), 52, Color("e7b95d"), 7.0, true)
	draw_arc(centre, radius + 8.0, _hour_angle(19.0), _hour_angle(30.0), 44, Color("526fa8"), 7.0, true)
	for hour in 24:
		var angle := _hour_angle(float(hour))
		var major := hour % 3 == 0
		var outer := centre + Vector2(cos(angle), sin(angle)) * (radius + 2.0)
		var inner := centre + Vector2(cos(angle), sin(angle)) * (radius - (8.0 if major else 4.0))
		draw_line(inner, outer, Color("d8e0e8") if major else Color("7b8794"), 1.6 if major else 1.0)
	var minute := int(conditions.get("minute_of_day", 0))
	var marker_angle := _hour_angle(float(minute) / 60.0)
	var marker := centre + Vector2(cos(marker_angle), sin(marker_angle)) * (radius + 8.0)
	var is_night := bool(conditions.get("is_night", false))
	draw_circle(marker, 7.0, Color("91b7ff") if is_night else Color("ffd56b"))
	draw_circle(marker, 3.0, Color("eaf2ff") if is_night else Color("fff5cf"))
	draw_string(ThemeDB.fallback_font, centre + Vector2(-56, -5), _clock_text(), HORIZONTAL_ALIGNMENT_CENTER, 112, 24, Color("f3f6f9"))
	draw_string(ThemeDB.fallback_font, centre + Vector2(-58, 18), "Day %d  ·  %s" % [int(conditions.get("day", 1)), str(conditions.get("period", ""))], HORIZONTAL_ALIGNMENT_CENTER, 116, 11, Color("b9c5d1"))
	draw_string(ThemeDB.fallback_font, centre + Vector2(-58, 36), str(conditions.get("weather_label", "")), HORIZONTAL_ALIGNMENT_CENTER, 116, 11, Color("7ed3ee"))
	draw_string(ThemeDB.fallback_font, centre + Vector2(-72, 52), _pace_text(), HORIZONTAL_ALIGNMENT_CENTER, 144, 10, Color("e2b96d") if _is_falling_behind() else Color("8fa3b5"))
	draw_string(ThemeDB.fallback_font, Vector2(10, size.y - 8), "Click or drag the 24-hour dial to set city time", HORIZONTAL_ALIGNMENT_CENTER, size.x - 20, 10, Color("8996a4"))
	draw_string(ThemeDB.fallback_font, Vector2(size.x - 30, 23), "×", HORIZONTAL_ALIGNMENT_CENTER, 20, 18, Color("b9c7d5"))


func _draw_compact() -> void:
	var is_night := bool(conditions.get("is_night", false))
	var accent := Color("91b7ff") if is_night else Color("ffd56b")
	draw_string(ThemeDB.fallback_font, Vector2(10, 25), _clock_text(), HORIZONTAL_ALIGNMENT_LEFT, 112, 20 if str(conditions.get("clock_text", "")).length() > 5 else 21, Color("f3f6f9"))
	draw_string(ThemeDB.fallback_font, Vector2(10, 44), "Day %d · %s" % [int(conditions.get("day", 1)), str(conditions.get("period", ""))], HORIZONTAL_ALIGNMENT_LEFT, 112, 10, Color("bac7d3"))
	draw_string(ThemeDB.fallback_font, Vector2(10, 61), "Click to expand", HORIZONTAL_ALIGNMENT_LEFT, 112, 9, Color("7f91a2"))
	var centre := Vector2(size.x - 39.0, size.y * 0.5)
	var radius := 24.0
	draw_circle(centre, radius + 4.0, Color("151c27"))
	draw_arc(centre, radius, _hour_angle(6.0), _hour_angle(19.0), 32, Color("e7b95d"), 4.0, true)
	draw_arc(centre, radius, _hour_angle(19.0), _hour_angle(30.0), 28, Color("526fa8"), 4.0, true)
	var minute := int(conditions.get("minute_of_day", 0))
	var marker_angle := _hour_angle(float(minute) / 60.0)
	draw_circle(centre + Vector2(cos(marker_angle), sin(marker_angle)) * radius, 4.0, accent)
	draw_string(ThemeDB.fallback_font, centre + Vector2(-11, 4), "☾" if is_night else "☀", HORIZONTAL_ALIGNMENT_CENTER, 22, 13, accent)


func _clock_text() -> String:
	if bool(conditions.get("paused", false)):
		return "%s ⏸" % str(conditions.get("clock_text", conditions.get("time_text", "--:--")))
	return str(conditions.get("clock_text", conditions.get("time_text", "--:--")))


func _is_falling_behind() -> bool:
	return float(conditions.get("speed_delivered", 1.0)) < float(conditions.get("speed", 1.0)) * 0.9


func _pace_text() -> String:
	## What the clock is running at: its rate, and the simulation speed actually
	## being delivered when the machine cannot keep up with the one asked for.
	var rate := float(conditions.get("clock_rate", 1.0))
	var label := "real time" if rate <= 1.001 else "1 s = %s" % _duration_text(rate)
	var speed := float(conditions.get("speed", 1.0))
	var delivered := float(conditions.get("speed_delivered", speed))
	if speed > 1.001 and _is_falling_behind():
		return "%s · %d× asked, %.1f× delivered" % [label, int(speed), delivered]
	return label if speed <= 1.001 else "%s · %d×" % [label, int(speed)]


func _duration_text(seconds: float) -> String:
	if seconds >= 60.0:
		return "%d min" % int(round(seconds / 60.0))
	return "%d s" % int(round(seconds))


func _hour_angle(hour: float) -> float:
	return -PI * 0.5 + hour / 24.0 * TAU
