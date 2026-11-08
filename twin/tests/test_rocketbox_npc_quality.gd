extends SceneTree

const ROLE_MODEL_TOKEN := {
	"parcel courier": "Delivery_",
	"sanitation worker": "Construction_",
	"utilities technician": "Construction_",
	"parks worker": "Gardener_",
	"clinic assistant": "Medical_",
	"transit steward": "Business_",
	"market stocker": "Chef_",
	"resident": "Adult_",
	"local resident": "Adult_",
	"road maintenance technician": "Construction_",
	"building maintenance technician": "Construction_",
	"public information technician": "Business_",
	"horticulture specialist": "Gardener_",
	"street vendor": "Chef_",
	"shopkeeper": "Chef_",
	"security guard": "Business_",
	"paramedic": "Medical_",
	"electrician": "Construction_",
	"freight handler": "Delivery_",
	"facade technician": "Construction_",
	"office clerk": "Business_",
	"amenities attendant": "Gardener_",
}

const REQUIRED_BONES := [
	"Bip01 Pelvis", "Bip01 Spine", "Bip01 Spine1", "Bip01 Spine2", "Bip01 Neck", "Bip01 Head",
	"Bip01 L UpperArm", "Bip01 L Forearm", "Bip01 L Hand",
	"Bip01 R UpperArm", "Bip01 R Forearm", "Bip01 R Hand",
	"Bip01 L Thigh", "Bip01 L Calf", "Bip01 L Foot",
	"Bip01 R Thigh", "Bip01 R Calf", "Bip01 R Foot",
]

const SEGMENTS := [
	["Bip01 L UpperArm", "Bip01 L Forearm"], ["Bip01 L Forearm", "Bip01 L Hand"],
	["Bip01 R UpperArm", "Bip01 R Forearm"], ["Bip01 R Forearm", "Bip01 R Hand"],
	["Bip01 L Thigh", "Bip01 L Calf"], ["Bip01 L Calf", "Bip01 L Foot"],
	["Bip01 R Thigh", "Bip01 R Calf"], ["Bip01 R Calf", "Bip01 R Foot"],
]

var _failed := false

func _init() -> void:
	call_deferred("_test")

func _fail(message: String) -> void:
	_failed = true
	push_error("ROCKETBOX NPC QUALITY TEST FAILED: " + message)

func _test() -> void:
	var city := (load("res://scenes/City.tscn") as PackedScene).instantiate() as Node3D
	root.add_child(city)
	await process_frame
	await create_timer(0.35).timeout
	var agents := city.get_node("NpcAi").call("get_agent_debug") as Array
	if agents.size() != 32:
		_fail("expected 32 active citizens, got %d" % agents.size())
		quit(1)
		return
	var unique_models := {}
	var female_agent: Dictionary = {}
	var male_agent: Dictionary = {}
	for state_value in agents:
		var state := state_value as Dictionary
		var person := state.node as Node3D
		var path := person.scene_file_path
		unique_models[path] = true
		if not path.begins_with("res://assets/rocketbox/") or str(person.get_meta("model_family", "")) != "rocketbox_realistic":
			_fail("%s is not a replaced Rocketbox citizen: %s" % [person.name, path])
		var expected_token := str(ROLE_MODEL_TOKEN.get(str(state.role), ""))
		if expected_token.is_empty() or not path.contains(expected_token):
			_fail("illogical job wardrobe: %s uses %s" % [state.role, path.get_file()])
		if person.scale.distance_to(Vector3.ONE) > 0.001:
			_fail("%s uses non-uniform or oversized character scale %s" % [person.name, person.scale])
		var player := person.find_child("AnimationPlayer", true, false) as AnimationPlayer
		var skeleton := person.find_child("Skeleton3D", true, false) as Skeleton3D
		if player == null or skeleton == null or not player.has_animation_library("rocketbox"):
			_fail("%s is missing its production skeleton or mocap library" % person.name)
			continue
		_validate_skeleton(person, skeleton)
		_validate_library(person, player, skeleton)
		if path.contains("Female_") and female_agent.is_empty():
			female_agent = state
		elif not path.contains("Female_") and male_agent.is_empty():
			male_agent = state
	if unique_models.size() < 19:
		_fail("expected at least 19 distinct high-definition models, got %d" % unique_models.size())
	for representative in [female_agent, male_agent]:
		if representative.is_empty():
			_fail("both male and female animation rigs must be represented")
			continue
		_validate_all_pose_envelopes(representative)
	print("ROCKETBOX_NPC_QUALITY|%s|32 citizens|%d distinct models|%d semantic clips" % ["FAIL" if _failed else "PASS", unique_models.size(), _semantic_alias_count()])
	quit(1 if _failed else 0)

func _validate_skeleton(person: Node3D, skeleton: Skeleton3D) -> void:
	if skeleton.get_bone_count() < 80:
		_fail("%s has an incomplete skeleton (%d bones)" % [person.name, skeleton.get_bone_count()])
	for bone_name in REQUIRED_BONES:
		if skeleton.find_bone(bone_name) < 0:
			_fail("%s is missing required bone %s" % [person.name, bone_name])
	for segment in SEGMENTS:
		var parent_index := skeleton.find_bone(str(segment[0]))
		var child_index := skeleton.find_bone(str(segment[1]))
		if child_index < 0 or skeleton.get_bone_parent(child_index) != parent_index:
			_fail("%s has broken hierarchy %s -> %s" % [person.name, segment[0], segment[1]])

