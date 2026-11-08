extends SceneTree
const SOURCES := [
	"res://assets/crafting_animations/source/HumanM@Gathering01.fbx",
	"res://assets/crafting_animations/source/HumanM@HammeringGround01_R - Loop.fbx",
	"res://assets/crafting_animations/source/HumanM@MiningOneHand01_R - Wall.fbx"
]
func _init()->void: call_deferred("_run")
func _run()->void:
	for path in SOURCES:
		var scene:Node3D=(load(path) as PackedScene).instantiate(); root.add_child(scene); await process_frame
		var skeleton:=scene.find_children("*","Skeleton3D",true,false)[0] as Skeleton3D
		var player:=scene.find_child("AnimationPlayer",true,false) as AnimationPlayer
		print("CRAFT_SOURCE|",path,"|skeleton_path=",scene.get_path_to(skeleton),"|anims=",player.get_animation_list(),"|bones=",Array(range(skeleton.get_bone_count())).map(func(i): return skeleton.get_bone_name(i)))
		if path.ends_with("Gathering01.fbx") or path.ends_with("Gathering02.fbx"):
			var animation:=player.get_animation(player.get_animation_list()[0])
			for track in animation.get_track_count(): print("TRACK|",animation.track_get_type(track),"|",animation.track_get_path(track))
		scene.queue_free(); await process_frame
	quit()
