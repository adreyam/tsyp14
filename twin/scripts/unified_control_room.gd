extends Node

## One native Godot application: the city is rendered directly into the centre
## viewport and the surrounding panels are controls, not an overlay or a second
## app. Selecting a traffic camera changes this viewport's active Camera3D.

const WorldTime := preload("res://scripts/world_time.gd")
const BG := Color("25252d")
const PANEL := Color("3b3b3b")
const BORDER := Color("1b1b1b")
const TEXT := Color("e5e5e5")
const MUTED := Color("c1c1c1")
const ACCENT := Color("6ea7cf")
const GREEN := Color("85c58b")
const AMBER := Color("e2b96d")
const RED := Color("df8585")
const UI_REFRESH_INTERVAL := 0.50
const SENSOR_CATEGORIES := [
	["all", "All"], ["traffic", "Traffic"], ["camera", "Cameras"], ["air", "Air"], ["noise", "Noise"],
	["weather", "Weather"], ["flood", "Flood"], ["utility", "Utilities"], ["power_meter", "Power kW"],
	["water_meter", "Water m³"], ["waste", "Bins"], ["signal", "Signals"], ["power_switch", "Switches"],
	["water_valve", "Valves"], ["lighting", "Lighting"], ["irrigation", "Irrigation"], ["reservoir", "Reservoirs"],
	["grid", "Grid"], ["service_zone", "Service zones"], ["seismic", "Seismic"], ["parking", "Parking"],
]
const SimApi := preload("res://scripts/sim_api.gd")
const CityZones := preload("res://scripts/city_zones.gd")
const DEFAULT_API_PORT := 8820

const ASSET_HIGHLIGHT_DEFINITIONS := {
	"traffic_detectors":{"label":"Traffic detectors", "group":"traffic_sensors", "color":Color("21c7e8"), "detail":"Roadside radar/counting gateways"},
	"air_quality":{"label":"Air quality", "group":"air_quality_monitors", "color":Color("65d68a"), "detail":"PM2.5, PM10 and exhaust monitors"},
	"noise":{"label":"Noise monitors", "group":"noise_monitors", "color":Color("bd72ef"), "detail":"Class-one environmental sound monitors"},
	"weather":{"label":"Weather stations", "group":"weather_stations", "color":Color("f6a04d"), "detail":"Wind, rain and temperature stations"},
	"flood":{"label":"Flood / level", "group":"flood_level_sensors", "color":Color("3e7ef5"), "detail":"Non-contact surface-water radar sensors"},
	"utility_telemetry":{"label":"Utility telemetry", "group":"utility_telemetry_sensors", "color":Color("38c7b2"), "detail":"Power, pressure and cabinet telemetry"},
	"traffic_cameras":{"label":"Street cameras", "group":"traffic_cameras", "color":Color("9bc8ff"), "detail":"Physical live traffic-camera rigs"},
	"traffic_signals":{"label":"Traffic signals", "group":"traffic_signals", "color":Color("f15d55"), "detail":"SUMO-authoritative signal actuators"},
	"water_valves":{"label":"Water valves", "group":"water_valves", "color":Color("33a9f4"), "detail":"Controllable distribution valves"},
	"power_switches":{"label":"Power switches", "group":"power_switches", "color":Color("f0b83f"), "detail":"Grid isolation and switching cabinets"},
	"street_lights":{"label":"Smart lights", "group":"smart_street_lights", "color":Color("fff07a"), "detail":"Adaptive public-realm lighting actuators"},
	"irrigation":{"label":"Irrigation", "group":"irrigation_valves", "color":Color("3ed077"), "detail":"Park irrigation flow actuators"},
	"power_meters":{"label":"Power meters", "group":"", "color":Color("ff8f33"), "detail":"Every zone, job and interaction power meter"},
	"water_meters":{"label":"Water meters", "group":"", "color":Color("27d4d1"), "detail":"Every zone, job and interaction water meter"},
	"hospitals":{"label":"Hospitals", "group":"hospitals", "color":Color("f47ba4"), "detail":"Emergency and clinical facilities"},
	"police_stations":{"label":"Police building", "group":"police_facilities", "color":Color("5b8dff"), "detail":"Police headquarters and public service facility"},
	"police_vehicles":{"label":"Police cars", "group":"police_vehicles", "color":Color("3b6fe3"), "detail":"Active and district police vehicles"},
	"ambulances":{"label":"Ambulances", "group":"ambulance_vehicles", "color":Color("f04d68"), "detail":"Moving emergency medical vehicles"},
	"smoky_vehicles":{"label":"High emissions", "group":"smoky_vehicles", "color":Color("d96b2b"), "detail":"Vehicles emitting visible black exhaust smoke"},
	"emergency_stations":{"label":"Response stations", "group":"emergency_stations", "color":Color("ff6f61"), "detail":"Police, EMS, fire, tow, utility and sanitation station bays"},
	"fire_engines":{"label":"Fire engines", "group":"fire_vehicles", "color":Color("e8412c"), "detail":"Fire service pumpers"},
	"tow_trucks":{"label":"Tow trucks", "group":"tow_vehicles", "color":Color("f2a541"), "detail":"Recovery flatbeds that clear wrecks"},
	"utility_vans":{"label":"Utility crews", "group":"utility_vehicles", "color":Color("f0d34a"), "detail":"Water and power repair vans"},
	"seismic_sensors":{"label":"Seismic sensors", "group":"seismic_sensors", "color":Color("ff9a1f"), "detail":"One strong-motion accelerograph per zone"},
	"parking_lots":{"label":"Smart car parks", "group":"parking_lots", "color":Color("4fd3ff"), "detail":"Barrier, occupancy board and bays"},
	"bin_weights":{"label":"Bin weight sensors", "group":"bin_weight_sensors", "color":Color("8fd45a"), "detail":"Load cells on every street bin and container"},
	"garbage_bins":{"label":"Garbage containers", "group":"garbage_bins", "color":Color("4cbf63"), "detail":"Large green four-wheel street containers"},
	"refuse_trucks":{"label":"Refuse trucks", "group":"garbage_vehicles", "color":Color("2f9e4f"), "detail":"Rear-loader collection trucks and their crews"},
}
const NPC_ROLE_PURPOSE := {
	"parcel courier":"Deliver verified parcels",
	"sanitation worker":"Keep public paths and waste points safe",
	"utilities technician":"Inspect utilities and report service conditions",
	"parks worker":"Maintain planting areas and public seating",
	"clinic assistant":"Support visitors and organize clinic supplies",
	"transit steward":"Assist travellers and monitor transit facilities",
	"market stocker":"Maintain local stock and complete deposits",
	"local resident":"Complete daily neighborhood routines",
	"road maintenance technician":"Inspect street hardware and secure work areas",
	"building maintenance technician":"Verify building services and emergency access",
	"public information technician":"Maintain civic notices and public displays",
	"horticulture specialist":"Care for greenhouse plants and public flowers"
}

var _city: Node3D
var _city_viewport: SubViewport
var _command_udp := PacketPeerUDP.new()
var _metrics: Dictionary = {}
var _connection: Label
var _phase: Label
var _camera_buttons: Array[Button] = []
var _follow_buttons: Array[Button] = []
var _infrastructure_buttons: Array[Button] = []
var _asset_highlight_buttons: Array[Button] = []
var _camera_status: Label
var _event_log: RichTextLabel
var _events: Array[String] = []
var _refresh_elapsed := 0.0
var _last_phase := ""
var _last_bridge_action := ""
var _last_event_message := ""
var _last_event_repeats := 1
var _active_infrastructure_highlight := ""
var _active_asset_highlight := ""
var _asset_frame_materials: Dictionary = {}
var _asset_highlight_holder: Node3D
var _focus_highlight_holder: Node3D
var _focused_city_object: Node3D
var _water_frame_material: StandardMaterial3D
var _power_frame_material: StandardMaterial3D
var _power_line_highlight_material: StandardMaterial3D
var _utility_power_label: Label
var _utility_power_bar: ProgressBar
var _utility_water_labels: Dictionary = {}
var _utility_water_bars: Dictionary = {}
var _utility_block_labels: Dictionary = {}
var _utility_schedule_label: Label
var _utility_auto_button: Button
var _utility_outage_label: Label
var _utility_water_cluster_buttons: Dictionary = {}
var _utility_power_cluster_buttons: Dictionary = {}
var _last_utility_service_signature := ""
var _control_tabs: TabContainer
var _npc_tab_index := -1
var _selected_npc: Node3D
var _npc_selection_label: Label
var _npc_character_label: Label
var _npc_personality_label: Label
var _npc_identity_context_label: Label
var _npc_now_label: Label
var _npc_now_context_label: Label
var _npc_status_label: Label
var _npc_partner_label: Label
var _npc_activity_label: Label
var _npc_objective_label: Label
var _npc_intent_label: Label
var _npc_route_label: Label
var _npc_planner_label: Label
var _npc_payload: RichTextLabel
var _npc_response: RichTextLabel
var _npc_detail_bodies: Array[Control] = []
var _layout_root: Control
var _left_panel: PanelContainer
var _centre_panel: PanelContainer
var _right_panel: PanelContainer
var _log_panel: PanelContainer
var _view_only := false   # Ctrl+F: the viewport fills the whole window (still a normal window, not fullscreen)
var _left_divider: Control
var _right_divider: Control
var _bottom_divider: Control
var _left_width := 220.0
var _right_width := 380.0
var _log_height := 118.0
var _active_layout_drag := ""
var _drag_origin := Vector2.ZERO
var _drag_start_value := 0.0
var _incident_panel: VBoxContainer
var _environment_system: Node
var _speed_buttons: Dictionary = {}
var _clock_rate_buttons: Dictionary = {}
var _weather_buttons: Dictionary = {}
var _sensor_reading_grid: GridContainer
var _sensor_device_grid: GridContainer
var _sensor_search: LineEdit
var _asset_status: Label
var _asset_group_bodies := {}
var _asset_group_headers := {}
var _sensor_category := "all"
var _sensor_category_buttons := {}
var _sensor_page_label: Label
var _sensor_visible_ids: Array[String] = []
var _sensor_page := 0
var _sensor_chart: Control
var _sensor_status: Label
var _sensor_detail_label: Label
var _sensor_history: Dictionary = {}
var _sensor_catalog: Array[Dictionary] = []
var _sim_api: Node
var _zone_all_button: Button
var _zone_buttons := {}
var _selected_sensor_id := ""
var _selected_sensor_metric := ""
var _last_sensor_sample_minute := -99999.0
var _last_sensor_sample_msec := 0
var _world_clock: Control
var _clock_overlay: PanelContainer


func _ready() -> void:
	DisplayServer.window_set_title("City Ground Zero / Live Operations Control")
	# Keep the normal desktop title bar while opening at full usable desktop size.
	# This is deliberately maximized, not exclusive/borderless fullscreen.
	DisplayServer.window_set_mode(DisplayServer.WINDOW_MODE_MAXIMIZED)
	DisplayServer.window_set_min_size(Vector2i(1100, 680))
	var control_port := OS.get_environment("CITY_SUMO_CONTROL_PORT")
	_command_udp.connect_to_host("127.0.0.1", int(control_port) if control_port.is_valid_int() else 8815)
	_build_window()
	get_viewport().size_changed.connect(_apply_layout)
	call_deferred("_apply_layout")
	call_deferred("_install_sim_api")
	# Dynamic systems finish registering emergency fleets and emission sources at
	# the end of this startup frame; refresh badges after that deferred pass.
	call_deferred("_refresh_asset_button_labels")
	_add_event("Unified control room started — SUMO bridge is connecting")


func _process(delta: float) -> void:
	_refresh_elapsed += delta
	if _refresh_elapsed < UI_REFRESH_INTERVAL:
		return
	_refresh_elapsed = 0.0
	_refresh_ui()


func _build_window() -> void:
	var layer := CanvasLayer.new()
	add_child(layer)
	var root := ColorRect.new()
	root.color = BG
	root.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	layer.add_child(root)
	_layout_root = root
	var title := _label("CITY GROUND ZERO  /  LIVE OPERATIONS CONTROL", 19, TEXT)
	title.position = Vector2(20, 14)
	root.add_child(title)
	_connection = _label("●  CONNECTING TO SUMO", 12, AMBER)
	_connection.position = Vector2(20, 42)
	root.add_child(_connection)

	_left_panel = _panel(root, Vector2.ZERO, Vector2.ZERO)
	_build_left_panel(_left_panel)
	_centre_panel = _panel(root, Vector2.ZERO, Vector2.ZERO)
	_build_centre_panel(_centre_panel)
	_right_panel = _panel(root, Vector2.ZERO, Vector2.ZERO)
	_build_right_panel(_right_panel)
	_log_panel = _panel(root, Vector2.ZERO, Vector2.ZERO)
	var log_box := VBoxContainer.new()
	_log_panel.add_child(log_box)
	_add_caption(log_box, "CITY GROUND ZERO  ·  EVENT LOG")
	_event_log = RichTextLabel.new()
	_event_log.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_event_log.custom_minimum_size = Vector2(0, 42)
	_event_log.scroll_active = true
	_event_log.add_theme_font_size_override("normal_font_size", 12)
	log_box.add_child(_event_log)
	_left_divider = _layout_divider(root, "left")
	_right_divider = _layout_divider(root, "right")
	_bottom_divider = _layout_divider(root, "bottom")


func _input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and not event.echo and event.ctrl_pressed and event.keycode == KEY_F:
		_view_only = not _view_only
		_apply_layout()
		get_viewport().set_input_as_handled()


func _apply_layout() -> void:
	if _layout_root == null or _left_panel == null:
		return
	var viewport_size := get_viewport().get_visible_rect().size
	if viewport_size.x < 10.0 or viewport_size.y < 10.0:
		return
	var margin := 12.0
	var gap := 8.0
	var header_height := 64.0
	for hidden_part in [_left_panel, _right_panel, _log_panel, _left_divider, _right_divider, _bottom_divider]:
		if hidden_part != null:
			(hidden_part as Control).visible = not _view_only
	if _view_only:
		_centre_panel.position = Vector2(4.0, 4.0)
		_centre_panel.size = viewport_size - Vector2(8.0, 8.0)
		return
	# Every column runs the full height: the viewport has no fixed aspect and
	# the side rails use their whole height (no dead space, no clipped rows).
	var rail_height := maxf(400.0, viewport_size.y - header_height - margin)
	_left_width = clampf(_left_width, 220.0, 320.0)
	_right_width = clampf(_right_width, 380.0, 600.0)
	var centre_width := viewport_size.x - margin * 2.0 - gap * 2.0 - _left_width - _right_width
	if centre_width < 480.0:
		var missing := 480.0 - centre_width
		_right_width = maxf(340.0, _right_width - missing)
		centre_width = viewport_size.x - margin * 2.0 - gap * 2.0 - _left_width - _right_width
	var centre_x := margin + _left_width + gap
	var right_x := centre_x + centre_width + gap
	_left_panel.position = Vector2(margin, header_height)
	_left_panel.size = Vector2(_left_width, rail_height)
	# The event log runs along the bottom of the centre column; the viewport
	# fills everything above it (draggable border between the two).
	_log_height = clampf(_log_height, 70.0, rail_height * 0.45)
	var main_height := rail_height - gap - _log_height
	_centre_panel.position = Vector2(centre_x, header_height)
	_centre_panel.size = Vector2(centre_width, main_height)
	_log_panel.position = Vector2(centre_x, header_height + main_height + gap)
	_log_panel.size = Vector2(centre_width, _log_height)
	_right_panel.position = Vector2(right_x, header_height)
	_right_panel.size = Vector2(_right_width, rail_height)
	_layout_divider_position(_left_divider, Vector2(centre_x - gap, header_height), Vector2(gap * 2.0, rail_height), Control.CURSOR_HSIZE)
	_layout_divider_position(_right_divider, Vector2(right_x - gap, header_height), Vector2(gap * 2.0, rail_height), Control.CURSOR_HSIZE)
	_layout_divider_position(_bottom_divider, Vector2(centre_x, header_height + main_height - gap * 0.5), Vector2(centre_width, gap * 2.0), Control.CURSOR_VSIZE)
	call_deferred("_fit_all_right_tab_frames")


