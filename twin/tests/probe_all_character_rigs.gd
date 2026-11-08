extends SceneTree

const TARGETS := {
	"business_man":"res://assets/poly_pizza/city_pack/Business Man.glb",
	"casual_character":"res://assets/poly_pizza/city_pack/Casual Character.glb",
	"punk":"res://assets/poly_pizza/city_pack/Punk.glb",
	"suit":"res://assets/poly_pizza/city_pack/Suit.glb",
	"worker":"res://assets/poly_pizza/city_pack/Worker.glb",
	"worker_alt":"res://assets/poly_pizza/city_pack/Worker-Yg2bQZO6Hj.glb",
	"adventurer":"res://assets/poly_pizza/city_props/Adventurer.glb",
	"woman_b":"res://assets/poly_pizza/city_props/Animated Woman-nIItLV9nxS.glb",
	"woman_c":"res://assets/poly_pizza/city_props/Animated Woman-qJ2gsTUBHL.glb",
}

func _init() -> void:
	call_deferred("_run")

func _run() -> void:
	for slug in TARGETS:
		var scene := (load(TARGETS[slug]) as PackedScene).instantiate()
		root.add_child(scene)
		await process_frame
		var skeletons := scene.find_children("*", "Skeleton3D", true, false)
		if skeletons.is_empty():
			print("RIG|", slug, "|NO_SKELETON")
		else:
			var skeleton := skeletons[0] as Skeleton3D
			print("RIG|", slug, "|path=", scene.get_path_to(skeleton), "|body=", skeleton.find_bone("Body"), "|hips=", skeleton.find_bone("Hips"), "|bones=", skeleton.get_bone_count())
		scene.queue_free()
		await process_frame
	quit()
