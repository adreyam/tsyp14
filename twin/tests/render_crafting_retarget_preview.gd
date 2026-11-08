extends SceneTree

const TARGET_MAP := {"Root":"Root","Hips":"Hips","Abdomen":"Spine","Torso":"Chest","Chest":"UpperChest","Neck":"Neck","Head":"Head","Shoulder.L":"LeftShoulder","UpperArm.L":"LeftUpperArm","LowerArm.L":"LeftLowerArm","Wrist.L":"LeftHand","UpperLeg.L":"LeftUpperLeg","LowerLeg.L":"LeftLowerLeg","Foot.L":"LeftFoot","PT.L":"LeftToes","Shoulder.R":"RightShoulder","UpperArm.R":"RightUpperArm","LowerArm.R":"RightLowerArm","Wrist.R":"RightHand","UpperLeg.R":"RightUpperLeg","LowerLeg.R":"RightLowerLeg","Foot.R":"RightFoot","PT.R":"RightToes"}
const SOURCE_MAP := {"B-root":"Root","B-hips":"Hips","B-spine":"Spine","B-chest":"Chest","B-neck":"Neck","B-head":"Head","B-shoulder.L":"LeftShoulder","B-upperArm.L":"LeftUpperArm","B-forearm.L":"LeftLowerArm","B-hand.L":"LeftHand","B-thigh.L":"LeftUpperLeg","B-shin.L":"LeftLowerLeg","B-foot.L":"LeftFoot","B-toe.L":"LeftToes","B-shoulder.R":"RightShoulder","B-upperArm.R":"RightUpperArm","B-forearm.R":"RightLowerArm","B-hand.R":"RightHand","B-thigh.R":"RightUpperLeg","B-shin.R":"RightLowerLeg","B-foot.R":"RightFoot","B-toe.R":"RightToes"}
const SOURCES := {
	"Gathering01":"res://assets/crafting_animations/source/HumanM@Gathering01.fbx",
	"HammerGroundLoop":"res://assets/crafting_animations/source/HumanM@HammeringGround01_R - Loop.fbx",
	"MiningWall":"res://assets/crafting_animations/source/HumanM@MiningOneHand01_R - Wall.fbx",
	"FarmingLoop":"res://assets/crafting_animations/source/HumanM@FarmingWithPlow01_R - Loop.fbx"
}
func _init()->void: call_deferred("_run")
func _rename(skeleton:Skeleton3D,mapping:Dictionary)->void:
	var indices:={}
	for old in mapping:
		var i:=skeleton.find_bone(str(old))
		if i>=0: indices[old]=i; skeleton.set_bone_name(i,"__tmp_%d"%i)
	for old in indices: skeleton.set_bone_name(int(indices[old]),str(mapping[old]))
func _rewrite(player:AnimationPlayer,mapping:Dictionary)->void:
	for name in player.get_animation_list():
		var animation:=player.get_animation(name)
		for track in animation.get_track_count():
			var path:=str(animation.track_get_path(track))
			for old in mapping:
				if path.ends_with(":"+str(old)): animation.track_set_path(track,NodePath(path.trim_suffix(str(old))+str(mapping[old]))); break
func _collect_paths(player:AnimationPlayer)->Dictionary:
	var result:={}
	for animation_name in player.get_animation_list():
		var animation:=player.get_animation(animation_name)
		for track in animation.get_track_count():
			if animation.track_get_type(track)!=Animation.TYPE_ROTATION_3D: continue
			var path:=animation.track_get_path(track); var bone:=str(path).get_slice(":",1)
			if not bone.is_empty(): result[bone]=path
		if not result.is_empty(): break
	return result
