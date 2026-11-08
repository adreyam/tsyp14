extends VBoxContainer

## "Incidents" tab of the unified control room: trigger, follow, control and
## replay incidents, and see the emergency fleet. Read-only towards the
## simulation except through bridge commands (BridgeLink), like every other
## operator control.

const RoadGeometry := preload("res://scripts/incidents/road_geometry.gd")
const CityZones := preload("res://scripts/city_zones.gd")
const CitySpatialSafety := preload("res://scripts/city_spatial_safety.gd")
const TEXT := Color("e5e5e5")
const MUTED := Color("c1c1c1")
const ACCENT := Color("6ea7cf")
const GREEN := Color("85c58b")
const AMBER := Color("e2b96d")
const RED := Color("df8585")
const INCIDENT_TYPES := [
	["rear_end", "Rear-end collision", "Distracted driver hits a car braking hard ahead"],
	["red_light_tbone", "Red-light collision", "Late red-light runner strikes a car pulling away on green"],
	["unsafe_overtake", "Unsafe overtake", "Overtaking with insufficient room; cut-in or head-on contact"],
	["breakdown", "Breakdown", "Engine failure; vehicle stopped in lane with hazards"],
	["vehicle_fire", "Vehicle fire", "Breakdown escalating into an engine-bay fire"],
	["lost_load", "Lost load", "Cargo spilled behind a stopped vehicle"],
	["building_fire", "Building fire", "Fire in a building facade (auto or picked building)"],
	["water_main_break", "Water main break", "Kerb-side burst main flooding the lane"],
	["signal_failure", "Signal failure", "Controller fault: junction goes to all-way stop"],
	["garbage_collection", "Garbage collection", "Refuse truck round: crew empties the street containers"],
]
const PHASE_COLOURS := {"arming": AMBER, "approach": AMBER, "coasting": AMBER, "sliding": RED, "responding": RED, "working": AMBER, "clearing": GREEN, "resolved": GREEN}

var city: Node3D
var camera: Camera3D
var manager: Node
var add_event: Callable
var style: Callable
var _status: Label
var _target_label: Label
var _cards: VBoxContainer
var _card_nodes: Dictionary = {}
var _fleet: RichTextLabel
var _replay_box: VBoxContainer
var _replay_label: Label
var _replay_slider: HSlider
var _use_selected_car := false
var _picked_building: Node3D
var _pending_triggers: Dictionary = {}
var _quake_zone: OptionButton
var _quake_magnitude: HSlider
var _quake_value: Label
var _quake_status: Label
var _watching := ""
var _watch_stage := ""
var _watch_node: Node3D = null
var _rng := RandomNumberGenerator.new()


