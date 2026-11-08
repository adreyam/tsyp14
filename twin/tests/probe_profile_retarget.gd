extends SceneTree

const SOURCE_MAP := {"root":"Root","pelvis":"Hips","spine_01":"Spine","spine_02":"Chest","spine_03":"UpperChest","neck_01":"Neck","Head":"Head","clavicle_l":"LeftShoulder","upperarm_l":"LeftUpperArm","lowerarm_l":"LeftLowerArm","hand_l":"LeftHand","thigh_l":"LeftUpperLeg","calf_l":"LeftLowerLeg","foot_l":"LeftFoot","ball_l":"LeftToes","clavicle_r":"RightShoulder","upperarm_r":"RightUpperArm","lowerarm_r":"RightLowerArm","hand_r":"RightHand","thigh_r":"RightUpperLeg","calf_r":"RightLowerLeg","foot_r":"RightFoot","ball_r":"RightToes"}
const TARGET_MAP := {"Root":"Root","Hips":"Hips","Abdomen":"Spine","Torso":"Chest","Chest":"UpperChest","Neck":"Neck","Head":"Head","Shoulder.L":"LeftShoulder","UpperArm.L":"LeftUpperArm","LowerArm.L":"LeftLowerArm","Wrist.L":"LeftHand","UpperLeg.L":"LeftUpperLeg","LowerLeg.L":"LeftLowerLeg","Foot.L":"LeftFoot","PT.L":"LeftToes","Shoulder.R":"RightShoulder","UpperArm.R":"RightUpperArm","LowerArm.R":"RightLowerArm","Wrist.R":"RightHand","UpperLeg.R":"RightUpperLeg","LowerLeg.R":"RightLowerLeg","Foot.R":"RightFoot","PT.R":"RightToes"}

func _init() -> void: call_deferred("_run")
func _run() -> void:
	var doc:=GLTFDocument.new(); var state:=GLTFState.new(); var error:=doc.append_from_file(ProjectSettings.globalize_path("res://assets/animation_library/ual2_standard.glb"),state)
	if error!=OK: printerr("load failed ",error); quit(1); return
	var source:=doc.generate_scene(state); root.add_child(source)
	var target:=(load("res://assets/poly_pizza/city_pack/Worker.glb") as PackedScene).instantiate(); root.add_child(target)
	await process_frame
	var source_skeleton:=source.find_children("*","Skeleton3D",true,false)[0] as Skeleton3D
	var target_skeleton:=target.find_children("*","Skeleton3D",true,false)[0] as Skeleton3D
	var player:=source.find_children("*","AnimationPlayer",true,false)[0] as AnimationPlayer
	_rewrite_bone_tracks(player,SOURCE_MAP); _rename_two_pass(source_skeleton,SOURCE_MAP); _rename_two_pass(target_skeleton,TARGET_MAP)
	var modifier:=RetargetModifier3D.new(); modifier.name="ProfileRetarget"; modifier.profile=SkeletonProfileHumanoid.new(); modifier.use_global_pose=false; modifier.set_position_enabled(false); modifier.set_scale_enabled(false)
	source_skeleton.add_child(modifier); target_skeleton.reparent(modifier,false)
	player.play("Farm_Watering"); player.seek(0.0,true); await process_frame; await process_frame
	var source_arm:=source_skeleton.get_bone_pose_rotation(source_skeleton.find_bone("LeftUpperArm")); var target_arm0:=target_skeleton.get_bone_pose_rotation(target_skeleton.find_bone("LeftUpperArm")); var lower0:=source_skeleton.get_bone_pose_rotation(source_skeleton.find_bone("LeftLowerArm")); var target_lower0:=target_skeleton.get_bone_pose_rotation(target_skeleton.find_bone("LeftLowerArm"))
	player.seek(1.2,true); await process_frame; await process_frame
	var target_arm1:=target_skeleton.get_bone_pose_rotation(target_skeleton.find_bone("LeftUpperArm")); var source_arm1:=source_skeleton.get_bone_pose_rotation(source_skeleton.find_bone("LeftUpperArm")); var lower1:=source_skeleton.get_bone_pose_rotation(source_skeleton.find_bone("LeftLowerArm")); var target_lower1:=target_skeleton.get_bone_pose_rotation(target_skeleton.find_bone("LeftLowerArm"))
	var changed:=0
	for index in target_skeleton.get_bone_count():
		if target_skeleton.get_bone_pose_rotation(index).get_angle()>0.01: changed+=1
	print("PROFILE_RETARGET|changed_bones=",changed," source0=",source_arm," source1=",source_arm1," lower0=",lower0," lower1=",lower1," target0=",target_arm0," target1=",target_arm1," target_lower0=",target_lower0," target_lower1=",target_lower1)
	quit(0 if changed>8 else 1)
func _rename_two_pass(skeleton:Skeleton3D,mapping:Dictionary)->void:
	var indices:={}
	for old_name in mapping:
		var index:=skeleton.find_bone(str(old_name))
		if index>=0: indices[old_name]=index; skeleton.set_bone_name(index,"__retarget_%d"%index)
	for old_name in indices: skeleton.set_bone_name(int(indices[old_name]),str(mapping[old_name]))
func _rewrite_bone_tracks(player:AnimationPlayer,mapping:Dictionary)->void:
	for animation_name in player.get_animation_list():
		var animation:=player.get_animation(animation_name)
		for track in animation.get_track_count():
			var path_text:=str(animation.track_get_path(track))
			for old_name in mapping:
				if path_text.ends_with(":"+str(old_name)):
					animation.track_set_path(track,NodePath(path_text.trim_suffix(str(old_name))+str(mapping[old_name]))); break
