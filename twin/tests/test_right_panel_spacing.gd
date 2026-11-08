extends SceneTree

func _init()->void: call_deferred("_test")
func _fail(message:String)->void: printerr("RIGHT PANEL SPACING TEST FAILED: "+message); quit(1)
func _test()->void:
	var room:Node=(load("res://scenes/ControlRoom.tscn") as PackedScene).instantiate(); root.add_child(room)
	for unused in 8: await process_frame
	var tabs:=room.find_child("ControlTabs",true,false) as TabContainer
	if not is_equal_approx(float(room.get("_left_width")),220.0) or not is_equal_approx(float(room.get("_right_width")),380.0): _fail("sidebars did not start at their true minimum drag widths"); return
	for label_value in tabs.get_node("Operations").find_children("*","Label",true,false):
		if "LIVE SUMO THINGS" in (label_value as Label).text: _fail("removed live SUMO things section is still present"); return
	var specs:=[["Operations","OperationsContentFrame","OperationsContent"],["Utilities","UtilitiesContentFrame","UtilitiesContent"],["NPC Intent","NpcContentFrame","NpcReadableLayout"],["Sensors","SensorsContentFrame","SensorTelemetryLayout"]]
	for spec in specs:
		tabs.current_tab=tabs.get_tab_idx_from_control(tabs.get_node(str(spec[0]))); await process_frame
		var scroll:=tabs.get_node(str(spec[0])) as ScrollContainer; var frame:=scroll.find_child(str(spec[1]),true,false) as MarginContainer; var content:=scroll.find_child(str(spec[2]),true,false) as VBoxContainer
		if frame==null or content==null: _fail("%s is missing its common padded container"%spec[0]); return
		var available:=scroll.size.x-(scroll.get_v_scroll_bar().size.x if scroll.get_v_scroll_bar().visible else 0.0)
		if frame.size.x<available-7.0 or frame.size.x>available+1.0: _fail("%s container leaves an inconsistent outer gap"%spec[0]); return
		if content.size.x<frame.size.x-24.0 or content.size.x>frame.size.x-18.0: _fail("%s does not keep the common 10 px side padding (content %.1f, frame %.1f)"%[spec[0],content.size.x,frame.size.x]); return
		if content.size_flags_horizontal!=Control.SIZE_EXPAND_FILL: _fail("%s content is not responsive"%spec[0]); return
	var compact_sensor_scroll:=tabs.get_node("Sensors") as ScrollContainer
	var compact_sensor_layout:=compact_sensor_scroll.find_child("SensorTelemetryLayout",true,false) as VBoxContainer
	if compact_sensor_layout.global_position.x+compact_sensor_layout.size.x>compact_sensor_scroll.global_position.x+compact_sensor_scroll.size.x-8.0: _fail("Sensors content overflows the default compact rail"); return
	if not _sensor_descendants_fit(compact_sensor_scroll,compact_sensor_layout): return
	tabs.current_tab=tabs.get_tab_idx_from_control(tabs.get_node("NPC Intent")); await process_frame; await process_frame
	var society_card:=tabs.get_node("NPC Intent").find_child("NpcSummaryCard",true,false) as PanelContainer
	if society_card==null or society_card.size.y>360.0: _fail("Society summary still expands vertically because its status wraps into a narrow column"); return
	room.set("_right_width",430.0); room.call("_apply_layout")
	for unused in 5: await process_frame
	for spec in specs:
		tabs.current_tab=tabs.get_tab_idx_from_control(tabs.get_node(str(spec[0]))); await process_frame
		var scroll:=tabs.get_node(str(spec[0])) as ScrollContainer; var frame:=scroll.find_child(str(spec[1]),true,false) as MarginContainer
		var available:=scroll.size.x-(scroll.get_v_scroll_bar().size.x if scroll.get_v_scroll_bar().visible else 0.0)
		if frame.size.x<available-7.0: _fail("%s did not follow a dragged wider panel"%spec[0]); return
	var sensor_scroll:=tabs.get_node("Sensors") as ScrollContainer
	var sensor_layout:=sensor_scroll.find_child("SensorTelemetryLayout",true,false) as VBoxContainer
	if sensor_layout.global_position.x+sensor_layout.size.x>sensor_scroll.global_position.x+sensor_scroll.size.x-8.0:
		_fail("Sensors layout still overflows its rail: content end %.1f, rail end %.1f"%[sensor_layout.global_position.x+sensor_layout.size.x,sensor_scroll.global_position.x+sensor_scroll.size.x]); return
	if not _sensor_descendants_fit(sensor_scroll,sensor_layout): return
	var camera_grid:GridContainer=null
	for value in tabs.get_node("Operations").find_children("*","GridContainer",true,false):
		if (value as GridContainer).get_child_count()==16: camera_grid=value as GridContainer; break
	if camera_grid==null or camera_grid.columns!=4: _fail("container refactor changed the four-column camera layout"); return
	var water_grid:=tabs.get_node("Utilities").find_child("WaterZoneToggles",true,false) as GridContainer
	var power_grid:=tabs.get_node("Utilities").find_child("PowerZoneToggles",true,false) as GridContainer
	if water_grid==null or power_grid==null or water_grid.columns!=2 or power_grid.columns!=2: _fail("container refactor changed the two-column utility controls"); return
	print("RIGHT PANEL SPACING TEST PASSED: Society and Sensors remain compact inside responsive rails with preserved internal grids")
	quit()

func _sensor_descendants_fit(scroll:ScrollContainer,layout:VBoxContainer)->bool:
	var right_edge:=scroll.global_position.x+scroll.size.x-(scroll.get_v_scroll_bar().size.x if scroll.get_v_scroll_bar().visible else 0.0)-3.0
	for value in layout.find_children("*","Control",true,false):
		var control:=value as Control
		if not control.visible or control is VScrollBar or control is HScrollBar: continue
		var control_end:=control.global_position.x+control.size.x
		if control_end>right_edge+0.5:
			_fail("Sensors child %s overflows the compact rail: child end %.1f, usable end %.1f"%[control.name,control_end,right_edge])
			return false
	return true
