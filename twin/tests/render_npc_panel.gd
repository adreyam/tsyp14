extends SceneTree

func _init()->void: call_deferred("_render")
func _render()->void:
	root.size=Vector2i(1600,900)
	var control_room:Node=(load("res://scenes/ControlRoom.tscn") as PackedScene).instantiate(); root.add_child(control_room)
	for unused in 20: await process_frame
	var tabs:=control_room.find_child("ControlTabs",true,false) as TabContainer; tabs.current_tab=int(control_room.get("_npc_tab_index"))
	for unused in 4: await process_frame
	var empty_result:=root.get_texture().get_image().save_png("/tmp/city_npc_panel_fix68_empty.png")
	var city:=control_room.get("_city") as Node3D; var target:Node3D=null
	for value in city.get_tree().get_nodes_in_group("pedestrians"):
		var state:=city.get_node("NpcAi").call("get_agent_state",value as Node3D) as Dictionary
		if not str(state.get("interaction_id","")).is_empty(): target=value as Node3D; break
	if target==null: quit(1); return
	(city.get_node("EstablishingCamera") as Camera3D).call("_select_target",target,true)
	for unused in 8: await process_frame
	var result:=root.get_texture().get_image().save_png("/tmp/city_npc_panel_fix68_selected.png")
	print("NPC_PANEL_RENDER|",empty_result,"|",result,"|",target.name); quit(0 if result==OK and empty_result==OK else 1)
