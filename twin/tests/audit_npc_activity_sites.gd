extends SceneTree

const ROAD_CENTERS := [-380.0,-285.0,-190.0,-95.0,0.0,95.0,190.0,285.0,380.0]
const ROAD_HALF_WIDTH := 7.5

func _init() -> void: call_deferred("_run")
func _run() -> void:
	var city:=(load("res://scenes/City.tscn") as PackedScene).instantiate(); root.add_child(city)
	await process_frame
	var props:=get_nodes_in_group("npc_activity_props")
	props.append_array(get_nodes_in_group("npc_interaction_props"))
	if props.size()!=70: return _fail("expected 64 job assets plus 6 shared interaction assets, got %d"%props.size())
	for value in props:
		var prop:=value as Node3D; var bounds:=_world_bounds(prop)
		if bounds.size==Vector3.ZERO: return _fail("%s has no renderable model"%prop.name)
		# Small equipment is intentionally human scale (a flower pot, parcel or
		# cone is not a one-metre object); exact semantic sizes are covered by the
		# dedicated prop-scale test. This spatial audit rejects only unusable or
		# implausibly huge station geometry.
		var largest:=maxf(bounds.size.x,maxf(bounds.size.y,bounds.size.z))
		if largest<0.20: return _fail("%s is too small for its human interaction"%prop.name)
		if largest>4.5: return _fail("%s is too large for its human interaction"%prop.name)
		if _overlaps_road(bounds): return _fail("%s overlaps a traffic corridor"%prop.name)
	print("NPC ACTIVITY-SITE AUDIT PASSED: 70 scaled job and interaction assets stay outside every road corridor")
	quit(0)

func _world_bounds(owner:Node3D)->AABB:
	var found:=false; var result:=AABB()
	for value in owner.find_children("*","MeshInstance3D",true,false):
		var mesh_node:=value as MeshInstance3D
		if mesh_node.mesh==null: continue
		var bounds:=mesh_node.global_transform*mesh_node.mesh.get_aabb()
		result=bounds if not found else result.merge(bounds); found=true
	return result if found else AABB()

func _overlaps_road(bounds:AABB)->bool:
	for center in ROAD_CENTERS:
		if bounds.position.x<center+ROAD_HALF_WIDTH and bounds.end.x>center-ROAD_HALF_WIDTH: return true
		if bounds.position.z<center+ROAD_HALF_WIDTH and bounds.end.z>center-ROAD_HALF_WIDTH: return true
	return false

func _fail(message:String)->void:
	printerr("NPC ACTIVITY-SITE AUDIT FAILED: "+message); quit(1)
