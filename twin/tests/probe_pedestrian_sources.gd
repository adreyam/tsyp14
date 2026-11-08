extends SceneTree
func _init()->void: call_deferred("_run")
func _run()->void:
	var scene=(load("res://scenes/Pedestrians.tscn") as PackedScene).instantiate(); root.add_child(scene); await process_frame
	for person in get_nodes_in_group("pedestrians"): print("PED_SOURCE|",person.name,"|",(person as Node3D).scene_file_path)
	quit()
