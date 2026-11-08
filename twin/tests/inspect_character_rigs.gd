extends SceneTree

func _initialize() -> void:
	for path in ["res://assets/poly_pizza/city_props/Animated Woman-nIItLV9nxS.glb", "res://assets/poly_pizza/city_props/Animated Woman-qJ2gsTUBHL.glb", "res://assets/poly_pizza/city_props/Adventurer.glb"]:
		var root := (load(path) as PackedScene).instantiate()
		get_root().add_child(root)
		await process_frame
		var skeleton := root.find_child("*", true, false) as Node
		for child in root.find_children("*", "Skeleton3D", true, false):
			var sk := child as Skeleton3D
			var bones: Array[String] = []
			for index in sk.get_bone_count(): bones.append(sk.get_bone_name(index))
			print("RIG|%s|%s" % [path, ",".join(bones)])
		for child in root.find_children("*", "AnimationPlayer", true, false):
			var player := child as AnimationPlayer
			print("ANIMS|%s|%s" % [path, ",".join(player.get_animation_list())])
		root.queue_free()
		await process_frame
	quit()
