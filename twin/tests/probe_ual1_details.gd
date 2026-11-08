extends SceneTree
func _init()->void: call_deferred("_run")
func _run()->void:
	var scene:Node3D=(load("res://assets/animation_library/AnimationLibrary_Godot_Standard.glb") as PackedScene).instantiate(); root.add_child(scene); await process_frame
	var skeleton:=scene.find_children("*","Skeleton3D",true,false)[0] as Skeleton3D
	var player:=scene.find_children("*","AnimationPlayer",true,false)[0] as AnimationPlayer
	var bones:=[]
	for i in skeleton.get_bone_count(): bones.append(skeleton.get_bone_name(i))
	print("UAL1_BONES|",bones)
	for wanted in ["Idle_Talking","Interact","PickUp_Table"]:
		var animation:=player.get_animation(wanted); var tracks:=[]
		for i in animation.get_track_count(): tracks.append(str(animation.track_get_path(i)))
		print("UAL1_TRACKS|",wanted,"|",tracks.slice(0,24))
	quit()
