extends SceneTree

func _init()->void: call_deferred("_run")
func _run()->void:
	root.size=Vector2i(1280,720)
	var city:Node3D=(load("res://scenes/City.tscn") as PackedScene).instantiate(); root.add_child(city)
	var environment:=city.get_node("WorldEnvironment") as WorldEnvironment
	environment.environment.background_mode=Environment.BG_COLOR; environment.environment.background_color=Color(0.48,0.62,0.78)
	environment.environment.ambient_light_source=Environment.AMBIENT_SOURCE_COLOR; environment.environment.ambient_light_color=Color(0.9,0.94,1.0); environment.environment.ambient_light_energy=0.8; environment.environment.fog_enabled=false
	(city.get_node("Sun") as DirectionalLight3D).light_energy=2.0
	for layer in city.find_children("*","CanvasLayer",true,false): (layer as CanvasLayer).visible=false
	var camera:=Camera3D.new(); city.add_child(camera); camera.current=true; camera.fov=48.0
	for unused in 35: await process_frame
	var rendered:=0
	for phase in 3:
		if phase>0: await create_timer(4.5).timeout
		var grouped:={}
		for value in city.get_node("NpcAi").call("get_agent_debug"):
			var state:=value as Dictionary; var interaction_id:=str(state.get("interaction_id",""))
			if interaction_id.is_empty(): continue
			if not grouped.has(interaction_id): grouped[interaction_id]=[]
			(grouped[interaction_id] as Array).append(state)
		var index:=0
		for interaction_id in grouped:
			var pair:=grouped[interaction_id] as Array; var a:Node3D=(pair[0] as Dictionary).node; var b:Node3D=(pair[1] as Dictionary).node
			# Capture the middle of each gesture rather than accidentally sampling the
			# neutral loop seam shared by every upright interaction animation.
			for actor in [a,b]:
				var player:=(actor as Node3D).find_child("AnimationPlayer",true,false) as AnimationPlayer
				if player!=null and not player.current_animation.is_empty():
					var animation:=player.get_animation(player.current_animation)
					if animation!=null: player.seek(animation.length*0.30,true)
			var midpoint:=(a.global_position+b.global_position)*0.5+Vector3.UP*1.0
			var line:=(b.global_position-a.global_position).normalized(); var side:=Vector3(-line.z,0.0,line.x)
			var prop:Node3D=null
			for prop_value in get_nodes_in_group("npc_interaction_props"):
				if str((prop_value as Node3D).get_meta("interaction_id",""))==str(interaction_id): prop=prop_value as Node3D; break
			if prop!=null and side.dot(prop.global_position-midpoint)<0.0: side=-side
			camera.look_at_from_position(midpoint-side*5.0+Vector3.UP*1.35,midpoint,Vector3.UP)
			await process_frame
			var save_error:=root.get_texture().get_image().save_png("/tmp/city_npc_interaction_%d_%02d.png"%[phase,index])
			print("NPC_INTERACTION_RENDER|",phase,"|",index,"|",save_error,"|",interaction_id,"|",(pair[0] as Dictionary).animation_clip,"|",(pair[1] as Dictionary).animation_clip)
			index+=1; rendered+=1
	quit(0 if rendered==18 else 1)