func _layout_divider(parent: Control, drag_kind: String) -> Control:
	var divider := ColorRect.new()
	divider.name = "%s_resize_border" % drag_kind.capitalize()
	divider.color = Color(0.40, 0.58, 0.70, 0.22)
	divider.mouse_filter = Control.MOUSE_FILTER_STOP
	divider.set_meta("drag_kind", drag_kind)
	divider.gui_input.connect(_on_layout_divider_input.bind(drag_kind))
	parent.add_child(divider)
	return divider


func _layout_divider_position(divider: Control, position_value: Vector2, size_value: Vector2, cursor: Control.CursorShape) -> void:
	if divider == null:
		return
	divider.position = position_value
	divider.size = size_value
	divider.mouse_default_cursor_shape = cursor


func _on_layout_divider_input(event: InputEvent, drag_kind: String) -> void:
	if event is InputEventMouseButton and event.button_index == MOUSE_BUTTON_LEFT:
		if event.pressed:
			_active_layout_drag = drag_kind
			_drag_origin = get_viewport().get_mouse_position()
			_drag_start_value = _left_width if drag_kind == "left" else (_right_width if drag_kind == "right" else _log_height)
		else:
			_active_layout_drag = ""
		get_viewport().set_input_as_handled()
	elif event is InputEventMouseMotion and _active_layout_drag == drag_kind:
		var delta := get_viewport().get_mouse_position() - _drag_origin
		if drag_kind == "left":
			_left_width = _drag_start_value + delta.x
		elif drag_kind == "right":
			_right_width = _drag_start_value - delta.x
		else:
			_log_height = _drag_start_value - delta.y
		_apply_layout()
		get_viewport().set_input_as_handled()


func _panel(parent: Control, position_value: Vector2, size_value: Vector2) -> PanelContainer:
	var panel := PanelContainer.new()
	panel.position = position_value
	panel.size = size_value
	panel.add_theme_stylebox_override("panel", _style(PANEL, BORDER, 1, 2))
	parent.add_child(panel)
	return panel


func _build_left_panel(panel: PanelContainer) -> void:
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 9)
	panel.add_child(box)
	_add_caption(box, "CITY GROUND ZERO")
	box.add_child(_label("Live local operations · SUMO / Godot", 12, MUTED))
	box.add_child(HSeparator.new())
	for entry in [["Time", "time"], ["Weather", "weather"], ["Speed", "speed"], ["Traffic", "simulated"], ["Rendered vehicles", "visible"], ["Stopped", "stopped"], ["Turning", "turning"], ["Traffic signals", "signals"], ["Power grid", "power"], ["People", "people"]]:
		var row := HBoxContainer.new()
		var name := _label(str(entry[0]), 13, TEXT)
		name.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		row.add_child(name)
		var value := _label("—", 13, MUTED)
		row.add_child(value)
		_metrics[str(entry[1])] = value
		box.add_child(row)
	box.add_child(HSeparator.new())
	_add_caption(box, "SCENE")
	for detail in ["2 renewable source hubs", "redundant transmission ring", "16 physical traffic cameras", "200 persistent citizens · 32 embodied"]:
		box.add_child(_label(detail, 12, MUTED))
	box.add_child(HSeparator.new())
	_add_caption(box, "NAVIGATION")
	_add_button(box, "Frame city overview", _show_overview)
	_add_button(box, "Street-detail position", _show_street)
	_add_button(box, "Reset city camera", _reset_camera)
	box.add_child(HSeparator.new())
	_add_caption(box, "SIMULATION")
	_add_button(box, "Pause SUMO", func() -> void: _send_sumo_command("pause"))
	_add_button(box, "Resume SUMO", func() -> void: _send_sumo_command("resume"))