func setup(city_node: Node3D, city_camera: Camera3D, event_logger: Callable, style_factory: Callable) -> void:
	city = city_node
	camera = city_camera
	add_event = event_logger
	style = style_factory
	manager = city.get_node_or_null("Incidents")
	_rng.seed = 4242
	name = "IncidentCommand"
	add_theme_constant_override("separation", 8)
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_caption("INCIDENT COMMAND")
	var help := _label("Trigger an incident: the camera follows it while units respond from their stations.", 11, MUTED)
	help.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	add_child(help)
	var grid := GridContainer.new()
	grid.columns = 2
	grid.add_theme_constant_override("h_separation", 6)
	grid.add_theme_constant_override("v_separation", 6)
	grid.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	add_child(grid)
	for entry in INCIDENT_TYPES:
		var button := Button.new()
		button.text = str(entry[1])
		button.tooltip_text = str(entry[2])
		button.custom_minimum_size = Vector2(0, 32)
		button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		button.add_theme_font_size_override("font_size", 12)
		button.pressed.connect(_trigger.bind(str(entry[0])))
		grid.add_child(button)
	var target_row := HBoxContainer.new()
	target_row.add_theme_constant_override("separation", 6)
	add_child(target_row)
	var car_toggle := Button.new()
	car_toggle.text = "Use selected car"
	car_toggle.toggle_mode = true
	car_toggle.tooltip_text = "Vehicle incidents target the car selected in the viewport (click a car first)"
	car_toggle.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	car_toggle.add_theme_font_size_override("font_size", 12)
	car_toggle.toggled.connect(func(on: bool) -> void: _use_selected_car = on; _refresh_target_label())
	target_row.add_child(car_toggle)
	var pick := Button.new()
	pick.text = "Pick building"
	pick.tooltip_text = "Click a building in the viewport to target the next building fire"
	pick.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	pick.add_theme_font_size_override("font_size", 12)
	pick.pressed.connect(_begin_building_pick)
	target_row.add_child(pick)
	_target_label = _label("Target: automatic", 11, MUTED)
	_target_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	add_child(_target_label)
	_status = _label("", 11, MUTED)
	_status.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	add_child(_status)
	_build_earthquake_controls()
	add_child(HSeparator.new())
	_caption("ACTIVE INCIDENTS")
	# Replay sits above the list; cards are newest first.
	_build_replay_deck()
	_cards = VBoxContainer.new()
	_cards.add_theme_constant_override("separation", 6)
	add_child(_cards)
	var resolve_all := Button.new()
	resolve_all.text = "RESOLVE ALL INCIDENTS"
	resolve_all.tooltip_text = "Fast-forwards every scene through its normal clearance (nothing is deleted in view)"
	resolve_all.custom_minimum_size = Vector2(0, 30)
	resolve_all.add_theme_font_size_override("font_size", 12)
	resolve_all.pressed.connect(func() -> void: _send({"action": "resolve_all"}); add_event.call("Operator: resolve all incidents"))
	add_child(resolve_all)
	add_child(HSeparator.new())
	_caption("EMERGENCY FLEET")
	_fleet = RichTextLabel.new()
	_fleet.bbcode_enabled = true
	_fleet.fit_content = true
	_fleet.scroll_active = false
	_fleet.add_theme_font_size_override("normal_font_size", 11)
	add_child(_fleet)
	if manager != null:
		manager.connect("incident_event", _on_incident_event)


func _build_earthquake_controls() -> void:
	add_child(HSeparator.new())
	_caption("EARTHQUAKE")
	var help := _label("Shake one zone or the whole city: seismic sensors record it and buildings in the area collapse. Clean up removes every trace.", 11, MUTED)
	help.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	add_child(help)
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 6)
	add_child(row)
	_quake_zone = OptionButton.new()
	_quake_zone.name = "EarthquakeZone"
	_quake_zone.add_item("Whole city")
	for zone in CityZones.IDS:
		_quake_zone.add_item("Zone %s" % zone)
	_quake_zone.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(_quake_zone)
	_quake_magnitude = HSlider.new()
	_quake_magnitude.name = "EarthquakeMagnitude"
	_quake_magnitude.min_value = 4.0
	_quake_magnitude.max_value = 8.0
	_quake_magnitude.step = 0.1
	_quake_magnitude.value = 6.4
	_quake_magnitude.custom_minimum_size = Vector2(110, 24)
	row.add_child(_quake_magnitude)
	_quake_value = _label("M 6.4", 12, TEXT)
	_quake_value.custom_minimum_size = Vector2(52, 0)
	row.add_child(_quake_value)
	_quake_magnitude.value_changed.connect(func(value: float) -> void: _quake_value.text = "M %.1f" % value)
	var buttons := HBoxContainer.new()
	buttons.add_theme_constant_override("separation", 6)
	add_child(buttons)
	var trigger := Button.new()
	trigger.name = "TriggerEarthquake"
	trigger.text = "Trigger earthquake"
	trigger.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	trigger.add_theme_font_size_override("font_size", 12)
	trigger.pressed.connect(_trigger_earthquake)
	buttons.add_child(trigger)
	var clean := Button.new()
	clean.name = "CleanUpEarthquakes"
	clean.text = "Clean up damage"
	clean.tooltip_text = "Instantly restores every collapsed building, as if the earthquakes never happened"
	clean.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	clean.add_theme_font_size_override("font_size", 12)
	clean.pressed.connect(_clean_up_earthquakes)
	buttons.add_child(clean)
	_quake_status = _label("No earthquake.", 11, MUTED)
	_quake_status.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	add_child(_quake_status)


