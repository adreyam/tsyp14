extends SceneTree

func _init()->void: call_deferred("_render")
func _render()->void:
	root.size=Vector2i(1600,900)
	var room:Node=(load("res://scenes/ControlRoom.tscn") as PackedScene).instantiate(); root.add_child(room)
	for unused in 35: await process_frame
	var tabs:=room.find_child("ControlTabs",true,false) as TabContainer
	var names:=["operations","utilities","npc"]
	for index in 3:
		tabs.current_tab=index
		for unused in 5: await process_frame
		var result:=root.get_texture().get_image().save_png("/tmp/city_right_panel_fix69_%s.png"%names[index])
		print("RIGHT_PANEL_RENDER|",names[index],"|",result)
	quit(0)
