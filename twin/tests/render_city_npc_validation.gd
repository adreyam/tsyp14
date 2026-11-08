extends SceneTree
func _init() -> void: call_deferred("_run")
func _run() -> void:
	root.size=Vector2i(1280,720)
	var city:=(load("res://scenes/City.tscn") as PackedScene).instantiate(); root.add_child(city)
	var world_environment:=city.get_node("WorldEnvironment") as WorldEnvironment
	world_environment.environment.background_mode=Environment.BG_COLOR
	world_environment.environment.background_color=Color(0.48,0.62,0.78)
	world_environment.environment.ambient_light_source=Environment.AMBIENT_SOURCE_COLOR
	world_environment.environment.ambient_light_color=Color(0.9,0.94,1.0)
	world_environment.environment.ambient_light_energy=0.8
	world_environment.environment.fog_enabled=false
	(city.get_node("Sun") as DirectionalLight3D).light_energy=2.0
	for node in city.find_children("*","CanvasLayer",true,false): (node as CanvasLayer).visible=false
	var camera:=Camera3D.new(); city.add_child(camera); camera.current=true; camera.fov=54.0
	for unused in 40: await process_frame
	var director:=city.get_node("NpcAi")
	var states:Array=director.get_agent_debug(); var subjects:Array[Dictionary]=[]
	for state in states:
		if str(state.motion_state).begins_with("performing_") and ["bench_pause","power_read","plant_check","timetable"].has(str(state.task)): subjects.append(state)
		if subjects.size()>=6: break
	if subjects.size()<6:
		for state in states:
			if str(state.motion_state).begins_with("performing_") and not subjects.has(state): subjects.append(state)
			if subjects.size()>=6: break
	var error:=OK
	for index in subjects.size():
		var state:=subjects[index]
		var subject:=state.node as Node3D
		var target:=subject.global_position+Vector3(0.0,0.95,0.0)
		var angle:=float(index)*1.047+0.35
		var offset:=Vector3(cos(angle)*4.8,1.9,sin(angle)*4.8)
		camera.look_at_from_position(target+offset,target,Vector3.UP)
		for unused in 4: await process_frame
		var image:=root.get_texture().get_image(); var save_error:=image.save_png("/tmp/city_npc_validation_%02d.png"%index)
		if save_error!=OK: error=save_error
		print("CITY_NPC_RENDER|",index,"|",save_error,"|",str(state.task),"|",str(state.animation_clip),"|",str(subject.global_position))
	quit(0 if error==OK and subjects.size()>=6 else 1)