func _earthquakes() -> Node:
	return city.get_node_or_null("Earthquakes") if city != null else null


func _trigger_earthquake() -> void:
	var system := _earthquakes()
	if system == null:
		return
	var index := _quake_zone.selected
	var zone := "ALL" if index <= 0 else str(CityZones.IDS[index - 1])
	var event := system.call("trigger", zone, _quake_magnitude.value) as Dictionary
	if event.has("error"):
		_set_status(str((event.error as Dictionary).message), RED)
		return
	add_event.call("Earthquake %s: magnitude %.1f in %s" % [event.id, float(event.magnitude), "the whole city" if zone == "ALL" else "zone %s" % zone])
	_set_status("Earthquake %s started: %d buildings will fall" % [event.id, int(event.buildings_planned)], AMBER)


func _clean_up_earthquakes() -> void:
	var system := _earthquakes()
	if system == null:
		return
	var result := system.call("clear_all") as Dictionary
	add_event.call("Earthquake damage cleaned up: %d buildings restored" % int(result.restored_buildings))
	_set_status("Cleaned up: %d earthquakes removed, %d buildings restored" % [int(result.cleared_events), int(result.restored_buildings)], GREEN)


func _refresh_earthquake_status() -> void:
	var system := _earthquakes()
	if system == null or _quake_status == null:
		return
	var parts: PackedStringArray = []
	for event_value in system.call("public_events") as Array:
		var event := event_value as Dictionary
		parts.append("%s  M%.1f  %s  %s  %d/%d collapsed" % [event.id, float(event.magnitude), "city" if event.zone == "ALL" else "zone %s" % event.zone, str(event.status), int(event.buildings_collapsed), int(event.buildings_planned)])
	_quake_status.text = "\n".join(parts) if not parts.is_empty() else "No earthquake."


func _caption(text_value: String) -> void:
	add_child(_label(text_value, 13, ACCENT))


func _label(text_value: String, size_value: int, colour: Color) -> Label:
	var label := Label.new()
	label.text = text_value
	label.add_theme_font_size_override("font_size", size_value)
	label.add_theme_color_override("font_color", colour)
	return label


func _send(payload: Dictionary) -> String:
	if manager == null:
		return ""
	return str(manager.link.call("send", payload))


# --- Triggers --------------------------------------------------------------------

func _trigger(kind: String) -> void:
	var params := {}
	if _use_selected_car and kind in ["rear_end", "breakdown", "vehicle_fire", "lost_load"]:
		var selected := camera.call("get_selected_target") as Node3D if camera != null else null
		var vehicle_id := str(selected.get_meta("sumo_vehicle_id", "")) if selected != null else ""
		if vehicle_id.is_empty():
			_set_status("Select a car in the viewport first (or untoggle 'Use selected car').", RED)
			return
		params["vehicle"] = vehicle_id
	if kind == "building_fire":
		var building := _picked_building if _picked_building != null else _random_building()
		if building == null:
			_set_status("No building with road access found.", RED)
			return
		params["building"] = _building_address(building)
		_picked_building = null
		_refresh_target_label()
	var request := _send({"action": "trigger", "type": kind, "params": params})
	_pending_triggers[request] = kind
	_set_status("Requested %s…" % kind.replace("_", " "), AMBER)
	add_event.call("Operator triggered %s" % kind.replace("_", " "))


func track_request(request: String, kind: String) -> void:
	## A trigger sent by someone else (the device API: a Brain scenario): once the bridge opens it, the
	## viewport watches it as it does for the panel's own buttons.
	if request.is_empty():
		return
	_pending_triggers[request] = kind
	add_event.call("Device API triggered %s" % kind.replace("_", " "))


func _begin_building_pick() -> void:
	if camera == null:
		return
	camera.set("building_pick_callback", func(building: Node3D) -> void:
		_picked_building = building
		_refresh_target_label()
		add_event.call("Building targeted: %s" % building.name))
	_set_status("Click a building in the viewport…", AMBER)


