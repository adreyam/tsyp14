extends SceneTree

func _init() -> void: call_deferred("_test")
func _fail(message: String) -> void:
	printerr("NPC PROGRESSION TEST FAILED: "+message); Engine.time_scale=1.0; quit(1)

func _test() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate()
	root.add_child(city)
	await process_frame
	Engine.time_scale=6.0
	var minimum_distance:=INF
	var observed_tasks:={}; var observed_clips:={}
	for sample in 40:
		await create_timer(0.18,true,false,true).timeout
		var people:=get_nodes_in_group("pedestrians")
		for first in people.size():
			for second in range(first+1,people.size()):
				minimum_distance=minf(minimum_distance,(people[first] as Node3D).global_position.distance_to((people[second] as Node3D).global_position))
		for state_value in city.get_node("NpcAi").call("get_agent_debug"):
			var sampled:=state_value as Dictionary; observed_tasks[str(sampled.task)]=true; observed_clips[str(sampled.animation_clip)]=true
	Engine.time_scale=1.0
	var agents:Array=city.get_node("NpcAi").call("get_agent_debug")
	var completed:=0; var active:=0; var moving:=0; var tasks:={}; var clips:={}
	for value in agents:
		var state:=value as Dictionary
		completed+=int(state.completed_actions); tasks[str(state.task)]=true; clips[str(state.animation_clip)]=true
		if str(state.motion_state).begins_with("performing_") or str(state.motion_state).begins_with("interacting_"): active+=1
		elif str(state.motion_state).begins_with("walking_") or str(state.motion_state).begins_with("yielding_"): moving+=1
	if completed<6: _fail("only %d actions completed during the sampled city interval"%completed); return
	if observed_tasks.size()<12 or observed_clips.size()<4: _fail("visible activity diversity collapsed to %d tasks / clips %s"%[observed_tasks.size(),str(observed_clips.keys())]); return
	if active<4 or moving<4: _fail("city state is not mixed: %d active / %d moving"%[active,moving]); return
	if minimum_distance<1.35: _fail("citizens overlapped (minimum %.2f m)"%minimum_distance); return
	print("NPC PROGRESSION TEST PASSED: %d completed actions, %d observed tasks, %d observed clips, %.2f m minimum separation"%[completed,observed_tasks.size(),observed_clips.size(),minimum_distance])
	quit()
