extends SceneTree


func _init() -> void:
	call_deferred("_run")


func _run() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate()
	root.add_child(city)
	await process_frame
	Engine.time_scale = 6.0
	var minimum_distance := INF
	var closest := []
	for sample in 50:
		await create_timer(0.15, true, false, true).timeout
		var states := city.get_node("NpcAi").call("get_agent_debug") as Array
		for first in states.size():
			for second in range(first + 1, states.size()):
				var a := states[first] as Dictionary
				var b := states[second] as Dictionary
				var distance := (a.node as Node3D).global_position.distance_to((b.node as Node3D).global_position)
				if distance < minimum_distance:
					minimum_distance = distance
					closest = [sample, str(a.display_name), str(a.motion_state), str(a.station_id), str(b.display_name), str(b.motion_state), str(b.station_id)]
	Engine.time_scale = 1.0
	print("SOCIETY_SPACING_AUDIT minimum=%.3f detail=%s" % [minimum_distance, str(closest)])
	quit(0 if minimum_distance >= 1.35 else 1)
