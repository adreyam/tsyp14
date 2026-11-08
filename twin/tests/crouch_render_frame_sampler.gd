extends Node

signal completed

const FOOT_BONES := ["Bip01 L Foot", "Bip01 R Foot", "Bip01 L Toe0", "Bip01 R Toe0"]

var director: Node
var frames_remaining := 180
var live_crouch_samples := 0
var maximum_live_contact_error := 0.0


func _ready() -> void:
	# NpcAi grounds at idle priority -50. Sample later in the same render frame.
	process_priority = 200


func _lowest_foot_world_y(skeleton: Skeleton3D) -> float:
	var lowest := INF
	for bone_name in FOOT_BONES:
		var bone_index := skeleton.find_bone(str(bone_name))
		if bone_index >= 0:
			lowest = minf(lowest, (skeleton.global_transform * skeleton.get_bone_global_pose(bone_index).origin).y)
	return lowest


func _process(_delta: float) -> void:
	if director == null:
		return
	for state_value in director.call("get_agent_debug"):
		var state := state_value as Dictionary
		if str(state.animation_clip) != "InspectLow":
			continue
		var person := state.node as Node3D
		var skeleton := person.find_child("Skeleton3D", true, false) as Skeleton3D
		var expected := float(state.surface_y) + float(person.get_meta("npc_foot_reference_y", 0.0))
		maximum_live_contact_error = maxf(maximum_live_contact_error, absf(_lowest_foot_world_y(skeleton) - expected))
		live_crouch_samples += 1
	frames_remaining -= 1
	if frames_remaining <= 0:
		set_process(false)
		completed.emit()
