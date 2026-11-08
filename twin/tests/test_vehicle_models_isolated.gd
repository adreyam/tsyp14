extends SceneTree

## Every SUMO class (and every private-car body style) is built by the
## procedural fleet at its exact class size, rests on its tyres, has four
## axle-centred wheels and carries its lamps as lens sections of the body.

const ProceduralVehicle := preload("res://scripts/vehicles/procedural_vehicle.gd")
const STRETCHED_CLASSES := ["police", "ambulance", "fire", "tow", "utility"]


func _init() -> void:
	call_deferred("_test")


func _fail(message: String) -> void:
	printerr("VEHICLE ISOLATION TEST FAILED: " + message)
	quit(1)


func _test() -> void:
	var specs := (JSON.parse_string(FileAccess.get_file_as_string("res://sumo/vehicle_classes.json")) as Dictionary).classes as Dictionary
	var cases: Array = []
	for visual_class in specs:
		cases.append([visual_class, 0])
	for variant in ProceduralVehicle.CIVILIAN_STYLES.size():
		cases.append(["car", variant])
	var styles := {}
	for case in cases:
		var visual_class := str(case[0])
		var spec := specs[visual_class] as Dictionary
		var car := ProceduralVehicle.build(visual_class, spec, int(case[1]))
		root.add_child(car)
		await process_frame
		var label := "%s/%s" % [visual_class, car.get_meta("procedural_style")]
		styles[car.get_meta("procedural_style")] = true
		var body := car.get_node_or_null("Body") as MeshInstance3D
		if body == null or body.mesh == null or body.mesh.get_surface_count() < 4:
			_fail("%s has no complete body mesh" % label)
			return
		var bounds := AABB()
		var wheels := 0
		for node in car.find_children("*", "MeshInstance3D", true, false):
			var mesh_node := node as MeshInstance3D
			bounds = mesh_node.get_aabb() if bounds.size == Vector3.ZERO else bounds.merge(mesh_node.get_aabb())
			if "wheel" in mesh_node.name.to_lower():
				var wheel_bounds := mesh_node.mesh.get_aabb()
				if wheel_bounds.get_center().length() < 0.4 or absf(wheel_bounds.size.y - wheel_bounds.size.z) > 0.01:
					_fail("%s wheel %s is not round, axle-centred geometry" % [label, mesh_node.name])
					return
				if absf(wheel_bounds.position.y) > 0.005:
					_fail("%s wheel %s does not touch the road" % [label, mesh_node.name])
					return
				wheels += 1
		if wheels != 4:
			_fail("%s has %d wheels" % [label, wheels])
			return
		var length := float(spec.length)
		var width := float(spec.width)
		if absf(bounds.size.z - length) > 0.01:
			_fail("%s is %.3f m long; its SUMO class is %.2f m" % [label, bounds.size.z, length])
			return
		if bounds.size.x > width + 0.01 or (visual_class in STRETCHED_CLASSES and absf(bounds.size.x - width) > 0.01):
			_fail("%s is %.3f m wide; its SUMO class is %.2f m" % [label, bounds.size.x, width])
			return
		if absf(bounds.position.y) > 0.005:
			_fail("%s does not rest on its tyres (lowest point %.3f m)" % [label, bounds.position.y])
			return
		var kinds := body.mesh.get_meta("lamp_kinds", []) as Array
		var required := [ProceduralVehicle.LAMP_HEAD, ProceduralVehicle.LAMP_TAIL, ProceduralVehicle.LAMP_INDICATOR_FL, ProceduralVehicle.LAMP_INDICATOR_FR, ProceduralVehicle.LAMP_INDICATOR_RL, ProceduralVehicle.LAMP_INDICATOR_RR]
		if visual_class in ["police", "ambulance", "fire"]:
			required += [ProceduralVehicle.LAMP_BEACON_A, ProceduralVehicle.LAMP_BEACON_B]
		if visual_class in ["tow", "utility"]:
			required.append(ProceduralVehicle.LAMP_BEACON_AMBER)
		for kind in required:
			if not kinds.has(kind):
				_fail("%s lacks integrated lamp kind %d (has %s)" % [label, kind, kinds])
				return
		if car.find_child("VehicleLightRig", true, false) != null:
			_fail("%s carries external lamp nodes" % label)
			return
		car.queue_free()
		await process_frame
	print("VEHICLE ISOLATION TEST PASSED: %d classes and %d body styles are built at exact SUMO size, grounded on four round axle-centred wheels, with head/tail/indicator/beacon lenses integrated in the body" % [specs.size(), styles.size()])
	quit(0)