func _refresh_target_label() -> void:
	var parts: Array[String] = []
	parts.append("vehicle: selected car" if _use_selected_car else "vehicle: automatic")
	parts.append("building: %s" % (_picked_building.name if _picked_building != null else "automatic"))
	_target_label.text = "Target — " + "  ·  ".join(parts)


func _random_building() -> Node3D:
	var candidates: Array[Node3D] = []
	for value in city.get_tree().get_nodes_in_group("buildings"):
		var building := value as Node3D
		if building == null or not building.is_visible_in_tree() or building.is_in_group("hospitals") or building.is_in_group("police_facilities"):
			continue
		var address := _building_address(building)
		if not address.is_empty() and float(address.get("distance", 99.0)) < 22.0:
			candidates.append(building)
	return candidates[_rng.randi() % candidates.size()] if not candidates.is_empty() else null


func _building_address(building: Node3D) -> Dictionary:
	## Nearest road edge whose kerb side faces the building, and the lane
	## position abreast of it (where apparatus park).
	var bounds := CitySpatialSafety.world_bounds(building)
	var centre := bounds.get_center()
	var best := {}
	var best_distance := INF
	for index in 9:
		var road := float(index - 4) * 95.0
		# Vertical road at x = road (travel along z).
		var gap_x := absf(centre.x - road) - bounds.size.x * 0.5
		if gap_x < best_distance and absf(centre.z) < 380.0:
			var row := clampi(int(floor((centre.z + 380.0) / 95.0)) + 1, 1, 8)
			var southbound := centre.x > road
			var column := index + 1
			var edge := "E_r%dc%d_r%dc%d" % [row, column, row + 1, column] if southbound else "E_r%dc%d_r%dc%d" % [row + 1, column, row, column]
			best_distance = gap_x
			best = {"edge": edge, "distance": gap_x}
		# Horizontal road at z = road (travel along x).
		var gap_z := absf(centre.z - road) - bounds.size.z * 0.5
		if gap_z < best_distance and absf(centre.x) < 380.0:
			var column := clampi(int(floor((centre.x + 380.0) / 95.0)) + 1, 1, 8)
			var westbound := centre.z > road
			var row := index + 1
			var edge := "E_r%dc%d_r%dc%d" % [row, column + 1, row, column] if westbound else "E_r%dc%d_r%dc%d" % [row, column, row, column + 1]
			best_distance = gap_z
			best = {"edge": edge, "distance": gap_z}
	if best.is_empty():
		return {}
	var edge_data := RoadGeometry.parse_edge(str(best.edge))
	if edge_data.is_empty() or (centre - (edge_data.start as Vector3)).dot(edge_data.right as Vector3) <= 0.0:
		return {}
	var pos := clampf(RoadGeometry.lane_position_of(edge_data, centre), 16.0, 52.0)
	return {"edge": str(best.edge), "pos": pos, "x": centre.x, "z": centre.z, "node": str(building.get_path()), "path": building.name, "distance": float(best.distance)}


# --- Incident cards -----------------------------------------------------------------

func refresh() -> void:
	_refresh_earthquake_status()
	if manager == null:
		return
	var state := manager.get("last_state") as Dictionary
	for request in _pending_triggers.keys():
		var result := str(manager.link.call("result_of", request))
		if not result.is_empty():
			var kind := str(_pending_triggers[request])
			_pending_triggers.erase(request)
			if result.begins_with("INC-"):
				_set_status("%s opened as %s" % [kind.replace("_", " ").capitalize(), result], GREEN)
				# A freshly triggered incident takes the viewport straight away.
				_watch(result)
			else:
				_set_status("Trigger failed: %s" % result, RED)
	var active: Dictionary = {}
	for record_value in state.get("incidents", []):
		var record := record_value as Dictionary
		var incident_id := str(record.id)
		active[incident_id] = true
		if not _card_nodes.has(incident_id):
			_card_nodes[incident_id] = _make_card(incident_id)
		_update_card(_card_nodes[incident_id] as Dictionary, record, float(state.get("time", 0.0)))
	for incident_id in _card_nodes.keys():
		if not active.has(incident_id):
			((_card_nodes[incident_id] as Dictionary).panel as Control).queue_free()
			_card_nodes.erase(incident_id)
	if _cards.get_child_count() == 0 or active.is_empty():
		if _cards.get_node_or_null("Empty") == null:
			var empty := _label("No active incidents.", 11, MUTED)
			empty.name = "Empty"
			_cards.add_child(empty)
	elif _cards.get_node_or_null("Empty") != null:
		_cards.get_node("Empty").queue_free()
	_refresh_fleet(state)
	_refresh_replay()
	_track_watch(state)


