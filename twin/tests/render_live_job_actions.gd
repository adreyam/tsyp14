extends SceneTree

func _init()->void: call_deferred("_run")
func _run()->void:
	root.size=Vector2i(960,720); var city:Node3D=(load("res://scenes/City.tscn") as PackedScene).instantiate(); root.add_child(city)
	var environment:=city.get_node("WorldEnvironment") as WorldEnvironment; environment.environment.fog_enabled=false; environment.environment.ambient_light_energy=0.8
	for layer in city.find_children("*","CanvasLayer",true,false): (layer as CanvasLayer).visible=false
	var camera:=Camera3D.new(); city.add_child(camera); camera.current=true; camera.fov=45.0
	for unused in 30: await process_frame
	for phase in 3:
		if phase>0: await create_timer(3.5).timeout
		var rendered:=0
		for value in city.get_node("NpcAi").call("get_agent_debug"):
			var state:=value as Dictionary
			if not str(state.motion_state).begins_with("performing_"): continue
			var actor:=state.node as Node3D; var prop:Node3D=null
			for candidate in get_nodes_in_group("npc_activity_props"):
				if str((candidate as Node3D).get_meta("activity_station",""))==str(state.station_id): prop=candidate as Node3D; break
			var target:=actor.global_position+Vector3.UP*1.4; var toward:=actor.global_transform.basis.z.normalized(); var side:=Vector3(-toward.z,0.0,toward.x)
			if prop!=null: target=(actor.global_position+prop.global_position)*0.5+Vector3.UP*1.25
			camera.look_at_from_position(target-side*7.5-toward*5.0+Vector3.UP*2.2,target,Vector3.UP); await process_frame
			var path:="/tmp/city_job_%d_%s.png"%[phase,str(state.task)]; var error:=root.get_texture().get_image().save_png(path)
			print("LIVE_JOB_RENDER|",phase,"|",state.task,"|",state.animation_clip,"|",error); rendered+=1
		print("LIVE_JOB_PHASE|",phase,"|",rendered)
	quit()
