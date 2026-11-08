extends RefCounted

## Real-world prop definitions for everything citizens and sensors interact with.
##
## Every size here is metres. Poly Haven models are authored at real scale, so a
## model is never rescaled to "look right": `unit` only converts a model whose
## export is in centimetres. Procedural props are built to the dimension quoted
## next to them. Root convention for every prop: origin at the centre of the
## footprint on the ground, +Z is the front (the side a person stands on).
##
## Fields
##   model     res:// glTF, or "" for a procedural builder named by `builder`
##   hide      node-name fragments of unused variants inside a glTF "variant sheet"
##   yaw       degrees applied to the model so its authored front faces +Z
##   rest      node placements that put loose parts (a lid lying beside the can) back where they belong
##   parts     movable parts: pivot point in AABB fractions of `node`, axis, open angle / travel
##   anchors   named contact points in AABB fractions of `node` (x right, y up, z front)
##   height    expected real height, used by the audit to catch a mis-scaled model

const PH := "res://assets/props/polyhaven/%s/%s.gltf"

static func polyhaven(id: String) -> String:
	return PH % [id, id]

const ENTRIES := {
	"trash": {
		"model": "metal_trash_can", "hide": ["rust"], "yaw": 0.0, "height": 0.91,
		"rest": [{"node": "metal_trash_can_lid", "on_top_of": "metal_trash_can"}],
		"parts": {"lid": {"node": "metal_trash_can_lid", "pivot": Vector3(0.5, 0.0, 0.0), "axis": Vector3.RIGHT, "open": -38.0, "lift": 0.0}},
		"anchors": {"lid_edge": {"node": "metal_trash_can_lid", "f": Vector3(0.5, 0.5, 1.0), "part": "lid"},
			"rim": {"node": "metal_trash_can", "f": Vector3(0.5, 1.0, 0.5)}, "drop": {"node": "metal_trash_can", "f": Vector3(0.5, 0.97, 0.72)}},
	},
	"hydrant": {
		"model": "fire_hydrant", "hide": ["aged"], "yaw": 0.0, "height": 0.80,
		"parts": {"cap_side": {"node": "fire_hydrant_cap_02", "pivot": Vector3(0.5, 0.5, 0.5), "axis": Vector3.RIGHT, "open": 540.0, "lift": 0.05}},
		"anchors": {"cap_side": {"node": "fire_hydrant_cap_02", "f": Vector3(1.0, 0.5, 0.5), "part": "cap_side"},
			"top": {"node": "fire_hydrant", "f": Vector3(0.5, 1.0, 0.5)}, "body": {"node": "fire_hydrant", "f": Vector3(0.5, 0.45, 1.0)}},
	},
	"bench": {"builder": "park_bench", "height": 0.85, "seat": {"height": 0.45, "depth": 0.42}},
	"picnic_table": {"model": "wooden_picnic_table", "yaw": 0.0, "height": 0.75, "anchors": {"table_top": {"node": "wooden_picnic_table", "f": Vector3(0.5, 1.0, 0.92)}, "seat_front": {"node": "wooden_picnic_table", "f": Vector3(0.5, 0.58, 1.0)}}},
	"box": {
		"model": "wooden_crate_01", "yaw": 0.0, "height": 0.35,
		"parts": {"lid": {"node": "wooden_crate_01_lid", "pivot": Vector3(0.5, 0.0, 0.0), "axis": Vector3.RIGHT, "open": -48.0, "lift": 0.0}},
		"anchors": {"lid_edge": {"node": "wooden_crate_01_lid", "f": Vector3(0.5, 0.5, 1.0), "part": "lid"},
			"inside": {"node": "wooden_crate_01", "f": Vector3(0.5, 0.6, 0.5)}, "rim_left": {"node": "wooden_crate_01", "f": Vector3(0.05, 1.0, 0.5)},
			"rim_right": {"node": "wooden_crate_01", "f": Vector3(0.95, 1.0, 0.5)}},
	},
	"planter": {
		"model": "planter_box_02", "yaw": 0.0, "height": 0.45,
		"anchors": {"soil": {"node": "planter_box_02", "f": Vector3(0.5, 0.95, 0.5)}, "rim": {"node": "planter_box_02", "f": Vector3(0.5, 1.0, 1.0)}},
	},
	"flower_pot": {
		"model": "planter_pot_clay", "yaw": 0.0, "height": 0.22,
		"anchors": {"soil": {"node": "planter_pot_clay", "f": Vector3(0.5, 0.95, 0.5)}},
	},
	"greenhouse": {
		"model": "planter_box_03", "yaw": 0.0, "height": 0.86,
		"anchors": {"soil": {"node": "planter_box_03", "f": Vector3(0.5, 0.95, 0.5)}},
	},
	"power": {
		"model": "utility_box_01", "yaw": 0.0, "height": 1.12,
		"anchors": {"panel": {"node": "utility_box_01_box", "f": Vector3(0.5, 0.65, 1.0)}, "label": {"node": "utility_box_01_box", "f": Vector3(0.5, 0.8, 1.0)}},
	},
	"aircon": {
		"model": "exterior_aircon_unit", "hide": ["rusted"], "yaw": 0.0, "height": 0.93, "builder": "aircon_pad",
		"anchors": {"panel": {"node": "exterior_aircon_unit", "f": Vector3(0.5, 0.5, 1.0)}, "top": {"node": "exterior_aircon_unit", "f": Vector3(0.5, 1.0, 0.5)}},
	},
	"manhole": {
		"model": "water_manhole_cover", "yaw": 0.0, "height": 0.07, "flush": true,
		"parts": {"cover": {"node": "water_manhole_cover", "pivot": Vector3(0.5, 0.5, 0.5), "axis": Vector3.RIGHT, "open": 0.0, "lift": 0.0, "slide": Vector3(0.0, 0.0, -0.36)}},
		"anchors": {"cover_edge": {"node": "water_manhole_cover", "f": Vector3(0.5, 0.8, 1.0), "part": "cover"}, "centre": {"node": "water_manhole_cover", "f": Vector3(0.5, 1.0, 0.5)}},
	},
	"water_meter": {
		"model": "water_manhole_cover", "yaw": 0.0, "height": 0.07, "flush": true, "builder": "meter_chamber",
		"parts": {"cover": {"node": "water_manhole_cover", "pivot": Vector3(0.5, 0.5, 0.5), "axis": Vector3.RIGHT, "open": 0.0, "lift": 0.0, "slide": Vector3(0.0, 0.0, -0.36)}},
		"anchors": {"cover_edge": {"node": "water_manhole_cover", "f": Vector3(0.5, 0.8, 1.0), "part": "cover"}, "centre": {"node": "water_manhole_cover", "f": Vector3(0.5, 1.0, 0.5)}},
	},
	"vending_machine": {"builder": "vending_machine", "height": 1.85},
	"parking_meter": {"builder": "parking_meter", "height": 1.5},
	"drinking_fountain": {"builder": "drinking_fountain", "height": 1.0},
	"payphone": {"builder": "payphone", "height": 1.55},
	"market_counter": {"builder": "market_counter", "height": 1.57},
	"coffee_cart": {"model": "CoffeeCart_01", "yaw": 0.0, "height": 1.72,
		"anchors": {"counter": {"node": "CoffeeCart_01_cart", "f": Vector3(0.45, 0.50, 1.0)}, "mugs": {"node": "CoffeeCart_01_mugs", "f": Vector3(0.5, 1.0, 0.5)}, "machine": {"node": "CoffeeCart_01_props", "f": Vector3(0.5, 0.6, 1.0)}, "serve": {"node": "CoffeeCart_01_cart", "f": Vector3(0.3, 0.52, 1.0)}}},
	"sandwich_board": {"model": "standing_chalkboard_01", "yaw": 0.0, "height": 1.51,
		"anchors": {"board": {"node": "standing_chalkboard_01", "f": Vector3(0.5, 0.6, 1.0)}, "board_top": {"node": "standing_chalkboard_01", "f": Vector3(0.5, 0.85, 1.0)}, "board_low": {"node": "standing_chalkboard_01", "f": Vector3(0.5, 0.3, 1.0)}, "frame": {"node": "standing_chalkboard_01", "f": Vector3(0.9, 0.9, 0.9)}}},
	"hand_truck": {"model": "hand_truck", "yaw": 180.0, "height": 1.40,
		"anchors": {"handle": {"node": "hand_truck", "f": Vector3(0.5, 0.97, 0.85)}, "grip_left": {"node": "hand_truck", "f": Vector3(0.1, 0.95, 0.85)}, "grip_right": {"node": "hand_truck", "f": Vector3(0.9, 0.95, 0.85)}, "shelf": {"node": "hand_truck", "f": Vector3(0.5, 0.12, 0.15)}}},
	"ladder": {"model": "ladder_sectioned_01", "yaw": 0.0, "height": 2.13,
		"anchors": {"rail_left": {"node": "ladder_section_01", "f": Vector3(0.08, 0.45, 0.5)}, "rail_right": {"node": "ladder_section_01", "f": Vector3(0.92, 0.45, 0.5)}, "rung": {"node": "ladder_section_01", "f": Vector3(0.5, 0.35, 0.5)}, "foot": {"node": "ladder_section_01", "f": Vector3(0.5, 0.05, 0.5)}}},
	"wheelchair": {"model": "wheelchair_01", "yaw": 180.0, "height": 1.10,
		"anchors": {"handle_left": {"node": "wheelchair_01", "f": Vector3(0.12, 0.88, 0.95)}, "handle_right": {"node": "wheelchair_01", "f": Vector3(0.88, 0.88, 0.95)}, "brake": {"node": "wheelchair_01", "f": Vector3(0.9, 0.5, 0.55)}, "seat": {"node": "wheelchair_01", "f": Vector3(0.5, 0.5, 0.5)}, "footrest": {"node": "wheelchair_01", "f": Vector3(0.5, 0.18, 0.1)}}},
	"tool_cart": {"model": "tool_cart", "yaw": 0.0, "height": 0.96,
		"anchors": {"tray": {"node": "tool_cart", "f": Vector3(0.5, 0.95, 0.5)}, "shelf": {"node": "tool_cart", "f": Vector3(0.5, 0.45, 0.6)}, "handle": {"node": "tool_cart", "f": Vector3(0.5, 0.97, 0.05)}}},
	"extinguisher": {"model": "korean_fire_extinguisher_01", "yaw": 0.0, "height": 0.66,
		"anchors": {"top": {"node": "korean_fire_extinguisher_01", "f": Vector3(0.5, 1.0, 0.5)}, "gauge": {"node": "korean_fire_extinguisher_01", "f": Vector3(0.5, 0.85, 1.0)}, "body": {"node": "korean_fire_extinguisher_01", "f": Vector3(0.5, 0.4, 1.0)}}},
	"toolbox": {"model": "metal_toolbox", "yaw": 0.0, "height": 0.17, "rest": [{"node": "metal_toolbox_lid", "rotation_deg": Vector3.ZERO}],
		"parts": {"lid": {"node": "metal_toolbox_lid", "pivot": Vector3(0.5, 0.0, 0.0), "axis": Vector3.RIGHT, "open": -60.0, "lift": 0.0}},
		"anchors": {"lid_edge": {"node": "metal_toolbox_lid", "f": Vector3(0.5, 0.5, 1.0), "part": "lid"}, "tray": {"node": "metal_toolbox", "f": Vector3(0.5, 0.9, 0.5)}, "handle": {"node": "metal_toolbox", "f": Vector3(0.5, 1.0, 0.9)}}},
	"power_meter": {"builder": "meter_pedestal", "height": 1.55},
	"mailbox": {"builder": "mailbox", "height": 1.14},
	"dumpster": {"builder": "dumpster", "height": 1.25},
	"atm": {"builder": "atm", "height": 1.75},
	"bus_stop": {"builder": "bus_stop", "height": 2.7},
	"bicycle": {"builder": "bicycle", "height": 1.05},
	"cone": {"builder": "cone", "height": 0.70},
	"fire_exit": {"builder": "fire_point", "height": 1.5},
	"billboard": {"builder": "info_board", "height": 2.3},
	"poster": {"builder": "poster_column", "height": 2.6},
}