func _make_card(incident_id: String) -> Dictionary:
	## Compact: what, how far along, the latest development, and the actions.
	var panel := PanelContainer.new()
	panel.add_theme_stylebox_override("panel", style.call(Color("26262c"), Color("4a4a52"), 1, 4))
	_cards.add_child(panel)
	_cards.move_child(panel, 0)
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 3)
	panel.add_child(box)
	var header := HBoxContainer.new()
	box.add_child(header)
	var title := _label(incident_id, 13, TEXT)
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	title.clip_text = true
	header.add_child(title)
	var age := _label("", 11, MUTED)
	header.add_child(age)
	var phase := _label("", 11, AMBER)
	box.add_child(phase)
	var latest := _label("", 10, MUTED)
	latest.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(latest)
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 4)
	box.add_child(row)
	var buttons := {}
	for entry in [["Watch", _watch], ["Replay", _replay], ["Resolve", _resolve]]:
		var button := Button.new()
		button.text = str(entry[0])
		button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		button.add_theme_font_size_override("font_size", 11)
		button.pressed.connect((entry[1] as Callable).bind(incident_id))
		row.add_child(button)
		buttons[str(entry[0])] = button
	var extra := MenuButton.new()
	extra.text = "+ Unit"
	extra.tooltip_text = "Dispatch an additional unit to %s" % incident_id
	extra.flat = false
	extra.add_theme_font_size_override("font_size", 11)
	var kinds := ["police", "ambulance", "fire", "tow"]
	for kind in kinds:
		extra.get_popup().add_item(kind.capitalize())
	extra.get_popup().id_pressed.connect(func(index: int) -> void:
		var kind := str(kinds[index])
		_send({"action": "dispatch_extra", "incident": incident_id, "kind": kind})
		add_event.call("Operator: extra %s → %s" % [kind, incident_id]))
	row.add_child(extra)
	return {"panel": panel, "title": title, "age": age, "phase": phase, "latest": latest, "buttons": buttons}


func _update_card(card: Dictionary, record: Dictionary, now: float) -> void:
	(card.title as Label).text = str(record.title)
	(card.title as Label).tooltip_text = str(record.id)
	(card.age as Label).text = "%d s" % int(maxf(0.0, now - float(record.get("created", now))))
	var units := record.get("units", []) as Array
	var on_scene := units.filter(func(unit: Dictionary) -> bool: return str(unit.get("status", "")) == "on_scene").size()
	var phase_text := str(record.phase).replace("_", " ").capitalize()
	var progress := "units %d/%d on scene" % [on_scene, units.size()] if not units.is_empty() else "awaiting dispatch"
	(card.phase as Label).text = "%s  ·  %s" % [phase_text, progress]
	(card.phase as Label).add_theme_color_override("font_color", PHASE_COLOURS.get(str(record.phase), AMBER))
	var timeline := record.get("timeline", []) as Array
	(card.latest as Label).text = str((timeline[-1] as Dictionary).text) if not timeline.is_empty() else ""
	((card.buttons as Dictionary).Replay as Button).disabled = not bool(manager.replay.call("has_clip", str(record.id)))


func _watch(incident_id: String) -> void:
	_watching = incident_id
	_watch_stage = ""
	add_event.call("Viewport watching %s" % incident_id)
	# Act on the latest known state now, not at the next panel refresh.
	if manager != null:
		_track_watch(manager.get("last_state") as Dictionary)


