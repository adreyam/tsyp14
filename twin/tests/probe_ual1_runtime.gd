extends SceneTree

func _init()->void: call_deferred("_probe")
func _probe()->void:
	var scene:Node3D=(load("res://assets/animation_library/AnimationLibrary_Godot_Standard.glb") as PackedScene).instantiate(); root.add_child(scene); await process_frame
	for value in scene.find_children("*","AnimationPlayer",true,false):
		print("UAL1_ANIMS|",(value as AnimationPlayer).get_animation_list())
	quit()
