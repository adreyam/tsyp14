extends SceneTree
const CLIPS := ["Crouch_Fwd","Crouch_Idle","Fixing_Kneeling","PickUp_Table"]
func _init()->void: call_deferred("_run")
func _run()->void:
	root.size=Vector2i(900,650); var world:=Node3D.new(); root.add_child(world)
	var env_node:=WorldEnvironment.new(); var env:=Environment.new(); env.background_mode=Environment.BG_COLOR; env.background_color=Color("748596"); env.ambient_light_source=Environment.AMBIENT_SOURCE_COLOR; env.ambient_light_energy=1.0; env_node.environment=env; world.add_child(env_node)
	var sun:=DirectionalLight3D.new(); sun.rotation_degrees=Vector3(-45,-35,0); sun.light_energy=1.5; world.add_child(sun)
	var scene:Node3D=(load("res://assets/animation_library/AnimationLibrary_Godot_Standard.glb") as PackedScene).instantiate(); world.add_child(scene)
	var player:=scene.find_children("*","AnimationPlayer",true,false)[0] as AnimationPlayer
	var camera:=Camera3D.new(); world.add_child(camera); camera.current=true; camera.look_at_from_position(Vector3(4,2.5,6),Vector3(0,1,0))
	for clip in CLIPS:
		var animation:=player.get_animation(clip); player.play(clip); player.seek(animation.length*0.55,true)
		for unused in 4: await process_frame
		root.get_texture().get_image().save_png("/tmp/ual1_reference_%s.png"%clip)
		print("UAL1_REFERENCE|",clip,"|",animation.length)
	quit()
