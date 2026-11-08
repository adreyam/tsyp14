extends RefCounted

## Activities built entirely from Microsoft Rocketbox motion-capture clips (assets/rocketbox/clips_*.res).
## Nothing is posed by code: a scene is a list of beats, each beat one real performance (sit down, idle while
## seated, stand up, crouch, work at waist/chest height, phone call, drink, read, photograph ...). The prop is
## met by PLACEMENT: clip_stats.json says where the actor's hands were, so the person stands where those hands
## land on the prop's working surface; for seats the capture's own step-back lands the pelvis on the seat.

const STATS_PATH := "res://assets/rocketbox/clip_stats.json"
static var _stats := {}

static func stats(clip: String) -> Dictionary:
	if _stats.is_empty():
		var file := FileAccess.open(STATS_PATH, FileAccess.READ)
		_stats = JSON.parse_string(file.get_as_text()) as Dictionary
	return ((_stats.get(clip, {}) as Dictionary).get("m", {})) as Dictionary

static func clip_length(clip: String) -> float:
	return float(stats(clip).get("length", 3.0))

const IDLE_POOL := ["idle_neutral_01", "idle_look_around_01", "idle_breathe_01", "idle_look_around_02", "idle_waiting_01", "idle_stretch_arms_01", "idle_waiting_02",
	"idle_shake_leg_01", "idle_nervous_01", "idle_touch_hair_01", "idle_look_around_03", "idle_yawn_01", "idle_scratch_head_01", "idle_neutral_02", "idle_roll_head_01", "idle_dust_01"]
const SIT_POOL := ["sit_chair_idle_neutral_01", "sit_chair_idle_look_around", "sit_chair_idle_relaxed_01", "sit_chair_idle_waiting_01", "sit_chair_idle_touch_hair",
	"sit_chair_idle_yawn", "sit_chair_idle_neutral_02", "sit_chair_idle_scratch_head", "sit_chair_idle_touch_face", "sit_chair_idle_roll_head", "sit_chair_idle_waiting_02", "sit_chair_gestic_thoughtful"]
const TABLE_POOL := ["sit_table_idle_neutral_01", "sit_table_idle_look_around", "sit_table_idle_relaxed_01", "sit_table_idle_waiting_01", "sit_table_idle_touch_hair",
	"sit_table_idle_yawn", "sit_table_idle_neutral_02", "sit_table_idle_scratch_head", "sit_table_idle_touch_face", "sit_table_gestic_thoughtful"]

## Which kinds are seats, and which scene a stage uses at each kind.
const SEAT_KINDS := ["bench"]
const TABLE_SEAT_KINDS := ["picnic_table"]
const PHOTO_KINDS := ["poster", "billboard"]
const DOOR_KINDS := ["fire_exit"]
const BIN_KINDS := ["trash", "garbage_container"]
const LITTER_ITEMS := ["can", "cup", "waste_bag"]
const LOW_KINDS := ["hydrant", "manhole", "water_meter", "cone", "bicycle", "flower_pot", "box", "toolbox", "extinguisher", "planter", "wheelchair_low"]
const TABLE_KINDS := ["market_counter", "tool_cart", "greenhouse", "coffee_cart", "dumpster", "sandwich_board", "planter"]
const PANEL_KINDS := ["power", "power_meter", "aircon", "mailbox", "atm", "ladder", "parking_meter", "bus_stop", "drinking_fountain"]
const PHONE_KINDS := ["payphone"]
const DRINK_KINDS := ["vending_machine"]
const TROLLEY_KINDS := ["hand_truck", "wheelchair"]

static func _pick(pool: Array, seed_value: int, count: int, avoid := "") -> Array:
	var out: Array = []
	var index := absi(seed_value)
	while out.size() < count:
		var candidate := str(pool[index % pool.size()])
		index += 3
		if candidate != avoid and not candidate in out:
			out.append(candidate)
	return out

