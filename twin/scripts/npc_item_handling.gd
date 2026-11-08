extends RefCounted

## Physical custody of every handled object (parcels, papers, waste bags,
## cups, watering cans, exchange items).
##
## An object is always somewhere real: resting on a surface (a crate, a
## bench seat, the pavement, a mailbox shelf) or carried. It changes between
## the two only when the citizen's hand actually reaches it — the moment is
## taken from the live hand position, not from a clip name — and the move is
## a short continuous blend, never a pop. Boxes and papers are carried between
## both hands whenever the animation brings the hands together; otherwise in
## the right-hand finger cradle. In exchanges the object travels from the
## giver's hands to the receiver's.

const GRAB_REACH := 0.32
const PUT_REACH := 0.30
const MOVE_SECONDS := 0.28
const HANDOVER_SECONDS := 0.55
const FALLBACK_FRACTION := 0.55
const TWO_HAND_KINDS := ["parcel", "medical_supply", "market_goods", "document", "notice", "mail"]
const TWO_HAND_GAP := Vector2(0.16, 0.62)
const LEFT_GRIP_FINGER_JOINTS := ["Bip01 L Finger11", "Bip01 L Finger21", "Bip01 L Finger31", "Bip01 L Finger41"]

var _items: Array[Dictionary] = []
var _left_anchors := {}
var _container: Node3D


func _init(container: Node3D) -> void:
	_container = container


func register(model: Node3D, kind: String, owner_index: int, rest: Transform3D, surface_y: float) -> Dictionary:
	## model must currently sit in its right-hand finger-cradle anchor; that
	## local pose is kept as the one-hand carry pose. The object then lives in
	## the world container and is posed every frame from its custody state.
	var anchor := model.get_parent() as Node3D
	var held_local := model.transform
	model.get_parent().remove_child(model)
	_container.add_child(model)
	var entry := {
		"node": model, "kind": kind, "anchor": anchor, "held_local": held_local,
		"holder": -1, "owner": owner_index, "state": "rest", "rest": rest, "surface_y": surface_y,
		"from": rest, "t": 0.0, "target": "rest", "target_holder": -1,
	}
	_place_world(entry, rest)
	model.visible = true
	model.set_meta("npc_carry", "rest")
	model.set_meta("npc_held_local", held_local)
	model.set_meta("npc_surface_y", surface_y)
	_items.append(entry)
	return entry


func is_held_by(entry: Dictionary, agent_index: int) -> bool:
	## In (or on its way into) this person's hands.
	return int(entry.holder) == agent_index and str(entry.target) == "hand"


func take(entry: Dictionary, agent: Dictionary, agent_index: int, immediate := false, seconds := MOVE_SECONDS) -> void:
	## Start moving the object into agent's hands (from its rest or from
	## another holder's hands).
	if int(entry.holder) == agent_index and str(entry.target) == "hand":
		return
	entry.from = (entry.node as Node3D).global_transform
	entry.t = 1.0 if immediate else 0.0
	entry.target = "hand"
	entry.holder = agent_index
	entry.state = "moving"
	entry.seconds = seconds


func put(entry: Dictionary, rest: Transform3D, surface_y: float) -> void:
	if str(entry.target) == "rest":
		return
	entry.from = (entry.node as Node3D).global_transform
	entry.t = 0.0
	entry.seconds = MOVE_SECONDS
	entry.target = "rest"
	entry.rest = rest
	entry.surface_y = surface_y
	entry.state = "moving"


func hand_reach(agent: Dictionary, point: Vector3) -> float:
	var anchor := right_cradle(agent)
	return anchor.global_position.distance_to(point) if anchor != null else INF


func right_cradle(agent: Dictionary) -> Node3D:
	return agent.get("cradle") as Node3D


func left_cradle(agent: Dictionary) -> Node3D:
	var person := agent.node as Node3D
	var key := person.get_instance_id()
	if _left_anchors.has(key):
		return _left_anchors[key] as Node3D
	var skeleton := agent.skeleton as Skeleton3D
	var hand := skeleton.find_bone("Bip01 L Hand")
	if hand < 0:
		return null
	var attachment := BoneAttachment3D.new()
	attachment.name = "LeftHandCradle"
	attachment.bone_name = "Bip01 L Hand"
	skeleton.add_child(attachment)
	var anchor := Node3D.new()
	anchor.name = "LeftFingerGrip"
	attachment.add_child(anchor)
	var centre := Vector3.ZERO
	var found := 0
	for bone_name in LEFT_GRIP_FINGER_JOINTS:
		var index := skeleton.find_bone(bone_name)
		if index >= 0:
			centre += skeleton.get_bone_global_pose(index).origin
			found += 1
	if found > 0:
		anchor.position = skeleton.get_bone_global_pose(hand).affine_inverse() * (centre / float(found))
	_left_anchors[key] = anchor
	return anchor


