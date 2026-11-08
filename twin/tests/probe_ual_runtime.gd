extends SceneTree
func _init() -> void: call_deferred("_run")
func _run() -> void:
	var document:=GLTFDocument.new(); var state:=GLTFState.new()
	var error:=document.append_from_file(ProjectSettings.globalize_path("res://assets/animation_library/ual2_standard.glb"),state)
	print("UAL_LOAD|",error)
	if error!=OK: quit(1); return
	var scene:=document.generate_scene(state)
	root.add_child(scene); await process_frame
	for value in scene.find_children("*","AnimationPlayer",true,false):
		var player:=value as AnimationPlayer; print("UAL_ANIMS|",player.get_animation_list()); var animation:=player.get_animation("Farm_Watering")
		for track in min(8,animation.get_track_count()): print("TRACK|",animation.track_get_path(track))
	for value in scene.find_children("*","Skeleton3D",true,false):
		var skeleton:=value as Skeleton3D; var names:=PackedStringArray()
		for index in skeleton.get_bone_count(): names.append(skeleton.get_bone_name(index))
		print("UAL_RIG|",names)
	quit()