static func _beat(clip: String, hold := 0.0, extra := {}) -> Dictionary:
	var beat := {"name": clip, "clip": clip, "hold": hold}
	beat.merge(extra, true)
	return beat

## One posture class per prop kind (so the person never has to shuffle between stages); the stage index only
## picks which performance of that class is shown.
static func scene_class(kind: String, _stage_index := 0) -> String:
	if kind in SEAT_KINDS: return "sit"
	if kind in TABLE_SEAT_KINDS: return "sit_table"
	if kind in PHOTO_KINDS: return "photo"
	if kind in DOOR_KINDS: return "door"
	if kind in BIN_KINDS: return "dispose"
	if kind in PHONE_KINDS: return "phone"
	if kind in DRINK_KINDS: return "drink"
	if kind in TROLLEY_KINDS: return "trolley"
	if kind in LOW_KINDS: return "crouch"
	if kind in TABLE_KINDS: return "work_table"
	if kind == "bus_stop": return "read_board"
	if kind == "drinking_fountain": return "drink"
	return "work_mid"

static func beats_for(kind: String, stage_index: int, seed_value: int, item_hint := "") -> Array:
	var scene := scene_class(kind)
	var variant := stage_index % 4
	match scene:
		"sit":
			var idles := _pick(SIT_POOL, seed_value + stage_index * 5, 3)
			var beats: Array = [_beat("sit_down_chair_01", 0.0, {"seat": true})]
			for idle in idles:
				beats.append(_beat(str(idle), 6.0 + float((seed_value + beats.size()) % 4) * 1.5, {"seat": true}))
			beats.append(_beat("sit_stand_up_chair_01", 0.0, {"seat": true}))
			return beats
		"sit_table":
			var idles2 := _pick(TABLE_POOL, seed_value, 3)
			var table_beats: Array = [_beat("sit_down_table_left", 0.0, {"seat": true})]
			for idle2 in idles2:
				table_beats.append(_beat(str(idle2), 6.0, {"seat": true}))
			table_beats.append(_beat("sit_stand_up_table_left", 0.0, {"seat": true}))
			return table_beats
		"photo":
			var shots: Array = [["take_picture", "idle_look_around_01"], ["idle_look_around_02", "take_picture"]]
			var order: Array = shots[variant % 2]
			return [_beat(str(order[0]), 0.0, {"items": [{"do": "attach", "kind": "phone", "hand": "R", "at": 0.0}]}), _beat(str(order[1])),
				_beat("cell_phone_textmessage", 6.0), _beat("idle_look_around_03", 1.5, {"items": [{"do": "hide", "kind": "phone", "at": 0.0}]})]
		"dispose":
			if stage_index % 2 == 1:
				return [_beat("work_mid", 2.0), _beat("idle_dust_01"), _beat("idle_look_around_01", 1.2)]
			var litter := item_hint if item_hint in LITTER_ITEMS else str(LITTER_ITEMS[seed_value % LITTER_ITEMS.size()])
			return [_beat("idle_neutral_01", 0.5), _beat("work_mid", 2.4, {"items": [{"do": "attach", "kind": litter, "hand": "R", "at": 0.0}, {"do": "throw", "kind": litter, "at": 0.55}]}),
				_beat("idle_dust_01" if variant % 2 == 0 else "idle_look_around_01", 0.0)]
		"door":
			var doors: Array = [["knock_door", "listen_door", "knock_door"], ["try_door_outwards", "idle_shrug_placeholder"], ["listen_door", "knock_door", "idle_waiting_01"]]
			var chosen: Array = doors[variant % 3]
			var out_d: Array = []
			for clip_name in chosen:
				out_d.append(_beat(str(clip_name) if clip_name != "idle_shrug_placeholder" else "gestic_shrug_01"))
			return out_d
		"phone":
			return [_beat("idle_neutral_01", 0.8), _beat("cell_phone_talk_01" if variant % 2 == 0 else "cell_phone_talk_02", 10.0, {"items": [{"do": "attach", "kind": "phone", "hand": "R", "at": 0.0}]}),
				_beat("idle_look_around_01", 2.0, {"items": [{"do": "hide", "kind": "phone", "at": 0.0}]})]
		"drink":
			var drink_item := "can" if kind == "vending_machine" else "cup"
			return [_beat("idle_waiting_01", 1.0), _beat("drink_drinking", 0.0, {"items": [{"do": "attach", "kind": drink_item, "hand": "R", "at": 0.0}]}),
				_beat("drink_idle", 4.0), _beat("idle_look_around_01", 1.5, {"items": [{"do": "hide", "kind": drink_item, "at": 0.0}]})]
		"read_board":
			var reads: Array = [[_beat("documents_check", 8.0), _beat("idle_look_around_01", 2.0)],
				[_beat("idle_waiting_01", 5.0), _beat("cell_phone_textmessage", 8.0, {"items": [{"do": "attach", "kind": "phone", "hand": "R", "at": 0.0}]}), _beat("idle_look_around_02", 2.0, {"items": [{"do": "hide", "kind": "phone", "at": 0.0}]})],
				[_beat("headphones_idle", 7.0), _beat("dancing_neutral", 6.0), _beat("idle_stretch_arms_01")],
				[_beat("idle_waiting_02", 6.0), _beat("idle_look_around_03", 4.0), _beat("idle_shake_leg_01", 4.0)]]
			return reads[variant]
		"crouch":
			var first := "crouch_idle" if variant % 2 == 0 else "crouch_gestic"
			var second := "crouch_gestic" if first == "crouch_idle" else "crouch_idle"
			return [_beat("crouch_in"), _beat(first, 7.0), _beat(second, 5.0), _beat("crouch_out")]
		"work_table":
			var tab: Array = [[_beat("work_table", 9.0), _beat("idle_stretch_arms_01"), _beat("work_table", 6.0)],
				[_beat("work_table", 7.0), _beat("idle_look_around_01", 2.0), _beat("work_table", 8.0)],
				[_beat("work_table", 10.0), _beat("idle_dust_01"), _beat("work_table", 5.0)],
				[_beat("work_table", 6.0), _beat("idle_shake_arms_01"), _beat("work_table", 9.0)]]
			return tab[variant]
		"trolley":
			return [_beat("trolley_idle", 9.0), _beat("idle_look_around_01", 2.0), _beat("trolley_idle", 6.0)]
		_:
			var mid: Array = [[_beat("work_mid", 9.0), _beat("idle_stretch_arms_01"), _beat("work_mid", 6.0)],
				[_beat("documents_in", 0.0, {"items": [{"do": "attach", "kind": "document", "hand": "L", "at": 0.3}]}), _beat("documents_note", 8.0), _beat("documents_out", 0.0, {"items": [{"do": "hide", "kind": "document", "at": 0.8}]})],
				[_beat("work_mid", 7.0), _beat("idle_nervous_01"), _beat("work_mid", 8.0)],
				[_beat("documents_check", 7.0), _beat("work_mid", 7.0), _beat("idle_look_around_02", 2.0)]]
			return mid[variant]

