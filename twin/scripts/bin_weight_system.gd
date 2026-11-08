extends Node

## Installs a load-cell weight sensor on every street waste can and every green refuse container, and keeps
## the registry current as the incident layer builds its containers.

const BinWeight := preload("res://scripts/props/bin_weight.gd")
const SCAN_INTERVAL := 2.0
## Street furniture is pruned for device overlaps in the first moments; cans are fitted once it has settled.
const CAN_SETTLE_SECONDS := 1.0

var _elapsed := SCAN_INTERVAL - CAN_SETTLE_SECONDS
var _age := 0.0
var _can_count := 0
var _container_count := 0

func _ready() -> void:
	call_deferred("scan")

func _process(delta: float) -> void:
	_age += delta
	_elapsed += delta
	if _elapsed < SCAN_INTERVAL:
		return
	_elapsed = 0.0
	scan()

func scan() -> void:
	var cans: Array[Node3D] = []
	if _age >= CAN_SETTLE_SECONDS:
		for value in get_tree().get_nodes_in_group("urban_props"):
			var node := value as Node3D
			if node != null and str(node.get_meta("urban_prop_kind", "")) == "trash" and not node.has_meta("bin_weight_kg") and node.visible and not node.is_queued_for_deletion():
				cans.append(node)
	var containers: Array[Node3D] = []
	for value in get_tree().get_nodes_in_group("garbage_bins"):
		if value is Node3D and not (value as Node3D).has_meta("bin_weight_kg"):
			containers.append(value as Node3D)
	cans.sort_custom(_by_position)
	containers.sort_custom(_by_position)
	for can in cans:
		_can_count += 1
		BinWeight.register(can, "can", _can_count)
	for container in containers:
		_container_count += 1
		BinWeight.register(container, "container", _container_count)

func _by_position(a: Node3D, b: Node3D) -> bool:
	var pa := a.global_position
	var pb := b.global_position
	return pa.x < pb.x if not is_equal_approx(pa.x, pb.x) else pa.z < pb.z

func clock_seconds() -> float:
	return Time.get_ticks_msec() / 1000.0
