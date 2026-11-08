extends SceneTree

## Builds job aliases from the animation clips authored for each exact City
## character model.  No foreign skeleton, positional track, or runtime IK is
## involved: every alias stays on the source model's own rig.

const MODELS := {
    "business_man":"res://assets/poly_pizza/city_pack/Business Man.glb",
    "casual_character":"res://assets/poly_pizza/city_pack/Casual Character.glb",
    "punk":"res://assets/poly_pizza/city_pack/Punk.glb",
    "suit":"res://assets/poly_pizza/city_pack/Suit.glb",
    "worker":"res://assets/poly_pizza/city_pack/Worker.glb",
    "worker_alt":"res://assets/poly_pizza/city_pack/Worker-Yg2bQZO6Hj.glb",
    "adventurer":"res://assets/poly_pizza/city_props/Adventurer.glb",
    "woman_b":"res://assets/poly_pizza/city_props/Animated Woman-nIItLV9nxS.glb",
    "woman_c":"res://assets/poly_pizza/city_props/Animated Woman-qJ2gsTUBHL.glb"
}

const ALIASES := {
    "Idle_Talking":"Idle_Neutral",
    "Interact":"Interact",
    "PickUp_Table":"Interact",
    "Sitting_Talking":"Idle_Neutral",
    "Farm_Harvest":"Interact",
    "Farm_PlantSeed":"Interact",
    "Farm_Watering":"Interact",
    "Walk_Carry_Loop":"Walk",
    "Idle_TalkingPhone_Loop":"Idle_Neutral",
    "Consume":"Idle_Neutral",
    "Chest_Open":"Interact",
    "Yes":"Interact"
}

func _init() -> void:
    call_deferred("_build")

func _find_suffix(player:AnimationPlayer,suffix:String)->String:
    for animation_name in player.get_animation_list():
        if str(animation_name).ends_with("|"+suffix):
            return str(animation_name)
    return ""

func _build() -> void:
    for slug_value in MODELS:
        var slug:=str(slug_value)
        var packed:=load(str(MODELS[slug])) as PackedScene
        if packed==null:
            printerr("NATIVE_ACTION_BUILD_FAILED|missing model|",slug)
            quit(1)
            return
        var character:=packed.instantiate()
        root.add_child(character)
        await process_frame
        var player:=character.find_child("AnimationPlayer",true,false) as AnimationPlayer
        if player==null:
            printerr("NATIVE_ACTION_BUILD_FAILED|missing player|",slug)
            quit(1)
            return
        var library:=AnimationLibrary.new()
        for alias_value in ALIASES:
            var alias:=str(alias_value)
            var source_name:=_find_suffix(player,str(ALIASES[alias]))
            if source_name.is_empty():
                printerr("NATIVE_ACTION_BUILD_FAILED|",slug,"|",alias,"|",ALIASES[alias])
                quit(1)
                return
            var source:=player.get_animation(source_name)
            var duplicate:=source.duplicate(true) as Animation
            # Imported character clips sometimes carry bone scale keys.  They are
            # not needed for the authored pose, but they rescale a BoneAttachment3D
            # (phone/cup/can) during playback.  Preserve translation and rotation
            # exactly; discard only dynamic scale so a real object remains physical.
            for track_index in range(duplicate.get_track_count()-1,-1,-1):
                if duplicate.track_get_type(track_index)==Animation.TYPE_SCALE_3D:
                    duplicate.remove_track(track_index)
            duplicate.loop_mode=Animation.LOOP_LINEAR if alias in ["Idle_Talking","Sitting_Talking","Walk_Carry_Loop","Idle_TalkingPhone_Loop","Consume"] else Animation.LOOP_NONE
            library.add_animation("CityAction|"+alias,duplicate)
        var output:="res://assets/animation_library/native_%s_city_actions.tres"%slug
        var error:=ResourceSaver.save(library,output)
        if error!=OK:
            printerr("NATIVE_ACTION_BUILD_FAILED|save|",slug,"|",error)
            quit(1)
            return
        character.queue_free()
    print("NATIVE_ACTION_BUILD_OK|9 exact-model libraries")
    quit()