## Reference clip whose hands define where the person stands for a scene class.
static func placement_clip(scene: String) -> String:
	match scene:
		"crouch": return "crouch_idle"
		"work_table": return "work_table"
		"read_board": return "documents_check"
		"trolley": return "trolley_idle"
		"drink": return "drink_drinking"
		"phone", "phone_stand": return "cell_phone_talk_01"
		"newspaper": return "newspaper_hand_idle"
		"photo": return "take_picture"
		"dispose": return "work_mid"
		"door": return "knock_door"
	return "work_mid"

## World stand transform for a prop and scene. For standing scenes the actor's hands (clip_stats) land on the
## prop's working anchor; for seats the capture's step-back puts the pelvis on the seat; facing is toward the prop
## (away from it when sitting).
static func stand_transform(prop: Node3D, kind: String, scene: String, anchor_world := Vector3.INF) -> Dictionary:
	var side := float(prop.get_meta("use_side", 1.0))
	var front := prop.global_transform.basis * Vector3(0, 0, side)
	front.y = 0.0
	front = front.normalized()
	if scene == "sit" or scene == "sit_table":
		var seat := prop.get_meta("anchors", {}).get("seat_centre") as Node3D
		if seat == null:
			seat = prop.get_meta("anchors", {}).get("seat_front") as Node3D
		var seat_point := seat.global_position if seat != null else prop.global_position
		var step_back := float(stats("sit_down_chair_01" if scene == "sit" else "sit_down_table_left").get("root_end", [0, 0, -0.46])[2])
		var facing := front if scene == "sit" else -front   # a bench looks out; a table seat faces the table
		var start := Vector3(seat_point.x, prop.global_position.y, seat_point.z) - facing * step_back
		return {"position": start, "yaw": atan2(facing.x, facing.z), "front": front, "facing": facing}
	var anchor := anchor_world
	if anchor == Vector3.INF:
		anchor = _working_anchor(prop, kind, scene)
	var facing2 := -front
	var s := stats(placement_clip(scene))
	var rh := s.get("rh", [-0.16, 1.3, 0.34]) as Array
	var lh := s.get("lh", [0.15, 1.3, 0.34]) as Array
	var centroid_x := (float(rh[0]) + float(lh[0])) * 0.5
	var centroid_z := (float(rh[2]) + float(lh[2])) * 0.5
	var left := Vector3.UP.cross(facing2)
	var position := Vector3(anchor.x, prop.global_position.y, anchor.z) - (facing2 * centroid_z + left * centroid_x)
	# Never inside the prop's own footprint: slide the person out along the prop's front if the hands' reach put them in.
	var size := prop.get_meta("prop_size", Vector3(0.6, 1.0, 0.6)) as Vector3
	var local := prop.global_transform.affine_inverse() * position
	if size.y > 0.2 and absf(local.x) < size.x * 0.5 and absf(local.z) < size.z * 0.5 and not (kind in ["manhole", "water_meter"]):
		local.z = side * (size.z * 0.5 + 0.06)
		position = prop.global_transform * local
		position.y = prop.global_position.y
	return {"position": position, "yaw": atan2(facing2.x, facing2.z), "front": front, "facing": facing2}