func _build_centre_panel(panel: PanelContainer) -> void:
	var box := VBoxContainer.new()
	panel.add_child(box)
	var header := HBoxContainer.new()
	box.add_child(header)
	var caption := _label("Viewport", 14, TEXT)
	caption.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	header.add_child(caption)
	_phase = _label("WAITING FOR SIGNAL DATA", 12, AMBER)
	header.add_child(_phase)
	# The city fills the whole centre column at any window shape; the camera
	# keeps its vertical field of view, so only the horizontal extent varies.
	var viewport_frame := SubViewportContainer.new()
	viewport_frame.name = "CityViewportFrame"
	viewport_frame.stretch = true
	viewport_frame.size_flags_vertical = Control.SIZE_EXPAND_FILL
	viewport_frame.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	viewport_frame.custom_minimum_size = Vector2(320, 240)
	viewport_frame.mouse_filter = Control.MOUSE_FILTER_STOP
	box.add_child(viewport_frame)
	_city_viewport = SubViewport.new()
	_city_viewport.size = Vector2i(1280, 720)
	# Same anti-aliasing as the stand-alone city: TAA resolves the soft
	# shadow / ambient occlusion sampling noise and edge aliasing together.
	_city_viewport.use_taa = true
	_city_viewport.anisotropic_filtering_level = Viewport.ANISOTROPY_16X
	_city_viewport.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	viewport_frame.add_child(_city_viewport)
	_city = (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	_city_viewport.add_child(_city)
	_enable_operational_object_focus()
	call_deferred("_enable_operational_object_focus")
	_environment_system = _city.get_node_or_null("EnvironmentSystem")
	_build_world_clock_overlay(viewport_frame)
	var city_camera := _city.get_node_or_null("EstablishingCamera") as Camera3D
	if city_camera != null and city_camera.has_signal("target_selected"):
		city_camera.target_selected.connect(_on_city_target_selected)
	if city_camera != null and city_camera.has_signal("selection_cleared"):
		city_camera.selection_cleared.connect(_clear_city_object_focus)
	if city_camera != null and city_camera.has_signal("street_camera_released"):
		city_camera.street_camera_released.connect(_on_street_camera_released)
	# The original help/status labels are intended for the normal launcher. The
	# control room provides the surrounding UI instead, leaving the view clean.
	var city_hud := _city.get_node_or_null("CameraControls") as CanvasLayer
	if city_hud != null:
		city_hud.visible = false


func _build_world_clock_overlay(viewport_frame: SubViewportContainer) -> void:
	_clock_overlay = PanelContainer.new()
	_clock_overlay.name = "WorldClockOverlay"
	_clock_overlay.z_index = 20
	_clock_overlay.mouse_filter = Control.MOUSE_FILTER_STOP
	_clock_overlay.add_theme_stylebox_override("panel", _style(Color(0.035, 0.05, 0.075, 0.94), Color(0.38, 0.58, 0.78, 0.8), 1, 7))
	viewport_frame.add_child(_clock_overlay)
	_world_clock = (load("res://scripts/world_clock.gd") as GDScript).new() as Control
	_world_clock.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_world_clock.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_world_clock.connect("time_selected", _set_time_of_day)
	_world_clock.connect("expanded_changed", _set_world_clock_expanded)
	_clock_overlay.add_child(_world_clock)
	_set_world_clock_expanded(false)


func _set_world_clock_expanded(expanded: bool) -> void:
	if _clock_overlay == null:
		return
	var overlay_size := Vector2(318, 230) if expanded else Vector2(182, 78)
	_clock_overlay.set_anchor(SIDE_LEFT, 1.0)
	_clock_overlay.set_anchor(SIDE_RIGHT, 1.0)
	_clock_overlay.set_anchor(SIDE_TOP, 0.0)
	_clock_overlay.set_anchor(SIDE_BOTTOM, 0.0)
	_clock_overlay.offset_left = -overlay_size.x - 10.0
	_clock_overlay.offset_right = -10.0
	_clock_overlay.offset_top = 10.0
	_clock_overlay.offset_bottom = 10.0 + overlay_size.y


func _build_right_panel(panel: PanelContainer) -> void:
	var tabs := TabContainer.new()
	_control_tabs = tabs
	tabs.name = "ControlTabs"
	tabs.size_flags_vertical = Control.SIZE_EXPAND_FILL
	tabs.add_theme_font_size_override("font_size", 12)
	# The tab bar must obey the draggable rail width; otherwise its combined
	# title minimum silently expands the panel while the stored width stays put.
	tabs.clip_tabs = true
	panel.add_child(tabs)
	var operations_scroll := ScrollContainer.new()
	operations_scroll.name = "Operations"
	operations_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	tabs.add_child(operations_scroll)
	var operations_frame:=_right_tab_content_frame(operations_scroll,"OperationsContentFrame",960.0)
	var box := VBoxContainer.new()
	box.name="OperationsContent"
	box.size_flags_horizontal=Control.SIZE_EXPAND_FILL
	box.add_theme_constant_override("separation", 8)
	operations_frame.add_child(box)
	_add_caption(box, "CITY CONTROL")
	_camera_status = _label("One direct viewport camera active", 12, AMBER)
	_camera_status.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(_camera_status)
	_build_environment_controls(box)
	box.add_child(HSeparator.new())
	var camera_grid := GridContainer.new()
	camera_grid.columns = 4
	camera_grid.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	camera_grid.add_theme_constant_override("h_separation", 7)
	camera_grid.add_theme_constant_override("v_separation", 7)
	for index in range(1, 17):
		var camera_id := "CAM-%02d" % index
		var camera_button := Button.new()
		camera_button.text = camera_id
		camera_button.tooltip_text = "%s — view its live junction" % camera_id
		camera_button.custom_minimum_size = Vector2(0, 30)
		camera_button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		camera_button.add_theme_font_size_override("font_size", 12)
		camera_button.pressed.connect(_open_camera_id.bind(camera_id))
		camera_grid.add_child(camera_button)
		_camera_buttons.append(camera_button)
	box.add_child(camera_grid)
	var reset_view := Button.new()
	reset_view.text = "RETURN TO CITY OVERVIEW"
	reset_view.custom_minimum_size = Vector2(0, 32)
	reset_view.add_theme_font_size_override("font_size", 12)
	reset_view.pressed.connect(_return_to_city_view)
	box.add_child(reset_view)
	_add_caption(box, "RANDOM LIVE FOLLOW")
	var follow_row := HBoxContainer.new()
	follow_row.add_theme_constant_override("separation", 7)
	var random_person := Button.new()
	random_person.text = "Random Citizen"
	random_person.tooltip_text = "Follow a citizen who is currently working with a prop/item or actively interacting — walkers are excluded"
	random_person.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	random_person.custom_minimum_size = Vector2(0, 30)
	random_person.add_theme_font_size_override("font_size", 12)
	random_person.pressed.connect(_follow_random_person)
	follow_row.add_child(random_person)
	_follow_buttons.append(random_person)
	var random_car := Button.new()
	random_car.text = "Random Car"
	random_car.tooltip_text = "Frame and follow a random active traffic vehicle"
	random_car.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	random_car.custom_minimum_size = Vector2(0, 30)
	random_car.add_theme_font_size_override("font_size", 12)
	random_car.pressed.connect(_follow_random_car)
	follow_row.add_child(random_car)
	_follow_buttons.append(random_car)
	box.add_child(follow_row)
	box.add_child(HSeparator.new())
	_add_caption(box, "COORDINATED SIGNAL CONTROL")
	box.add_child(_label("All 49 controllers update together.", 11, MUTED))
	_add_button(box, "North / South green", func() -> void: _send_sumo_command("ns_green"))
	_add_button(box, "North / South amber", func() -> void: _send_sumo_command("ns_yellow"))
	_add_button(box, "All-red clearance", func() -> void: _send_sumo_command("all_red"))
	_add_button(box, "East / West green", func() -> void: _send_sumo_command("ew_green"))
	_add_button(box, "East / West amber", func() -> void: _send_sumo_command("ew_yellow"))
	_add_button(box, "Restart normal cycle", func() -> void: _send_sumo_command("reset_cycle"))
	_build_incidents_tab(tabs)
	_build_asset_map_tab(tabs)
	_build_utilities_tab(tabs)
	_build_npc_tab(tabs)
	_build_sensor_tab(tabs)
	for index in tabs.get_tab_count():
		tabs.set_tab_title(index, ["Control", "Incidents", "Assets", "Utilities", "Society", "Sensors"][index])


func _build_environment_controls(parent: VBoxContainer) -> void:
	_add_caption(parent, "TIME OF DAY  ·  WEATHER  ·  SPEED")
	var condition_label := _label("The live city clock is over the viewport. Click it to expand, drag its dial, or use a shortcut.", 11, MUTED)
	condition_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	parent.add_child(condition_label)
	var time_grid := GridContainer.new()
	time_grid.columns = 4
	time_grid.add_theme_constant_override("h_separation", 6)
	time_grid.add_theme_constant_override("v_separation", 6)
	for entry in [["Dawn", 6 * 60], ["Noon", 12 * 60], ["Dusk", 18 * 60 + 30], ["Night", 22 * 60]]:
		var button := Button.new()
		button.text = str(entry[0])
		button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		button.custom_minimum_size = Vector2(0, 29)
		button.pressed.connect(_set_time_of_day.bind(int(entry[1])))
		time_grid.add_child(button)
	parent.add_child(time_grid)
	var speed_row := HBoxContainer.new()
	speed_row.add_theme_constant_override("separation", 6)
	for multiplier in [1, 2, 4, 8]:
		var button := Button.new()
		button.text = "%dx" % multiplier
		button.toggle_mode = true
		button.button_pressed = multiplier == 1
		button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		button.pressed.connect(_set_simulation_speed.bind(float(multiplier)))
		speed_row.add_child(button)
		_speed_buttons[multiplier] = button
	parent.add_child(speed_row)
	var rate_caption := _label("City clock pace", 11, MUTED)
	parent.add_child(rate_caption)
	var rate_row := HBoxContainer.new()
	rate_row.add_theme_constant_override("separation", 6)
	for preset in WorldTime.PRESETS:
		var rate_button := Button.new()
		rate_button.text = str(preset[0])
		rate_button.tooltip_text = "One simulated second advances the city clock by %d s" % int(float(preset[1]))
		rate_button.toggle_mode = true
		rate_button.button_pressed = is_equal_approx(float(preset[1]), WorldTime.clock_rate)
		rate_button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		rate_button.custom_minimum_size = Vector2(0, 28)
		rate_button.pressed.connect(_set_clock_rate.bind(float(preset[1])))
		rate_row.add_child(rate_button)
		_clock_rate_buttons[float(preset[1])] = rate_button
	parent.add_child(rate_row)
	var weather_grid := GridContainer.new()
	weather_grid.columns = 3
	weather_grid.add_theme_constant_override("h_separation", 6)
	weather_grid.add_theme_constant_override("v_separation", 6)
	for entry in [["Auto", "auto"], ["Clear", "clear"], ["Cloudy", "cloudy"], ["Rain", "rain"], ["Fog", "fog"], ["Storm", "storm"]]:
		var button := Button.new()
		button.text = str(entry[0])
		button.toggle_mode = true
		button.button_pressed = str(entry[1]) == "auto"
		button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		button.custom_minimum_size = Vector2(0, 28)
		button.pressed.connect(_set_weather.bind(str(entry[1])))
		weather_grid.add_child(button)
		_weather_buttons[str(entry[1])] = button
	parent.add_child(weather_grid)


func _install_sim_api() -> void:
	if _city == null or _sim_api != null:
		return
	_sim_api = SimApi.new()
	_sim_api.name = "SimApi"
	add_child(_sim_api)
	_sim_api.call("setup", _city, self)
	# Any MCP client finds the city without configuration: the port is CITY_SIM_API_PORT if given, else 8820 (a free
	# port when that is taken), and is always written to a well-known file. Headless test runs stay closed unless asked.
	var port_text := OS.get_environment("CITY_SIM_API_PORT")
	var wanted := -1
	if port_text.is_valid_int():
		wanted = int(port_text)
	elif DisplayServer.get_name().to_lower() != "headless":
		wanted = DEFAULT_API_PORT
	if wanted < 0:
		return
	var listening := bool(_sim_api.call("start_server", wanted))
	if not listening and wanted != 0:
		listening = bool(_sim_api.call("start_server", 0))
	if not listening:
		return
	var port_value := str(_sim_api.get("port"))
	var tmp_dir := OS.get_environment("TMPDIR") if not OS.get_environment("TMPDIR").is_empty() else "/tmp"
	for path in [OS.get_environment("CITY_SIM_API_READY_FILE"), tmp_dir.path_join("city_ground_zero_api_port")]:
		if str(path).is_empty():
			continue
		var file := FileAccess.open(str(path), FileAccess.WRITE)
		if file != null:
			file.store_string(port_value)
			file.close()
	_add_event("Device API listening on 127.0.0.1:%s (MCP sensors and actuators)" % port_value)


func _set_time_of_day(minute_of_day: int) -> void:
	if _environment_system == null:
		return
	_environment_system.call("set_time_of_day", minute_of_day)
	_add_event("City clock set to %02d:%02d" % [int(minute_of_day / 60), minute_of_day % 60])


func _set_simulation_speed(multiplier: float) -> void:
	if _environment_system == null:
		return
	_environment_system.call("set_simulation_speed", multiplier)
	for key in _speed_buttons:
		(_speed_buttons[key] as Button).button_pressed = is_equal_approx(float(key), multiplier)
	_add_event("Simulation speed set to %dx — city, society, incidents and SUMO remain synchronized" % int(multiplier))


func _set_clock_rate(rate: float) -> void:
	if _environment_system == null:
		return
	_environment_system.call("set_clock_rate", rate)
	for key in _clock_rate_buttons:
		(_clock_rate_buttons[key] as Button).button_pressed = is_equal_approx(float(key), rate)
	_add_event("City clock pace: one simulated second = %d clock seconds" % int(rate))


func _set_weather(weather_name: String) -> void:
	if _environment_system == null:
		return
	if weather_name == "auto":
		_environment_system.call("set_automatic_weather", true)
	else:
		_environment_system.call("set_weather", weather_name)
	for key in _weather_buttons:
		(_weather_buttons[key] as Button).button_pressed = str(key) == weather_name
	_add_event("Weather control: %s" % weather_name.capitalize())


func _build_incidents_tab(tabs: TabContainer) -> void:
	var incident_scroll := ScrollContainer.new()
	incident_scroll.name = "Incidents"
	incident_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	tabs.add_child(incident_scroll)
	var frame := _right_tab_content_frame(incident_scroll, "IncidentsContentFrame", 980.0)
	_incident_panel = (load("res://scripts/incidents/incident_command_panel.gd") as GDScript).new()
	frame.add_child(_incident_panel)
	var city_camera := _city.get_node_or_null("EstablishingCamera") as Camera3D
	_incident_panel.call("setup", _city, city_camera, _add_event, _style)


func _build_asset_map_tab(tabs: TabContainer) -> void:
	var asset_scroll := ScrollContainer.new()
	asset_scroll.name = "Asset Map"
	asset_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	tabs.add_child(asset_scroll)
	var frame := _right_tab_content_frame(asset_scroll, "AssetMapContentFrame", 900.0)
	var box := VBoxContainer.new()
	box.name = "AssetMapContent"
	box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	box.add_theme_constant_override("separation", 6)
	frame.add_child(box)
	_add_caption(box, "ASSET MAP")
	_asset_status = _label("Pick a category to frame every matching asset in the city.", 12, MUTED)
	_asset_status.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(_asset_status)
	var clear_button := Button.new()
	clear_button.name = "AssetClearHighlight"
	clear_button.text = "Clear highlight"
	clear_button.clip_text = true
	clear_button.custom_minimum_size = Vector2(0, 28)
	clear_button.add_theme_font_size_override("font_size", 12)
	clear_button.pressed.connect(_clear_all_highlights)
	box.add_child(clear_button)
	_add_asset_group(box, "CITY ZONES  ·  SHOW ON THE MAP", "zones", [], true)
	_build_zone_toggles(_asset_group_bodies["zones"])
	_add_asset_group(box, "WATER & ELECTRICITY NETWORKS", "water_power", [], true)
	var network_row := HBoxContainer.new()
	network_row.name = "InfrastructureNetworks"
	network_row.add_theme_constant_override("separation", 7)
	_add_infrastructure_button(network_row, "water", "Water network")
	_add_infrastructure_button(network_row, "power", "Power grid")
	_asset_group_bodies["water_power"].add_child(network_row)
	var network_hint := _label("Frames reservoirs, poles, cables and meters in 3D. Press again to clear.", 11, MUTED)
	network_hint.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_asset_group_bodies["water_power"].add_child(network_hint)
	_add_asset_group(box, "SENSORS", "sensors", [
		"traffic_detectors", "air_quality", "noise", "weather", "flood", "utility_telemetry", "traffic_cameras", "bin_weights", "seismic_sensors",
	], false)
	_add_asset_group(box, "ACTUATORS & METERS", "actuators", [
		"parking_lots", "traffic_signals", "water_valves", "power_switches", "street_lights", "irrigation", "power_meters", "water_meters",
	], false)
	_add_asset_group(box, "CIVIC RESPONSE & FLEET", "civic", [
		"emergency_stations", "hospitals", "police_stations", "police_vehicles", "ambulances", "fire_engines", "tow_trucks", "utility_vans", "smoky_vehicles",
	], false)
	_add_asset_group(box, "WASTE SERVICE", "waste", [
		"garbage_bins", "refuse_trucks",
	], false)



## Show the zone tint and letter for every zone at once, or only for chosen zones. Hidden by default.
func _build_zone_toggles(body: VBoxContainer) -> void:
	var hint := _label("Zones A (north-west) to P (south-east), 2 x 2 blocks each. Show them all or pick some.", 11, MUTED)
	hint.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	body.add_child(hint)
	_zone_all_button = Button.new()
	_zone_all_button.name = "ZoneToggleAll"
	_zone_all_button.text = "All zones"
	_zone_all_button.toggle_mode = true
	_zone_all_button.custom_minimum_size = Vector2(0, 30)
	_zone_all_button.add_theme_font_size_override("font_size", 12)
	_zone_all_button.toggled.connect(func(on: bool) -> void: _set_all_zones(on))
	body.add_child(_zone_all_button)
	var grid := GridContainer.new()
	grid.name = "ZoneToggles"
	grid.columns = 4
	grid.add_theme_constant_override("h_separation", 6)
	grid.add_theme_constant_override("v_separation", 6)
	body.add_child(grid)
	for zone in CityZones.IDS:
		var button := Button.new()
		button.name = "ZoneToggle_%s" % zone
		button.text = str(zone)
		button.toggle_mode = true
		button.custom_minimum_size = Vector2(0, 30)
		button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		button.add_theme_font_size_override("font_size", 13)
		button.toggled.connect(func(on: bool) -> void: _set_zone_shown(str(zone), on))
		grid.add_child(button)
		_zone_buttons[str(zone)] = button


func _zone_overlay() -> Node:
	return _city.get_node_or_null("ZoneOverlay") if _city != null else null


func _set_zone_shown(zone: String, shown: bool) -> void:
	var overlay := _zone_overlay()
	if overlay != null:
		overlay.call("set_zone_visible", zone, shown)
	_sync_zone_buttons()


func _set_all_zones(shown: bool) -> void:
	var overlay := _zone_overlay()
	if overlay != null:
		overlay.call("set_all_visible", shown)
	_sync_zone_buttons()


func _sync_zone_buttons() -> void:
	var overlay := _zone_overlay()
	if overlay == null:
		return
	var shown := overlay.call("shown_zones") as Array
	for zone in _zone_buttons:
		(_zone_buttons[zone] as Button).set_pressed_no_signal(shown.has(zone))
	if _zone_all_button != null:
		_zone_all_button.set_pressed_no_signal(shown.size() == CityZones.IDS.size())


func _add_asset_group(parent: VBoxContainer, heading: String, key: String, kinds: Array, open: bool) -> void:
	## A collapsible group: the header shows its title and how many categories it
	## holds; its body keeps one readable row per category.
	var header := Button.new()
	header.name = "AssetGroup_%s" % key
	header.toggle_mode = true
	header.button_pressed = open
	header.alignment = HORIZONTAL_ALIGNMENT_LEFT
	header.clip_text = true
	header.custom_minimum_size = Vector2(0, 32)
	header.add_theme_font_size_override("font_size", 12)
	header.add_theme_color_override("font_color", AMBER)
	header.add_theme_stylebox_override("normal", _style(Color("1c2a36"), Color("2f4558"), 1, 4))
	parent.add_child(header)
	var body := VBoxContainer.new()
	body.name = "AssetGroupBody_%s" % key
	body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	body.add_theme_constant_override("separation", 4)
	body.visible = open
	parent.add_child(body)
	_asset_group_bodies[key] = body
	header.set_meta("group_heading", heading)
	header.set_meta("group_count", kinds.size())
	header.toggled.connect(func(pressed: bool) -> void:
		body.visible = pressed
		_refresh_asset_group_header(header))
	_refresh_asset_group_header(header)
	_asset_group_headers[key] = header
	for kind_value in kinds:
		_add_asset_highlight_button(body, str(kind_value), key)


func _refresh_asset_group_header(header: Button) -> void:
	var count := int(header.get_meta("group_count", 0))
	header.text = "%s %s%s" % ["▾" if header.button_pressed else "▸", str(header.get_meta("group_heading", "")), "   ·   %d categories" % count if count > 0 else ""]


func _clear_all_highlights() -> void:
	_clear_asset_highlight(false)
	_clear_infrastructure_highlight()
	_refresh_asset_status()


func _refresh_asset_status() -> void:
	if _asset_status == null:
		return
	if not _active_asset_highlight.is_empty():
		var definition := ASSET_HIGHLIGHT_DEFINITIONS.get(_active_asset_highlight, {}) as Dictionary
		_asset_status.text = "Framing %s · %d in the city" % [str(definition.get("label", _active_asset_highlight)), _asset_highlight_targets(_active_asset_highlight).size()]
		_asset_status.add_theme_color_override("font_color", GREEN)
	elif not _active_infrastructure_highlight.is_empty():
		_asset_status.text = "Framing the %s network" % _active_infrastructure_highlight
		_asset_status.add_theme_color_override("font_color", GREEN)
	else:
		_asset_status.text = "Pick a category to frame every matching asset in the city."
		_asset_status.add_theme_color_override("font_color", MUTED)


func _add_asset_highlight_button(parent: Control, kind: String, group_key := "") -> void:
	var definition := ASSET_HIGHLIGHT_DEFINITIONS.get(kind, {}) as Dictionary
	var button := Button.new()
	button.set_meta("asset_highlight_kind", kind)
	button.set_meta("asset_group", group_key)
	button.alignment = HORIZONTAL_ALIGNMENT_LEFT
	button.clip_text = true
	button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	button.custom_minimum_size = Vector2(0, 30)
	button.add_theme_font_size_override("font_size", 12)
	button.tooltip_text = "%s · matching assets receive this button's frame color · interaction-ready 3D model" % str(definition.get("detail", kind))
	button.pressed.connect(_toggle_asset_highlight.bind(kind))
	parent.add_child(button)
	_asset_highlight_buttons.append(button)
	_refresh_asset_button(button)


func _build_npc_tab(tabs: TabContainer) -> void:
	var npc_scroll := ScrollContainer.new()
	npc_scroll.name = "NPC Intent"
	npc_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	tabs.add_child(npc_scroll)
	_npc_tab_index = tabs.get_tab_count() - 1
	var npc_frame:=_right_tab_content_frame(npc_scroll,"NpcContentFrame",620.0)
	var box := VBoxContainer.new()
	box.name = "NpcReadableLayout"
	box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	box.add_theme_constant_override("separation", 9)
	npc_frame.add_child(box)
	# One compact card: who, doing what, and why. Everything technical sits
	# behind a single collapsed "Details" section.
	var guidance := _label("Click a citizen in the viewport to see what they are doing and why.", 11, MUTED)
	guidance.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(guidance)
	var card:=_npc_card(box,Color("273038"),ACCENT)
	card.get_parent().name="NpcSummaryCard"
	var header := HBoxContainer.new()
	header.add_theme_constant_override("separation", 10)
	card.add_child(header)
	_npc_selection_label = _label("No citizen selected", 16, AMBER)
	_npc_selection_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_npc_selection_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	header.add_child(_npc_selection_label)
	_npc_status_label = _label("Waiting for selection", 11, AMBER)
	_npc_status_label.custom_minimum_size = Vector2(118, 0)
	_npc_status_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	_npc_status_label.clip_text = true
	_npc_status_label.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	header.add_child(_npc_status_label)
	_npc_character_label = _label("—", 12, TEXT)
	card.add_child(_npc_character_label)
	_npc_personality_label = _label("—", 11, MUTED)
	_npc_personality_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	card.add_child(_npc_personality_label)
	card.add_child(HSeparator.new())
	_npc_now_label=_npc_row(card,"Doing",TEXT)
	_npc_now_context_label=_npc_row(card,"At",MUTED)
	_npc_partner_label=_npc_row(card,"With",MUTED)
	_npc_activity_label=_npc_row(card,"Together",MUTED)
	_npc_partner_label.get_parent().visible=false; _npc_activity_label.get_parent().visible=false
	_npc_objective_label=_npc_row(card,"Goal",TEXT)
	_npc_intent_label=_npc_row(card,"Why",Color("c5b8e4"))
	var details:=_npc_disclosure(box,"DETAILS","NpcMovementDetails")
	_npc_identity_context_label = _label("—", 11, MUTED)
	_npc_identity_context_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	details.add_child(_npc_identity_context_label)
	_npc_route_label = _label("—", 11, MUTED)
	_npc_route_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	details.add_child(_npc_route_label)
	_npc_planner_label = _label("—", 11, MUTED)
	_npc_planner_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	details.add_child(_npc_planner_label)
	_npc_payload = RichTextLabel.new()
	_npc_payload.fit_content = true
	_npc_payload.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_npc_payload.add_theme_font_size_override("normal_font_size", 10)
	details.add_child(_npc_payload)
	_npc_response = RichTextLabel.new()
	_npc_response.fit_content = true
	_npc_response.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_npc_response.add_theme_font_size_override("normal_font_size", 10)
	details.add_child(_npc_response)


func _npc_row(parent:VBoxContainer,key:String,colour:Color)->Label:
	var row:=HBoxContainer.new(); row.add_theme_constant_override("separation",8); parent.add_child(row)
	var key_label:=_label(key,11,MUTED); key_label.custom_minimum_size=Vector2(56,0); row.add_child(key_label)
	var value_label:=_label("—",12,colour); value_label.autowrap_mode=TextServer.AUTOWRAP_WORD_SMART; value_label.size_flags_horizontal=Control.SIZE_EXPAND_FILL; row.add_child(value_label)
	return value_label


func _npc_card(parent:VBoxContainer,fill:Color,border:Color)->VBoxContainer:
	var panel:=PanelContainer.new()
	panel.size_flags_horizontal=Control.SIZE_EXPAND_FILL
	panel.size_flags_vertical=Control.SIZE_SHRINK_BEGIN
	panel.add_theme_stylebox_override("panel",_style(fill,border,1,5))
	parent.add_child(panel)
	var content:=VBoxContainer.new(); content.size_flags_horizontal=Control.SIZE_EXPAND_FILL; content.size_flags_vertical=Control.SIZE_SHRINK_BEGIN; content.add_theme_constant_override("separation",5); panel.add_child(content)
	return content


func _npc_fact(parent:VBoxContainer,key:String,value:String,colour:Color,font_size:int)->Label:
	var field:=VBoxContainer.new(); field.add_theme_constant_override("separation",1); field.size_flags_horizontal=Control.SIZE_EXPAND_FILL; parent.add_child(field)
	var key_label:=_label(key,9,MUTED); field.add_child(key_label)
	var value_label:=_label(value,font_size,colour); value_label.autowrap_mode=TextServer.AUTOWRAP_WORD_SMART; value_label.size_flags_horizontal=Control.SIZE_EXPAND_FILL; field.add_child(value_label)
	return value_label


func _right_tab_content_frame(scroll:ScrollContainer,frame_name:String,minimum_height:float)->MarginContainer:
	var frame:=MarginContainer.new(); frame.name=frame_name; frame.size_flags_horizontal=Control.SIZE_EXPAND_FILL
	frame.add_theme_constant_override("margin_left",10); frame.add_theme_constant_override("margin_right",10); frame.add_theme_constant_override("margin_top",8); frame.add_theme_constant_override("margin_bottom",12)
	scroll.add_child(frame)
	scroll.resized.connect(_fit_right_tab_frame.bind(scroll,frame,minimum_height))
	scroll.get_v_scroll_bar().visibility_changed.connect(_fit_right_tab_frame.bind(scroll,frame,minimum_height))
	call_deferred("_fit_right_tab_frame",scroll,frame,minimum_height)
	return frame


func _fit_right_tab_frame(scroll:ScrollContainer,frame:MarginContainer,minimum_height:float)->void:
	if scroll==null or frame==null: return
	var scrollbar_width:=scroll.get_v_scroll_bar().size.x if scroll.get_v_scroll_bar().visible else 0.0
	# The rail width is authoritative. Deriving this from a stale child width
	# created a minimum-size feedback loop: the rail moved immediately but its
	# content stayed at the previous width until the divider moved again.
	var rail_content_width:=maxf(0.0,_right_panel.size.x-18.0)
	var frame_width:=maxf(0.0,rail_content_width-scrollbar_width-3.0)
	frame.custom_minimum_size=Vector2(frame_width,0.0)
	for descendant_value in frame.find_children("*", "Control", true, false):
		var descendant := descendant_value as Control
		if descendant is Button:
			(descendant as Button).clip_text = true
			(descendant as Button).text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	for child_value in frame.get_children():
		var child:=child_value as Control
		if child!=null:
			var child_minimum:=child.custom_minimum_size; child_minimum.x=maxf(0.0,frame_width-20.0); child.custom_minimum_size=child_minimum
			# Size only structural rows. Grids and HBoxes keep their own columns and
			# child proportions; this prevents a global stretch of individual items.
			if child is Container:
				for row_value in child.get_children():
					var row:=row_value as Container
					if row!=null:
						var row_minimum:=row.custom_minimum_size; row_minimum.x=maxf(0.0,frame_width-20.0); row.custom_minimum_size=row_minimum
			for toggle_value in child.find_children("Toggle_Npc*","Button",true,false):
				var toggle:=toggle_value as Button; var toggle_minimum:=toggle.custom_minimum_size; toggle_minimum.x=maxf(0.0,frame_width-20.0); toggle.custom_minimum_size=toggle_minimum
	frame.queue_sort()


func _fit_all_right_tab_frames() -> void:
	if _control_tabs == null:
		return
	for tab_value in _control_tabs.get_children():
		if not tab_value is ScrollContainer:
			continue
		var scroll := tab_value as ScrollContainer
		for frame_value in scroll.get_children():
			if frame_value is MarginContainer:
				_fit_right_tab_frame(scroll, frame_value as MarginContainer, 0.0)
				break


func _npc_disclosure(parent:VBoxContainer,title:String,body_name:String)->VBoxContainer:
	var section:=VBoxContainer.new(); section.size_flags_horizontal=Control.SIZE_EXPAND_FILL; section.add_theme_constant_override("separation",0); parent.add_child(section)
	var toggle:=Button.new(); toggle.name="Toggle_%s"%body_name; toggle.toggle_mode=true; toggle.button_pressed=false; toggle.text="▶  %s"%title; toggle.alignment=HORIZONTAL_ALIGNMENT_LEFT; toggle.custom_minimum_size=Vector2(0,34); toggle.add_theme_font_size_override("font_size",11)
	toggle.size_flags_horizontal=Control.SIZE_EXPAND_FILL
	toggle.add_theme_stylebox_override("normal",_style(Color("292929"),Color("454545"),1,3)); toggle.add_theme_stylebox_override("hover",_style(Color("34434d"),ACCENT,1,3)); section.add_child(toggle)
	var panel:=PanelContainer.new(); panel.name=body_name; panel.visible=false; panel.size_flags_horizontal=Control.SIZE_EXPAND_FILL; panel.add_theme_stylebox_override("panel",_style(Color("202025"),Color("454545"),1,3)); section.add_child(panel)
	var body:=VBoxContainer.new(); body.size_flags_horizontal=Control.SIZE_EXPAND_FILL; body.add_theme_constant_override("separation",5); panel.add_child(body)
	_npc_detail_bodies.append(panel)
	toggle.toggled.connect(_toggle_npc_detail.bind(toggle,panel,title))
	return body


func _toggle_npc_detail(open:bool,toggle:Button,panel:PanelContainer,title:String)->void:
	panel.visible=open; toggle.text=("▼  " if open else "▶  ")+title


func _build_sensor_tab(tabs: TabContainer) -> void:
	var scroll := ScrollContainer.new()
	scroll.name = "Sensors"
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	tabs.add_child(scroll)
	var frame := _right_tab_content_frame(scroll, "SensorsContentFrame", 760.0)
	var box := VBoxContainer.new()
	box.name = "SensorTelemetryLayout"
	box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	box.add_theme_constant_override("separation", 8)
	frame.add_child(box)
	_add_caption(box, "SENSOR NETWORK")
	_sensor_status = _label("Discovering physical sensor streams…", 12, AMBER)
	_sensor_status.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(_sensor_status)
	_add_caption(box, "1 · CHOOSE A CATEGORY")
	var category_grid := GridContainer.new()
	category_grid.name = "SensorCategories"
	category_grid.columns = 4
	category_grid.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	category_grid.add_theme_constant_override("h_separation", 6)
	category_grid.add_theme_constant_override("v_separation", 6)
	for entry in SENSOR_CATEGORIES:
		var chip := Button.new()
		chip.name = "SensorCategory_%s" % str(entry[0])
		chip.toggle_mode = true
		chip.button_pressed = str(entry[0]) == _sensor_category
		chip.text = str(entry[1])
		chip.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		chip.custom_minimum_size = Vector2(0, 30)
		chip.clip_text = true
		chip.add_theme_font_size_override("font_size", 12)
		chip.pressed.connect(_select_sensor_category.bind(str(entry[0])))
		category_grid.add_child(chip)
		_sensor_category_buttons[str(entry[0])] = chip
	box.add_child(category_grid)
	_add_caption(box, "2 · PICK A DEVICE")
	_sensor_search = LineEdit.new()
	_sensor_search.name = "SensorSearch"
	_sensor_search.placeholder_text = "Filter by ID, name or reading…"
	_sensor_search.clear_button_enabled = true
	_sensor_search.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_sensor_search.custom_minimum_size = Vector2(0, 32)
	_sensor_search.text_changed.connect(_on_sensor_search_changed)
	box.add_child(_sensor_search)
	_sensor_device_grid = GridContainer.new()
	_sensor_device_grid.name = "SensorDeviceButtons"
	_sensor_device_grid.columns = 1
	_sensor_device_grid.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_sensor_device_grid.add_theme_constant_override("v_separation", 4)
	box.add_child(_sensor_device_grid)
	var picker_row := HBoxContainer.new()
	picker_row.name = "SensorPageControls"
	picker_row.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	picker_row.add_theme_constant_override("separation", 6)
	var previous := Button.new()
	previous.text = "‹ Previous"
	previous.custom_minimum_size = Vector2(0, 32)
	previous.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	previous.pressed.connect(_change_sensor_page.bind(-1))
	picker_row.add_child(previous)
	_sensor_page_label = _label("1 / 1", 10, MUTED)
	_sensor_page_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_sensor_page_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	_sensor_page_label.custom_minimum_size = Vector2(68, 32)
	picker_row.add_child(_sensor_page_label)
	var next := Button.new()
	next.text = "Next ›"
	next.custom_minimum_size = Vector2(0, 32)
	next.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	next.pressed.connect(_change_sensor_page.bind(1))
	picker_row.add_child(next)
	box.add_child(picker_row)
	box.add_child(HSeparator.new())
	_add_caption(box, "3 · LIVE READINGS")
	_sensor_detail_label = _label("Choose a device above.", 12, AMBER)
	_sensor_detail_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(_sensor_detail_label)
	var frame_button := Button.new()
	frame_button.name = "SensorFrameInCity"
	frame_button.text = "Show this device in the city"
	frame_button.custom_minimum_size = Vector2(0, 30)
	frame_button.add_theme_font_size_override("font_size", 12)
	frame_button.pressed.connect(func() -> void: _focus_sensor_in_city(_sensor_by_id(_selected_sensor_id)))
	box.add_child(frame_button)
	_sensor_reading_grid = GridContainer.new()
	_sensor_reading_grid.columns = 2
	_sensor_reading_grid.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_sensor_reading_grid.add_theme_constant_override("h_separation", 7)
	_sensor_reading_grid.add_theme_constant_override("v_separation", 7)
	box.add_child(_sensor_reading_grid)
	_sensor_chart = (load("res://scripts/sensor_history_chart.gd") as GDScript).new() as Control
	_sensor_chart.custom_minimum_size = Vector2(0, 230)
	_sensor_chart.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	box.add_child(_sensor_chart)
	var history_note := _label("Press a reading to graph it. History is sampled live and kept for this session only.", 10, MUTED)
	history_note.name = "SensorHistoryNote"
	history_note.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	history_note.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	box.add_child(history_note)


func _sample_sensors() -> void:
	if _environment_system == null:
		return
	var conditions := _environment_system.call("get_conditions") as Dictionary
	var simulation_minute := float(conditions.get("simulation_minute", 0.0))
	var now_msec := Time.get_ticks_msec()
	# Sample by wall time as well as simulation time. Weather/utility controls and
	# traffic packets can change while the clock is paused or within one displayed
	# minute, and those changes must appear immediately in the live readings.
	if now_msec - _last_sensor_sample_msec < 250 and absf(simulation_minute - _last_sensor_sample_minute) < 0.01:
		return
	_last_sensor_sample_minute = simulation_minute
	_last_sensor_sample_msec = now_msec
	_sensor_catalog.assign(_sim_api.call("sensors") if _sim_api != null else _environment_system.call("get_sensor_snapshot"))
	for sensor in _sensor_catalog:
		var sensor_id := str(sensor.id)
		if not _sensor_history.has(sensor_id):
			_sensor_history[sensor_id] = {}
		var metric_history := _sensor_history[sensor_id] as Dictionary
		for metric_value in (sensor.readings as Dictionary).keys():
			var metric := str(metric_value)
			var reading := (sensor.readings as Dictionary)[metric] as Dictionary
			if not metric_history.has(metric):
				metric_history[metric] = {"unit":str(reading.unit), "samples":[]}
			var series := metric_history[metric] as Dictionary
			var samples := series.samples as Array
			samples.append(Vector2(simulation_minute, float(reading.value)))
			while samples.size() > 240:
				samples.pop_front()
	_refresh_sensor_category_buttons()
	if _selected_sensor_id.is_empty() and not _sensor_catalog.is_empty():
		_selected_sensor_id = str(_sensor_catalog[0].id)
		_selected_sensor_metric = str((_sensor_catalog[0].readings as Dictionary).keys()[0])
		_rebuild_sensor_device_buttons()
		_rebuild_sensor_readings()
	else:
		_refresh_sensor_device_buttons()
		_refresh_sensor_readings()
	var live_sequence := int(_sensor_catalog[0].get("sample_sequence", 0)) if not _sensor_catalog.is_empty() else 0
	_sensor_status.text = "%d devices online  ·  live update #%d  ·  %s  ·  %s" % [_sensor_catalog.size(), live_sequence, str(conditions.time_text), str(conditions.weather_label)]
	_sensor_status.add_theme_color_override("font_color", GREEN)


func _sensor_family(sensor: Dictionary) -> String:
	return SimApi.family_of(str(sensor.get("id", "")))


func _select_sensor_category(category: String) -> void:
	_sensor_category = category
	_sensor_page = 0
	_refresh_sensor_category_buttons()
	_rebuild_sensor_device_buttons()


func _refresh_sensor_category_buttons() -> void:
	var counts := {"all": _sensor_catalog.size()}
	for sensor in _sensor_catalog:
		var family := _sensor_family(sensor)
		counts[family] = int(counts.get(family, 0)) + 1
	for entry in SENSOR_CATEGORIES:
		var chip := _sensor_category_buttons.get(str(entry[0])) as Button
		if chip != null:
			chip.text = "%s %d" % [str(entry[1]), int(counts.get(str(entry[0]), 0))]
			chip.button_pressed = str(entry[0]) == _sensor_category


func _on_sensor_search_changed(_query: String) -> void:
	_sensor_page = 0
	_rebuild_sensor_device_buttons()


func _change_sensor_page(direction: int) -> void:
	var pages := maxi(1, ceili(float(_sensor_visible_ids.size()) / 8.0))
	_sensor_page = clampi(_sensor_page + direction, 0, pages - 1)
	_rebuild_sensor_device_buttons(false)


func _rebuild_sensor_device_buttons(rebuild_filter := true) -> void:
	if _sensor_device_grid == null:
		return
	if rebuild_filter:
		_sensor_visible_ids.clear()
		var query := _sensor_search.text.strip_edges().to_lower() if _sensor_search != null else ""
		for sensor in _sensor_catalog:
			var searchable := "%s %s %s" % [str(sensor.id), str(sensor.name), str(sensor.type)]
			for metric_value in (sensor.readings as Dictionary).keys():
				searchable += " " + _sensor_metric_label(str(metric_value))
			if _sensor_category != "all" and _sensor_family(sensor) != _sensor_category:
				continue
			if query.is_empty() or query in searchable.to_lower():
				_sensor_visible_ids.append(str(sensor.id))
	for child in _sensor_device_grid.get_children():
		child.free()
	var pages := maxi(1, ceili(float(_sensor_visible_ids.size()) / 8.0))
	_sensor_page = clampi(_sensor_page, 0, pages - 1)
	if _sensor_page_label != null:
		_sensor_page_label.text = "%d / %d\n%d devices" % [_sensor_page + 1, pages, _sensor_visible_ids.size()]
	var begin := _sensor_page * 8
	var finish := mini(begin + 8, _sensor_visible_ids.size())
	for index in range(begin, finish):
		var sensor := _sensor_by_id(_sensor_visible_ids[index])
		if sensor.is_empty():
			continue
		var button := Button.new()
		button.name = "SensorDevice_%s" % str(sensor.id)
		button.set_meta("sensor_id", str(sensor.id))
		button.toggle_mode = true
		button.alignment = HORIZONTAL_ALIGNMENT_LEFT
		button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		button.custom_minimum_size = Vector2(0, 34)
		button.clip_text = true
		button.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
		button.add_theme_font_size_override("font_size", 12)
		button.tooltip_text = "%s · %s · click to frame its physical model" % [str(sensor.id), str(sensor.type)]
		button.pressed.connect(_select_sensor.bind(str(sensor.id), true))
		_sensor_device_grid.add_child(button)
	_refresh_sensor_device_buttons()


func _refresh_sensor_device_buttons() -> void:
	if _sensor_device_grid == null:
		return
	for value in _sensor_device_grid.get_children():
		var button := value as Button
		if button == null:
			continue
		var sensor := _sensor_by_id(str(button.get_meta("sensor_id", "")))
		if sensor.is_empty():
			continue
		var readings := sensor.readings as Dictionary
		var first_metric := str(readings.keys()[0]) if not readings.is_empty() else ""
		var reading := readings.get(first_metric, {}) as Dictionary
		button.text = "%s · %s   %s %.1f %s" % [str(sensor.id), _sensor_picker_text(sensor).get_slice("·", 1).strip_edges(), _sensor_metric_label(first_metric), float(reading.get("value", 0.0)), str(reading.get("unit", ""))]
		button.button_pressed = str(sensor.id) == _selected_sensor_id


func _sensor_picker_text(sensor: Dictionary) -> String:
	var sensor_id := str(sensor.get("id", ""))
	return "%s  ·  %s" % [sensor_id, str(sensor.get("type", "Device"))]


func _select_sensor(sensor_id: String, focus_in_city := true) -> void:
	_selected_sensor_id = sensor_id
	var sensor := _sensor_by_id(sensor_id)
	_selected_sensor_metric = str((sensor.readings as Dictionary).keys()[0]) if not sensor.is_empty() and not (sensor.readings as Dictionary).is_empty() else ""
	_rebuild_sensor_readings()
	_refresh_sensor_device_buttons()
	if focus_in_city:
		_focus_sensor_in_city(sensor)


func _focus_sensor_in_city(sensor: Dictionary) -> void:
	var target := sensor.get("node") as Node3D
	var city_camera := _city.get_node_or_null("EstablishingCamera") as Camera3D
	if target == null or city_camera == null:
		return
	_return_to_city_view()
	city_camera.call("follow_target", target)
	_add_event("Sensor focus: %s framed in the live city" % str(sensor.id))


func _rebuild_sensor_readings() -> void:
	for child in _sensor_reading_grid.get_children():
		child.free()
	var sensor := _sensor_by_id(_selected_sensor_id)
	if sensor.is_empty():
		return
	_sensor_detail_label.text = "%s  ·  %s  ·  physical position %.0f, %.0f" % [str(sensor.id), str(sensor.type), float((sensor.position as Vector3).x), float((sensor.position as Vector3).z)]
	for metric_value in (sensor.readings as Dictionary).keys():
		var metric := str(metric_value)
		var button := Button.new()
		button.name = "Reading_" + metric
		button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		button.custom_minimum_size = Vector2(0, 48)
		button.toggle_mode = true
		button.alignment = HORIZONTAL_ALIGNMENT_LEFT
		button.clip_text = true
		button.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
		button.pressed.connect(_select_sensor_metric.bind(metric))
		_sensor_reading_grid.add_child(button)
	_refresh_sensor_readings()


func _refresh_sensor_readings() -> void:
	var sensor := _sensor_by_id(_selected_sensor_id)
	if sensor.is_empty() or _sensor_reading_grid == null:
		return
	for metric_value in (sensor.readings as Dictionary).keys():
		var metric := str(metric_value)
		var button := _sensor_reading_grid.get_node_or_null("Reading_" + metric) as Button
		if button == null:
			continue
		var reading := (sensor.readings as Dictionary)[metric] as Dictionary
		button.text = "%s\n%.1f %s" % [_sensor_metric_label(metric), float(reading.value), str(reading.unit)]
		button.button_pressed = metric == _selected_sensor_metric
	if not _selected_sensor_metric.is_empty():
		_update_sensor_chart()


func _select_sensor_metric(metric: String) -> void:
	_selected_sensor_metric = metric
	_update_sensor_chart()
	_refresh_sensor_readings()


func _update_sensor_chart() -> void:
	if _selected_sensor_id.is_empty() or _selected_sensor_metric.is_empty() or not _sensor_history.has(_selected_sensor_id):
		return
	var metric_history := _sensor_history[_selected_sensor_id] as Dictionary
	if not metric_history.has(_selected_sensor_metric):
		return
	var series := metric_history[_selected_sensor_metric] as Dictionary
	var typed_samples: Array[Vector2] = []
	for sample in series.samples as Array:
		typed_samples.append(sample as Vector2)
	_sensor_chart.call("set_series", _sensor_metric_label(_selected_sensor_metric), str(series.unit), typed_samples)


func _sensor_metric_label(metric: String) -> String:
	var labels := {
		"pm2_5":"PM2.5", "pm10":"PM10", "nitrogen_dioxide":"Nitrogen dioxide", "vehicle_smoke":"Vehicle smoke",
		"vehicle_count":"Vehicle count", "speed":"Mean speed", "occupancy":"Road occupancy",
		"db_a":"Sound level", "peak_noise":"Peak level", "traffic_noise":"Traffic noise", "rainfall":"Rain rate",
		"wind_speed":"Wind speed", "surface_water_depth":"Water depth", "rise_rate":"Rise rate",
		"voltage":"Grid voltage", "water_pressure":"Water pressure", "cabinet_temperature":"Cabinet temperature",
		"power_demand":"Power demand", "water_flow":"Water flow", "supply_status":"Supply online",
		"ground_acceleration":"Ground acceleration", "ground_velocity":"Ground velocity", "shaking_intensity":"Shaking intensity",
		"bin_weight":"Bin weight", "fill_level":"Fill level", "deposits":"Items deposited",
		"ns_signal":"North–south signal", "ew_signal":"East–west signal", "valve_open":"Valve open", "switch_closed":"Switch closed",
		"powered":"Powered", "light_level":"Light output", "irrigation_flow":"Irrigation flow", "water_level":"Reservoir level",
		"water_volume":"Stored water", "water_demand":"Water demand", "energy_level":"Stored energy", "energy_available":"Energy available",
		"grid_demand":"Grid demand", "power_supplied":"Power supplied", "water_supplied":"Water supplied", "zone_power":"Zone power", "zone_water":"Zone water",
	}
	return str(labels.get(metric, metric.replace("_", " ").capitalize()))


func _sensor_by_id(sensor_id: String) -> Dictionary:
	for sensor in _sensor_catalog:
		if str(sensor.id) == sensor_id:
			return sensor
	return {}


func _build_utilities_tab(tabs: TabContainer) -> void:
	var utility_scroll := ScrollContainer.new()
	utility_scroll.name = "Utilities"
	utility_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	tabs.add_child(utility_scroll)
	var utility_frame:=_right_tab_content_frame(utility_scroll,"UtilitiesContentFrame",1680.0)
	var box := VBoxContainer.new()
	box.name="UtilitiesContent"
	box.size_flags_horizontal=Control.SIZE_EXPAND_FILL
	box.add_theme_constant_override("separation", 7)
	utility_frame.add_child(box)
	_add_caption(box, "LIVE WATER + POWER")
	var meter_note := _label("Meter levels consume in simulation time; values are local and deterministic.", 11, MUTED)
	meter_note.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(meter_note)
	_utility_power_label = _label("POWER RESERVE — loading", 12, TEXT)
	_utility_power_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(_utility_power_label)
	_utility_power_bar = _utility_bar(AMBER)
	box.add_child(_utility_power_bar)
	box.add_child(HSeparator.new())
	_add_caption(box, "RESERVOIR STORAGE")
	var water_box := VBoxContainer.new()
	water_box.name = "ReservoirMeters"
	water_box.add_theme_constant_override("separation", 3)
	box.add_child(water_box)
	box.add_child(HSeparator.new())
	_add_caption(box, "RECHARGE CONTROL")
	var recharge := Button.new()
	recharge.text = "RECHARGE ALL UTILITIES"
	recharge.custom_minimum_size = Vector2(0, 36)
	recharge.add_theme_font_size_override("font_size", 12)
	recharge.add_theme_stylebox_override("normal", _style(Color("27322b"), Color("579563"), 1, 3))
	recharge.add_theme_stylebox_override("hover", _style(Color("365143"), GREEN, 1, 3))
	recharge.pressed.connect(_recharge_utilities)
	box.add_child(recharge)
	_utility_auto_button = Button.new()
	_utility_auto_button.text = "AUTO RECHARGE: ON"
	_utility_auto_button.custom_minimum_size = Vector2(0, 32)
	_utility_auto_button.add_theme_font_size_override("font_size", 12)
	_utility_auto_button.pressed.connect(_toggle_utility_auto_recharge)
	box.add_child(_utility_auto_button)
	_utility_schedule_label = _label("Automatic recharge — loading", 11, MUTED)
	_utility_schedule_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(_utility_schedule_label)
	box.add_child(HSeparator.new())
	_add_caption(box, "AREA SERVICE CUTS")
	_utility_outage_label = _label("Automatic outage protection: ON (default)", 11, GREEN)
	_utility_outage_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(_utility_outage_label)
	var zone_note := _label("Small shared zones. Water and power use the same city areas, with separate controls below.", 11, MUTED)
	zone_note.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(zone_note)
	_add_caption(box, "WATER SERVICE CUTS  ·  BLUE")
	var water_controls := GridContainer.new()
	water_controls.name = "WaterZoneToggles"
	water_controls.columns = 2
	water_controls.add_theme_constant_override("h_separation", 6)
	water_controls.add_theme_constant_override("v_separation", 6)
	box.add_child(water_controls)
	_add_caption(box, "POWER SERVICE CUTS  ·  RED")
	var power_controls := GridContainer.new()
	power_controls.name = "PowerZoneToggles"
	power_controls.columns = 2
	power_controls.add_theme_constant_override("h_separation", 6)
	power_controls.add_theme_constant_override("v_separation", 6)
	box.add_child(power_controls)
	box.add_child(HSeparator.new())
	_add_caption(box, "BLOCK DEMAND")
	box.add_child(_label("Power and water vary by each block's actual building mix.", 11, MUTED))
	var block_box := VBoxContainer.new()
	block_box.name = "BlockDemandRows"
	block_box.add_theme_constant_override("separation", 2)
	box.add_child(block_box)


func _refresh_ui() -> void:
	var life := _city.get_node_or_null("CityLife")
	if life == null:
		return
	if _environment_system != null:
		var conditions := _environment_system.call("get_conditions") as Dictionary
		if _world_clock != null:
			_world_clock.call("set_conditions", conditions)
		_set_metric("time", "Day %d · %s" % [int(conditions.day), str(conditions.time_text)])
		_set_metric("weather", str(conditions.weather_label))
		_set_metric("speed", "%dx" % int(conditions.speed))
		for key in _speed_buttons:
			(_speed_buttons[key] as Button).button_pressed = int(key) == int(conditions.speed)
		var weather_key := "auto" if bool(conditions.automatic_weather) else str(conditions.weather)
		for key in _weather_buttons:
			(_weather_buttons[key] as Button).button_pressed = str(key) == weather_key
		_sample_sensors()
	var connected := bool(life.get("sumo_connected"))
	_set_label_state(_connection, "●  SUMO LIVE / AUTHORITATIVE DATA" if connected else "●  WAITING FOR SUMO BRIDGE", GREEN if connected else AMBER)
	_set_metric("simulated", str(int(life.get("_sumo_simulated_count"))))
	_set_metric("visible", str((life.get("_sumo_nodes_by_id") as Dictionary).size()))
	_set_metric("stopped", str(int(life.get("_sumo_stopped_count"))))
	_set_metric("turning", str(int(life.get("_sumo_turning_count"))))
	_set_metric("signals", str((life.get("_sumo_signal_approaches") as Dictionary).size()))
	var power_grid := _city.get_node_or_null("PowerGrid") as Node3D
	if power_grid != null:
		var power_status := power_grid.call("get_grid_status") as Dictionary
		_set_metric("power", "%d/%d online" % [int(power_status.get("powered_consumers", 0)), int(power_status.get("total_consumers", 0))])
	else:
		_set_metric("power", "offline")
	var utilities := _city.get_node_or_null("UtilityResources") as Node
	if utilities != null:
		var utility_status := utilities.call("get_utility_status") as Dictionary
		var utility_power := utility_status.get("power", {}) as Dictionary
		_set_metric("power", "%.0f%% reserve" % float(utility_power.get("percent", 0.0)))
		_refresh_utility_panel(utility_status)
	_refresh_npc_panel()
	if _incident_panel != null:
		_incident_panel.call("refresh")
	var npc_ai := _city.get_node_or_null("NpcAi") as Node
	var society := npc_ai.call("get_society_debug") as Dictionary if npc_ai != null else {}
	_set_metric("people", "%d · %d shown" % [int(society.get("population", 0)), int(society.get("visible_citizens", 0))])
	var phase_text := _phase_text(life.get("_sumo_signal_approaches") as Dictionary)
	_set_label_state(_phase, phase_text, GREEN if phase_text.contains("GREEN") else (AMBER if phase_text.contains("AMBER") else RED))
	# The live signal phase is already shown in the viewport header; it no longer floods the event log.
	if _phase.text != _last_phase and connected:
		_last_phase = _phase.text
	var operator := life.get("_sumo_operator") as Dictionary
	var action := str(operator.get("last_action", ""))
	if not action.is_empty() and action != _last_bridge_action:
		_last_bridge_action = action
		# Parking drivers boarding / queuing to leave are routine bookkeeping, not events for the operator.
		if not (action.ends_with(" boarding") or action.ends_with(" queued to leave") or action.contains(" ignored ") or action.begins_with("unknown parking vehicle")):
			_add_event("Twin applied: %s" % action)


func _refresh_utility_panel(status: Dictionary) -> void:
	if _utility_power_label == null or _utility_power_bar == null:
		return
	var power := status.get("power", {}) as Dictionary
	var shed_cluster := str(status.get("power_shed_cluster", ""))
	_utility_power_label.text = "POWER RESERVE  %.1f%%\n%.0f / %.0f kWh  ·  city load %.0f kW%s" % [float(power.get("percent", 0.0)), float(power.get("available_kwh", 0.0)), float(power.get("capacity_kwh", 0.0)), float(power.get("demand_kw", 0.0)), "\nLOAD-SHED ZONE: %s (red)" % _cluster_label(shed_cluster) if not shed_cluster.is_empty() else ""]
	_utility_power_label.add_theme_color_override("font_color", RED if not shed_cluster.is_empty() else TEXT)
	_utility_power_bar.value = float(power.get("percent", 0.0))
	var water_box := _utility_power_label.get_parent().get_node_or_null("ReservoirMeters") as VBoxContainer
	if water_box != null and _utility_water_labels.is_empty():
		for reservoir_value in status.get("reservoirs", []) as Array:
			var reservoir := reservoir_value as Dictionary
			var name := str(reservoir.get("name", "Reservoir"))
			var label := _label("", 11, TEXT)
			label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
			water_box.add_child(label)
			var bar := _utility_bar(ACCENT)
			water_box.add_child(bar)
			_utility_water_labels[name] = label
			_utility_water_bars[name] = bar
	for reservoir_value in status.get("reservoirs", []) as Array:
		var reservoir := reservoir_value as Dictionary
		var name := str(reservoir.get("name", ""))
		var label := _utility_water_labels.get(name) as Label
		var bar := _utility_water_bars.get(name) as ProgressBar
		if label != null:
			label.text = "%s  %.1f%%  ·  %.0f/%.0f m³  ·  %.1f m³/h%s" % [name.replace("Reservoir", ""), float(reservoir.get("percent", 0.0)), float(reservoir.get("current_m3", 0.0)), float(reservoir.get("capacity_m3", 0.0)), float(reservoir.get("demand_m3h", 0.0)), "  ·  WATER CUT" if float(reservoir.get("current_m3", 0.0)) <= 0.01 else ""]
			label.add_theme_color_override("font_color", Color(0.30, 0.72, 1.0) if float(reservoir.get("current_m3", 0.0)) <= 0.01 else TEXT)
		if bar != null:
			bar.value = float(reservoir.get("percent", 0.0))
	if _utility_auto_button != null:
		var automatic := bool(status.get("auto_recharge_enabled", false))
		_utility_auto_button.text = "AUTO RECHARGE: ON" if automatic else "AUTO RECHARGE: OFF"
		_utility_auto_button.add_theme_color_override("font_color", GREEN if automatic else RED)
		_utility_schedule_label.text = "Next full recharge in %.0f s" % float(status.get("seconds_until_recharge", 0.0)) if automatic else "Automatic recharge is disabled — use Full Recharge Now."
	if _utility_outage_label != null:
		var protected := bool(status.get("auto_outage_enabled", true))
		_utility_outage_label.text = "Automatic outage protection: %s" % ("ON (default)" if protected else "OFF")
		_utility_outage_label.add_theme_color_override("font_color", GREEN if protected else RED)
	var service_signature := _utility_service_signature(status)
	var service_changed := service_signature != _last_utility_service_signature
	if service_changed:
		_refresh_cluster_buttons(status)
		_last_utility_service_signature = service_signature
	var block_box := _utility_power_label.get_parent().get_node_or_null("BlockDemandRows") as VBoxContainer
	if block_box != null and _utility_block_labels.is_empty():
		var blocks := status.get("blocks", []) as Array
		blocks.sort_custom(func(a: Dictionary, b: Dictionary) -> bool: return float(a.get("power_kw", 0.0)) > float(b.get("power_kw", 0.0)))
		for block_value in blocks:
			var block := block_value as Dictionary
			var name := str(block.get("name", "Block"))
			var label := _label("", 10, MUTED)
			label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
			block_box.add_child(label)
			_utility_block_labels[name] = label
	if service_changed:
		for block_value in status.get("blocks", []) as Array:
			var block := block_value as Dictionary
			var name := str(block.get("name", ""))
			var label := _utility_block_labels.get(name) as Label
			if label != null:
				var service := "POWER ON · WATER ON"
				var colour := MUTED
				if not bool(block.get("power_connected", true)):
					service = "POWER OFF (RED)"
					colour = RED
				elif not bool(block.get("water_connected", true)):
					service = "WATER OFF (BLUE)"
					colour = ACCENT
				_set_label_state(label, "%s  ·  %.1f kW  ·  %.1f m³/h  →  %s\n%s" % [name.replace("Outer", ""), float(block.get("power_kw", 0.0)), float(block.get("water_m3h", 0.0)), str(block.get("reservoir", "")).replace("Reservoir", ""), service], colour)


func _recharge_utilities() -> void:
	var utilities := _city.get_node_or_null("UtilityResources") as Node
	if utilities != null:
		utilities.call("recharge_all")
		_add_event("Utilities fully recharged by operator")


func _toggle_utility_auto_recharge() -> void:
	var utilities := _city.get_node_or_null("UtilityResources") as Node
	if utilities != null:
		utilities.call("set_auto_recharge_enabled", not bool(utilities.get("auto_recharge_enabled")))
		_add_event("Utility auto-recharge toggled")


func _cluster_button(cluster_name: String, kind: String) -> Button:
	var button := Button.new()
	button.toggle_mode = true
	button.text = "%s\nCUT %s" % [_cluster_label(cluster_name), kind.to_upper()]
	button.custom_minimum_size = Vector2(0, 42)
	button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	button.add_theme_font_size_override("font_size", 10)
	button.tooltip_text = "Toggle %s service for %s" % [kind, _cluster_label(cluster_name)]
	button.set_meta("cluster_name", cluster_name)
	button.set_meta("cluster_kind", kind)
	button.toggled.connect(_set_cluster_service.bind(cluster_name, kind))
	return button


func _cluster_label(cluster_name: String) -> String:
	return cluster_name.replace("Zone_", "").replace("NorthCentral", "North Central").replace("SouthCentral", "South Central").replace("_", " ")


func _set_cluster_service(enabled: bool, cluster_name: String, kind: String) -> void:
	var utilities := _city.get_node_or_null("UtilityResources") as Node
	if utilities == null:
		return
	utilities.call("set_manual_%s_cluster" % kind, cluster_name, enabled)
	_add_event("%s service %s for %s" % [kind.capitalize(), "cut" if enabled else "restored", _cluster_label(cluster_name)])


func _refresh_cluster_buttons(status: Dictionary) -> void:
	if _utility_water_cluster_buttons.is_empty():
		_build_cluster_controls(status)
	var water_cuts := status.get("manual_water_clusters", []) as Array
	var power_cuts := status.get("manual_power_clusters", []) as Array
	for cluster_name in _utility_water_cluster_buttons:
		var water_button := _utility_water_cluster_buttons[cluster_name] as Button
		var water_cut := water_cuts.has(cluster_name)
		water_button.set_pressed_no_signal(water_cut)
		water_button.text = "%s\n%s" % [_cluster_label(str(cluster_name)), "RESTORE WATER" if water_cut else "CUT WATER"]
		water_button.add_theme_color_override("font_color", TEXT)
		water_button.add_theme_stylebox_override("normal", _style(Color("19415d") if water_cut else Color("242d35"), ACCENT, 1, 3))
		water_button.add_theme_stylebox_override("hover", _style(Color("245b80"), Color("8bc8ee"), 1, 3))
		var power_button := _utility_power_cluster_buttons[cluster_name] as Button
		var power_cut := power_cuts.has(cluster_name)
		power_button.set_pressed_no_signal(power_cut)
		power_button.text = "%s\n%s" % [_cluster_label(str(cluster_name)), "RESTORE POWER" if power_cut else "CUT POWER"]
		power_button.add_theme_color_override("font_color", TEXT)
		power_button.add_theme_stylebox_override("normal", _style(Color("5a2525") if power_cut else Color("342727"), RED, 1, 3))
		power_button.add_theme_stylebox_override("hover", _style(Color("783737"), Color("f09b9b"), 1, 3))


func _build_cluster_controls(status: Dictionary) -> void:
	var parent := _utility_power_label.get_parent()
	var water_controls := parent.get_node_or_null("WaterZoneToggles") as GridContainer
	var power_controls := parent.get_node_or_null("PowerZoneToggles") as GridContainer
	if water_controls == null or power_controls == null:
		return
	for cluster_value in status.get("service_clusters", []) as Array:
		var cluster := cluster_value as Dictionary
		var cluster_name := str(cluster.get("name", ""))
		if cluster_name.is_empty():
			continue
		var water_button := _cluster_button(cluster_name, "water")
		water_button.tooltip_text = "%s · %d blocks · %.1f m³/h\nClick to cut/restore water for this shared zone." % [_cluster_label(cluster_name), int(cluster.get("blocks", 0)), float(cluster.get("water_m3h", 0.0))]
		water_controls.add_child(water_button)
		_utility_water_cluster_buttons[cluster_name] = water_button
		var power_button := _cluster_button(cluster_name, "power")
		power_button.tooltip_text = "%s · %d blocks · %.1f kW\nClick to cut/restore power for this shared zone." % [_cluster_label(cluster_name), int(cluster.get("blocks", 0)), float(cluster.get("power_kw", 0.0))]
		power_controls.add_child(power_button)
		_utility_power_cluster_buttons[cluster_name] = power_button


func _utility_bar(colour: Color) -> ProgressBar:
	var bar := ProgressBar.new()
	bar.show_percentage = false
	bar.custom_minimum_size = Vector2(0, 14)
	bar.max_value = 100.0
	var fill := StyleBoxFlat.new()
	fill.bg_color = colour
	fill.set_corner_radius_all(2)
	bar.add_theme_stylebox_override("fill", fill)
	return bar


func _open_camera_id(camera_id: String) -> void:
	var device := _city.get_node_or_null("TrafficCameraSystem/TrafficCamera_%s" % camera_id.replace("-", "_")) as Node3D
	var feed_camera: Camera3D = null
	if device != null:
		feed_camera = device.get_node_or_null("FeedCamera") as Camera3D
	if feed_camera == null:
		_camera_status.text = "Selected camera is unavailable"
		_camera_status.add_theme_color_override("font_color", RED)
		return
	feed_camera.current = true
	_camera_status.text = "%s live — direct main viewport render" % camera_id
	_camera_status.add_theme_color_override("font_color", GREEN)
	_add_event("Viewport switched directly to %s" % camera_id)


func _return_to_city_view() -> void:
	var city_camera := _city.get_node_or_null("EstablishingCamera") as Camera3D
	if city_camera != null:
		city_camera.current = true
	_camera_status.text = "City overview camera active"
	_camera_status.add_theme_color_override("font_color", AMBER)
	_add_event("Viewport returned to city camera")


func _on_street_camera_released(camera_id: String) -> void:
	_camera_status.text = "%s released — free city camera and MMB orbit restored" % camera_id
	_camera_status.add_theme_color_override("font_color", AMBER)
	_add_event("Escape released %s and restored the free city camera" % camera_id)


func _follow_random_person() -> void:
	# Random follow is a presentation control: the chosen frame must immediately
	# show readable city life. A citizen who has a valid larger trip/work purpose
	# but is currently only walking is deliberately ineligible.
	var candidates: Array[Node3D] = []
	var npc_ai := _city.get_node_or_null("NpcAi")
	if npc_ai != null:
		for node_value in _city.get_tree().get_nodes_in_group("pedestrians"):
			var candidate := node_value as Node3D
			if candidate == null or not candidate.visible or not candidate.is_visible_in_tree():
				continue
			var state := npc_ai.call("get_agent_state", candidate) as Dictionary
			if _is_random_followable_citizen(state):
				candidates.append(candidate)
	_follow_random_candidates(candidates, "active citizen")


func _follow_random_car() -> void:
	_follow_random_from_groups(["vehicles", "backdrop_vehicles"], "car")


func _is_random_followable_citizen(state: Dictionary) -> bool:
	if state.is_empty() or float(state.get("speed_mps", 0.0)) > 0.01:
		return false
	var motion_state := str(state.get("motion_state", ""))
	for walking_prefix in ["walking", "travelling", "yielding"]:
		if motion_state.begins_with(walking_prefix):
			return false
	var tangible_activity := bool(state.get("prop_interaction_active", false)) \
		or not str(state.get("interaction_id", "")).is_empty() \
		or not str(state.get("active_tool_kind", "")).is_empty() \
		or not str(state.get("active_hand_item_kind", "")).is_empty()
	return tangible_activity and bool(state.get("stationary_action_readable", false))


func _follow_random_from_groups(group_names: Array[String], kind: String) -> void:
	var candidates: Array[Node3D] = []
	for group_name in group_names:
		for node_value in _city.get_tree().get_nodes_in_group(group_name):
			var candidate := node_value as Node3D
			if candidate != null and candidate.visible and candidate.is_visible_in_tree():
				candidates.append(candidate)
	_follow_random_candidates(candidates, kind)


func _follow_random_candidates(candidates: Array[Node3D], kind: String) -> void:
	if candidates.is_empty():
		_camera_status.text = "No active %s is available to follow" % kind
		_camera_status.add_theme_color_override("font_color", RED)
		return
	var target := candidates[randi() % candidates.size()]
	var city_camera := _city.get_node_or_null("EstablishingCamera") as Camera3D
	if city_camera == null or not bool(city_camera.call("follow_target", target)):
		_camera_status.text = "Could not start follow mode"
		_camera_status.add_theme_color_override("font_color", RED)
		return
	city_camera.current = true
	target.set_meta("random_follow_selected_while_active", kind == "active citizen")
	_camera_status.text = "Following %s — %s" % [kind.to_upper(), target.name]
	_camera_status.add_theme_color_override("font_color", GREEN)
	_add_event("Viewport following %s %s" % [kind, target.name])


func _on_city_target_selected(target: Node3D, direct_city_selection: bool) -> void:
	if _is_focusable_city_object(target):
		_show_city_object_focus(target)
	else:
		_clear_city_object_focus()
	if not _is_npc(target):
		return
	_selected_npc = target
	_refresh_npc_panel()
	# Random follow calls the exact same camera framing API but passes false, so
	# it never steals the operator away from the tab they are using.
	if direct_city_selection and _control_tabs != null and _npc_tab_index >= 0:
		_control_tabs.current_tab = _npc_tab_index
		_add_event("Citizen selected directly: %s — society tab opened" % target.name)


func _is_focusable_city_object(target: Node3D) -> bool:
	if target == null or not bool(target.get_meta("highlightable", false)):
		return false
	for excluded_group in ["vehicles", "backdrop_vehicles", "pedestrians", "backdrop_pedestrians", "incident_responders", "buildings", "backdrop_buildings"]:
		if target.is_in_group(excluded_group):
			return false
	return true


func _enable_operational_object_focus() -> void:
	if _city == null:
		return
	var operational_groups := {
		"power_poles":"power_distribution_pole", "power_lines":"power_distribution_line", "power_sources":"power_source",
		"water_reservoirs":"water_reservoir", "water_reservoir_meters":"reservoir_level_meter",
		"power_service_meters":"zone_power_meter", "water_service_meters":"zone_water_meter",
		"garbage_bins":"garbage_container", "emergency_stations":"response_station",
	}
	for definition_value in ASSET_HIGHLIGHT_DEFINITIONS.values():
		var definition := definition_value as Dictionary
		var group_name := str(definition.get("group", ""))
		if not group_name.is_empty() and not operational_groups.has(group_name):
			operational_groups[group_name] = str(definition.get("label", "city_asset")).to_snake_case()
	for group_name_value in operational_groups:
		var group_name := str(group_name_value)
		for value in _city.get_tree().get_nodes_in_group(group_name):
			var target := value as Node3D
			if target == null:
				continue
			target.set_meta("highlightable", true)
			if str(target.get_meta("asset_type", "")).is_empty():
				target.set_meta("asset_type", str(operational_groups[group_name]))
			if str(target.get_meta("infrastructure_id", "")).is_empty():
				target.set_meta("infrastructure_id", target.name.replace("_", "-"))
	for value in _city.get_tree().get_nodes_in_group("npc_meter_work_props"):
		var meter := value as Node3D
		if meter == null:
			continue
		meter.set_meta("highlightable", true)
		meter.set_meta("asset_type", "%s_service_meter" % str(meter.get_meta("meter_kind", "utility")))
		meter.set_meta("infrastructure_id", meter.name.replace("ActivityProp_", "").replace("_", "-"))


func _show_city_object_focus(target: Node3D) -> void:
	_clear_city_object_focus()
	_clear_asset_highlight(false)
	_clear_infrastructure_highlight()
	var holder := Node3D.new()
	holder.name = "FocusedCityObject"
	_city.add_child(holder)
	_focus_highlight_holder = holder
	_focused_city_object = target
	var colour := Color("64d9f3")
	_add_model_frame(holder, target, _make_highlight_material(colour), "focused_city_object_frame")
	var bounds := _global_model_bounds(target)
	var tag := Label3D.new()
	tag.name = "FocusTag"
	tag.text = _city_object_tag(target).replace("\n", "  ·  ")
	tag.font_size = 30
	tag.pixel_size = 0.010
	tag.outline_size = 8
	tag.modulate = Color("e9fbff")
	tag.outline_modulate = Color("071318")
	tag.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	tag.no_depth_test = true
	tag.position = Vector3(bounds.get_center().x, bounds.end.y + maxf(0.75, bounds.size.y * 0.16), bounds.get_center().z)
	holder.add_child(tag)
	_camera_status.text = "%s focused — click empty city space to release" % _city_object_tag(target).replace("\n", " · ")
	_camera_status.add_theme_color_override("font_color", colour)


func _city_object_tag(target: Node3D) -> String:
	var identity := str(target.get_meta("sensor_id", target.get_meta("actuator_id", target.get_meta("camera_id", target.get_meta("infrastructure_id", target.get_meta("asset_id", ""))))))
	if identity.is_empty():
		identity = target.name.replace("_", " ")
	var kind := str(target.get_meta("asset_type", "city device")).replace("_", " ").capitalize()
	return "%s\n%s" % [identity, kind]


func _clear_city_object_focus() -> void:
	if is_instance_valid(_focus_highlight_holder):
		_focus_highlight_holder.free()
	_focus_highlight_holder = null
	_focused_city_object = null


func _is_npc(target: Node3D) -> bool:
	return target.is_in_group("pedestrians") or target.is_in_group("backdrop_pedestrians")


func _refresh_npc_panel() -> void:
	if _npc_selection_label == null:
		return
	if _selected_npc == null or not is_instance_valid(_selected_npc):
		_set_label_state(_npc_selection_label, "No citizen selected", AMBER)
		_set_label_state(_npc_character_label, "—", TEXT)
		_set_label_state(_npc_personality_label, "—", MUTED)
		_set_label_state(_npc_identity_context_label, "—", MUTED)
		_set_label_state(_npc_now_label, "—", MUTED)
		_set_label_state(_npc_status_label, "Waiting for selection", AMBER)
		_set_label_state(_npc_now_context_label, "—", MUTED)
		_npc_now_context_label.get_parent().visible=true
		_npc_partner_label.get_parent().visible=false; _npc_activity_label.get_parent().visible=false
		_set_label_state(_npc_objective_label, "—", TEXT)
		_set_label_state(_npc_intent_label, "—", MUTED)
		_set_label_state(_npc_route_label, "—", MUTED)
		_set_label_state(_npc_planner_label, "—", MUTED)
		if _npc_payload != null: _npc_payload.text = ""
		if _npc_response != null: _npc_response.text = ""
		return
	var npc_ai := _city.get_node_or_null("NpcAi") as Node
	var state := npc_ai.call("get_agent_state", _selected_npc) as Dictionary if npc_ai != null else {}
	if state.is_empty():
		_set_label_state(_npc_selection_label, _selected_npc.name, AMBER)
		_set_label_state(_npc_character_label, "Unregistered character", TEXT)
		_set_label_state(_npc_personality_label, "—", MUTED)
		_set_label_state(_npc_identity_context_label, "—", MUTED)
		_set_label_state(_npc_now_label, "No registered activity", AMBER)
		_set_label_state(_npc_status_label, "Unavailable", RED)
		_set_label_state(_npc_now_context_label, "—", MUTED)
		_npc_now_context_label.get_parent().visible=true
		_npc_partner_label.get_parent().visible=false; _npc_activity_label.get_parent().visible=false
		_set_label_state(_npc_objective_label, "—", TEXT)
		_set_label_state(_npc_intent_label, "—", MUTED)
		_set_label_state(_npc_route_label, "No active role controller is available for this model.", MUTED)
		_set_label_state(_npc_planner_label, "Decision source: none", MUTED)
		return
	var role := str(state.get("role", "city resident"))
	var personality := str(state.get("personality", ""))
	_set_label_state(_npc_selection_label, str(state.get("display_name", state.get("npc_id", _selected_npc.name))), GREEN)
	_set_label_state(_npc_character_label, role.capitalize(), TEXT)
	_set_label_state(_npc_personality_label, personality.replace(" and ","  ·  ").capitalize() if not personality.is_empty() else "—", MUTED)
	_set_label_state(_npc_identity_context_label, "%s  ·  %s (%d people)" % [str(state.get("citizen_id","—")),str(state.get("household_id","—")),int(state.get("household_size",0))], MUTED)
	var task := str(state.get("task", ""))
	var waypoint := state.get("next_waypoint", Vector3.ZERO) as Vector3
	var action_text := str(state.get("action", "walking sidewalk"))
	var action_target := str(state.get("action_target", ""))
	var action_remaining := float(state.get("seconds_remaining_in_action", 0.0))
	var interaction_id := str(state.get("interaction_id", ""))
	_set_label_state(_npc_now_label, _short_npc_action(action_text), TEXT)
	_set_label_state(_npc_status_label,_npc_status_value(str(state.get("motion_state",""))),GREEN)
	_set_label_state(_npc_now_context_label,action_target.capitalize() if not action_target.is_empty() else "—",MUTED)
	if not interaction_id.is_empty():
		_npc_now_context_label.get_parent().visible=false
		_npc_partner_label.get_parent().visible=true; _npc_activity_label.get_parent().visible=true
		_set_label_state(_npc_partner_label,str(state.get("interaction_partner","—")),MUTED)
		_set_label_state(_npc_activity_label,interaction_id.replace("_"," ").capitalize(),MUTED)
	else:
		_npc_now_context_label.get_parent().visible=true
		_npc_partner_label.get_parent().visible=false; _npc_activity_label.get_parent().visible=false
	var decision_reason := str(state.get("decision_reason", ""))
	var purpose := decision_reason if not decision_reason.is_empty() else str(NPC_ROLE_PURPOSE.get(role,"carry out an assigned role in the city"))
	_set_label_state(_npc_objective_label,task.replace("_"," ").capitalize() if not task.is_empty() else "—",TEXT)
	_set_label_state(_npc_intent_label,purpose,Color("c5b8e4"))
	var activity := state.get("current_activity", {}) as Dictionary
	var work_order := state.get("active_work_order", {}) as Dictionary
	var active_trip := state.get("active_trip", {}) as Dictionary
	var minute_of_day := int(state.get("simulation_minute_of_day", 0))
	var clock_text := "%02d:%02d" % [int(minute_of_day / 60), minute_of_day % 60]
	var work_beat := str(state.get("interaction_turn", "")) if not interaction_id.is_empty() else str(state.get("work_stage", ""))
	var held_items: Array[String] = []
	if not str(state.get("active_tool_kind", "")).is_empty(): held_items.append(str(state.active_tool_kind).replace("_", " "))
	if not str(state.get("active_hand_item_kind", "")).is_empty(): held_items.append(str(state.active_hand_item_kind).replace("_", " "))
	var held_text := ", ".join(held_items) if not held_items.is_empty() else "none"
	_set_label_state(_npc_route_label, "Citizen: %s  ·  Household: %s (%d people)  ·  Home: %s\nAge: %d  ·  Employer: %s  ·  Society time: day %d, %s\nAgenda: %s  ·  Work order: %s  ·  Trip: %s\nMotion: %s\nWork beat: %s (%d/%d)  ·  Held: %s\nSpeed: %.2f m/s  ·  Animation: %s\nStation: %s\nNext legal sidewalk waypoint: (%.1f, %.1f)\nSeconds remaining: %.1f" % [str(state.get("citizen_id",state.get("npc_id",_selected_npc.name))),str(state.get("household_id","—")),int(state.get("household_size",0)),str(state.get("home_block","—")),int(state.get("age",0)),str(state.get("employer_id","none")),int(state.get("simulation_day",0)),clock_text,str(activity.get("purpose","—")).replace("_"," "),str(work_order.get("work_order_id","—")),str(active_trip.get("trip_id","—")),str(state.get("motion_state", "—")).replace("_", " ").capitalize(), work_beat.replace("_", " ").capitalize() if not work_beat.is_empty() else "—", int(state.get("work_stage_index", -1)) + 1, int(state.get("work_stage_count", 0)), held_text, float(state.get("speed_mps", 0.0)), str(state.get("animation_clip","—")), str(state.get("station_id","—")), waypoint.x, waypoint.z,action_remaining], MUTED)
	_set_label_state(_npc_planner_label, "Authority: %s\nWhy now: %s\nWork cause: %s\nTransport feedback: %s" % [str(state.get("plan_source", "—")),str(state.get("decision_reason","—")),str(work_order.get("cause","not a work-order action")),str((state.get("transport_snapshot",{}) as Dictionary).get("source","local traffic fallback"))], MUTED)
	if _npc_payload != null:
		_npc_payload.text = JSON.stringify(state.get("planner_payload", {}), "  ")
	if _npc_response != null:
		_npc_response.text = JSON.stringify(state.get("planner_response", {}), "  ")


func _short_npc_action(action_text:String)->String:
	var result:=action_text.strip_edges()
	for prefix in ["public-information technician","building maintenance technician","road maintenance technician","horticulture specialist","utilities technician","sanitation worker","clinic assistant","market stocker","transit steward","parks worker","parcel courier","courier","resident","patient","traveller"]:
		if result.to_lower().begins_with(prefix+" "): result=result.substr(prefix.length()+1); break
	return result.capitalize() if not result.is_empty() else "—"


func _npc_status_value(motion_state:String)->String:
	if motion_state.begins_with("interacting_"): return "Interacting"
	if motion_state.begins_with("performing_"): return "Working"
	if motion_state.begins_with("walking_") or motion_state.begins_with("travelling_"): return "Travelling"
	if motion_state.begins_with("yielding_"): return "Yielding / rerouting"
	if motion_state=="starting_shift": return "Starting shift"
	return motion_state.replace("_"," ").capitalize() if not motion_state.is_empty() else "Unknown"


func _add_infrastructure_button(parent: HBoxContainer, kind: String, label_text: String) -> void:
	var button := Button.new()
	button.text = label_text
	button.tooltip_text = "Show a bright 3D frame around the %s infrastructure" % kind
	button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	button.custom_minimum_size = Vector2(0, 30)
	button.add_theme_font_size_override("font_size", 12)
	button.set_meta("highlight_kind", kind)
	button.pressed.connect(_toggle_infrastructure_highlight.bind(kind))
	parent.add_child(button)
	_infrastructure_buttons.append(button)


func _toggle_infrastructure_highlight(kind: String) -> void:
	if _active_infrastructure_highlight == kind:
		_clear_infrastructure_highlight()
		_add_event("Infrastructure highlight cleared")
		return
	_clear_asset_highlight(false)
	_clear_infrastructure_highlight()
	_active_infrastructure_highlight = kind
	var holder := Node3D.new()
	holder.name = "InfrastructureHighlight_%s" % kind.capitalize()
	_city.add_child(holder)
	if kind == "water":
		var framed_assets := 0
		for target_value in _city.get_tree().get_nodes_in_group("water_reservoirs"):
			var target := target_value as Node3D
			if target != null and _add_model_frame(holder, target, _get_water_frame_material(), "water_infrastructure_frames"):
				framed_assets += 1
		for target_value in _city.get_tree().get_nodes_in_group("water_service_meters"):
			var target := target_value as Node3D
			if target != null and _add_model_frame(holder, target, _get_water_frame_material(), "water_infrastructure_frames"):
				framed_assets += 1
		_camera_status.text = "Water infrastructure framed — %d assets including zone meters" % framed_assets
		_camera_status.add_theme_color_override("font_color", ACCENT)
		_add_event("Water infrastructure highlighted: %d reservoir assets" % framed_assets)
	else:
		var framed_poles := 0
		for pole_value in _city.get_tree().get_nodes_in_group("power_poles"):
			var pole := pole_value as Node3D
			if pole != null and _add_model_frame(holder, pole, _get_power_frame_material(), "power_infrastructure_frames"):
				framed_poles += 1
		var framed_meters := 0
		for meter in _power_meter_highlight_targets():
			if meter != null and _add_model_frame(holder, meter, _get_power_frame_material(), "power_infrastructure_frames"):
				framed_meters += 1
		for line_value in _city.get_tree().get_nodes_in_group("power_lines"):
			(line_value as GeometryInstance3D).material_overlay = _get_power_line_highlight_material()
		_camera_status.text = "Power grid framed — %d poles, all %d power meters and live cables" % [framed_poles, framed_meters]
		_camera_status.add_theme_color_override("font_color", AMBER)
		_add_event("Power grid highlighted: %d poles and cable spans" % framed_poles)
	_refresh_infrastructure_button_labels()


func _toggle_asset_highlight(kind: String) -> void:
	if _active_asset_highlight == kind:
		_clear_asset_highlight()
		_add_event("Asset Map highlight cleared")
		return
	_clear_infrastructure_highlight()
	_clear_asset_highlight(false)
	_active_asset_highlight = kind
	var definition := ASSET_HIGHLIGHT_DEFINITIONS.get(kind, {}) as Dictionary
	var holder := Node3D.new()
	holder.name = "AssetMapHighlight"
	_city.add_child(holder)
	_asset_highlight_holder = holder
	var targets := _asset_highlight_targets(kind)
	var framed := 0
	var material := _asset_frame_material(kind)
	for target in targets:
		if target != null and _add_model_frame(holder, target, material, "asset_map_frames"):
			framed += 1
	var label_text := str(definition.get("label", kind))
	var colour := definition.get("color", ACCENT) as Color
	_camera_status.text = "%s framed — %d visible, interaction-ready 3D assets" % [label_text, framed]
	_camera_status.add_theme_color_override("font_color", colour)
	_add_event("Asset Map · %s highlighted: %d assets" % [label_text, framed])
	_refresh_asset_button_labels()


func _asset_highlight_targets(kind: String) -> Array[Node3D]:
	if kind == "power_meters":
		return _power_meter_highlight_targets()
	if kind == "water_meters":
		return _water_meter_highlight_targets()
	var definition := ASSET_HIGHLIGHT_DEFINITIONS.get(kind, {}) as Dictionary
	var group_name := str(definition.get("group", ""))
	var targets: Array[Node3D] = []
	var seen := {}
	if group_name.is_empty():
		return targets
	for value in _city.get_tree().get_nodes_in_group(group_name):
		if not value is Node3D:
			continue
		var target := value as Node3D
		if not target.is_visible_in_tree() or seen.has(target.get_instance_id()):
			continue
		seen[target.get_instance_id()] = true
		targets.append(target)
	return targets


func _water_meter_highlight_targets() -> Array[Node3D]:
	var targets: Array[Node3D] = []
	var seen := {}
	for group_name in ["water_service_meters", "npc_meter_work_props", "npc_activity_props", "npc_interaction_props"]:
		for value in _city.get_tree().get_nodes_in_group(group_name):
			if not value is Node3D:
				continue
			var meter := value as Node3D
			if group_name != "water_service_meters" and str(meter.get_meta("meter_kind", "")) != "water" and str(meter.get_meta("activity_prop_kind", "")) != "water_meter":
				continue
			if not meter.is_visible_in_tree() or seen.has(meter.get_instance_id()):
				continue
			seen[meter.get_instance_id()] = true
			targets.append(meter)
	return targets


func _clear_asset_highlight(refresh_status := true) -> void:
	if is_instance_valid(_asset_highlight_holder):
		_asset_highlight_holder.free()
	_asset_highlight_holder = null
	_active_asset_highlight = ""
	_refresh_asset_button_labels()
	if refresh_status and _camera_status != null:
		_camera_status.text = "Asset Map clear — direct city camera active"
		_camera_status.add_theme_color_override("font_color", AMBER)


func _asset_frame_material(kind: String) -> StandardMaterial3D:
	if _asset_frame_materials.has(kind):
		return _asset_frame_materials[kind] as StandardMaterial3D
	var definition := ASSET_HIGHLIGHT_DEFINITIONS.get(kind, {}) as Dictionary
	var material := _make_highlight_material(definition.get("color", ACCENT) as Color)
	_asset_frame_materials[kind] = material
	return material


func _refresh_asset_button_labels() -> void:
	for button in _asset_highlight_buttons:
		_refresh_asset_button(button)
	# Keep the group that holds the active category open and visible.
	if not _active_asset_highlight.is_empty():
		for button in _asset_highlight_buttons:
			if str(button.get_meta("asset_highlight_kind", "")) == _active_asset_highlight:
				var key := str(button.get_meta("asset_group", ""))
				var header := _asset_group_headers.get(key) as Button
				if header != null and not header.button_pressed:
					header.button_pressed = true
	_refresh_asset_status()


func _refresh_asset_button(button: Button) -> void:
	var kind := str(button.get_meta("asset_highlight_kind", ""))
	var definition := ASSET_HIGHLIGHT_DEFINITIONS.get(kind, {}) as Dictionary
	var selected := kind == _active_asset_highlight
	var count := _asset_highlight_targets(kind).size() if _city != null else 0
	button.text = "● %s  ·  %d%s" % [str(definition.get("label", kind)), count, "  ✓" if selected else ""]
	button.add_theme_color_override("font_color", definition.get("color", TEXT) as Color)
	if selected:
		button.add_theme_stylebox_override("normal", _style(Color("253746"), definition.get("color", ACCENT) as Color, 2, 5))
	else:
		button.remove_theme_stylebox_override("normal")


func _power_meter_highlight_targets() -> Array[Node3D]:
	# The utility simulation owns sixteen live zone meters, while job stations
	# contain additional visible power-meter cabinets used by technicians. Both
	# read as power infrastructure in the rendered city and must highlight. Keep
	# the union unique in case an authored meter belongs to both groups later.
	var targets: Array[Node3D] = []
	var seen := {}
	for group_name in ["power_service_meters", "npc_meter_work_props", "npc_activity_props", "npc_interaction_props"]:
		for value in _city.get_tree().get_nodes_in_group(group_name):
			if not value is Node3D:
				continue
			var meter := value as Node3D
			if group_name != "power_service_meters" and str(meter.get_meta("meter_kind", "")) != "power" and str(meter.get_meta("activity_prop_kind", "")) != "power_meter":
				continue
			var instance_id := meter.get_instance_id()
			if seen.has(instance_id):
				continue
			seen[instance_id] = true
			targets.append(meter)
	return targets


func _clear_infrastructure_highlight() -> void:
	var existing := _city.get_node_or_null("InfrastructureHighlight_Water") as Node3D
	if existing == null:
		existing = _city.get_node_or_null("InfrastructureHighlight_Power") as Node3D
	if existing != null:
		existing.queue_free()
	for line_value in _city.get_tree().get_nodes_in_group("power_lines"):
		(line_value as GeometryInstance3D).material_overlay = null
	_active_infrastructure_highlight = ""
	_refresh_infrastructure_button_labels()


func _refresh_infrastructure_button_labels() -> void:
	for button in _infrastructure_buttons:
		var kind := str(button.get_meta("highlight_kind", ""))
		var selected := kind == _active_infrastructure_highlight
		button.text = ("Water network" if kind == "water" else "Power grid") + ("  ✓" if selected else "")
		button.add_theme_color_override("font_color", ACCENT if selected and kind == "water" else (AMBER if selected else TEXT))
	_refresh_asset_status()


func _add_model_frame(parent: Node3D, target: Node3D, material: StandardMaterial3D, group_name: String) -> bool:
	var bounds := _global_model_bounds(target)
	if bounds.size.length_squared() <= 0.0001:
		return false
	var frame := Node3D.new()
	frame.name = "Frame_%s" % target.name
	frame.add_to_group(group_name)
	parent.add_child(frame)
	# Scale the outline to the asset. A fixed 0.55 m margin and 0.18 m diameter
	# made compact power meters look hidden inside an orange scaffold.
	var margin_size := clampf(bounds.size.length() * 0.045, 0.12, 0.55)
	var margin := Vector3.ONE * margin_size
	var shortest_side := minf(bounds.size.x, minf(bounds.size.y, bounds.size.z))
	var edge_radius := clampf(shortest_side * 0.045, 0.025, 0.075)
	var low := bounds.position - margin
	var high := bounds.end + margin
	var corners := [
		Vector3(low.x, low.y, low.z), Vector3(high.x, low.y, low.z), Vector3(high.x, low.y, high.z), Vector3(low.x, low.y, high.z),
		Vector3(low.x, high.y, low.z), Vector3(high.x, high.y, low.z), Vector3(high.x, high.y, high.z), Vector3(low.x, high.y, high.z),
	]
	# All twelve edge cylinders share one mesh and one material. MultiMesh keeps a
	# traffic-signal category at 49 draw calls instead of creating 588 individual
	# GeometryInstance3D nodes, which is important in the live simulation.
	var edge_pairs := [[0, 1], [1, 2], [2, 3], [3, 0], [4, 5], [5, 6], [6, 7], [7, 4], [0, 4], [1, 5], [2, 6], [3, 7]]
	var edge_mesh := CylinderMesh.new()
	edge_mesh.top_radius = 1.0
	edge_mesh.bottom_radius = 1.0
	edge_mesh.height = 1.0
	edge_mesh.radial_segments = 6
	edge_mesh.material = material
	var multi_mesh := MultiMesh.new()
	multi_mesh.transform_format = MultiMesh.TRANSFORM_3D
	multi_mesh.mesh = edge_mesh
	multi_mesh.instance_count = edge_pairs.size()
	for edge_index in edge_pairs.size():
		var edge := edge_pairs[edge_index] as Array
		var start := corners[int(edge[0])] as Vector3
		var end := corners[int(edge[1])] as Vector3
		var delta := end - start
		var orientation := Basis(Quaternion(Vector3.UP, delta.normalized()))
		var scaled_basis := orientation * Basis.from_scale(Vector3(edge_radius, delta.length(), edge_radius))
		multi_mesh.set_instance_transform(edge_index, Transform3D(scaled_basis, start.lerp(end, 0.5)))
	var batched_edges := MultiMeshInstance3D.new()
	batched_edges.name = "BatchedFrameEdges"
	batched_edges.multimesh = multi_mesh
	batched_edges.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	frame.add_child(batched_edges)
	return true


func _add_frame_edge(parent: Node3D, start: Vector3, end: Vector3, material: StandardMaterial3D, radius: float) -> void:
	var edge := MeshInstance3D.new()
	var mesh := CylinderMesh.new()
	mesh.top_radius = radius
	mesh.bottom_radius = radius
	mesh.height = start.distance_to(end)
	mesh.radial_segments = 6
	edge.mesh = mesh
	edge.material_override = material
	edge.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	parent.add_child(edge)
	edge.global_position = start.lerp(end, 0.5)
	edge.global_transform.basis = Basis(Quaternion(Vector3.UP, (end - start).normalized()))


func _global_model_bounds(target: Node3D) -> AABB:
	var has_bounds := false
	var minimum := Vector3.ZERO
	var maximum := Vector3.ZERO
	var mesh_values: Array = [target] if target is MeshInstance3D else []
	mesh_values.append_array(target.find_children("*", "MeshInstance3D", true, false))
	for mesh_value in mesh_values:
		var mesh_node := mesh_value as MeshInstance3D
		if mesh_node.mesh == null:
			continue
		var local_bounds := mesh_node.get_aabb()
		for x_value in [local_bounds.position.x, local_bounds.end.x]:
			for y_value in [local_bounds.position.y, local_bounds.end.y]:
				for z_value in [local_bounds.position.z, local_bounds.end.z]:
					var point := mesh_node.global_transform * Vector3(x_value, y_value, z_value)
					if not has_bounds:
						minimum = point
						maximum = point
						has_bounds = true
					else:
						minimum = minimum.min(point)
						maximum = maximum.max(point)
	return AABB(minimum, maximum - minimum) if has_bounds else AABB()


func _get_water_frame_material() -> StandardMaterial3D:
	if _water_frame_material == null:
		_water_frame_material = _make_highlight_material(Color(0.04, 0.72, 1.0))
	return _water_frame_material


func _get_power_frame_material() -> StandardMaterial3D:
	if _power_frame_material == null:
		_power_frame_material = _make_highlight_material(Color(1.0, 0.48, 0.06))
	return _power_frame_material


func _get_power_line_highlight_material() -> StandardMaterial3D:
	if _power_line_highlight_material == null:
		_power_line_highlight_material = _make_highlight_material(Color(1.0, 0.66, 0.08))
	return _power_line_highlight_material


func _make_highlight_material(colour: Color) -> StandardMaterial3D:
	var material := StandardMaterial3D.new()
	material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	material.albedo_color = colour
	material.emission_enabled = true
	material.emission = colour
	material.emission_energy_multiplier = 2.4
	material.no_depth_test = false
	return material


func _show_overview() -> void:
	_return_to_city_view()
	var camera := _city.get_node_or_null("EstablishingCamera") as Camera3D
	if camera != null:
		camera.call("_clear_target_follow")
		camera.global_position = Vector3(720.0, 520.0, 720.0)
		camera.rotation_degrees = Vector3(-28.8, 45.0, 0.0)


func _show_street() -> void:
	_return_to_city_view()
	var camera := _city.get_node_or_null("EstablishingCamera") as Camera3D
	if camera != null:
		camera.call("_clear_target_follow")
		camera.global_position = Vector3(105.0, 32.0, 154.0)
		camera.rotation_degrees = Vector3(-13.0, 34.0, 0.0)


func _reset_camera() -> void:
	_return_to_city_view()
	var camera := _city.get_node_or_null("EstablishingCamera") as Camera3D
	if camera != null:
		camera.call("_clear_target_follow")
		camera.global_transform = camera.get("_start_transform") as Transform3D


func _send_bridge(payload: Dictionary) -> void:
	_command_udp.put_packet(JSON.stringify(payload).to_utf8_buffer())


func _send_sumo_command(command: String) -> void:
	_command_udp.put_packet(JSON.stringify({"command": command}).to_utf8_buffer())
	_add_event("Operator command: %s" % command.replace("_", " "))


func _phase_text(approaches_by_signal: Dictionary) -> String:
	if approaches_by_signal.is_empty():
		return "WAITING FOR SIGNAL DATA"
	var sample := approaches_by_signal.values()[0] as Dictionary
	var north := str(sample.get("north", "r")).to_lower()
	var east := str(sample.get("east", "r")).to_lower()
	if north == "g": return "NORTH / SOUTH GREEN · EAST / WEST RED"
	if east == "g": return "EAST / WEST GREEN · NORTH / SOUTH RED"
	if north == "y": return "NORTH / SOUTH AMBER · EAST / WEST RED"
	if east == "y": return "EAST / WEST AMBER · NORTH / SOUTH RED"
	return "ALL-RED CLEARANCE"


func _utility_service_signature(status: Dictionary) -> String:
	# Only the service state drives the costly block list/button rewrite. Meter
	# amounts keep updating, but 64 labels and 32 styles are untouched unless a
	# utility action or automatic outage actually changes a zone's service.
	var parts: Array[String] = [str(status.get("power_shed_cluster", ""))]
	parts.append("W:" + _string_signature(status.get("manual_water_clusters", []) as Array))
	parts.append("P:" + _string_signature(status.get("manual_power_clusters", []) as Array))
	for block_value in status.get("blocks", []) as Array:
		var block := block_value as Dictionary
		parts.append("%s:%d:%d" % [str(block.get("name", "")), 1 if bool(block.get("power_connected", true)) else 0, 1 if bool(block.get("water_connected", true)) else 0])
	return "|".join(parts)


func _string_signature(values: Array) -> String:
	var items: Array[String] = []
	for value in values:
		items.append(str(value))
	items.sort()
	return ",".join(items)


func _compact_signal(approaches: Dictionary) -> String:
	return "NS:%s/%s EW:%s/%s" % [approaches.get("north", "r"), approaches.get("south", "r"), approaches.get("east", "r"), approaches.get("west", "r")]


func _set_metric(key: String, value: String) -> void:
	var label := _metrics.get(key) as Label
	if label != null and label.text != value:
		label.text = value


func _set_label_state(label: Label, text_value: String, colour: Color) -> void:
	if label.text != text_value:
		label.text = text_value
	if label.get_theme_color("font_color") != colour:
		label.add_theme_color_override("font_color", colour)


func _label(text_value: String, size_value: int, colour: Color) -> Label:
	var label := Label.new()
	label.text = text_value
	label.add_theme_font_size_override("font_size", size_value)
	label.add_theme_color_override("font_color", colour)
	return label


func _add_caption(parent: VBoxContainer, text_value: String) -> void:
	parent.add_child(_label(text_value, 12, TEXT))


func _add_button(parent: VBoxContainer, text_value: String, action: Callable) -> void:
	var button := Button.new()
	button.text = text_value
	button.custom_minimum_size = Vector2(0, 29)
	button.add_theme_font_size_override("font_size", 12)
	button.add_theme_color_override("font_color", TEXT)
	button.add_theme_stylebox_override("normal", _style(Color("242424"), Color("1a1a1a"), 1, 3))
	button.add_theme_stylebox_override("hover", _style(Color("3e5362"), ACCENT, 1, 3))
	button.pressed.connect(action)
	parent.add_child(button)


func _style(fill: Color, border: Color, width: int, radius: int) -> StyleBoxFlat:
	var style := StyleBoxFlat.new()
	style.bg_color = fill
	style.border_color = border
	style.set_border_width_all(width)
	style.set_corner_radius_all(radius)
	style.content_margin_left = 9.0
	style.content_margin_right = 9.0
	style.content_margin_top = 7.0
	style.content_margin_bottom = 7.0
	return style


func _add_event(message: String) -> void:
	# The same message again collapses into one line with a counter ("×3") instead of filling the log.
	if message == _last_event_message and not _events.is_empty():
		_last_event_repeats += 1
		_events[0] = "%s  %s  ×%d" % [Time.get_time_string_from_system(), message, _last_event_repeats]
	else:
		_last_event_message = message
		_last_event_repeats = 1
		_events.push_front("%s  %s" % [Time.get_time_string_from_system(), message])
	if _events.size() > 300: _events.resize(300)
	if _event_log != null: _event_log.text = "\n".join(_events.slice(0, 60))


# ---- Operator actions for the device API (SimApi / MCP): the same things the panel buttons do ----

func api_camera(view: String) -> Dictionary:
	var wanted := view.to_lower()
	if wanted == "overview" or wanted == "city":
		_show_overview()
	elif wanted == "street":
		_show_street()
	elif wanted == "reset":
		_reset_camera()
	elif wanted.begins_with("cam-") and wanted.substr(4).is_valid_int() and int(wanted.substr(4)) >= 1 and int(wanted.substr(4)) <= 16:
		_open_camera_id("CAM-%02d" % int(wanted.substr(4)))
	else:
		return {"error": {"code": "bad_value", "message": "view must be CAM-01..CAM-16, overview, street, reset or city"}}
	return api_ui_state()


func api_follow(kind: String, target_id: String) -> Dictionary:
	if target_id.is_empty():
		if kind == "person":
			_follow_random_person()
		elif kind == "car":
			_follow_random_car()
		else:
			return {"error": {"code": "bad_value", "message": "follow needs kind 'person' or 'car' (random), or an 'id'"}}
		return api_ui_state()
	var target: Node3D = null
	var npc := _city.get_node_or_null("NpcAi")
	if npc != null and npc.has_method("get_agent_debug"):
		for value in npc.call("get_agent_debug") as Array:
			var state := value as Dictionary
			if str(state.get("citizen_id", "")) == target_id or str(state.get("npc_id", "")) == target_id:
				target = state.get("node") as Node3D
	if target == null:
		# Emergency units and garbage trucks are keyed by their unit id (FIRE-1, GRB-1) in CityLife, also while
		# garaged; every SUMO vehicle model carries its SUMO id and unit in metadata.
		var life := _city.get_node_or_null("CityLife")
		if life != null and life.has_method("get_keyed_vehicle"):
			target = life.call("get_keyed_vehicle", target_id) as Node3D
	if target == null:
		for group_name in ["vehicles", "emergency_vehicles", "backdrop_vehicles"]:
			for value in _city.get_tree().get_nodes_in_group(group_name):
				var node := value as Node3D
				if node == null or not node.visible:
					continue
				if node.name == target_id or str(node.get_meta("sumo_vehicle_id", "")) == target_id or str(node.get_meta("vehicle_unit", "")) == target_id \
						or str(node.get_meta("vehicle_id", "")) == target_id or str(node.get_meta("sumo_id", "")) == target_id:
					target = node
					break
			if target != null:
				break
	var city_camera := _city.get_node_or_null("EstablishingCamera") as Camera3D
	if target == null or city_camera == null or not bool(city_camera.call("follow_target", target)):
		return {"error": {"code": "unknown_target", "message": "'%s' is not a citizen or vehicle that can be followed right now" % target_id}}
	city_camera.current = true
	_camera_status.text = "Following %s" % target_id
	_add_event("Viewport following %s" % target_id)
	return api_ui_state()


func api_highlight(kind: String) -> Dictionary:
	if kind.is_empty() or kind == "clear":
		_clear_all_highlights()
		_add_event("Asset Map highlight cleared")
	elif kind in ["water_network", "power_network"]:
		var network_kind := kind.trim_suffix("_network")
		if _active_infrastructure_highlight != network_kind:
			_toggle_infrastructure_highlight(network_kind)
	elif ASSET_HIGHLIGHT_DEFINITIONS.has(kind):
		if _active_asset_highlight != kind:
			_toggle_asset_highlight(kind)
	else:
		return {"error": {"code": "bad_value", "message": "highlight kind must be water_network, power_network, one of: %s, or clear" % ", ".join(PackedStringArray(ASSET_HIGHLIGHT_DEFINITIONS.keys()))}}
	return api_ui_state()


func api_focus_device(device_id: String) -> Dictionary:
	var sensor := _sensor_by_id(device_id)
	if sensor.is_empty():
		return {"error":{"code":"unknown_device", "message":"no device with id '%s'" % device_id}}
	_select_sensor(device_id, true)
	var state := api_ui_state()
	state["focused_device"] = device_id
	return state


func api_set_view_only(enabled: bool) -> Dictionary:
	_view_only = enabled
	_apply_layout()
	_add_event("Viewport layout: %s" % ("full window" if enabled else "control room"))
	return api_ui_state()


func api_watch_incident(incident_id: String) -> Dictionary:
	if _incident_panel == null:
		return {"error":{"code":"unavailable", "message":"the incident panel is not attached"}}
	_incident_panel.call("_watch", incident_id)
	return api_ui_state()


func api_recharge() -> void:
	_recharge_utilities()


func api_event_log(limit: int) -> Array:
	return _events.slice(0, clampi(limit, 1, 300))


func api_ui_state() -> Dictionary:
	var city_camera := _city.get_node_or_null("EstablishingCamera") as Camera3D
	var highlight := _active_asset_highlight if not _active_asset_highlight.is_empty() else (_active_infrastructure_highlight + "_network" if not _active_infrastructure_highlight.is_empty() else "")
	var highlight_kinds: Array = ["water_network", "power_network"]
	highlight_kinds.append_array(ASSET_HIGHLIGHT_DEFINITIONS.keys())
	var replay_state := {"playing":false}
	var incident_manager := _city.get_node_or_null("Incidents")
	if incident_manager != null and incident_manager.get("replay") != null:
		var replay: Node = incident_manager.get("replay") as Node
		replay_state = {"playing":bool(replay.call("is_playing")), "paused":bool(replay.get("paused")), "speed":float(replay.get("playback_speed")),
			"time":float(replay.get("playback_time")), "duration":float(replay.call("duration")), "camera_mode":str(replay.get("camera_mode"))}
	return {"camera_status": _camera_status.text if _camera_status != null else "", "highlight": highlight,
		"highlight_kinds": highlight_kinds, "city_camera_active": city_camera != null and city_camera.current,
		"focus_view": _view_only, "replay":replay_state}
