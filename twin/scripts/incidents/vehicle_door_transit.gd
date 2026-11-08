extends RefCounted

## Getting into and out of a vehicle through a real door, for every crew
## member, driver and ride-along: the door swings open on its hinge, the
## person rises out of the seat and steps through the opening to the kerb
## (or steps in and sits down), and the door swings shut behind them. People
## inside sit below the roof line behind the tinted glass, so nobody appears
## from nowhere or vanishes in the open. Doors are shared: a door opens for
## its first user and closes after its last.

const ProceduralVehicle := preload("res://scripts/vehicles/procedural_vehicle.gd")
const STEP_SECONDS := 0.75
const REACH_SECONDS := 0.5


static func slot_for(vehicle: Node3D, index: int, side := 0.0) -> Dictionary:
	## The door the index-th occupant uses on side (+1 the vehicle's +X side,
	## -1 its -X side, 0 either): front doors first, then rear doors.
	if not _usable(vehicle):
		return {}
	var slots := ProceduralVehicle.door_slots(vehicle).filter(func(slot: Dictionary) -> bool: return side == 0.0 or float(slot.side) == side)
	slots.sort_custom(func(a: Dictionary, b: Dictionary) -> bool: return int(a.bit) < int(b.bit))
	return {} if slots.is_empty() else slots[index % slots.size()] as Dictionary


static func nearest_slot(vehicle: Node3D, point: Vector3) -> Dictionary:
	if not _usable(vehicle):
		return {}
	var best := {}
	var best_distance := INF
	for slot in ProceduralVehicle.door_slots(vehicle):
		var outside := slot.outside as Vector3
		var distance := Vector2(outside.x - point.x, outside.z - point.z).length()
		if distance < best_distance:
			best_distance = distance
			best = slot
	return best


static func occupants_per_door(vehicle: Node3D, side := 0.0) -> int:
	return maxi(1, ProceduralVehicle.door_slots(vehicle).filter(func(slot: Dictionary) -> bool: return side == 0.0 or float(slot.side) == side).size()) if _usable(vehicle) else 1


static func alight(vehicle: Node3D, agent: Node, slot: Dictionary, delay: float, done: Callable) -> void:
	## agent starts seated inside; done runs once it stands outside the door.
	agent.call("place", slot.seat as Vector3, float(slot.yaw_out))
	agent.call("set_seated_drop", ProceduralVehicle.SEATED_DROP)
	if delay > 0.0:
		await agent.get_tree().create_timer(delay).timeout
	if not is_instance_valid(agent):
		return
	if not _usable(vehicle):
		agent.call("place", slot.outside as Vector3, float(slot.yaw_out))
		agent.call("set_seated_drop", 0.0)
		done.call()
		return
	await _open(vehicle, int(slot.bit), agent)
	if not is_instance_valid(agent):
		_close(vehicle, int(slot.bit))
		return
	agent.call("transit", slot.outside as Vector3, STEP_SECONDS, 0.0, float(slot.yaw_out))
	await agent.transit_finished
	_close(vehicle, int(slot.bit))
	done.call()


static func board(vehicle: Node3D, agent: Node, slot: Dictionary, done: Callable) -> void:
	## agent stands near the door; done runs once it sits inside, door shut.
	if not _usable(vehicle) or slot.is_empty():
		done.call()
		return
	var outside := slot.outside as Vector3
	var position_value := agent.call("position") as Vector3
	if Vector2(outside.x - position_value.x, outside.z - position_value.z).length() > 0.15:
		agent.call("transit", outside, 0.5, 0.0, float(slot.yaw_out) + PI)
		await agent.transit_finished
	if not is_instance_valid(agent):
		return
	# Reach for the handle while the door swings open.
	agent.call("perform", "TryDoor", REACH_SECONDS, slot.seat as Vector3)
	await _open(vehicle, int(slot.bit), agent)
	if not is_instance_valid(agent):
		_close(vehicle, int(slot.bit))
		return
	agent.call("transit", slot.seat as Vector3, STEP_SECONDS, ProceduralVehicle.SEATED_DROP, float(slot.yaw_out) + PI)
	await agent.transit_finished
	_close(vehicle, int(slot.bit))
	await agent.get_tree().create_timer(ProceduralVehicle.DOOR_SECONDS).timeout
	done.call()


static func _usable(vehicle: Node3D) -> bool:
	## Only an upright procedural vehicle has doors to use (a rolled wreck is
	## left the old way).
	return is_instance_valid(vehicle) and vehicle.has_meta("procedural_key") and vehicle.global_transform.basis.y.normalized().dot(Vector3.UP) > 0.9


static func _open(vehicle: Node3D, bit: int, waiter: Node) -> void:
	var users := vehicle.get_meta("door_users", {}) as Dictionary
	var count := int(users.get(bit, 0))
	users[bit] = count + 1
	vehicle.set_meta("door_users", users)
	if count == 0:
		ProceduralVehicle.swing_door(vehicle, bit, true)
		await waiter.get_tree().create_timer(ProceduralVehicle.DOOR_SECONDS).timeout


static func _close(vehicle: Node3D, bit: int) -> void:
	if not is_instance_valid(vehicle):
		return
	var users := vehicle.get_meta("door_users", {}) as Dictionary
	var count := maxi(int(users.get(bit, 1)) - 1, 0)
	users[bit] = count
	vehicle.set_meta("door_users", users)
	if count == 0:
		ProceduralVehicle.swing_door(vehicle, bit, false)