func _validate_library(person: Node3D, player: AnimationPlayer, skeleton: Skeleton3D) -> void:
	var helper_script := load("res://scripts/rocketbox_animation_library.gd") as Script
	var aliases := (helper_script.get_script_constant_map().get("SEMANTIC_ALIASES", {}) as Dictionary)
	for alias_name in aliases:
		var full_name := "rocketbox/%s" % str(alias_name)
		if not player.has_animation(full_name):
			_fail("%s is missing semantic clip %s" % [person.name, alias_name])
			continue
		var animation := player.get_animation(full_name)
		if animation == null or animation.length < 0.45:
			_fail("%s clip %s is empty or too short" % [person.name, alias_name])
			continue
		var rotation_tracks := 0
		for track_index in animation.get_track_count():
			var path := animation.track_get_path(track_index)
			var bone_name := str(path.get_subname(0))
			if not str(path).begins_with("Skeleton3D:") or skeleton.find_bone(bone_name) < 0:
				_fail("%s clip %s targets missing node/bone %s" % [person.name, alias_name, path])
			if animation.track_get_type(track_index) == Animation.TYPE_SCALE_3D:
				_fail("%s clip %s contains limb-stretching scale keys" % [person.name, alias_name])
			if animation.track_get_type(track_index) == Animation.TYPE_POSITION_3D and bone_name != "Bip01 Pelvis":
				_fail("%s clip %s translates limb bone %s" % [person.name, alias_name, bone_name])
			if animation.track_get_type(track_index) == Animation.TYPE_ROTATION_3D:
				rotation_tracks += 1
		if rotation_tracks < 12:
			_fail("%s clip %s has insufficient articulated motion (%d tracks)" % [person.name, alias_name, rotation_tracks])

func _validate_all_pose_envelopes(state: Dictionary) -> void:
	var person := state.node as Node3D
	var player := person.find_child("AnimationPlayer", true, false) as AnimationPlayer
	var skeleton := person.find_child("Skeleton3D", true, false) as Skeleton3D
	var original_animation := player.current_animation
	var aliases := (load("res://scripts/rocketbox_animation_library.gd") as Script).get_script_constant_map().get("SEMANTIC_ALIASES", {}) as Dictionary
	var rest_lengths := {}
	for segment in SEGMENTS:
		var parent_index := skeleton.find_bone(str(segment[0]))
		var child_index := skeleton.find_bone(str(segment[1]))
		rest_lengths[str(segment)] = skeleton.get_bone_global_rest(parent_index).origin.distance_to(skeleton.get_bone_global_rest(child_index).origin)
	for alias_name in aliases:
		var full_name := "rocketbox/%s" % str(alias_name)
		var animation := player.get_animation(full_name)
		for fraction in [0.18, 0.50, 0.82]:
			player.play(full_name)
			player.seek(animation.length * float(fraction), true)
			player.advance(0.0)
			for segment in SEGMENTS:
				var parent_index := skeleton.find_bone(str(segment[0]))
				var child_index := skeleton.find_bone(str(segment[1]))
				var posed_length := skeleton.get_bone_global_pose(parent_index).origin.distance_to(skeleton.get_bone_global_pose(child_index).origin)
				var rest_length := float(rest_lengths[str(segment)])
				if rest_length > 0.001 and absf(posed_length / rest_length - 1.0) > 0.025:
					_fail("%s stretches %s in %s at %.0f%% (%.3f vs %.3f)" % [person.name, str(segment), alias_name, float(fraction) * 100.0, posed_length, rest_length])
			var left_hand := skeleton.get_bone_global_pose(skeleton.find_bone("Bip01 L Hand")).origin
			var right_hand := skeleton.get_bone_global_pose(skeleton.find_bone("Bip01 R Hand")).origin
			var left_foot := skeleton.get_bone_global_pose(skeleton.find_bone("Bip01 L Foot")).origin
			var right_foot := skeleton.get_bone_global_pose(skeleton.find_bone("Bip01 R Foot")).origin
			var head := skeleton.get_bone_global_pose(skeleton.find_bone("Bip01 Head")).origin
			var pelvis := skeleton.get_bone_global_pose(skeleton.find_bone("Bip01 Pelvis")).origin
			for joint in [left_hand, right_hand, left_foot, right_foot, head, pelvis]:
				if not _finite_vector(joint):
					_fail("%s has non-finite joint position in %s" % [person.name, alias_name])
			if left_hand.distance_to(right_hand) > 2.0 or left_foot.distance_to(right_foot) > 1.55:
				_fail("%s exceeds human pose envelope in %s" % [person.name, alias_name])
	if not original_animation.is_empty():
		player.play(original_animation)

func _finite_vector(value: Vector3) -> bool:
	return not (is_nan(value.x) or is_nan(value.y) or is_nan(value.z) or is_inf(value.x) or is_inf(value.y) or is_inf(value.z))

func _semantic_alias_count() -> int:
	return ((load("res://scripts/rocketbox_animation_library.gd") as Script).get_script_constant_map().get("SEMANTIC_ALIASES", {}) as Dictionary).size()
