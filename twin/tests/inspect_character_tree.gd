extends SceneTree
func _init() -> void: call_deferred("_run")
func _run() -> void:
	var scene:=(load("res://assets/poly_pizza/city_pack/Worker.glb") as PackedScene).instantiate(); root.add_child(scene); await process_frame; _print(scene,0); quit()
func _print(node:Node,depth:int)->void:
	print("  ".repeat(depth),node.name," [",node.get_class(),"]")
	for child in node.get_children(): _print(child,depth+1)
