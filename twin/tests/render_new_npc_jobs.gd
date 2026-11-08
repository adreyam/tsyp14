extends SceneTree

func _init()->void: call_deferred("_run")
func _run()->void:
	root.size=Vector2i(1280,720)
	var city:Node3D=(load("res://scenes/City.tscn") as PackedScene).instantiate(); root.add_child(city)
	var environment:=city.get_node("WorldEnvironment") as WorldEnvironment
	environment.environment.background_mode=Environment.BG_COLOR; environment.environment.background_color=Color(0.48,0.62,0.78); environment.environment.ambient_light_source=Environment.AMBIENT_SOURCE_COLOR; environment.environment.ambient_light_color=Color(0.9,0.94,1.0); environment.environment.ambient_light_energy=0.8; environment.environment.fog_enabled=false
	(city.get_node("Sun") as DirectionalLight3D).light_energy=2.0
	for layer in city.find_children("*","CanvasLayer",true,false): (layer as CanvasLayer).visible=false
	var camera:=Camera3D.new(); city.add_child(camera); camera.current=true; camera.fov=46.0
	for unused in 35: await process_frame
	var wanted:={"road maintenance technician":true,"building maintenance technician":true,"public information technician":true,"horticulture specialist":true}; var selected:={}
	for value in city.get_node("NpcAi").call("get_agent_debug"):
		var state:=value as Dictionary; var role:=str(state.role)
		if wanted.has(role) and not selected.has(role) and str(state.motion_state).begins_with("performing_"): selected[role]=state
	var index:=0
	for role in selected:
		var state:=selected[role] as Dictionary; var person:=state.node as Node3D; var prop:Node3D=null
		for prop_value in get_nodes_in_group("npc_activity_props"):
			if str((prop_value as Node3D).get_meta("activity_station",""))==str(state.station_id): prop=prop_value as Node3D; break
		var target:=person.global_position+Vector3.UP*0.95
		var view_direction:=Vector3(0.7,0.0,1.0).normalized()
		if prop!=null:
			target=(person.global_position+prop.global_position)*0.5+Vector3.UP*0.9
			var line:=(prop.global_position-person.global_position).normalized(); view_direction=Vector3(-line.z,0.0,line.x)
		camera.look_at_from_position(target+view_direction*4.5+Vector3.UP*1.25,target,Vector3.UP)
		for unused in 5: await process_frame
		var save_error:=root.get_texture().get_image().save_png("/tmp/city_npc_new_job_%02d.png"%index)
		print("NPC_NEW_JOB_RENDER|",index,"|",save_error,"|",role,"|",state.task,"|",state.animation_clip)
		index+=1
	quit(0 if index==4 else 1)
