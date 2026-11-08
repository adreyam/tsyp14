extends SceneTree

const MODEL_PATH := "res://assets/rocketbox/Delivery_Male_01/Export/Delivery_Male_01.fbx"
const WALK_PATH := "res://assets/rocketbox/animations/m_walk_neutral.max.fbx"
const IDLE_PATH := "res://assets/rocketbox/animations/m_idle_neutral_01.max.fbx"

func _initialize() -> void:
	for path in [MODEL_PATH, WALK_PATH, IDLE_PATH]:
		var packed := load(path) as PackedScene
		if packed == null:
			print("LOAD_FAIL|", path)
			continue
		var instance := packed.instantiate()
		root.add_child(instance)
		print("SCENE|", path, "|", instance.name)
		for skeleton_value in instance.find_children("*", "Skeleton3D", true, false):
			var skeleton := skeleton_value as Skeleton3D
			var names := PackedStringArray()
			for bone_index in skeleton.get_bone_count():
				names.append(skeleton.get_bone_name(bone_index))
			print("SKELETON|", path, "|", skeleton.get_path(), "|", skeleton.get_bone_count(), "|", ",".join(names))
		for player_value in instance.find_children("*", "AnimationPlayer", true, false):
			var player := player_value as AnimationPlayer
			for animation_name in player.get_animation_list():
				var animation := player.get_animation(animation_name)
				var tracks := PackedStringArray()
				for track_index in mini(animation.get_track_count(), 12):
					tracks.append(str(animation.track_get_path(track_index)))
				print("ANIMATION|", path, "|", player.get_path(), "|", animation_name, "|", animation.length, "|", animation.get_track_count(), "|", ",".join(tracks))
		instance.queue_free()
	quit()
