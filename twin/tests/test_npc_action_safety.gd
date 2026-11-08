extends SceneTree

const REJECTED := ["Crouch_Fwd", "Crouch_Idle", "Fixing_Kneeling", "Idle_FoldArms_Loop", "Push"]
const ALLOWED := ["Idle_Talking", "Interact", "PickUp_Table", "Sitting_Talking", "Farm_Harvest", "Farm_PlantSeed", "Farm_Watering", "InspectLow", "RepairMid", "RepairSurface", "OpenGroundContainer", "SetGroundItem", "OpenLatch", "PanelKnock", "InspectLabel", "RecordReading", "PresentWork", "Walk_Carry_Loop", "Idle_TalkingPhone_Loop", "Consume", "Chest_Open", "Yes"]

func _init()->void: call_deferred("_test")
func _fail(message:String)->void: printerr("NPC ACTION SAFETY TEST FAILED: "+message); quit(1)

func _test()->void:
	var controller_script:=load("res://scripts/npc_ai_controller.gd") as Script
	var constants:=controller_script.get_script_constant_map()
	var workflows:=constants.get("WORKFLOWS",[]) as Array
	for workflow_value in workflows:
		var workflow:=workflow_value as Dictionary
		for stop_value in workflow.get("stops",[]):
			var stop:=stop_value as Dictionary; var clips:=stop.get("clips",[]) as Array; var words:=stop.get("words",{}) as Dictionary
			for clip_value in clips:
				var clip:=str(clip_value)
				if REJECTED.has(clip): _fail("%s still schedules rejected clip %s"%[str(stop.get("id","unknown")),clip]); return
				if not ALLOWED.has(clip): _fail("%s schedules unaudited clip %s"%[str(stop.get("id","unknown")),clip]); return
				if not words.has(clip): _fail("%s has no honest action text for %s"%[str(stop.get("id","unknown")),clip]); return
	var paths:=constants.get("ROCKETBOX_MODEL_GENDERS",{}) as Dictionary
	if paths.size()!=20: _fail("expected 20 curated high-definition Rocketbox models"); return
	for character_path in paths:
		if not str(character_path).begins_with("res://assets/rocketbox/") or not ResourceLoader.exists(str(character_path)):
			_fail("missing curated Rocketbox model %s"%str(character_path)); return
	var pedestrian_scene:=FileAccess.get_file_as_string("res://scenes/Pedestrians.tscn")
	if pedestrian_scene.contains("poly_pizza") or pedestrian_scene.contains("external_poly_pizza"):
		_fail("active pedestrian scene still references an old stylized model"); return
	if pedestrian_scene.count("rocketbox_realistic")!=32:
		_fail("not every one of the 32 citizens was replaced with a Rocketbox model"); return
	var animation_script:=load("res://scripts/rocketbox_animation_library.gd") as Script
	var animation_constants:=animation_script.get_script_constant_map()
	var aliases:=animation_constants.get("SEMANTIC_ALIASES",{}) as Dictionary
	for required in ALLOWED:
		if not aliases.has(required): _fail("scheduled action %s has no audited mocap alias"%required); return
	for phase_name in constants.get("INTERACTION_ANIMATION_PROFILES",{}):
		if not aliases.has(phase_name): _fail("interaction phase %s has no dedicated mocap assignment"%str(phase_name)); return
	var controller_text:=FileAccess.get_file_as_string("res://scripts/npc_ai_controller.gd")
	if controller_text.contains("SkeletonIK3D"):
		_fail("controller still includes runtime skeletal IK overrides"); return
	print("NPC ACTION SAFETY TEST PASSED: all 32 citizens use curated Rocketbox models and role-safe shared-rig mocap without runtime skeletal IK")
	quit()
