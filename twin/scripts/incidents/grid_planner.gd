extends RefCounted

## Small local A* planner for responders. The caller supplies a blocked(point)
## predicate that already encodes static obstacles, vehicle footprints and
## forbidden areas (open traffic lanes), so a planned path can never cross
## anything the scene considers solid. Paths are string-pulled with the same
## predicate sampled along each shortcut.

const NEIGHBOURS := [
	Vector2i(1, 0), Vector2i(-1, 0), Vector2i(0, 1), Vector2i(0, -1),
	Vector2i(1, 1), Vector2i(1, -1), Vector2i(-1, 1), Vector2i(-1, -1),
]


static func plan(start: Vector3, goal: Vector3, blocked: Callable, cell := 0.5, margin := 14.0, max_nodes := 9000, endpoint_grace := 0.9) -> PackedVector3Array:
	## endpoint_grace lets a responder step away from (or up to) a vehicle door
	## that lies inside the inflated vehicle footprint used for everyone else.
	var origin := Vector2(minf(start.x, goal.x) - margin, minf(start.z, goal.z) - margin)
	var size := Vector2(absf(start.x - goal.x) + margin * 2.0, absf(start.z - goal.z) + margin * 2.0)
	var width := int(ceil(size.x / cell))
	var height := int(ceil(size.y / cell))
	var to_cell := func(point: Vector3) -> Vector2i:
		return Vector2i(clampi(int((point.x - origin.x) / cell), 0, width - 1), clampi(int((point.z - origin.y) / cell), 0, height - 1))
	var to_point := func(index: Vector2i) -> Vector3:
		return Vector3(origin.x + (float(index.x) + 0.5) * cell, start.y, origin.y + (float(index.y) + 0.5) * cell)
	var start_cell: Vector2i = to_cell.call(start)
	var goal_cell: Vector2i = to_cell.call(goal)
	var heap: Array = [[Vector2(start_cell - goal_cell).length(), start_cell]]
	var came_from: Dictionary = {}
	var cost: Dictionary = {start_cell: 0.0}
	var closed: Dictionary = {}
	var blocked_cache: Dictionary = {}
	var expanded := 0
	var found := false
	while not heap.is_empty() and expanded < max_nodes:
		var current: Vector2i = _heap_pop(heap)[1]
		if closed.has(current):
			continue
		if current == goal_cell:
			found = true
			break
		closed[current] = true
		expanded += 1
		for step_value in NEIGHBOURS:
			var step := step_value as Vector2i
			var next := current + step
			if next.x < 0 or next.y < 0 or next.x >= width or next.y >= height or closed.has(next):
				continue
			var next_point: Vector3 = to_point.call(next)
			var near_endpoint := Vector2(next_point.x - start.x, next_point.z - start.z).length() < endpoint_grace or Vector2(next_point.x - goal.x, next_point.z - goal.z).length() < endpoint_grace
			if next != goal_cell and not near_endpoint:
				if not blocked_cache.has(next):
					blocked_cache[next] = bool(blocked.call(to_point.call(next)))
				if bool(blocked_cache[next]):
					continue
			var tentative := float(cost[current]) + (1.4142 if step.x != 0 and step.y != 0 else 1.0)
			if tentative < float(cost.get(next, INF)):
				came_from[next] = current
				cost[next] = tentative
				_heap_push(heap, [tentative + Vector2(next - goal_cell).length(), next])
	if not found:
		return PackedVector3Array()
	var cells: Array[Vector2i] = [goal_cell]
	while cells[0] != start_cell:
		cells.push_front(came_from[cells[0]])
	var raw := PackedVector3Array()
	raw.append(start)
	for index in range(1, cells.size() - 1):
		raw.append(to_point.call(cells[index]))
	raw.append(goal)
	return smooth(raw, blocked, cell * 0.5, endpoint_grace)


static func _heap_push(heap: Array, item: Array) -> void:
	heap.append(item)
	var index := heap.size() - 1
	while index > 0:
		var parent := (index - 1) >> 1
		if float(heap[parent][0]) <= float(item[0]):
			break
		heap[index] = heap[parent]
		index = parent
	heap[index] = item


static func _heap_pop(heap: Array) -> Array:
	var top: Array = heap[0]
	var last: Array = heap.pop_back()
	if heap.is_empty():
		return top
	var index := 0
	var size := heap.size()
	while true:
		var child := index * 2 + 1
		if child >= size:
			break
		if child + 1 < size and float(heap[child + 1][0]) < float(heap[child][0]):
			child += 1
		if float(heap[child][0]) >= float(last[0]):
			break
		heap[index] = heap[child]
		index = child
	heap[index] = last
	return top


static func segment_clear(a: Vector3, b: Vector3, blocked: Callable, spacing: float, grace_points: PackedVector3Array = PackedVector3Array(), grace := 0.0) -> bool:
	var distance := Vector2(a.x - b.x, a.z - b.z).length()
	var steps := maxi(1, int(ceil(distance / spacing)))
	for step in range(1, steps):
		var sample := a.lerp(b, float(step) / float(steps))
		var excused := false
		for grace_point in grace_points:
			if Vector2(sample.x - grace_point.x, sample.z - grace_point.z).length() < grace:
				excused = true
				break
		if not excused and bool(blocked.call(sample)):
			return false
	return true


static func smooth(points: PackedVector3Array, blocked: Callable, spacing: float, endpoint_grace := 0.0) -> PackedVector3Array:
	if points.size() <= 2:
		return points
	var output := PackedVector3Array()
	output.append(points[0])
	var anchor := 0
	while anchor < points.size() - 1:
		var furthest := anchor + 1
		for candidate in range(points.size() - 1, anchor + 1, -1):
			if segment_clear(points[anchor], points[candidate], blocked, spacing, PackedVector3Array([points[0], points[-1]]), endpoint_grace):
				furthest = candidate
				break
		output.append(points[furthest])
		anchor = furthest
	return output