static func _working_anchor(prop: Node3D, kind: String, scene: String) -> Vector3:
	var anchors := prop.get_meta("anchors", {}) as Dictionary
	var order := ["panel", "buttons", "keypad", "counter", "board", "timetable", "table_top", "display", "screen", "front", "top", "handle", "label", "slot", "coin_slot", "button"]
	if scene == "dispose":
		order = ["rim", "drop"]
	if scene == "crouch":
		order = ["cap_side", "cover_edge", "centre", "top", "soil", "rim", "panel", "front"]
	for name in order:
		var marker := anchors.get(name) as Node3D
		if marker != null:
			return marker.global_position
	# No named contact point: use the middle of the prop's front face at the height the scene's hands work.
	var front := prop.global_transform.basis * Vector3(0, 0, float(prop.get_meta("use_side", 1.0)))
	front.y = 0.0
	var size := prop.get_meta("prop_size", Vector3(0.6, 1.0, 0.6)) as Vector3
	var hand_height := 0.45 if scene == "crouch" else (0.96 if scene == "work_table" else 1.2)
	return prop.global_position + front.normalized() * (size.z * 0.5) + Vector3(0, minf(hand_height, size.y), 0)

## Kinds that belong on the kerb side of the footpath (the rest stand against the building side).
const KERB_KINDS := ["hydrant", "trash", "garbage_container", "cone", "bench", "bicycle", "parking_meter", "drinking_fountain", "vending_machine", "payphone", "bus_stop", "mailbox", "power", "billboard"]

static func is_kerb_kind(kind: String) -> bool:
	return kind in KERB_KINDS
