extends RefCounted

## Builds animation libraries directly from Microsoft Rocketbox motion-capture
## FBX files. Rocketbox avatars and clips share the Bip01 skeleton. We copy only
## tracks for bones that exist on the target and deliberately remove root motion,
## keeping navigation authoritative and preventing rotated, airborne characters.

const SOURCE_FILES := {
	"WalkNeutral": "xy/%s_walk_neutral.max.fbx",
	"IdleNeutral": "static/%s_idle_neutral_01.max.fbx",
	"IdleLook": "static/%s_idle_look_around_01.max.fbx",
	"PhoneText": "static/%s_cell_phone_textmessage.max.fbx",
	"Clap": "static/%s_claphands_01.max.fbx",
	"CrouchWork": "static/%s_crouch_gestic.max.fbx",
	"DocumentsCheck": "static/%s_documents_check.max.fbx",
	"DocumentsNote": "static/%s_documents_note.max.fbx",
	"ListenNeutral": "static/%s_gestic_listen_neutral_01.max.fbx",
	"PresentLeft": "static/%s_gestic_presentation_left_01.max.fbx",
	"PresentRight": "static/%s_gestic_presentation_right_01.max.fbx",
	"TalkNeutral": "static/%s_gestic_talk_neutral_01.max.fbx",
	"KnockDoor": "static/%s_knock_door.max.fbx",
	"Moderate": "static/%s_moderate_01.max.fbx",
	"TrolleyIdle": "static/%s_trolley_idle.max.fbx",
	"TryDoor": "static/%s_try_door_inwards.max.fbx",
	"Wave": "static/%s_wave_01.max.fbx",
	"WorkMid": "static/%s_work_mid.max.fbx",
	"WorkTable": "static/%s_work_table.max.fbx",
}

const SEMANTIC_ALIASES := {
	"Walking": "WalkNeutral", "Walk": "WalkNeutral",
	"Idle_Neutral": "IdleNeutral", "Idle": "IdleNeutral",
	"Idle_Talking": "TalkNeutral", "Sitting_Talking": "TalkNeutral",
	"Walk_Carry_Loop": "WalkNeutral",
	"Chest_Open": "DocumentsCheck", "PickUp_Table": "WorkTable",
	"Idle_TalkingPhone_Loop": "PhoneText", "Yes": "ListenNeutral",
	"Interact": "WorkMid", "Farm_Harvest": "CrouchWork",
	"Farm_PlantSeed": "CrouchWork", "Farm_Watering": "WorkTable",
	"InspectLow": "CrouchWork", "RepairMid": "WorkMid",
	"RepairSurface": "WorkTable",
	"OpenGroundContainer": "CrouchWork", "SetGroundItem": "CrouchWork",
	"OpenLatch": "TryDoor", "PanelKnock": "KnockDoor",
	"InspectLabel": "DocumentsCheck", "RecordReading": "DocumentsNote",
	"PresentWork": "PresentRight",
	"Consume": "WorkTable",
	"OfferParcel": "PresentLeft", "ReceiveParcel": "WorkTable",
	"DescribeParcel": "TalkNeutral", "CheckParcel": "DocumentsCheck",
	"CompleteHandoff": "ListenNeutral", "AcceptHandoff": "ListenNeutral",
	"AskCheckIn": "TalkNeutral", "AnswerCheckIn": "TalkNeutral",
	"RecordCheckIn": "DocumentsNote", "WaitForCheckIn": "ListenNeutral",
	"WelcomePatient": "Wave", "ConfirmCheckIn": "ListenNeutral",
	"ExplainMeter": "TalkNeutral", "LogMeterReading": "DocumentsNote",
	"PointToMeter": "PresentRight", "ReviewServiceNote": "DocumentsCheck",
	"ConfirmMeterWork": "ListenNeutral", "CloseServiceReport": "DocumentsCheck",
	"PresentGoods": "PresentLeft", "InspectGoods": "WorkTable",
	"DescribeGoods": "TalkNeutral", "ChooseGoods": "WorkTable",
	"CompletePurchase": "ListenNeutral", "AcceptPurchase": "ListenNeutral",
	"ShowCleanedArea": "PresentRight", "ReviewCleanedArea": "CrouchWork",
	"ExplainServiceWork": "TalkNeutral", "ConfirmClearPath": "PresentLeft",
	"CloseServiceRequest": "DocumentsCheck", "AcceptServiceReport": "ListenNeutral",
	"ExplainRoute": "PresentRight", "FollowRouteExplanation": "ListenNeutral",
	"ClarifyTransfer": "TalkNeutral", "CheckRouteDetails": "PhoneText",
	"ConfirmDirections": "ListenNeutral", "AcknowledgeDirections": "ListenNeutral",
}

