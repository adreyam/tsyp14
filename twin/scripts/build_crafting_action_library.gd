extends SceneTree

const CLIPS := {
	"Gather_Reach":"res://assets/crafting_animations/source/HumanM@Gathering01.fbx",
	"Gather_Low":"res://assets/crafting_animations/source/HumanM@Gathering02.fbx",
	"Gather_Pick":"res://assets/crafting_animations/source/HumanM@Gathering03.fbx",
	"Hammer_Ground_Begin":"res://assets/crafting_animations/source/HumanM@HammeringGround01_R - Begin.fbx",
	"Hammer_Ground":"res://assets/crafting_animations/source/HumanM@HammeringGround01_R - Loop.fbx",
	"Hammer_Ground_Stop":"res://assets/crafting_animations/source/HumanM@HammeringGround01_R - Stop.fbx",
	"Mine_Ground":"res://assets/crafting_animations/source/HumanM@MiningOneHand01_R - Ground.fbx",
	"Mine_Wall":"res://assets/crafting_animations/source/HumanM@MiningOneHand01_R - Wall.fbx",
	"Plow_Begin":"res://assets/crafting_animations/source/HumanM@FarmingWithPlow01_R - Begin.fbx",
	"Plow":"res://assets/crafting_animations/source/HumanM@FarmingWithPlow01_R - Loop.fbx",
	"Plow_Stop":"res://assets/crafting_animations/source/HumanM@FarmingWithPlow01_R - Stop.fbx",
}

const LOOP_CLIPS := [
	"Gather_Reach", "Gather_Low", "Gather_Pick", "Hammer_Ground",
	"Mine_Ground", "Mine_Wall", "Plow",
]

func _init() -> void:
	call_deferred("_build")

func _build() -> void:
	var library := AnimationLibrary.new()
	var failures := 0
	for output_name in CLIPS:
		var packed := load(CLIPS[output_name]) as PackedScene
		if packed == null:
			printerr("CRAFT_ACTION_MISSING|", output_name, "|", CLIPS[output_name])
			failures += 1
			continue
		var scene := packed.instantiate()
		root.add_child(scene)
		await process_frame
		var players := scene.find_children("*", "AnimationPlayer", true, false)
		if players.is_empty():
			printerr("CRAFT_ACTION_NO_PLAYER|", output_name)
			failures += 1
			scene.queue_free()
			await process_frame
			continue
		var player := players[0] as AnimationPlayer
		var source_names := player.get_animation_list()
		if source_names.is_empty():
			printerr("CRAFT_ACTION_NO_CLIP|", output_name)
			failures += 1
			scene.queue_free()
			await process_frame
			continue
		var animation := player.get_animation(source_names[0]).duplicate(true) as Animation
		for track in range(animation.get_track_count() - 1, -1, -1):
			if animation.track_get_type(track) == Animation.TYPE_SCALE_3D:
				animation.remove_track(track)
				continue
			var path_text := str(animation.track_get_path(track))
			if path_text.begins_with("%CraftingSkeleton:"):
				animation.track_set_path(track, NodePath(path_text.replace("%CraftingSkeleton:", "%GeneralSkeleton:")))
		animation.loop_mode = Animation.LOOP_LINEAR if LOOP_CLIPS.has(output_name) else Animation.LOOP_NONE
		library.add_animation(StringName(output_name), animation)
		print("CRAFT_ACTION|", output_name, "|seconds=", animation.length, "|tracks=", animation.get_track_count())
		scene.queue_free()
		await process_frame
	var output := "res://assets/crafting_animations/crafting_city_actions.tres"
	var save_error := ResourceSaver.save(library, output)
	print("CRAFT_ACTION_LIBRARY|", save_error, "|clips=", library.get_animation_list().size(), "|", output)
	quit(0 if save_error == OK and failures == 0 else 1)