func _track_watch(state: Dictionary) -> void:
	## Three stages: while the incident is still arming the camera frames its
	## location at once; before the collision it rides behind the striking
	## car; once the scene exists it frames it once (MMB orbit stays free).
	if _watching.is_empty() or camera == null or bool(manager.replay.call("is_playing")):
		return
	for record_value in state.get("incidents", []):
		var record := record_value as Dictionary
		if str(record.id) != _watching:
			continue
		var participants := record.get("participants", []) as Array
		var location := record.get("location", {}) as Dictionary
		if str(record.get("kind", "")) == "garbage_collection":
			# A round has no single scene: ride with the truck from stop to stop.
			var units := record.get("units", []) as Array
			var truck := city.get_node("CityLife").call("get_keyed_vehicle", str((units[0] as Dictionary).unit)) as Node3D if not units.is_empty() and bool(city.get_node("CityLife").call("has_keyed_vehicle", str((units[0] as Dictionary).unit))) else null
			# Re-follow when the truck's model changes (garage -> road -> garage hand-overs re-key it).
			if truck != null and (_watch_stage != "truck" or truck != _watch_node) and bool(camera.call("follow_target", truck)):
				camera.current = true
				_watch_stage = "truck"
				_watch_node = truck
			elif truck == null and _watch_stage == "" and location.has("x"):
				camera.call("watch_point", _watch_target(location), 22.0, null, Vector3.ZERO)
				camera.current = true
				_watch_stage = "arming"
			return
		var pre_onset := str(record.phase) in ["arming", "approach", "coasting"] and record.get("impact", null) == null
		var chase_node: Node3D = null
		if pre_onset and not participants.is_empty():
			chase_node = city.get_node("CityLife").call("get_keyed_vehicle", str((participants[0] as Dictionary).id)) as Node3D
		if pre_onset and chase_node != null and _watch_stage in ["", "arming"]:
			if bool(camera.call("follow_target", chase_node)):
				camera.current = true
				_watch_stage = "chase"
		elif pre_onset and _watch_stage == "" and location.has("x"):
			camera.call("watch_point", _watch_target(location), 22.0, null, Vector3.ZERO)
			camera.current = true
			_watch_stage = "arming"
		elif not pre_onset and location.has("x") and _watch_stage != "framed":
			var building_info := ((record.get("scene", {}) as Dictionary).get("building", {})) as Dictionary
			var subject := get_tree().root.get_node_or_null(str(building_info.get("node", ""))) as Node3D
			var facing := Vector3.ZERO
			if subject != null and location.has("edge"):
				var edge := RoadGeometry.parse_edge(str(location.edge))
				if not edge.is_empty():
					var street := RoadGeometry.lane_point(edge, float(location.get("pos", 40.0)), 0.0)
					facing = street - Vector3(float(location.x), 0.0, float(location.z))
			camera.call("watch_point", _watch_target(location), 22.0, subject, facing)
			camera.current = true
			_watch_stage = "framed"
		return
	_watching = ""


func _watch_target(location: Dictionary) -> Vector3:
	var point := Vector3(float(location.x), 0.5, float(location.z))
	if location.has("building") and location.has("edge"):
		# A building's centre is inside it: frame the burning façade from the
		# street instead.
		var edge := RoadGeometry.parse_edge(str(location.edge))
		if not edge.is_empty():
			var street := RoadGeometry.lane_point(edge, float(location.get("pos", 40.0)), 0.0)
			point = street.lerp(Vector3(point.x, 0.0, point.z), 0.35)
			point.y = 3.0
	return point


func _resolve(incident_id: String) -> void:
	_send({"action": "resolve", "incident": incident_id})
	add_event.call("Operator: resolve %s" % incident_id)


# --- Replay deck ---------------------------------------------------------------------------

