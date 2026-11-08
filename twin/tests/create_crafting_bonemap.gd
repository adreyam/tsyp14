extends SceneTree

const SOURCE_TO_PROFILE := {
	"B-root":"Root",
	"B-hips":"Hips",
	"B-spine":"Spine",
	"B-chest":"Chest",
	"B-neck":"Neck",
	"B-head":"Head",
	"B-shoulder.L":"LeftShoulder",
	"B-upperArm.L":"LeftUpperArm",
	"B-forearm.L":"LeftLowerArm",
	"B-hand.L":"LeftHand",
	"B-thigh.L":"LeftUpperLeg",
	"B-shin.L":"LeftLowerLeg",
	"B-foot.L":"LeftFoot",
	"B-toe.L":"LeftToes",
	"B-shoulder.R":"RightShoulder",
	"B-upperArm.R":"RightUpperArm",
	"B-forearm.R":"RightLowerArm",
	"B-hand.R":"RightHand",
	"B-thigh.R":"RightUpperLeg",
	"B-shin.R":"RightLowerLeg",
	"B-foot.R":"RightFoot",
	"B-toe.R":"RightToes",
}

const TARGET_TO_PROFILE := {
	"Root":"Root", "Body":"Hips", "Abdomen":"Spine", "Torso":"Chest",
	"Chest":"UpperChest", "Neck":"Neck", "Head":"Head",
	"Shoulder.L":"LeftShoulder", "UpperArm.L":"LeftUpperArm",
	"LowerArm.L":"LeftLowerArm", "Wrist.L":"LeftHand",
	"UpperLeg.L":"LeftUpperLeg", "LowerLeg.L":"LeftLowerLeg",
	"Foot.L":"LeftFoot", "PT.L":"LeftToes",
	"Shoulder.R":"RightShoulder", "UpperArm.R":"RightUpperArm",
	"LowerArm.R":"RightLowerArm", "Wrist.R":"RightHand",
	"UpperLeg.R":"RightUpperLeg", "LowerLeg.R":"RightLowerLeg",
	"Foot.R":"RightFoot", "PT.R":"RightToes",
}

func _init() -> void:
	var bone_map := BoneMap.new()
	bone_map.profile = SkeletonProfileHumanoid.new()
	for source_name in SOURCE_TO_PROFILE:
		bone_map.set_skeleton_bone_name(
			StringName(SOURCE_TO_PROFILE[source_name]),
			StringName(source_name)
		)
	var path := "res://assets/crafting_animations/crafting_humanoid_bone_map.tres"
	var error := ResourceSaver.save(bone_map, path)
	print("CRAFT_BONEMAP|", error, "|", path)
	var target_map := BoneMap.new()
	target_map.profile = SkeletonProfileHumanoid.new()
	for source_name in TARGET_TO_PROFILE:
		target_map.set_skeleton_bone_name(
			StringName(TARGET_TO_PROFILE[source_name]),
			StringName(source_name)
		)
	var target_path := "res://assets/crafting_animations/poly_pizza_humanoid_bone_map.tres"
	var target_error := ResourceSaver.save(target_map, target_path)
	print("TARGET_BONEMAP|", target_error, "|", target_path)
	quit(0 if error == OK and target_error == OK else 1)
