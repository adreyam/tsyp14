extends Node3D

## Reserve six existing green utility parcels in the live city. Clearing only
## decorative park trees preserves road, building, pedestrian, and vehicle
## layouts while making each reservoir compound visibly and physically clear.

func _ready() -> void:
	var city := get_parent()
	if city == null:
		return
	for site_value in get_tree().get_nodes_in_group("water_reservoir_sites"):
		var site := site_value as Node3D
		var block_name := str(site.get_meta("park_block", ""))
		var park := city.get_node_or_null("CityBackdrop/OuterDistricts/%s" % block_name) as Node3D
		if park == null:
			push_error("Reservoir parcel %s is missing" % block_name)
			continue
		park.set_meta("zone", "water_utility")
		for tree_value in park.find_children("*", "Node3D", true, false):
			var tree := tree_value as Node3D
			if tree.is_in_group("city_trees"):
				tree.visible = false
