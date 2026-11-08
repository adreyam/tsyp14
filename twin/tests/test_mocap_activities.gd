extends SceneTree

## Live city, motion-capture activities: while a citizen performs a work clip their hands are on the prop (placement),
## a seated pelvis rests on the seat, and they face what they use. Measured on the rendered final pose.
const Safety := preload("res://scripts/city_spatial_safety.gd")
const Activity := preload("res://scripts/characters/activity_library.gd")
func _initialize(): call_deferred("_run")
func _dist_to_box(p: Vector3, b: AABB) -> float:
	var q := Vector3(clampf(p.x, b.position.x, b.end.x), clampf(p.y, b.position.y, b.end.y), clampf(p.z, b.position.z, b.end.z))
	return p.distance_to(q)
func _run():
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	root.add_child(city)
	for i in 120: await process_frame
	var npc := city.get_node("NpcAi")
	var by_class := {}   # class -> [frames, contact_frames, grounded_bad]
	var sit := {"n": 0, "max_xz": 0.0, "min_dy": 9.0, "max_dy": -9.0}
	var facing_bad := 0; var facing_n := 0; var stuck := 0; var samples := 0
	var seen_clips := {}
	for t in [600, 1090]:
		npc._society.set_time_of_day(t)
		for f in 2400:
			await physics_frame
			if f % 12 != 0: continue
			for a in npc._agents:
				if str(a.motion_state).begins_with("yielding"): stuck += 1
				samples += 1
				if not str(a.motion_state).begins_with("performing"): continue
				var station: Dictionary = npc._stations.get(str(a.station_id), {})
				var prop: Node3D = station.get("prop_node")
				if prop == null or not is_instance_valid(prop) or not a.director.running: continue
				var kind := str(station.get("prop", ""))
				var cls := Activity.scene_class(kind)
				var beat: Dictionary = a.director.beats[min(a.director.beat_index, a.director.beats.size() - 1)]
				seen_clips[str(beat.clip)] = true
				var person: Node3D = a.node
				var fs: Dictionary = a.ik.final_state
				if cls == "sit":
					var seat := prop.get_meta("anchors")["seat_centre"] as Node3D
					if str(beat.clip).begins_with("sit_chair_idle") or str(beat.clip).begins_with("sit_chair_gestic"):
						var pel: Vector3 = fs["pelvis"]
						sit.n += 1
						sit.max_xz = maxf(sit.max_xz, Vector2(pel.x - seat.global_position.x, pel.z - seat.global_position.z).length())
						sit.min_dy = minf(sit.min_dy, pel.y - seat.global_position.y); sit.max_dy = maxf(sit.max_dy, pel.y - seat.global_position.y)
					continue
				var rec: Array = by_class.get(cls, [0, 0, 0.0])
				rec[0] += 1
				var box := Safety.world_bounds(prop).grow(0.05)
				var dmin := minf(_dist_to_box(fs["palm_R"], box), _dist_to_box(fs["palm_L"], box))
				var work_clip: bool = str(beat.clip) in ["work_mid", "work_table", "crouch_idle", "crouch_gestic", "documents_check", "documents_note", "trolley_idle", "documents_in"]
				if work_clip:
					rec[1] += 1
					if dmin <= 0.14: rec[2] += 1
				by_class[cls] = rec
				var to_prop := prop.global_position - person.global_position; to_prop.y = 0.0
				if to_prop.length() > 0.05 and cls != "phone":
					facing_n += 1
					if rad_to_deg((person.global_transform.basis * Vector3(0, 0, 1)).angle_to(to_prop.normalized())) > 45.0: facing_bad += 1
	var failures: Array[String] = []
	for cls in ["work_mid", "work_table", "trolley"]:
		var rec: Array = by_class.get(cls, [0, 0, 0.0])
		if int(rec[1]) < 100:
			failures.append("only %d %s work frames observed" % [int(rec[1]), cls])
		elif float(rec[2]) / float(rec[1]) < 0.7:
			failures.append("%s: hands within 14 cm of the prop in only %.0f%% of work frames" % [cls, 100.0 * float(rec[2]) / float(rec[1])])
	var crouch: Array = by_class.get("crouch", [0, 0, 0.0])
	if int(crouch[1]) > 100 and float(crouch[2]) / float(crouch[1]) < 0.35:
		failures.append("crouch: hands near the prop in only %.0f%% of work frames" % [100.0 * float(crouch[2]) / float(crouch[1])])
	if int(sit.n) < 20:
		failures.append("only %d seated frames observed" % int(sit.n))
	elif float(sit.max_xz) > 0.2 or float(sit.min_dy) < 0.0 or float(sit.max_dy) > 0.32:
		failures.append("seated pelvis off the seat: horizontal %.2f m, height %.2f..%.2f m above the seat" % [float(sit.max_xz), float(sit.min_dy), float(sit.max_dy)])
	if facing_n > 0 and float(facing_bad) / float(facing_n) > 0.025:
		failures.append("%d of %d working frames face away from the prop" % [facing_bad, facing_n])
	if not failures.is_empty():
		printerr("MOCAP ACTIVITIES TEST FAILED: " + "; ".join(failures))
		quit(1)
		return
	print("MOCAP_ACTIVITIES_TEST_OK classes=%s sit_frames=%d facing_bad=%d/%d" % [str(by_class), int(sit.n), facing_bad, facing_n])
	quit(0)
