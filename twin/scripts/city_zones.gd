extends RefCounted

## The city's 16 zones. The 8 x 8 block grid (95 m blocks, 380 m half extent) is cut into a 4 x 4 grid of zones of
## 2 x 2 blocks (190 m square). Zone A is the north-west corner and the letters run along the rows, so
##   A B C D
##   E F G H
##   I J K L
##   M N O P

const HALF_EXTENT := 380.0
const ZONE_SIZE := 190.0
const COLUMNS := 4
const IDS := ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P"]

static func zone_of(x: float, z: float) -> String:
	var column := clampi(floori((x + HALF_EXTENT) / ZONE_SIZE), 0, COLUMNS - 1)
	var row := clampi(floori((z + HALF_EXTENT) / ZONE_SIZE), 0, COLUMNS - 1)
	return IDS[row * COLUMNS + column]

static func zone_of_position(position: Vector3) -> String:
	return zone_of(position.x, position.z)

static func is_zone(zone: String) -> bool:
	return zone.to_upper() in IDS

static func centre(zone: String) -> Vector3:
	var index := IDS.find(zone.to_upper())
	if index < 0:
		return Vector3.ZERO
	return Vector3(-HALF_EXTENT + (index % COLUMNS + 0.5) * ZONE_SIZE, 0.0, -HALF_EXTENT + (index / COLUMNS + 0.5) * ZONE_SIZE)

static func rect(zone: String) -> Rect2:
	var middle := centre(zone)
	return Rect2(middle.x - ZONE_SIZE * 0.5, middle.z - ZONE_SIZE * 0.5, ZONE_SIZE, ZONE_SIZE)

static func neighbours(zone: String) -> Array:
	var index := IDS.find(zone.to_upper())
	var out: Array = []
	if index < 0:
		return out
	var row := index / COLUMNS
	var column := index % COLUMNS
	for step in [Vector2i(-1, 0), Vector2i(1, 0), Vector2i(0, -1), Vector2i(0, 1)]:
		var r: int = row + step.x
		var c: int = column + step.y
		if r >= 0 and r < COLUMNS and c >= 0 and c < COLUMNS:
			out.append(IDS[r * COLUMNS + c])
	return out

static func label(zone: String) -> String:
	return "Zone %s" % zone.to_upper()
