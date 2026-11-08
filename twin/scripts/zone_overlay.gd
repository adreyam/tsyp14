extends Node3D

## Marks the 16 city zones on the ground: a very faint colour wash per zone and a large letter above its centre, so
## the overview camera reads the city as zones A to P. Both are presentation only (no collisions, no shadows).
## Everything starts hidden: the operator shows all zones or only chosen ones (Assets tab, or the device interface).

const Zones := preload("res://scripts/city_zones.gd")
const WASH_Y := 0.215
const LETTER_Y := 62.0

func _ready() -> void:
	name = "ZoneOverlay"
	for index in Zones.IDS.size():
		var zone := str(Zones.IDS[index])
		var colour := Color.from_hsv(fmod(float(index) * 0.17 + 0.05, 1.0), 0.55, 0.95)
		var centre := Zones.centre(zone)
		var wash := MeshInstance3D.new()
		wash.name = "ZoneWash_%s" % zone
		var quad := PlaneMesh.new()
		quad.size = Vector2(Zones.ZONE_SIZE - 1.0, Zones.ZONE_SIZE - 1.0)
		wash.mesh = quad
		var material := StandardMaterial3D.new()
		material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
		material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
		material.albedo_color = Color(colour.r, colour.g, colour.b, 0.085)
		material.no_depth_test = false
		material.disable_receive_shadows = true
		wash.material_override = material
		wash.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		wash.position = Vector3(centre.x, WASH_Y, centre.z)
		wash.set_meta("zone", zone)
		wash.visible = false
		add_child(wash)
		var letter := Label3D.new()
		letter.name = "ZoneLetter_%s" % zone
		letter.text = zone
		letter.font_size = 360
		letter.pixel_size = 0.075
		letter.outline_size = 40
		letter.billboard = BaseMaterial3D.BILLBOARD_ENABLED
		letter.modulate = Color(colour.r, colour.g, colour.b, 0.9)
		letter.outline_modulate = Color(0.05, 0.08, 0.12, 0.95)
		letter.no_depth_test = false
		letter.shaded = false
		letter.fixed_size = false
		letter.position = Vector3(centre.x, LETTER_Y, centre.z)
		letter.set_meta("zone", zone)
		letter.visible = false
		add_child(letter)

func set_zone_visible(zone: String, shown: bool) -> void:
	for child in get_children():
		if str(child.get_meta("zone", "")) == zone.to_upper():
			(child as Node3D).visible = shown

func set_all_visible(shown: bool) -> void:
	for zone in Zones.IDS:
		set_zone_visible(str(zone), shown)

func shown_zones() -> Array:
	var out: Array = []
	for child in get_children():
		if child is MeshInstance3D and (child as Node3D).visible:
			out.append(str(child.get_meta("zone", "")))
	out.sort()
	return out
