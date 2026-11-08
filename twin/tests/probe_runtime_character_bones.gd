extends SceneTree
func _init()->void: call_deferred("_run")
func _run()->void:
	var scene:Node3D=(load("res://assets/poly_pizza/city_pack/Worker.glb") as PackedScene).instantiate(); root.add_child(scene)
	await process_frame
	var skeleton:=scene.find_child("Skeleton3D",true,false) as Skeleton3D; var names:=[]
	for i in skeleton.get_bone_count(): names.append(str(skeleton.get_bone_name(i)))
	print("RUNTIME_BONES|",names)
	quit()
