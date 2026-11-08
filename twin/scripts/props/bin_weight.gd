extends RefCounted

## Load-cell weight sensors on the city's waste bins. State lives in node metadata so any system (citizens,
## the refuse trucks, the sensor tab, the MCP interface) reads and changes the same live numbers:
##   bin_weight_kg, bin_capacity_kg, bin_deposits, bin_last_deposit_s (simulation clock seconds), bin_kind.

const STREET_CAN_CAPACITY_KG := 30.0
const CONTAINER_CAPACITY_KG := 240.0
const ITEM_WEIGHT_KG := {"can": 0.34, "cup": 0.26, "waste_bag": 2.4, "parcel": 1.1, "document": 0.08, "newspaper": 0.25}
const METRICS := ["bin_weight", "fill_level", "deposits"]

static func register(bin: Node3D, bin_kind: String, number: int) -> void:
	if bin.has_meta("bin_weight_kg"):
		return
	var capacity := CONTAINER_CAPACITY_KG if bin_kind == "container" else STREET_CAN_CAPACITY_KG
	var seed_fraction := fposmod(sin(float(number) * 12.9898 + (78.233 if bin_kind == "container" else 0.0)) * 43758.5453, 1.0)
	bin.set_meta("bin_kind", bin_kind)
	if bin_kind == "container":
		# The container's lid hinge is on the pavement side and its lifter comb faces the road: people use it from behind.
		bin.set_meta("prop_size", Vector3(1.36, 1.32, 1.06))
		bin.set_meta("prop_kind", "garbage_container")
		bin.set_meta("urban_prop_kind", "garbage_container")
		bin.set_meta("use_side", -1.0)
	bin.set_meta("bin_capacity_kg", capacity)
	bin.set_meta("bin_weight_kg", snappedf(capacity * (0.04 + seed_fraction * 0.46), 0.01))
	bin.set_meta("bin_deposits", 0)
	bin.set_meta("bin_last_deposit_s", -1.0)
	bin.set_meta("sensor_id", "WGT-%s%03d" % ["C" if bin_kind == "container" else "T", number])
	bin.set_meta("asset_type", "bin_weight_sensor")
	bin.set_meta("sensed_metrics", METRICS)
	bin.set_meta("highlightable", true)
	bin.add_to_group("sensors")
	bin.add_to_group("bin_weight_sensors")

static func weight_of(bin: Node) -> float:
	return float(bin.get_meta("bin_weight_kg", 0.0))

static func capacity_of(bin: Node) -> float:
	return maxf(float(bin.get_meta("bin_capacity_kg", STREET_CAN_CAPACITY_KG)), 0.001)

static func fill_of(bin: Node) -> float:
	return clampf(weight_of(bin) / capacity_of(bin), 0.0, 1.2)

static func has_room(bin: Node, extra_kg := 0.5) -> bool:
	return bin.has_meta("bin_weight_kg") and weight_of(bin) + extra_kg <= capacity_of(bin) * 0.95

static func weight_for(item_kind: String) -> float:
	return float(ITEM_WEIGHT_KG.get(item_kind, 0.3))

## A citizen's waste lands in the bin: the load cell reads the extra weight on the very next sample.
static func deposit(bin: Node, kilograms: float, clock_seconds := 0.0) -> float:
	if not bin.has_meta("bin_weight_kg"):
		return 0.0
	var next := minf(weight_of(bin) + kilograms, capacity_of(bin) * 1.05)
	bin.set_meta("bin_weight_kg", snappedf(next, 0.01))
	bin.set_meta("bin_deposits", int(bin.get_meta("bin_deposits", 0)) + 1)
	bin.set_meta("bin_last_deposit_s", clock_seconds)
	return next

## A collection crew tips the bin: the platform reads empty again (the liner keeps a little residue).
static func empty(bin: Node) -> float:
	if not bin.has_meta("bin_weight_kg"):
		return 0.0
	var removed := weight_of(bin)
	bin.set_meta("bin_weight_kg", snappedf(minf(removed, 0.2), 0.01))
	bin.set_meta("bin_last_emptied_msec", Time.get_ticks_msec())
	return removed - weight_of(bin)

static func readings(bin: Node) -> Dictionary:
	return {
		"bin_weight": {"value": snappedf(weight_of(bin), 0.01), "unit": "kg"},
		"fill_level": {"value": snappedf(fill_of(bin) * 100.0, 0.1), "unit": "%"},
		"deposits": {"value": float(int(bin.get_meta("bin_deposits", 0))), "unit": "items"},
	}

## World point just inside the bin's opening, where thrown waste lands.
static func drop_point(bin: Node3D) -> Vector3:
	var anchors := bin.get_meta("anchors", {}) as Dictionary
	for name in ["drop", "rim"]:
		var marker := anchors.get(name) as Node3D
		if marker != null:
			return marker.global_position
	var height := 1.25 if str(bin.get_meta("bin_kind", "can")) == "container" else float((bin.get_meta("prop_size", Vector3(0.5, 0.9, 0.5)) as Vector3).y) * 0.95
	return bin.global_position + Vector3(0.0, height, 0.0)

## A scenario fills the bin to a level (fraction of capacity): the load cell reads it on the next sample.
static func fill_to(bin: Node, fraction: float) -> float:
	if not bin.has_meta("bin_weight_kg"):
		return 0.0
	var target := clampf(fraction, 0.0, 1.05) * capacity_of(bin)
	if target > weight_of(bin):
		bin.set_meta("bin_deposits", int(bin.get_meta("bin_deposits", 0)) + 1)
	bin.set_meta("bin_weight_kg", snappedf(target, 0.01))
	return snappedf(fill_of(bin) * 100.0, 0.1)