func update(delta: float, agents: Array[Dictionary]) -> void:
	## Runs after animation evaluation every frame.
	for entry in _items:
		var node := entry.node as Node3D
		if not is_instance_valid(node):
			continue
		var target: Transform3D
		if str(entry.target) == "rest":
			target = entry.rest as Transform3D
		else:
			target = _carry_transform(entry, agents[int(entry.holder)])
		if str(entry.state) == "moving":
			entry.t = minf(float(entry.t) + delta / float(entry.get("seconds", MOVE_SECONDS)), 1.0)
			var eased := float(entry.t) * float(entry.t) * (3.0 - 2.0 * float(entry.t))
			node.global_transform = (entry.from as Transform3D).interpolate_with(target, eased)
			if float(entry.t) >= 1.0:
				entry.state = "rest" if str(entry.target) == "rest" else "held"
				if str(entry.state) == "rest":
					entry.holder = -1
					node.set_meta("npc_surface_y", float(entry.surface_y))
				node.set_meta("npc_carry", "rest" if str(entry.state) == "rest" else str(entry.get("carry", "right_hand")))
		elif str(entry.state) == "held":
			node.global_transform = target
			node.set_meta("npc_carry", str(entry.get("carry", "right_hand")))
			node.set_meta("npc_holder_skeleton", (agents[int(entry.holder)] as Dictionary).skeleton)
		else:
			node.global_transform = target


func _carry_transform(entry: Dictionary, agent: Dictionary) -> Transform3D:
	var right := agent.get("cradle") as Node3D
	var one_hand := (right.global_transform * (entry.held_local as Transform3D)) if right != null else (entry.node as Node3D).global_transform
	entry.carry = "right_hand"
	var left := left_cradle(agent) if right != null else null
	if right == null or left == null:
		return one_hand
	# A one-hand hold goes in whichever hand is reaching forward: a person
	# presenting with the left hand holds the object out in that hand.
	var body := agent.node as Node3D
	var body_forward := body.global_transform.basis.z.normalized()
	var right_reach := (right.global_position - body.global_position).dot(body_forward)
	var left_reach := (left.global_position - body.global_position).dot(body_forward)
	if left_reach > right_reach + 0.12:
		var mirror := Transform3D(Basis.from_scale(Vector3(-1, 1, 1)), Vector3.ZERO)
		one_hand = left.global_transform * mirror * (entry.held_local as Transform3D) * mirror
		one_hand.basis = one_hand.basis.orthonormalized().scaled((entry.node as Node3D).global_transform.basis.get_scale())
		entry.carry = "left_hand"
	if not str(entry.kind) in TWO_HAND_KINDS:
		return one_hand
	var gap := right.global_position.distance_to(left.global_position)
	if gap < TWO_HAND_GAP.x or gap > TWO_HAND_GAP.y:
		return one_hand
	# Held between both hands: centred between the finger cradles, level, its
	# width across the hands and facing the way the body faces.
	var across := (left.global_position - right.global_position).normalized()
	var up := Vector3.UP - across * across.dot(Vector3.UP)
	if up.length_squared() < 0.01:
		return one_hand
	up = up.normalized()
	var forward := across.cross(up).normalized()
	var basis := Basis(across, up, forward)
	var node := entry.node as Node3D
	var size := _local_size(node)
	var centre := (right.global_position + left.global_position) * 0.5 - up * size.y * 0.1
	entry.carry = "two_hands"
	return Transform3D(basis.scaled(node.global_transform.basis.get_scale()), centre - basis * (_local_centre(node) * node.global_transform.basis.get_scale()))


func _place_world(entry: Dictionary, rest: Transform3D) -> void:
	(entry.node as Node3D).global_transform = rest


static func _local_size(node: Node3D) -> Vector3:
	return _local_bounds(node).size * node.global_transform.basis.get_scale()


static func _local_centre(node: Node3D) -> Vector3:
	return _local_bounds(node).get_center()


static func _local_bounds(node: Node3D) -> AABB:
	if node.has_meta("npc_local_bounds"):
		return node.get_meta("npc_local_bounds") as AABB
	var inverse := node.global_transform.affine_inverse()
	var result := AABB()
	var found := false
	for value in node.find_children("*", "MeshInstance3D", true, false):
		var mesh := value as MeshInstance3D
		if mesh.mesh == null:
			continue
		var bounds := (inverse * mesh.global_transform) * mesh.mesh.get_aabb()
		result = bounds if not found else result.merge(bounds)
		found = true
	node.set_meta("npc_local_bounds", result)
	return result


static func rest_on_surface(node: Node3D, point: Vector3, yaw: float) -> Transform3D:
	## Upright resting pose whose lowest point touches the surface at point.
	var bounds := _local_bounds(node)
	var scale_value := node.global_transform.basis.get_scale()
	var basis := Basis(Vector3.UP, yaw).scaled(scale_value)
	var bottom_centre := Vector3(bounds.get_center().x, bounds.position.y, bounds.get_center().z)
	return Transform3D(basis, point - basis * bottom_centre)
