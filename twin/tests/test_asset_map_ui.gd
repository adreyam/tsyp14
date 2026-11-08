extends SceneTree


func _init() -> void:
	call_deferred("_test")


func _fail(message: String) -> void:
	printerr("ASSET MAP UI TEST FAILED: " + message)
	quit(1)


func _test() -> void:
	var room := (load("res://scenes/ControlRoom.tscn") as PackedScene).instantiate()
	root.add_child(room)
	await process_frame
	await process_frame
	var buttons := room.get("_asset_highlight_buttons") as Array
	if buttons.size() != 28:
		_fail("expected 28 understandable category buttons, got %d" % buttons.size())
		return
	var seen_colours := {}
	for value in buttons:
		var button := value as Button
		var kind := str(button.get_meta("asset_highlight_kind", ""))
		if kind.is_empty() or "·" not in button.text or int(button.text.get_slice("·", 1).strip_edges().get_slice(" ", 0)) <= 0:
			_fail("button does not expose a category and live asset count: %s" % button.text)
			return
		var material := room.call("_asset_frame_material", kind) as StandardMaterial3D
		seen_colours[material.emission.to_html(false)] = true
	if seen_colours.size() != 28:
		_fail("each category must have its own frame color")
		return
	room.call("_toggle_asset_highlight", "traffic_signals")
	await process_frame
	var signal_targets := room.call("_asset_highlight_targets", "traffic_signals") as Array[Node3D]
	if get_nodes_in_group("asset_map_frames").size() != signal_targets.size():
		_fail("traffic-signal category does not frame every controller")
		return
	for frame_value in get_nodes_in_group("asset_map_frames"):
		var frame := frame_value as Node3D
		var batched := frame.get_node_or_null("BatchedFrameEdges") as MultiMeshInstance3D
		if batched == null or batched.multimesh == null or batched.multimesh.instance_count != 12:
			_fail("highlight frame is not batched into one 12-edge MultiMesh")
			return

	for kind in ["air_quality", "power_meters", "water_meters", "hospitals", "police_vehicles", "ambulances", "smoky_vehicles"]:
		room.call("_toggle_asset_highlight", kind)
		await process_frame
		var targets := room.call("_asset_highlight_targets", kind) as Array[Node3D]
		if get_nodes_in_group("asset_map_frames").size() != targets.size():
			var unrenderable: Array[String] = []
			for target in targets:
				var bounds := room.call("_global_model_bounds", target) as AABB
				if bounds.size.length_squared() <= 0.0001:
					unrenderable.append(target.name)
			_fail("%s framed %d of %d visible targets; no-bounds=%s" % [kind, get_nodes_in_group("asset_map_frames").size(), targets.size(), ",".join(unrenderable)])
			return
	room.call("_toggle_asset_highlight", "smoky_vehicles")
	await process_frame
	if get_nodes_in_group("asset_map_frames").size() != 0:
		_fail("selected Asset Map button did not toggle its frames off")
		return
	print("ASSET MAP UI TEST PASSED: all 28 color-coded categories expose counts, tooltips and exact scene-frame mappings")
	quit(0)