# These bones must remain load-bearing during the standing work-table family.
# The source performance has useful authored spine, head and arm motion, but its
# lower-body take leans the entire stance onto angled toes.  We author a stable
# derivative once while building the shared animation library: upper-body
# mocap remains untouched and both legs use the neutral captured stance.
const PLANTED_STANCE_BONES := [
	# Rocketbox's Biped hierarchy parents both thighs to this base spine joint,
	# so it is part of the load-bearing chain even though its name says Spine.
	"Bip01 Pelvis", "Bip01 Spine",
	"Bip01 L Thigh", "Bip01 L Calf", "Bip01 L Foot", "Bip01 L Toe0",
	"Bip01 R Thigh", "Bip01 R Calf", "Bip01 R Foot", "Bip01 R Toe0",
]

static var _source_cache: Dictionary = {}
static var _library_cache: Dictionary = {}
static var _full_library_cache: Dictionary = {}

static func install_on(person: Node3D, player: AnimationPlayer) -> bool:
	var skeleton := person.find_child("Skeleton3D", true, false) as Skeleton3D
	if skeleton == null or player == null:
		return false
	var gender := "f" if person.scene_file_path.contains("Female_") else "m"
	# Every production Rocketbox avatar uses the same named Bip01 hierarchy, so
	# one immutable library per gender can safely be shared by all 32 agents.
	# This keeps startup and animation memory bounded as the population grows.
	if _library_cache.has(gender):
		player.add_animation_library("rocketbox", _library_cache[gender] as AnimationLibrary)
		return true
	var source_animations: Dictionary = {}
	for source_name in SOURCE_FILES:
		var source_animation := _source_animation(gender, str(SOURCE_FILES[source_name]))
		if source_animation == null:
			push_error("Rocketbox animation source failed: %s/%s" % [gender, source_name])
			return false
		source_animations[source_name] = _animation_for_target(source_animation, skeleton)
	# WorkTable is the standing forward-bend family used for picking up,
	# inspecting and handling objects.  Its legs are authored here, never
	# corrected with runtime IK or procedural bone rotation.
	source_animations["WorkTable"] = _with_planted_stance(
		source_animations["WorkTable"] as Animation,
		source_animations["IdleNeutral"] as Animation
	)
	var library := AnimationLibrary.new()
	for alias_name in SEMANTIC_ALIASES:
		var source_name := str(SEMANTIC_ALIASES[alias_name])
		var animation := (source_animations[source_name] as Animation).duplicate(true) as Animation
		animation.loop_mode = Animation.LOOP_LINEAR if alias_name in ["Walking", "Walk", "Idle_Neutral", "Idle"] else Animation.LOOP_NONE
		library.add_animation(str(alias_name), animation)
	_library_cache[gender] = library
	player.add_animation_library("rocketbox", library)
	return true


static func full_library_for(person: Node3D) -> AnimationLibrary:
	## Every authored Rocketbox clip under its own source name (WalkNeutral,
	## Wave, Moderate, PhoneText, TrolleyIdle, …) plus the citizens' semantic
	## aliases, built with exactly the same track filtering. Used by incident
	## responders and civilians, who need gestures the citizen set omits.
	## A separate cache keeps the citizens' shared library untouched.
	var skeleton := person.find_child("Skeleton3D", true, false) as Skeleton3D
	var gender := "f" if person.scene_file_path.contains("Female_") else "m"
	if _full_library_cache.has(gender):
		return _full_library_cache[gender] as AnimationLibrary
	if skeleton == null:
		return null
	var source_animations: Dictionary = {}
	for source_name in SOURCE_FILES:
		var source_animation := _source_animation(gender, str(SOURCE_FILES[source_name]))
		if source_animation == null:
			return null
		source_animations[source_name] = _animation_for_target(source_animation, skeleton)
	source_animations["WorkTable"] = _with_planted_stance(source_animations["WorkTable"] as Animation, source_animations["IdleNeutral"] as Animation)
	var library := AnimationLibrary.new()
	for source_name in source_animations:
		library.add_animation(str(source_name), (source_animations[source_name] as Animation).duplicate(true))
	for alias_name in SEMANTIC_ALIASES:
		library.add_animation(str(alias_name), (source_animations[str(SEMANTIC_ALIASES[alias_name])] as Animation).duplicate(true))
	_full_library_cache[gender] = library
	return library


