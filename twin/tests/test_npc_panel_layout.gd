extends SceneTree

func _init()->void: call_deferred("_test")
func _fail(message:String)->void: printerr("NPC PANEL LAYOUT TEST FAILED: "+message); quit(1)
func _test()->void:
	var control_room:Node=(load("res://scenes/ControlRoom.tscn") as PackedScene).instantiate(); root.add_child(control_room)
	for unused in 6: await process_frame
	var tabs:=control_room.find_child("ControlTabs",true,false) as TabContainer
	var npc_scroll:=tabs.get_node("NPC Intent") as ScrollContainer
	var frame:=npc_scroll.find_child("NpcContentFrame",true,false) as MarginContainer
	var layout:=npc_scroll.find_child("NpcReadableLayout",true,false) as VBoxContainer
	if frame==null or layout==null or layout.size.x<frame.size.x-24.0: _fail("NPC content does not fill its padded panel container"); return
	for card_name in ["NpcSummaryCard"]:
		var card:=layout.find_child(card_name,true,false) as PanelContainer
		if card==null or card.size.x<layout.size.x*0.94: _fail("%s is still a narrow text-width card"%card_name); return
	for disclosure_name in ["Toggle_NpcMovementDetails"]:
		var disclosure:=layout.find_child(disclosure_name,true,false) as Button
		if disclosure==null or disclosure.size.x<layout.size.x*0.94: _fail("%s does not span the panel"%disclosure_name); return
	var city:=control_room.get("_city") as Node3D; var camera:=city.get_node("EstablishingCamera") as Camera3D
	var target:Node3D=null
	for value in city.get_tree().get_nodes_in_group("pedestrians"):
		target=value as Node3D
		if target!=null: break
	if target==null: _fail("no active NPC was available"); return
	camera.call("_select_target",target,true); await process_frame
	var name_label:=control_room.get("_npc_selection_label") as Label
	var character_label:=control_room.get("_npc_character_label") as Label
	var personality_label:=control_room.get("_npc_personality_label") as Label
	var now_label:=control_room.get("_npc_now_label") as Label
	var target_label:=control_room.get("_npc_now_context_label") as Label
	var status_label:=control_room.get("_npc_status_label") as Label
	var partner_label:=control_room.get("_npc_partner_label") as Label
	var activity_label:=control_room.get("_npc_activity_label") as Label
	var objective_label:=control_room.get("_npc_objective_label") as Label
	var intent_label:=control_room.get("_npc_intent_label") as Label
	if name_label==null or character_label==null or personality_label==null or now_label==null or status_label==null or partner_label==null or activity_label==null or objective_label==null or intent_label==null: _fail("primary summary hierarchy is incomplete"); return
	for label in [name_label,character_label,personality_label,now_label,status_label,partner_label,activity_label,objective_label,intent_label]:
		if not label.visible or label.text.strip_edges().is_empty() or label.text=="—": _fail("a primary summary field is hidden or empty"); return
	if character_label.text.contains(":") or personality_label.text.contains(":") or now_label.text.contains(":") or intent_label.text.contains(":"): _fail("summary values still hide facts inside labelled sentences"); return
	if now_label.text.split(" ").size()>7 or intent_label.text.split(" ").size()>10: _fail("WHAT or WHY is still an explanatory paragraph instead of an extracted value"); return
	if target_label.get_parent().visible: _fail("paired interaction repeats the partner as a duplicate target field"); return
	var fact_keys:={}
	for label_value in layout.find_children("*","Label",true,false): fact_keys[(label_value as Label).text]=true
	for required_key in ["Doing","At","With","Together","Goal","Why"]:
		if not fact_keys.has(required_key): _fail("missing explicit %s field"%required_key); return
	var npc_ai:=city.get_node("NpcAi") as Node; var solo_target:Node3D=null
	for candidate_value in city.get_tree().get_nodes_in_group("pedestrians"):
		var candidate:=candidate_value as Node3D; var candidate_state:=npc_ai.call("get_agent_state",candidate) as Dictionary
		if not candidate_state.is_empty() and str(candidate_state.get("interaction_id","")).is_empty(): solo_target=candidate; break
	if solo_target==null: _fail("no individual worker was available for field-visibility validation"); return
	camera.call("_select_target",solo_target,true); await process_frame
	if not target_label.get_parent().visible or partner_label.get_parent().visible or activity_label.get_parent().visible: _fail("individual work does not replace partner fields with its actual target"); return
	var bodies:=control_room.get("_npc_detail_bodies") as Array
	if bodies.size()!=1: _fail("expected one collapsed technical details section, got %d"%bodies.size()); return
	for body_value in bodies:
		if (body_value as Control).visible: _fail("technical details are expanded by default"); return
	var movement:=control_room.find_child("NpcMovementDetails",true,false) as PanelContainer
	var toggle:=control_room.find_child("Toggle_NpcMovementDetails",true,false) as Button
	if movement==null or toggle==null: _fail("movement disclosure control is missing"); return
	toggle.emit_signal("toggled",true); await process_frame
	if not movement.visible or not toggle.text.begins_with("▼"): _fail("movement disclosure did not expand from its button"); return
	toggle.emit_signal("toggled",false); await process_frame
	if movement.visible or not toggle.text.begins_with("▶"): _fail("movement disclosure did not collapse from the same button"); return
	print("NPC PANEL LAYOUT TEST PASSED: one compact card keeps who, doing and why visible; technical details stay in one collapsible section")
	quit()
