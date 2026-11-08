extends SceneTree

## Every job prop in the live city is at its real-world size: its height matches the catalogue (scanned
## model or procedural dimension), nothing was rescaled to "fit", and nothing is cartoonishly large.

const PropCatalog := preload("res://scripts/props/prop_catalog.gd")

func _initialize() -> void:
	call_deferred("_test")

func _fail(message: String) -> void:
	printerr("NPC PROP SCALE TEST FAILED: " + message)
	quit(1)

func _test() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	root.add_child(city)
	await process_frame
	var checked := 0
	var kinds := {}
	for group_name in ["npc_activity_props", "npc_interaction_props", "urban_props"]:
		for value in get_nodes_in_group(group_name):
			var prop := value as Node3D
			if prop == null or prop.is_queued_for_deletion() or not prop.has_meta("prop_kind"):
				continue
			var kind := str(prop.get_meta("prop_kind"))
			var entry := PropCatalog.entry(kind)
			if prop.scale.distance_to(Vector3.ONE) > 0.001:
				_fail("%s was rescaled to %s; catalogued props keep their real size" % [prop.name, str(prop.scale)]); return
			var height := float(prop.get_meta("prop_height"))
			var expected := float(entry.get("height", 0.0))
			if expected > 0.0 and not bool(entry.get("flush", false)) and absf(height - expected) > maxf(0.12, expected * 0.18):
				_fail("%s (%s) is %.2f m tall, expected about %.2f m" % [prop.name, kind, height, expected]); return
			var size := prop.get_meta("prop_size") as Vector3
			if maxf(size.x, maxf(size.y, size.z)) > 3.2:
				_fail("%s (%s) is %.2f m long: too big for street furniture" % [prop.name, kind, maxf(size.x, maxf(size.y, size.z))]); return
			kinds[kind] = true
			checked += 1
	if kinds.size() < 20:
		_fail("only %d distinct prop kinds are in the city" % kinds.size()); return
	print("NPC PROP SCALE TEST PASSED: %d props across %d kinds at real-world size" % [checked, kinds.size()])
	quit(0)