static func _track_bone_name(animation: Animation, track_index: int) -> String:
	var path := animation.track_get_path(track_index)
	return str(path.get_subname(0)) if path.get_subname_count() > 0 else ""


static func _sample_track(animation: Animation, track_index: int, time: float) -> Variant:
	match animation.track_get_type(track_index):
		Animation.TYPE_POSITION_3D:
			return animation.position_track_interpolate(track_index, time)
		Animation.TYPE_ROTATION_3D:
			return animation.rotation_track_interpolate(track_index, time)
		_:
			return null


static func _with_planted_stance(work_animation: Animation, neutral_animation: Animation) -> Animation:
	var result := work_animation.duplicate(true) as Animation
	var neutral_time := neutral_animation.length * 0.5
	for track_index in result.get_track_count():
		if not PLANTED_STANCE_BONES.has(_track_bone_name(result, track_index)):
			continue
		var track_type := result.track_get_type(track_index)
		if track_type != Animation.TYPE_POSITION_3D and track_type != Animation.TYPE_ROTATION_3D:
			continue
		var neutral_track := neutral_animation.find_track(result.track_get_path(track_index), track_type)
		if neutral_track < 0:
			continue
		var planted_value: Variant = _sample_track(neutral_animation, neutral_track, neutral_time)
		if planted_value == null:
			continue
		for key_index in range(result.track_get_key_count(track_index) - 1, -1, -1):
			result.track_remove_key(track_index, key_index)
		result.track_insert_key(track_index, 0.0, planted_value)
		result.track_insert_key(track_index, result.length, planted_value)
	return result

static func _source_animation(gender: String, relative_template: String) -> Animation:
	var relative_path := relative_template % gender
	var resource_path := "res://assets/rocketbox/animations/%s" % relative_path
	if _source_cache.has(resource_path):
		return _source_cache[resource_path] as Animation
	var packed := load(resource_path) as PackedScene
	if packed == null:
		return null
	var source := packed.instantiate()
	var source_player := source.find_child("AnimationPlayer", true, false) as AnimationPlayer
	if source_player == null or source_player.get_animation_list().is_empty():
		source.free()
		return null
	var animation := source_player.get_animation(source_player.get_animation_list()[0]).duplicate(true) as Animation
	source.free()
	_source_cache[resource_path] = animation
	return animation

static func _animation_for_target(source: Animation, skeleton: Skeleton3D) -> Animation:
	var animation := source.duplicate(true) as Animation
	for track_index in range(animation.get_track_count() - 1, -1, -1):
		var track_path := animation.track_get_path(track_index)
		var path_text := str(track_path)
		if not path_text.begins_with("Skeleton3D:"):
			animation.remove_track(track_index)
			continue
		var bone_name := str(track_path.get_subname(0))
		if bone_name == "Bip01" or skeleton.find_bone(bone_name) < 0:
			animation.remove_track(track_index)
			continue
		# Rotations carry the mocap performance. Removing scale keys and all local
		# translations except the pelvis guarantees that a clip cannot change a
		# character's authored limb lengths or pull wrists/ankles out of sockets.
		var track_type := animation.track_get_type(track_index)
		if track_type == Animation.TYPE_SCALE_3D or (track_type == Animation.TYPE_POSITION_3D and bone_name != "Bip01 Pelvis"):
			animation.remove_track(track_index)
	animation.loop_mode = Animation.LOOP_NONE
	return animation