## Hand-held items. Origin = bottom centre of the item as it normally rests.
##   grip_point  where the palm closes (item-local metres)
##   grip_axis   item axis the fingers curl around (a handle's / a cup's axis)
##   radius      distance from the palm to that axis: how far the item centre is off the palm
##   pose        finger pose from CitizenIK.GRIP_POSES
##   roll        extra turn about the grip axis in degrees (tuned so the item hangs/stands correctly)
const HANDHELD := {
	"phone": {"builder": "phone", "grip_point": Vector3(0.0, 0.004, 0.02), "grip_axis": Vector3(1, 0, 0), "radius": 0.006, "pose": "hold_flat", "roll": 0.0},
	"meter_reader": {"builder": "meter_reader", "grip_point": Vector3(0.0, 0.015, 0.03), "grip_axis": Vector3(1, 0, 0), "radius": 0.012, "pose": "hold_flat", "roll": 0.0},
	"cup": {"builder": "cup", "grip_point": Vector3(0.0, 0.05, 0.0), "grip_axis": Vector3(0, 1, 0), "radius": 0.034, "pose": "hold_cup", "roll": 0.0},
	"watering_can": {"model": "watering_can_metal_01", "grip_point": Vector3(0.0, 0.17, -0.04), "grip_axis": Vector3(1, 0, 0), "radius": 0.012, "pose": "hold_handle", "roll": 0.0},
	"parcel": {"model": "cardboard_box_01", "grip_point": Vector3(0.19, 0.17, 0.0), "grip_axis": Vector3(0, 0, 1), "radius": 0.02, "pose": "hold_box", "roll": 0.0},
	"medical_supply": {"model": "medical_box", "grip_point": Vector3(0.0, 0.07, 0.17), "grip_axis": Vector3(1, 0, 0), "radius": 0.012, "pose": "hold_handle", "roll": 0.0},
	"market_goods": {"model": "wicker_basket_01", "grip_point": Vector3(0.0, 0.1, 0.0), "grip_axis": Vector3(0, 0, 1), "radius": 0.015, "pose": "hold_handle", "roll": 0.0},
	"waste_bag": {"model": "trashbag", "grip_point": Vector3(0.0, 0.5, 0.0), "grip_axis": Vector3(1, 0, 0), "radius": 0.03, "pose": "hold_neck", "roll": 0.0},
	"mail": {"builder": "envelope", "grip_point": Vector3(-0.09, 0.0015, 0.0), "grip_axis": Vector3(0, 0, 1), "radius": 0.002, "pose": "pinch", "roll": 0.0},
	"document": {"model": "clipboard", "grip_point": Vector3(-0.1, 0.02, 0.0), "grip_axis": Vector3(0, 0, 1), "radius": 0.02, "pose": "hold_flat", "roll": 0.0},
	"notice": {"builder": "notice_sheet", "grip_point": Vector3(0.0, 0.0015, -0.14), "grip_axis": Vector3(1, 0, 0), "radius": 0.002, "pose": "pinch", "roll": 0.0},
	"newspaper": {"builder": "newspaper", "grip_point": Vector3(0.0, 0.002, 0.0), "grip_axis": Vector3(1, 0, 0), "radius": 0.01, "pose": "hold_flat", "roll": 0.0},
	"can": {"builder": "soda_can", "grip_point": Vector3(0.0, 0.06, 0.0), "grip_axis": Vector3(0, 1, 0), "radius": 0.033, "pose": "hold_cup", "roll": 0.0},
	"handset": {"builder": "handset", "grip_point": Vector3(0.0, 0.0, 0.0), "grip_axis": Vector3(1, 0, 0), "radius": 0.025, "pose": "hold_handle", "roll": 0.0},
	"wrench": {"model": "pipe_wrench", "grip_point": Vector3(0.0, 0.1, 0.0), "grip_axis": Vector3(0, 0, 1), "radius": 0.015, "pose": "hold_handle", "roll": 0.0},
	"extinguisher_item": {"model": "korean_fire_extinguisher_01", "grip_point": Vector3(0.0, 0.5, 0.05), "grip_axis": Vector3(1, 0, 0), "radius": 0.025, "pose": "hold_handle", "roll": 0.0},
	"wet_sign": {"model": "WetFloorSign_01", "grip_point": Vector3(0.0, 0.5, 0.0), "grip_axis": Vector3(1, 0, 0), "radius": 0.02, "pose": "hold_handle", "roll": 0.0},
	"laptop": {"model": "classic_laptop", "grip_point": Vector3(0.0, 0.01, 0.0), "grip_axis": Vector3(1, 0, 0), "radius": 0.012, "pose": "hold_flat", "roll": 0.0},
	"coin": {"builder": "coin", "grip_point": Vector3(0.0, 0.001, 0.0), "grip_axis": Vector3(0, 0, 1), "radius": 0.002, "pose": "pinch", "roll": 0.0},
	"chalk": {"builder": "coin", "grip_point": Vector3(0.0, 0.001, 0.0), "grip_axis": Vector3(0, 0, 1), "radius": 0.002, "pose": "pinch", "roll": 0.0},
	"cleaner": {"model": "multi_cleaner_bottle", "grip_point": Vector3(0.0, 0.1, 0.0), "grip_axis": Vector3(0, 1, 0), "radius": 0.03, "pose": "hold_cup", "roll": 0.0},
	"broom": {"model": "wooden_broom", "grip_point": Vector3(0.0, 0.9, 0.0), "grip_axis": Vector3(1, 0, 0), "radius": 0.015, "pose": "hold_handle", "roll": 0.0},
	"safety_cone": {"builder": "cone", "grip_point": Vector3(0.0, 0.6, 0.0), "grip_axis": Vector3(1, 0, 0), "radius": 0.045, "pose": "hold_handle", "roll": 0.0},
}

static func entry(kind: String) -> Dictionary:
	return ENTRIES.get(kind, {}) as Dictionary

static func handheld(kind: String) -> Dictionary:
	return HANDHELD.get(kind, {}) as Dictionary
