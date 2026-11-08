extends RefCounted

## The one geometric definition of how an emergency unit leaves and enters its
## station bay. The siting tool validates exactly these swept footprints against
## the static world, and EmergencyFleetPresenter drives exactly these curves, so
## a unit can never visibly clip anything the validation did not check.

const RoadGeometry := preload("res://scripts/incidents/road_geometry.gd")
const SAMPLES := 24


static func pull_out_curve(edge: Dictionary, bay: Dictionary) -> Array[Vector3]:
	## Control points bay → lane at exit_pos (forward, S-curve).
	var start := Vector3(float(bay.x), 0.0, float(bay.z))
	var finish := RoadGeometry.lane_point(edge, float(bay.exit_pos))
	return _controls(start, finish, edge.direction as Vector3)


static func turn_in_curve(edge: Dictionary, bay: Dictionary) -> Array[Vector3]:
	## Control points lane at arrive_pos → bay (forward, S-curve).
	var start := RoadGeometry.lane_point(edge, float(bay.arrive_pos))
	var finish := Vector3(float(bay.x), 0.0, float(bay.z))
	return _controls(start, finish, edge.direction as Vector3)


static func _controls(start: Vector3, finish: Vector3, direction: Vector3) -> Array[Vector3]:
	var reach := (finish - start).dot(direction) * 0.46
	return [start, start + direction * reach, finish - direction * reach, finish]


static func point(controls: Array[Vector3], t: float) -> Vector3:
	var u := 1.0 - t
	return controls[0] * u * u * u + controls[1] * 3.0 * u * u * t + controls[2] * 3.0 * u * t * t + controls[3] * t * t * t


static func tangent(controls: Array[Vector3], t: float) -> Vector3:
	var u := 1.0 - t
	var derivative := (controls[1] - controls[0]) * 3.0 * u * u + (controls[2] - controls[1]) * 6.0 * u * t + (controls[3] - controls[2]) * 3.0 * t * t
	derivative.y = 0.0
	return derivative.normalized() if derivative.length_squared() > 0.000001 else Vector3.FORWARD


static func yaw_at(controls: Array[Vector3], t: float) -> float:
	return RoadGeometry.yaw_of(tangent(controls, t))


static func length(controls: Array[Vector3]) -> float:
	var total := 0.0
	var previous := controls[0]
	for index in range(1, SAMPLES + 1):
		var current := point(controls, float(index) / float(SAMPLES))
		total += previous.distance_to(current)
		previous = current
	return total


static func sweep_blockers(index, controls: Array[Vector3], body_length: float, body_width: float, margin: float, ignored: Array = []) -> Array[String]:
	## Static obstacles touched by the vehicle body anywhere along the curve.
	var blockers: Array[String] = []
	for sample in SAMPLES + 1:
		var t := float(sample) / float(SAMPLES)
		for label in index.oriented_box_blockers(point(controls, t), yaw_at(controls, t), body_length, body_width, margin, ignored):
			if not blockers.has(label):
				blockers.append(label)
	return blockers