func _build_replay_deck() -> void:
	_replay_box = VBoxContainer.new()
	_replay_box.add_theme_constant_override("separation", 5)
	_replay_box.visible = false
	add_child(_replay_box)
	_replay_box.add_child(_label("COLLISION REPLAY", 13, ACCENT))
	_replay_label = _label("", 11, TEXT)
	_replay_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_replay_box.add_child(_replay_label)
	_replay_slider = HSlider.new()
	_replay_slider.min_value = 0.0
	_replay_slider.step = 0.05
	_replay_slider.value_changed.connect(func(value: float) -> void:
		if bool(manager.replay.call("is_playing")) and absf(value - float(manager.replay.get("playback_time"))) > 0.2:
			manager.replay.call("seek", value))
	_replay_box.add_child(_replay_slider)
	var controls := HBoxContainer.new()
	controls.add_theme_constant_override("separation", 4)
	_replay_box.add_child(controls)
	for entry in [["⏯", func() -> void: manager.replay.set("paused", not bool(manager.replay.get("paused")))], ["⏮", func() -> void: manager.replay.call("seek", 0.0); manager.replay.set("paused", false)], ["×0.25", func() -> void: manager.replay.set("playback_speed", 0.25)], ["×0.5", func() -> void: manager.replay.set("playback_speed", 0.5)], ["×1", func() -> void: manager.replay.set("playback_speed", 1.0)], ["Close", func() -> void: manager.replay.call("stop")]]:
		var button := Button.new()
		button.text = str(entry[0])
		button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		button.add_theme_font_size_override("font_size", 11)
		button.pressed.connect(entry[1] as Callable)
		controls.add_child(button)
	var cameras := HBoxContainer.new()
	cameras.add_theme_constant_override("separation", 4)
	_replay_box.add_child(cameras)
	for mode in ["orbit", "chase", "driver", "street", "overhead"]:
		var button := Button.new()
		button.text = mode.capitalize()
		button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		button.add_theme_font_size_override("font_size", 10)
		button.pressed.connect(func() -> void: manager.replay.set("camera_mode", mode))
		cameras.add_child(button)


func _replay(incident_id: String) -> void:
	var viewport_camera := city.get_viewport().get_camera_3d()
	if bool(manager.replay.call("play", incident_id, viewport_camera)):
		_watching = ""
		add_event.call("Replaying %s" % incident_id)
	else:
		_set_status("No recorded collision for %s yet." % incident_id, RED)


func _refresh_replay() -> void:
	var playing := bool(manager.replay.call("is_playing"))
	_replay_box.visible = playing
	if playing:
		_replay_slider.max_value = float(manager.replay.call("duration"))
		_replay_slider.set_value_no_signal(float(manager.replay.get("playback_time")))
		_replay_label.text = str(manager.replay.call("overlay_text"))


# --- Fleet and events --------------------------------------------------------------------

func _refresh_fleet(state: Dictionary) -> void:
	var rows: Array[String] = []
	for record_value in state.get("fleet", []):
		var record := record_value as Dictionary
		var status := str(record.status)
		var colour := "#85c58b" if status in ["garaged", "patrol"] else ("#e2b96d" if status in ["mobilizing", "inserting", "returning", "entering_depot"] else "#df8585")
		rows.append("[color=#dce7f2]%s[/color]  %s  [color=%s]%s[/color]%s" % [str(record.unit), str(record.depot).replace("-", " ").to_lower(), colour, status.replace("_", " "), ("  → " + str(record.incident)) if not str(record.get("incident", "")).is_empty() else ""])
	_fleet.text = "\n".join(rows)


func _on_incident_event(event: Dictionary) -> void:
	var kind := str(event.get("kind", ""))
	match kind:
		"incident_note":
			add_event.call("[%s] %s" % [str(event.get("incident", "")), str(event.get("text", ""))])
		"impact":
			add_event.call("[%s] IMPACT — severity %s, Δv %.1f m/s" % [str(event.get("incident", "")), str(event.get("severity", "")), float(event.get("delta_v", 0.0))])


func _set_status(text_value: String, colour: Color) -> void:
	_status.text = text_value
	_status.add_theme_color_override("font_color", colour)