func _bake(source_skeleton:Skeleton3D,target_skeleton:Skeleton3D,source_player:AnimationPlayer,target_player:AnimationPlayer)->Animation:
	var paths:=_collect_paths(target_player); var target_parent:=target_skeleton.get_parent()
	_rewrite(source_player,SOURCE_MAP); _rename(source_skeleton,SOURCE_MAP); _rename(target_skeleton,TARGET_MAP)
	var modifier:=RetargetModifier3D.new(); modifier.profile=SkeletonProfileHumanoid.new(); modifier.use_global_pose=false; modifier.set_position_enabled(true); modifier.set_scale_enabled(false); source_skeleton.add_child(modifier); target_skeleton.reparent(modifier,false)
	var source_name:=str(source_player.get_animation_list()[0]); var source_animation:=source_player.get_animation(source_name)
	var baked:=Animation.new(); baked.length=source_animation.length; baked.loop_mode=source_animation.loop_mode
	var rotation_tracks:={}
	for original in TARGET_MAP:
		var standard:=str(TARGET_MAP[original]); var index:=target_skeleton.find_bone(standard)
		if index<0 or not paths.has(original): continue
		var track:=baked.add_track(Animation.TYPE_ROTATION_3D); baked.track_set_path(track,paths[original]); rotation_tracks[standard]=track
	var hips_position_track:=-1
	if paths.has("Hips"):
		hips_position_track=baked.add_track(Animation.TYPE_POSITION_3D); baked.track_set_path(hips_position_track,paths["Hips"])
	var sample_count:=ceili(source_animation.length*30.0)+1
	for sample in sample_count:
		var time:=minf(float(sample)/30.0,source_animation.length); source_player.play(source_name); source_player.seek(time,true); source_player.pause(); await process_frame
		for standard in rotation_tracks:
			var index:=target_skeleton.find_bone(str(standard)); baked.rotation_track_insert_key(int(rotation_tracks[standard]),time,target_skeleton.get_bone_pose_rotation(index))
		if hips_position_track>=0:
			baked.position_track_insert_key(hips_position_track,time,target_skeleton.get_bone_pose_position(target_skeleton.find_bone("Hips")))
	target_skeleton.reparent(target_parent,false); modifier.queue_free(); await process_frame
	var inverse:={}
	for original in TARGET_MAP: inverse[TARGET_MAP[original]]=original
	_rename(target_skeleton,inverse); target_skeleton.reset_bone_poses()
	return baked
func _run()->void:
	root.size=Vector2i(900,900)
	for label in SOURCES:
		var world:=Node3D.new(); root.add_child(world)
		var env_node:=WorldEnvironment.new(); var env:=Environment.new(); env.background_mode=Environment.BG_COLOR; env.background_color=Color("7d8996"); env.ambient_light_source=Environment.AMBIENT_SOURCE_COLOR; env.ambient_light_energy=1.0; env_node.environment=env; world.add_child(env_node)
		var light:=DirectionalLight3D.new(); light.rotation_degrees=Vector3(-48,-35,0); light.light_energy=1.4; light.shadow_enabled=true; world.add_child(light)
		var floor:=MeshInstance3D.new(); var floor_mesh:=BoxMesh.new(); floor_mesh.size=Vector3(8,0.1,8); floor.mesh=floor_mesh; floor.position.y=-0.05; world.add_child(floor)
		var source:Node3D=(load(str(SOURCES[label])) as PackedScene).instantiate(); world.add_child(source)
		var target:Node3D=(load("res://assets/poly_pizza/city_pack/Worker.glb") as PackedScene).instantiate(); world.add_child(target)
		for m in source.find_children("*","MeshInstance3D",true,false): (m as MeshInstance3D).visible=false
		var source_skeleton:=source.find_child("Skeleton3D",true,false) as Skeleton3D; var target_skeleton:=target.find_child("Skeleton3D",true,false) as Skeleton3D
		var player:=source.find_child("AnimationPlayer",true,false) as AnimationPlayer; var target_player:=target.find_child("AnimationPlayer",true,false) as AnimationPlayer; target_player.stop()
		var animation:=await _bake(source_skeleton,target_skeleton,player,target_player)
		var library:=AnimationLibrary.new(); library.add_animation("Preview",animation); target_player.add_animation_library("preview",library); target_player.play("preview/Preview"); target_player.seek(animation.length*0.55,true)
		var camera:=Camera3D.new(); world.add_child(camera); camera.current=true; camera.fov=42; camera.look_at_from_position(Vector3(4.0,2.8,6.0),Vector3(0,1.2,0))
		for unused in 8: await process_frame
		root.get_texture().get_image().save_png("/tmp/crafting_retarget_%s.png"%label)
		print("CRAFT_RETARGET_RENDER|",label,"|",animation.length)
		world.queue_free(); await process_frame
	quit()
