extends SceneTree
func _init()->void: call_deferred("_run")
func _run()->void:
	var doc:=GLTFDocument.new(); var state:=GLTFState.new(); doc.append_from_file(ProjectSettings.globalize_path("res://assets/animation_library/ual2_standard.glb"),state)
	var scene:=doc.generate_scene(state); root.add_child(scene); await process_frame
	var player:=scene.find_children("*","AnimationPlayer",true,false)[0] as AnimationPlayer
	print("UAL2_RAW_NAMES|",player.get_animation_list()); quit()
