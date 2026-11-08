extends SceneTree

func _init() -> void: call_deferred("_test")
func _fail(message:String)->void:
	printerr("NPC INTERACTION TEST FAILED: "+message); quit(1)

func _validate_interaction_animation(participant:Dictionary)->String:
	var player:=((participant as Dictionary).node as Node3D).find_child("AnimationPlayer",true,false) as AnimationPlayer
	if player==null or not player.has_animation_library("rocketbox"): return "has no Rocketbox motion-capture library"
	if not player.current_animation.begins_with("rocketbox/"): return "fell back to a non-Rocketbox interaction pose"
	var custom:=player.get_animation(player.current_animation)
	if custom==null: return "interaction animation resource is missing"
	var upper_body_motion:=false
	for track in custom.get_track_count():
		var path_text:=str(custom.track_get_path(track))
		if custom.track_get_type(track)==Animation.TYPE_ROTATION_3D and ("UpperArm" in path_text or "LowerArm" in path_text or path_text.ends_with(":Head")) and custom.track_get_key_count(track)>=2:
			var first:=custom.track_get_key_value(track,0) as Quaternion
			for key in range(1,custom.track_get_key_count(track)):
				if first.angle_to(custom.track_get_key_value(track,key) as Quaternion)>deg_to_rad(1.0): upper_body_motion=true; break
	if not upper_body_motion: return "has no authored upper-body movement"
	for rejected in ["Crouch_Fwd","Crouch_Idle","Fixing_Kneeling","Idle_FoldArms_Loop","Push"]:
		if player.current_animation.ends_with("|"+rejected): return "uses rejected clip "+rejected
	return ""

func _test()->void:
	var city:Node3D=(load("res://scenes/City.tscn") as PackedScene).instantiate()
	root.add_child(city); await process_frame; await create_timer(0.6).timeout
	var director:=city.get_node("NpcAi")
	var states:Array=director.call("get_agent_debug")
	var by_name:={}; var interactions:={}; var starting_phases:={}; var observed_clips:={}
	for value in states:
		var state:=value as Dictionary; by_name[str(state.display_name)]=state
		var interaction_id:=str(state.get("interaction_id",""))
		if interaction_id.is_empty(): continue
		if not interactions.has(interaction_id): interactions[interaction_id]=[]
		(interactions[interaction_id] as Array).append(state)
		starting_phases[str(state.npc_id)]=int(state.interaction_phase)
		observed_clips[str(state.animation_clip)]=true
	if interactions.size()!=6: _fail("expected 6 distinct visible exchanges, got %d"%interactions.size()); return
	for interaction_id in interactions:
		var pair:=interactions[interaction_id] as Array
		if pair.size()!=2: _fail("%s does not own exactly two reserved character slots"%interaction_id); return
		var a:=pair[0] as Dictionary; var b:=pair[1] as Dictionary
		if str(a.interaction_partner)!=str(b.display_name) or str(b.interaction_partner)!=str(a.display_name):
			_fail("%s has no reciprocal participant link"%interaction_id); return
		if str(a.interaction_role)==str(b.interaction_role) or str(a.action)==str(b.action):
			_fail("%s gives both participants the same role"%interaction_id); return
		var a_node:=a.node as Node3D; var b_node:=b.node as Node3D
		var distance:=a_node.global_position.distance_to(b_node.global_position)
		if distance<1.6 or distance>2.0: _fail("%s natural conversation spacing is %.2f m"%[interaction_id,distance]); return
		var toward_b:=(b_node.global_position-a_node.global_position).normalized()
		var toward_a:=(a_node.global_position-b_node.global_position).normalized()
		if a_node.global_transform.basis.z.normalized().dot(toward_b)<0.80 or b_node.global_transform.basis.z.normalized().dot(toward_a)<0.80:
			_fail("%s participants are not facing each other"%interaction_id); return
		if str(a.animation_clip)==str(b.animation_clip) and str(a.animation_clip)!="Interact":
			_fail("%s is not using complementary animation roles"%interaction_id); return
		for participant in [a,b]:
			var validation_error:=_validate_interaction_animation(participant as Dictionary)
			if not validation_error.is_empty(): _fail("%s %s"%[str((participant as Dictionary).npc_id),validation_error]); return
	await create_timer(4.8).timeout
	states=director.call("get_agent_debug"); var advanced:=0
	for value in states:
		var state:=value as Dictionary
		if not str(state.get("interaction_id","")).is_empty():
			if int(state.interaction_phase)!=int(starting_phases.get(str(state.npc_id),-1)): advanced+=1
			observed_clips[str(state.animation_clip)]=true
			var validation_error:=_validate_interaction_animation(state)
			if not validation_error.is_empty(): _fail("%s %s"%[str(state.npc_id),validation_error]); return
	if advanced!=12: _fail("only %d/12 interacting characters advanced to the response phase"%advanced); return
	await create_timer(4.8).timeout
	states=director.call("get_agent_debug")
	for value in states:
		var state:=value as Dictionary
		if not str(state.get("interaction_id","")).is_empty():
			observed_clips[str(state.animation_clip)]=true
			var validation_error:=_validate_interaction_animation(state)
			if not validation_error.is_empty(): _fail("%s %s"%[str(state.npc_id),validation_error]); return
	if observed_clips.size()!=36: _fail("expected 36 distinct semantic phase animations, observed %d: %s"%[observed_clips.size(),str(observed_clips.keys())]); return
	if get_nodes_in_group("npc_interaction_props").size()!=6: _fail("shared interaction props are missing"); return
	print("NPC INTERACTION TEST PASSED: 6 reciprocal exchanges use 36 distinct grounded Rocketbox mocap phases with authored upper-body motion")
	quit()
