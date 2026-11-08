extends SceneTree

## Render the exact custody poses used for a parking manoeuvre.  This is a
## visual regression check, not a synthetic math-only test: it instantiates the
## full city, its real parking geometry and the production vehicle model.

const OUTPUT_DIR := "/tmp/parking_maneuver_visuals"

func _init() -> void:
	call_deferred("_run")

func _packet(lot_id: String, phase: String, time_value: float, started_at: float) -> Dictionary:
	return {"time": time_value, "operator": {"paused": true}, "vehicles": [], "parking": {
		"time": time_value, "enter_s": 6.5, "leave_s": 6.5, "merge_s": 2.8, "lots": [
			{"id": lot_id, "capacity": 12, "occupied": 1, "barrier_mode": "auto", "gate_open": true, "warning": false, "cars": [
				{"id": "parking_visual_car", "slot": 2, "phase": phase, "t0": started_at, "cls": "car"}
			]}
		]
	}}

func _capture(city: Node3D, presenter: Node, lot: Dictionary, phase: String, progress: float, seconds: float, file_name: String) -> void:
	var time_value := 100.0
	presenter.call("_on_state", _packet(str(lot.data.id), phase, time_value, time_value - progress * seconds))
	if phase == "entering":
		(presenter.get("_entry_elapsed") as Dictionary)["parking_visual_car"] = progress * seconds
	for _unused in 5:
		await process_frame
	var pose: Dictionary
	if phase == "merge_turn":
		pose = presenter.call("pose_on", presenter.call("merge_path_for", lot), progress) as Dictionary
	else:
		pose = presenter.call("pose_on", presenter.call("path_for", lot, 2, phase == "entering"), progress) as Dictionary
	var camera := city.get_node("EstablishingCamera") as Camera3D
	var target := pose.p as Vector3
	camera.look_at_from_position(target + Vector3(15.0, 9.0, 15.0), target + Vector3.UP * 0.8, Vector3.UP)
	for _unused in 4:
		await process_frame
	root.get_texture().get_image().save_png("%s/%s" % [OUTPUT_DIR, file_name])

func _run() -> void:
	root.size = Vector2i(1280, 720)
	DirAccess.make_dir_recursive_absolute(OUTPUT_DIR)
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	root.add_child(city)
	for layer in city.find_children("*", "CanvasLayer", true, false):
		(layer as CanvasLayer).visible = false
	for _unused in 30:
		await process_frame
	var presenter := city.get_node("ParkingLots") as Node
	var lots := presenter.get("lots") as Dictionary
	var lot := lots["LOT-01"] as Dictionary
	await _capture(city, presenter, lot, "entering", 0.08, 6.5, "01a_entering_approach.png")
	await _capture(city, presenter, lot, "entering", 0.22, 6.5, "01b_entering_steer.png")
	await _capture(city, presenter, lot, "entering", 0.43, 6.5, "01c_entering_gate.png")
	await _capture(city, presenter, lot, "leaving", 0.45, 6.5, "02_reversing_to_gate.png")
	await _capture(city, presenter, lot, "merge_wait", 1.0, 1.0, "03_waiting_at_gate.png")
	await _capture(city, presenter, lot, "merge_turn", 0.55, 2.8, "04_curved_live_merge.png")
	print("PARKING_MANEUVER_VISUALS|%s" % OUTPUT_DIR)
	quit()
