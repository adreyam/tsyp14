extends SceneTree

const SpatialSafety := preload("res://scripts/city_spatial_safety.gd")
const SURFACE_Y := 0.16

func _initialize() -> void:
	call_deferred("_run")

func _unique_visible(values: Array) -> Array[Node3D]:
	var output: Array[Node3D] = []
	var seen := {}
	for value in values:
		if value is Node3D and (value as Node3D).visible and not seen.has((value as Node3D).get_instance_id()):
			seen[(value as Node3D).get_instance_id()] = true
			output.append(value as Node3D)
	return output

func _run() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	root.add_child(city)
	await process_frame
	await create_timer(0.5).timeout
	var props := _unique_visible(get_nodes_in_group("urban_props") + get_nodes_in_group("npc_activity_props") + get_nodes_in_group("npc_interaction_props"))
	var buildings := SpatialSafety.building_bounds(self)
	var floating := 0
	var sunk := 0
	var road := 0
	var building := 0
	var overlaps := 0
	for prop in props:
		var bounds := SpatialSafety.world_bounds(prop)
		var ground_error := bounds.position.y - SURFACE_Y
		if ground_error > 0.02:
			floating += 1
			print("PROP_FLOATING|", prop.name, "|bottom=", bounds.position.y, "|gap=", ground_error)
		elif ground_error < -0.02:
			sunk += 1
			print("PROP_SUNK|", prop.name, "|bottom=", bounds.position.y, "|depth=", -ground_error)
		if SpatialSafety.overlaps_road(bounds, 0.0):
			road += 1
			print("PROP_ROAD|", prop.name)
		for block_bounds in buildings:
			if SpatialSafety.horizontal_intersects(bounds, block_bounds, 0.02):
				building += 1
				print("PROP_BUILDING|", prop.name)
				break
	for first in props.size():
		var first_bounds := SpatialSafety.world_bounds(props[first])
		for second in range(first + 1, props.size()):
			var second_bounds := SpatialSafety.world_bounds(props[second])
			if first_bounds.intersects(second_bounds):
				overlaps += 1
				print("PROP_OVERLAP|", props[first].name, "|", props[second].name)
	print("FIX75_PROP_BASELINE|visible=%d|floating=%d|sunk=%d|road=%d|building=%d|overlaps=%d" % [props.size(), floating, sunk, road, building, overlaps])
	quit(0)
