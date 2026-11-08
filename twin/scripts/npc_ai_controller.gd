extends Node

## Citizen embodiment adapter.
##
## SocietyKernel owns persistent identity, households, daily agendas,
## relationships, institutions, work orders and action causes. This controller
## retains the verified Fix 75 responsibilities: legal sidewalk execution,
## smart-object placement/reservation, authored Rocketbox animation, contact
## evidence and pose grounding.

@export var walking_speed := 1.55
@export var personal_space := 1.15
@export var action_seconds := 15.0
const RocketboxAnimationLibrary := preload("res://scripts/rocketbox_animation_library.gd")
const CitySpatialSafety := preload("res://scripts/city_spatial_safety.gd")
const SocietyKernel := preload("res://scripts/society_kernel.gd")
const WalkStride := preload("res://scripts/walk_stride.gd")
const NpcItemHandling := preload("res://scripts/npc_item_handling.gd")
const PropFactory := preload("res://scripts/props/prop_factory.gd")
const PropCatalog := preload("res://scripts/props/prop_catalog.gd")
const InteractionDirector := preload("res://scripts/characters/clip_director.gd")
const ActivityLibrary := preload("res://scripts/characters/activity_library.gd")
const BinWeight := preload("res://scripts/props/bin_weight.gd")
const CitizenIK := preload("res://scripts/characters/citizen_ik.gd")
const StreetFurniturePlanner := preload("res://scripts/props/street_furniture_planner.gd")
# Handheld objects with a physical resting place (see npc_item_handling.gd).
const CUSTODY_TOOLS := ["cup", "watering_can"]
# Station props whose top is a usable work surface for a resting object.
const SURFACE_PROPS := ["box", "bench", "power", "power_meter", "water_meter", "trash", "atm", "mailbox", "aircon"]
const MINIMUM_PROP_JOB_WORKERS := 10
const MINIMUM_METER_JOB_WORKERS := 5
const PRESENTATION_WORKER_TARGET := 10
const ANIMATION_EVALUATION_PRIORITY := -100
const POSE_GROUNDING_PRIORITY := -50
const WALK_SPATIAL_CELL_SIZE := 24.0
const OBSTACLE_SPATIAL_CELL_SIZE := 16.0
const AGENT_SPATIAL_CELL_SIZE := 6.0
const MIN_CITIZEN_SEPARATION := 1.35
const FOOT_GROUND_BONES := ["Bip01 L Foot", "Bip01 R Foot", "Bip01 L Toe0", "Bip01 R Toe0"]
# Use the first articulated finger joints, not the knuckle roots.  Anchoring at
# the roots still puts a prop across the middle of the palm on Rocketbox rigs.
# The joint average is the actual finger cradle where a held edge/handle rests.
const RIGHT_GRIP_FINGER_JOINTS := ["Bip01 R Finger11", "Bip01 R Finger21", "Bip01 R Finger31", "Bip01 R Finger41"]
const ROCKETBOX_MODEL_GENDERS := {
	"res://assets/rocketbox/Female_Adult_01/Export/Female_Adult_01.fbx":"f",
	"res://assets/rocketbox/Female_Adult_05/Export/Female_Adult_05.fbx":"f",
	"res://assets/rocketbox/Male_Adult_01/Export/Male_Adult_01.fbx":"m",
	"res://assets/rocketbox/Male_Adult_05/Export/Male_Adult_05.fbx":"m",
	"res://assets/rocketbox/Business_Female_01/Export/Business_Female_01.fbx":"f",
	"res://assets/rocketbox/Business_Female_02/Export/Business_Female_02.fbx":"f",
	"res://assets/rocketbox/Business_Male_01/Export/Business_Male_01.fbx":"m",
	"res://assets/rocketbox/Business_Male_02/Export/Business_Male_02.fbx":"m",
	"res://assets/rocketbox/Business_Male_03/Export/Business_Male_03.fbx":"m",
	"res://assets/rocketbox/Chef_Female_01/Export/Chef_Female_01.fbx":"f",
	"res://assets/rocketbox/Construction_Female_01/Export/Construction_Female_01.fbx":"f",
	"res://assets/rocketbox/Construction_Male_01/Export/Construction_Male_01.fbx":"m",
	"res://assets/rocketbox/Construction_Male_02/Export/Construction_Male_02.fbx":"m",
	"res://assets/rocketbox/Construction_Male_03/Export/Construction_Male_03.fbx":"m",
	"res://assets/rocketbox/Construction_Male_04/Export/Construction_Male_04.fbx":"m",
	"res://assets/rocketbox/Delivery_Male_01/Export/Delivery_Male_01.fbx":"m",
	"res://assets/rocketbox/Gardener_Male_01/Export/Gardener_Male_01.fbx":"m",
	"res://assets/rocketbox/Medical_Female_01/Export/Medical_Female_01.fbx":"f",
	"res://assets/rocketbox/Medical_Female_02/Export/Medical_Female_02.fbx":"f",
	"res://assets/rocketbox/Medical_Male_01/Export/Medical_Male_01.fbx":"m",
}

# Pair exchanges alias restrained clips authored on that exact character model.
# No foreign skeleton, retarget, or procedural bone offsets are applied.
const INTERACTION_ANIMATION_PROFILES := {
	"OfferParcel":"Interact", "ReceiveParcel":"PickUp_Table", "DescribeParcel":"Idle_Talking", "CheckParcel":"Chest_Open", "CompleteHandoff":"Yes", "AcceptHandoff":"Yes",
	"AskCheckIn":"Idle_Talking", "AnswerCheckIn":"Idle_Talking", "RecordCheckIn":"Idle_TalkingPhone_Loop", "WaitForCheckIn":"PickUp_Table", "WelcomePatient":"Interact", "ConfirmCheckIn":"Yes",
	"ExplainMeter":"Idle_Talking", "LogMeterReading":"Idle_TalkingPhone_Loop", "PointToMeter":"Interact", "ReviewServiceNote":"PickUp_Table", "ConfirmMeterWork":"Yes", "CloseServiceReport":"Chest_Open",
	"PresentGoods":"Interact", "InspectGoods":"PickUp_Table", "DescribeGoods":"Idle_Talking", "ChooseGoods":"Chest_Open", "CompletePurchase":"Yes", "AcceptPurchase":"Yes",
	"ShowCleanedArea":"Interact", "ReviewCleanedArea":"PickUp_Table", "ExplainServiceWork":"Idle_Talking", "ConfirmClearPath":"Interact", "CloseServiceRequest":"Yes", "AcceptServiceReport":"Yes",
	"ExplainRoute":"Interact", "FollowRouteExplanation":"PickUp_Table", "ClarifyTransfer":"Idle_Talking", "CheckRouteDetails":"Idle_TalkingPhone_Loop", "ConfirmDirections":"Yes", "AcknowledgeDirections":"Yes"
}
const INTERACTION_ACTION_TEXT := {
	"OfferParcel":"offering the checked parcel", "ReceiveParcel":"reaching to receive the parcel", "DescribeParcel":"explaining the parcel manifest", "CheckParcel":"checking the parcel label", "CompleteHandoff":"confirming the handoff is complete", "AcceptHandoff":"acknowledging receipt of the parcel",
	"AskCheckIn":"asking the visitor for check-in details", "AnswerCheckIn":"answering the clinic check-in questions", "RecordCheckIn":"recording the visitor details", "WaitForCheckIn":"listening while the details are recorded", "WelcomePatient":"welcoming the checked-in visitor", "ConfirmCheckIn":"confirming the check-in is complete",
	"ExplainMeter":"explaining the power-meter reading", "LogMeterReading":"logging the meter reading", "PointToMeter":"indicating the inspected meter", "ReviewServiceNote":"reviewing the service note", "ConfirmMeterWork":"confirming the meter work is complete", "CloseServiceReport":"closing the service report",
	"PresentGoods":"presenting the requested market goods", "InspectGoods":"inspecting the presented goods", "DescribeGoods":"describing the order contents", "ChooseGoods":"selecting the requested goods", "CompletePurchase":"confirming the purchase", "AcceptPurchase":"acknowledging the completed purchase",
	"ShowCleanedArea":"showing the cleaned public area", "ReviewCleanedArea":"inspecting the cleaned area", "ExplainServiceWork":"explaining the completed street service", "ConfirmClearPath":"checking that the route is clear", "CloseServiceRequest":"closing the service request", "AcceptServiceReport":"accepting the completed service report",
	"ExplainRoute":"pointing out the next transit route", "FollowRouteExplanation":"listening to the route explanation", "ClarifyTransfer":"clarifying the transfer point", "CheckRouteDetails":"checking the route details", "ConfirmDirections":"confirming the directions", "AcknowledgeDirections":"acknowledging the route guidance"
}

const CITIZEN_NAMES := ["Amira Ben Salem","Youssef Gharbi","Leila Trabelsi","Nabil Mansour","Meriem Jebali","Sami Kallel","Ines Ayadi","Karim Baccar","Rania Haddad","Hatem Chouchane","Aya Khouaja","Walid Gdiri","Sana Bouzid","Fares Mhenni","Nour Saidi","Rami Cherif","Hiba Jaziri","Mehdi Kammoun","Salma Gharbi","Tarek Ben Amor","Mouna Hachicha","Adel Zidi","Maya Ben Yedder","Sofiene Boussetta","Nesrine Kefi","Omar Ben Youssef","Yara Dhouib","Anis Bousnina","Lina Charfi","Kais Ben Halima","Rahma Mestiri","Marwen Guesmi"]
const PROP_PATHS := {
	"box":"res://assets/poly_pizza/city_props/Box.glb", "mailbox":"res://assets/poly_pizza/city_props/Mailbox.glb",
	"trash":"res://assets/poly_pizza/city_props/Trash Can.glb", "dumpster":"res://assets/poly_pizza/city_props/Dumpster.glb",
	"power":"res://assets/poly_pizza/city_props/Power Box.glb", "hydrant":"res://assets/poly_pizza/city_props/Fire hydrant.glb",
	"power_meter":"res://assets/poly_pizza/city_props/Power Box.glb", "water_meter":"res://scenes/WaterMeterWorkProp.tscn",
	"planter":"res://assets/poly_pizza/city_props/Planter & Bushes.glb", "bench":"res://assets/poly_pizza/city_props/Bench.glb",
	"bus_stop":"res://assets/poly_pizza/city_props/Bus stop sign.glb", "bicycle":"res://assets/poly_pizza/city_props/Bicycle.glb",
	"atm":"res://assets/poly_pizza/city_props/ATM.glb",
	"manhole":"res://assets/poly_pizza/city_props/Manhole Cover.glb", "cone":"res://assets/poly_pizza/city_props/Cone.glb",
	"aircon":"res://assets/poly_pizza/city_props/Air conditioner.glb", "fire_exit":"res://assets/poly_pizza/city_props/Fire Exit.glb",
	"billboard":"res://assets/poly_pizza/city_props/Billboard.glb", "poster":"res://assets/poly_pizza/city_props/Rock band poster.glb",
	"greenhouse":"res://assets/poly_pizza/city_props/Greenhouse.glb", "flower_pot":"res://assets/poly_pizza/city_props/Flower Pot.glb"
}
# Dimensions are calibrated to 1.75-1.90 m adults and real work-scene reach.
const PROP_WORLD_SIZE := {"box":0.65,"mailbox":1.35,"trash":1.05,"dumpster":2.00,"power":1.15,"hydrant":0.80,"power_meter":1.25,"water_meter":1.20,"planter":1.40,"bench":2.00,"bus_stop":2.70,"bicycle":1.80,"atm":1.80,"manhole":0.75,"cone":0.70,"aircon":1.00,"fire_exit":1.50,"billboard":3.50,"poster":1.00,"greenhouse":3.80,"flower_pot":0.50}
const TOOL_PATHS := {"phone":"res://assets/poly_pizza/job_props/Phone.glb","meter_reader":"res://assets/poly_pizza/job_props/Phone.glb","cup":"res://assets/poly_pizza/job_props/Cup.glb","watering_can":"res://assets/poly_pizza/job_props/Watering Can.glb"}
const TOOL_WORLD_SIZE := {"phone":0.18,"meter_reader":0.18,"cup":0.18,"watering_can":0.42}
const TOOL_ROTATION_DEGREES := {"phone":Vector3(0.0,90.0,-90.0),"meter_reader":Vector3(0.0,90.0,-90.0),"cup":Vector3(0.0,90.0,-90.0),"watering_can":Vector3(0.0,90.0,90.0)}
const TOOL_ACTION_CLIPS := {
	"phone":["Idle_TalkingPhone_Loop", "RecordReading"],
	"meter_reader":["PickUp_Table", "RecordReading"],
	"cup":["PickUp_Table", "Consume", "SetGroundItem"],
	"watering_can":["Farm_Watering"]
}
const HAND_ITEM_PATHS := {
	"parcel":"res://assets/props/polyhaven/cardboard_box_01/cardboard_box_01.gltf",
	"waste_bag":"res://assets/props/polyhaven/trashbag/trashbag.gltf",
	"medical_supply":"res://assets/props/polyhaven/medical_box/medical_box.gltf",
	"market_goods":"res://assets/props/polyhaven/wicker_basket_01/wicker_basket_01.gltf",
	"mail":"res://assets/props/polyhaven/clipboard/clipboard.gltf",
	"document":"res://assets/props/polyhaven/clipboard/clipboard.gltf",
	"notice":"res://assets/props/polyhaven/clipboard/clipboard.gltf",
	"safety_cone":"res://assets/props/polyhaven/WetFloorSign_01/WetFloorSign_01.gltf"
}
# Real-world longest dimension of each carried item (metres).
const HAND_ITEM_WORLD_SIZE := {
	"parcel":0.52, "waste_bag":0.57, "medical_supply":0.53,
	"market_goods":0.38, "mail":0.34, "document":0.34,
	"notice":0.34, "safety_cone":0.63
}
# Normalized points within each already-oriented model AABB.  The selected
# point—not the mesh centre—is placed at the finger cradle.  Papers, devices
# and boxes are held at an edge; hanging objects are held at their upper grip.
const HANDHELD_GRIP_POINTS := {
	# Phone/Post-it assets are broad in Y/Z and thin in X after orientation.
	# Contact a real side edge on a major axis, never the middle of their face.
	"phone":Vector3(0.50,0.03,0.25), "meter_reader":Vector3(0.50,0.03,0.25),
	"cup":Vector3(0.00,0.55,0.50), "watering_can":Vector3(0.50,0.95,0.50),
	"parcel":Vector3(0.00,0.50,0.50), "medical_supply":Vector3(0.00,0.50,0.50),
	"market_goods":Vector3(0.00,0.50,0.50), "waste_bag":Vector3(0.50,1.00,0.50),
	"mail":Vector3(0.00,0.50,0.50), "document":Vector3(0.50,0.03,0.28),
	"notice":Vector3(0.50,0.03,0.28), "safety_cone":Vector3(0.50,0.95,0.50),
}
const HAND_ITEM_ACTION_CLIPS := {
	"parcel":["Chest_Open", "PickUp_Table", "InspectLabel", "SetGroundItem"],
	"waste_bag":["Farm_Harvest", "PickUp_Table", "SetGroundItem"],
	"medical_supply":["Chest_Open", "PickUp_Table", "InspectLabel", "SetGroundItem"],
	"market_goods":["OpenGroundContainer", "PickUp_Table", "InspectLabel", "SetGroundItem"],
	"mail":["Chest_Open", "PickUp_Table", "InspectLabel", "SetGroundItem"],
	"document":["PickUp_Table", "InspectLabel"],
	"notice":["PickUp_Table", "Interact", "RepairSurface", "InspectLabel"],
	"safety_cone":["PickUp_Table", "SetGroundItem"]
}
const WORK_STAGE_BY_CLIP := {
	"Chest_Open":"accessing_container", "OpenGroundContainer":"accessing_container", "OpenLatch":"opening_fixture",
	"PickUp_Table":"handling_item", "Farm_Harvest":"collecting_material", "SetGroundItem":"placing_item",
	"Farm_PlantSeed":"installing_material", "Farm_Watering":"applying_material",
	"RepairMid":"performing_repair", "RepairSurface":"performing_repair",
	"InspectLow":"close_inspection", "InspectLabel":"verifying_result", "PanelKnock":"testing_fixture",
	"RecordReading":"recording_result", "Idle_TalkingPhone_Loop":"recording_result",
	"Interact":"operating_fixture", "Consume":"using_personal_item", "PresentWork":"presenting_result"
}
# Physical work clips are constrained to an actual surface on their smart
# object. Dialogue, confirmation and phone clips deliberately keep the hands
# under the authored animation instead of inventing contact that is not there.
const CONTACT_CLIPS := ["Interact","PickUp_Table","Chest_Open","Farm_Harvest","Farm_PlantSeed","Farm_Watering","InspectLow","RepairMid","RepairSurface","OpenGroundContainer","SetGroundItem","OpenLatch","PanelKnock","InspectLabel","RecordReading"]
# RecordReading is a real service animation but is deliberately absent here:
# writing about a prop does not satisfy the physical-prop occupancy invariant.
const PROP_JOB_CLIPS := ["Interact","PickUp_Table","Farm_Harvest","Farm_PlantSeed","Farm_Watering","InspectLow","RepairMid","RepairSurface","OpenGroundContainer","SetGroundItem","OpenLatch","PanelKnock","InspectLabel"]
const TWO_HAND_CONTACT := ["box","aircon","poster","greenhouse"]
const PROP_CONTACT_HEIGHT := {"box":0.76,"mailbox":0.72,"trash":0.80,"dumpster":0.72,"power":0.68,"hydrant":0.60,"power_meter":0.66,"water_meter":0.62,"planter":0.82,"bench":0.72,"bus_stop":0.62,"bicycle":0.54,"atm":0.58,"manhole":0.18,"cone":0.64,"aircon":0.68,"fire_exit":0.58,"billboard":0.56,"poster":0.52,"greenhouse":0.50,"flower_pot":0.82}
# The distance is measured from the actor root to the nearest prop surface, not
# to the prop origin. Low/small objects sit closer to the crouched hand envelope;
# large upright fixtures retain extra torso clearance.
const PROP_SURFACE_REACH := {"box":0.38,"mailbox":0.58,"trash":0.58,"dumpster":0.62,"power":0.60,"hydrant":0.56,"power_meter":0.56,"water_meter":0.52,"planter":0.56,"bench":0.60,"bus_stop":0.62,"bicycle":0.58,"atm":0.60,"manhole":0.48,"cone":0.44,"aircon":0.42,"fire_exit":0.60,"billboard":0.32,"poster":0.54,"greenhouse":0.62,"flower_pot":0.50}
# Role choice follows the visible wardrobe of the established characters.
# Hard-hat meshes staff field work; formal meshes staff services; casual
# meshes staff deliveries, markets and neighborhood routines.
# This map is now only a wardrobe/occupation compatibility seed. SocietyKernel
# owns the resulting employment record and every action assignment.
const WARDROBE_WORKFLOW_BY_CHARACTER := [7,10,4,2,0,7,3,5,12,7,1,0,7,15,13,2,14,11,17,8,5,2,9,0,2,6,2,0,2,16,19,20]

# Clip-specific wording prevents a fallback animation from being mislabeled.
const BASE_WORKFLOWS := [
	{"role":"parcel courier","personality":"quick, methodical and friendly","stops":[
		{"id":"depot_scan","prop":"box","tool":"phone","item":"parcel","clips":["Chest_Open","PickUp_Table","Idle_TalkingPhone_Loop","SetGroundItem"],"words":{"Chest_Open":"opening the parcel crate","PickUp_Table":"lifting and checking the parcel label","Idle_TalkingPhone_Loop":"scanning the parcel manifest","SetGroundItem":"returning the scanned parcel to the dispatch stack"}},
		{"id":"door_delivery","prop":"mailbox","item":"parcel","clips":["OpenLatch","PickUp_Table","SetGroundItem","InspectLabel"],"words":{"OpenLatch":"opening the secured delivery compartment","PickUp_Table":"taking the addressed parcel in hand","SetGroundItem":"placing the parcel inside the delivery compartment","InspectLabel":"verifying the recipient label before closing"}}]},
	{"role":"sanitation worker","personality":"practical and attentive to public space","stops":[
		{"id":"bin_service","prop":"trash","item":"waste_bag","clips":["InspectLow","Farm_Harvest","PickUp_Table","SetGroundItem","Interact"],"words":{"InspectLow":"crouching to inspect the bin base and surrounding pavement","Farm_Harvest":"collecting loose litter beside the public bin","PickUp_Table":"taking hold of the secured waste bag","SetGroundItem":"placing the sealed bag at the collection point","Interact":"returning the bin lid to position"}},
		{"id":"dumpster_log","prop":"dumpster","tool":"phone","clips":["OpenLatch","InspectLabel","RepairMid","RecordReading"],"words":{"OpenLatch":"pulling and testing the dumpster enclosure latch","InspectLabel":"reading the physical waste-service tag","RepairMid":"tightening the enclosure fitting","RecordReading":"writing the completed dumpster service record"}}]},
	{"role":"utilities technician","personality":"safety-first and precise","stops":[
		{"id":"power_meter_service","prop":"power_meter","tool":"phone","clips":["PanelKnock","InspectLabel","RepairMid","RecordReading"],"words":{"PanelKnock":"opening and testing the power-meter cabinet","InspectLabel":"reading the live power meter display","RepairMid":"adjusting the power-meter service fitting","RecordReading":"logging the measured electrical load"}},
		{"id":"water_meter_service","prop":"water_meter","tool":"meter_reader","clips":["InspectLow","OpenGroundContainer","PickUp_Table","RecordReading"],"words":{"InspectLow":"crouching to inspect the water-meter inlet and seal","OpenGroundContainer":"opening the lower water-meter service cover","PickUp_Table":"scanning the gauge with the handheld meter reader","RecordReading":"logging the measured water flow"}}]},
	{"role":"parks worker","personality":"patient and proud of maintained spaces","stops":[
		{"id":"plant_check","prop":"planter","tool":"watering_can","clips":["Farm_Watering","Farm_Harvest","Farm_PlantSeed","Yes"],"words":{"Farm_Watering":"watering the public planter","Farm_Harvest":"removing damaged leaves","Farm_PlantSeed":"placing a replacement plant in the soil","Yes":"confirming the planter is serviced"}},
		{"id":"bench_check","prop":"bench","clips":["InspectLow","PanelKnock","RepairMid","RepairSurface"],"words":{"InspectLow":"checking the bench feet and ground anchors","PanelKnock":"testing the seat slats for looseness","RepairMid":"tightening the bench frame fitting","RepairSurface":"securing the repaired seat surface"}}]},
	{"role":"clinic assistant","personality":"calm, reassuring and organized","stops":[
		{"id":"patient_welcome","prop":"bench","tool":"phone","item":"document","clips":["InspectLow","PickUp_Table","RecordReading","SetGroundItem"],"words":{"InspectLow":"checking that the clinic waiting place is clear and safe","PickUp_Table":"taking the prepared intake form from the check-in point","RecordReading":"recording the ready waiting-space status","SetGroundItem":"returning the prepared form to the check-in point"}},
		{"id":"supply_check","prop":"box","tool":"phone","item":"medical_supply","clips":["Chest_Open","PickUp_Table","RecordReading","SetGroundItem"],"words":{"Chest_Open":"opening the sealed clinic supply crate","PickUp_Table":"lifting and checking the medical supply package","RecordReading":"logging the verified clinic supply count","SetGroundItem":"returning the counted package to the supply crate"}}]},
	{"role":"transit steward","personality":"observant and route-aware","stops":[
		{"id":"timetable","prop":"bus_stop","tool":"phone","clips":["InspectLabel","PanelKnock","RecordReading","Interact"],"words":{"InspectLabel":"reading the posted route and stop identification","PanelKnock":"testing the timetable case and mounting","RecordReading":"comparing the posted service with the live schedule","Interact":"closing and securing the timetable case"}},
		{"id":"cycle_check","prop":"bicycle","clips":["InspectLow","PanelKnock","RepairMid","RepairSurface"],"words":{"InspectLow":"checking the bicycle rack anchor and wheel channel","PanelKnock":"testing the lock point by hand","RepairMid":"tightening the rack fitting","RepairSurface":"securing the repaired lock surface"}}]},
	{"role":"market stocker","personality":"energetic and inventory-minded","stops":[
		{"id":"stock_crate","prop":"box","item":"market_goods","clips":["OpenGroundContainer","PickUp_Table","InspectLabel","SetGroundItem"],"words":{"OpenGroundContainer":"crouching to open the stock package on the ground","PickUp_Table":"lifting market stock from the crate","InspectLabel":"checking the physical stock label","SetGroundItem":"placing the verified stock into its assigned crate"}},
		{"id":"deposit","prop":"atm","tool":"phone","clips":["PanelKnock","Interact","RecordReading","InspectLabel"],"words":{"PanelKnock":"checking the deposit terminal housing","Interact":"entering the secured market deposit","RecordReading":"reconciling the deposit with the market record","InspectLabel":"verifying the terminal receipt and identifier"}}]},
	{"role":"local resident","personality":"sociable and community-minded","stops":[
		{"id":"bench_pause","prop":"bench","tool":"cup","clips":["PickUp_Table","Consume","SetGroundItem"],"words":{"PickUp_Table":"picking up the drink placed on the bench","Consume":"taking a short drink during a neighbourhood break","SetGroundItem":"placing the drink securely back on the bench"}},
		{"id":"mail_collection","prop":"mailbox","item":"mail","clips":["OpenLatch","Chest_Open","PickUp_Table","InspectLabel"],"words":{"OpenLatch":"opening the neighbourhood mailbox latch","Chest_Open":"sorting the collected mail at the compartment","PickUp_Table":"taking the addressed mail in hand","InspectLabel":"checking the address before carrying it home"}}]},
	{"role":"road maintenance technician","personality":"methodical, safety-conscious and practical","stops":[
		{"id":"manhole_inspection","prop":"manhole","tool":"phone","clips":["InspectLow","Farm_Harvest","RepairSurface","Idle_TalkingPhone_Loop"],"words":{"InspectLow":"crouching for a close storm-drain inspection","Farm_Harvest":"working low beside the drain cover","RepairSurface":"testing the cover fasteners at ground level","Idle_TalkingPhone_Loop":"recording the drain inspection"}},
		{"id":"safety_cone_check","prop":"cone","clips":["InspectLow","PickUp_Table","SetGroundItem","RepairMid"],"words":{"InspectLow":"crouching to inspect the cone base and pavement","PickUp_Table":"taking hold of the road-work safety cone","SetGroundItem":"crouching to place the cone on its safe boundary point","RepairMid":"checking the cone reflector at hand height"}}]},
	{"role":"building maintenance technician","personality":"careful, technically minded and dependable","stops":[
		{"id":"aircon_service","prop":"aircon","tool":"phone","clips":["PanelKnock","RepairMid","RepairSurface","RecordReading"],"words":{"PanelKnock":"testing the air-conditioning access panel","RepairMid":"repairing the air-conditioning service panel","RepairSurface":"checking the filter housing on its work surface","RecordReading":"writing the air-conditioning service reading"}},
		{"id":"fire_exit_audit","prop":"fire_exit","tool":"phone","clips":["OpenLatch","PanelKnock","InspectLabel","RecordReading"],"words":{"OpenLatch":"pulling and testing the fire-exit release","PanelKnock":"checking the fire-exit panel by hand","InspectLabel":"reading the physical evacuation-route label","RecordReading":"writing the completed evacuation-route audit"}}]},
	{"role":"public information technician","personality":"detail-focused, communicative and creative","stops":[
		{"id":"billboard_check","prop":"billboard","tool":"phone","clips":["PresentWork","PanelKnock","InspectLabel","RepairMid"],"words":{"PresentWork":"pointing across the information display surface","PanelKnock":"testing the billboard mounting by hand","InspectLabel":"reading the physical display condition label","RepairMid":"tightening the display support fitting"}},
		{"id":"poster_install","prop":"poster","item":"notice","clips":["PickUp_Table","Interact","RepairSurface","InspectLabel"],"words":{"PickUp_Table":"taking hold of the prepared community notice","Interact":"pressing and smoothing the notice onto the board","RepairSurface":"securing the lower edge of the notice","InspectLabel":"checking the installed notice text and alignment"}}]},
	{"role":"horticulture specialist","personality":"patient, observant and environmentally minded","stops":[
		{"id":"greenhouse_round","prop":"greenhouse","tool":"watering_can","clips":["Farm_Watering","Farm_Harvest","Farm_PlantSeed","Yes"],"words":{"Farm_Watering":"watering the greenhouse propagation bed","Farm_Harvest":"checking and pruning the propagated plants","Farm_PlantSeed":"placing a replacement seedling","Yes":"confirming the greenhouse bed is serviced"}},
		{"id":"flower_pot_service","prop":"flower_pot","tool":"watering_can","clips":["InspectLow","Farm_PlantSeed","Farm_Watering","Farm_Harvest"],"words":{"InspectLow":"crouching to inspect the flower pot and soil","Farm_PlantSeed":"planting a replacement flower","Farm_Watering":"watering the newly planted flower","Farm_Harvest":"removing damaged leaves"}}]}
]


# Added jobs: each owns real props with their own interactions (see InteractionLibrary).
const EXTRA_WORKFLOWS := [
	{"role":"street vendor","personality":"chatty, cheerful and early to rise","stops":[
		{"id":"cart_service","prop":"coffee_cart","clips":["Interact","PickUp_Table","RepairSurface","SetGroundItem"],"words":{"Interact":"brewing and pouring a coffee at the cart","PickUp_Table":"taking a cup from the shelf of mugs","RepairSurface":"wiping the serving counter down","SetGroundItem":"setting a filled cup on the counter for a customer"}},
		{"id":"menu_board","prop":"sandwich_board","clips":["Interact","RepairMid","InspectLabel"],"words":{"Interact":"chalking today's specials on the pavement board","RepairMid":"straightening the A-frame sign","InspectLabel":"reading the board from the kerb to check it can be seen"}}]},
	{"role":"shopkeeper","personality":"orderly, patient and good with customers","stops":[
		{"id":"till","prop":"market_counter","clips":["Interact","PickUp_Table","RecordReading","SetGroundItem"],"words":{"Interact":"keying in a sale at the till","PickUp_Table":"lifting a customer's goods from the crate","RecordReading":"opening the drawer and counting change","SetGroundItem":"setting the bagged goods on the counter"}},
		{"id":"restock_truck","prop":"hand_truck","clips":["Interact","PickUp_Table","RepairMid","SetGroundItem"],"words":{"Interact":"tipping the hand truck back onto its wheels","PickUp_Table":"gripping the handles of the loaded hand truck","RepairMid":"steadying the load on the hand truck","SetGroundItem":"easing the hand truck down to rest"}}]},
	{"role":"security guard","personality":"alert, calm and methodical","stops":[
		{"id":"extinguisher_round","prop":"extinguisher","clips":["Interact","InspectLabel","PickUp_Table","RepairMid"],"words":{"Interact":"lifting the extinguisher to check its weight","InspectLabel":"reading the pressure gauge and service tag","PickUp_Table":"kneeling to the extinguisher cylinder","RepairMid":"checking the pin and seal"}},
		{"id":"meter_patrol","prop":"parking_meter","clips":["InspectLabel","Interact","PanelKnock"],"words":{"InspectLabel":"reading the meter display against the permit","Interact":"feeding a coin and pressing the button to test the meter","PanelKnock":"checking the meter head is secure"}}]},
	{"role":"paramedic","personality":"composed, quick and kind","stops":[
		{"id":"chair_prep","prop":"wheelchair","clips":["Interact","PickUp_Table","RepairMid","InspectLow"],"words":{"Interact":"releasing the wheelchair brake","PickUp_Table":"plumping the seat cushion","RepairMid":"checking the brake lever travel","InspectLow":"kneeling to check the wheel and footrest"}},
		{"id":"kit_check","prop":"box","item":"medical_supply","clips":["Chest_Open","PickUp_Table","RecordReading","SetGroundItem"],"words":{"Chest_Open":"opening the sealed first-aid crate","PickUp_Table":"lifting and checking the medical kit","RecordReading":"logging the kit contents","SetGroundItem":"returning the kit to the crate"}}]},
	{"role":"electrician","personality":"cautious, exact and dry-humoured","stops":[
		{"id":"cabinet_service","prop":"power","tool":"phone","clips":["PanelKnock","InspectLabel","RepairMid","RecordReading"],"words":{"PanelKnock":"testing the street cabinet door","InspectLabel":"reading the cabinet's warning plate","RepairMid":"tightening the cabinet fitting","RecordReading":"logging the visit on the work tablet"}},
		{"id":"toolbox_round","prop":"toolbox","clips":["Interact","PickUp_Table","RepairMid","Chest_Open"],"words":{"Interact":"opening the toolbox and choosing a wrench","PickUp_Table":"taking the wrench out of the tray","RepairMid":"testing a fitting with the wrench","Chest_Open":"kneeling to open the toolbox"}}]},
	{"role":"freight handler","personality":"strong, unhurried and dependable","stops":[
		{"id":"truck_loading","prop":"hand_truck","clips":["Interact","PickUp_Table","RepairMid","SetGroundItem"],"words":{"Interact":"tipping the hand truck back under a heavy load","PickUp_Table":"taking both handles of the hand truck","RepairMid":"steadying the stacked parcels","SetGroundItem":"lowering the hand truck gently"}},
		{"id":"skip_clearance","prop":"dumpster","clips":["OpenLatch","InspectLabel","RepairMid","RecordReading"],"words":{"OpenLatch":"lifting the skip lid","InspectLabel":"reading the skip service tag","RepairMid":"tightening the skip lid hinge","RecordReading":"logging the skip fill level"}}]},
	{"role":"facade technician","personality":"head-for-heights, careful and tidy","stops":[
		{"id":"ladder_setup","prop":"ladder","clips":["Interact","InspectLabel","RepairMid","PickUp_Table"],"words":{"Interact":"testing the ladder with body weight","InspectLabel":"checking the rung and rail labels","RepairMid":"steadying the ladder on its rails","PickUp_Table":"taking both rails of the ladder"}},
		{"id":"tool_trolley","prop":"tool_cart","clips":["Interact","PickUp_Table","RepairMid","InspectLabel"],"words":{"Interact":"choosing a tool from the trolley tray","PickUp_Table":"lifting the wrench out of the tray","RepairMid":"checking the jaw of the wrench","InspectLabel":"checking the trolley shelf inventory"}}]},
	{"role":"office clerk","personality":"punctual, tidy and talkative on breaks","stops":[
		{"id":"payphone_call","prop":"payphone","clips":["Interact","Idle_TalkingPhone_Loop","InspectLabel","PanelKnock"],"words":{"Interact":"feeding a coin and dialling the office","Idle_TalkingPhone_Loop":"talking to the office over the handset","InspectLabel":"reading the call rates on the phone display","PanelKnock":"tapping the keypad to redial"}},
		{"id":"vending_break","prop":"vending_machine","clips":["Interact","InspectLabel","PanelKnock"],"words":{"Interact":"buying a cold drink from the vending machine","InspectLabel":"browsing the vending machine window","PanelKnock":"choosing a drink on the keypad"}}]},
	{"role":"amenities attendant","personality":"friendly, observant and community-minded","stops":[
		{"id":"fountain_check","prop":"drinking_fountain","clips":["Interact","InspectLow","PickUp_Table"],"words":{"Interact":"testing the drinking fountain and taking a sip","InspectLow":"leaning in to check the fountain spout","PickUp_Table":"pressing the fountain button to flush it"}},
		{"id":"bench_survey","prop":"bench","clips":["InspectLow","PanelKnock","RepairMid","RepairSurface"],"words":{"InspectLow":"checking the bench feet and ground anchors","PanelKnock":"testing the seat slats for looseness","RepairMid":"tightening a bench fitting","RepairSurface":"wiping down the bench slats"}}]},
]

const WORKFLOWS := BASE_WORKFLOWS + EXTRA_WORKFLOWS

# Each definition is a genuine two-person smart-object: separate reserved slots,
# reciprocal facing, one shared physical prop and complementary timed phases.
# No phase uses the old generic waving loop.
const PAIR_INTERACTIONS := [
	{"id":"parcel_handoff","a":4,"b":0,"a_occupation":"parcel courier","b_occupation":"local resident","block":"r1c1","edge":0,"prop":"box","hand_item":"parcel","item_owners":[0,1,1],"turns":[0,1,0],"title":"parcel handoff","a_role":"courier presenting the checked parcel","b_role":"resident receiving and confirming delivery","phases":[["OfferParcel","ReceiveParcel"],["DescribeParcel","CheckParcel"],["CompleteHandoff","AcceptHandoff"]]},
	{"id":"clinic_check_in","a":2,"b":12,"a_occupation":"clinic assistant","b_occupation":"local resident","block":"r1c3","edge":2,"prop":"bench","hand_item":"document","item_owners":[0,0,0],"turns":[0,1,0],"title":"clinic check-in","a_role":"clinic assistant recording the visitor details","b_role":"patient answering the check-in questions","phases":[["AskCheckIn","AnswerCheckIn"],["RecordCheckIn","WaitForCheckIn"],["WelcomePatient","ConfirmCheckIn"]]},
	{"id":"utility_briefing","a":15,"b":1,"a_occupation":"utilities technician","b_occupation":"public information technician","block":"r2c2","edge":4,"prop":"power_meter","hand_item":"document","item_owners":[1,1,1],"turns":[0,1,0],"title":"power-meter service briefing","a_role":"utilities technician explaining the meter reading","b_role":"public-information technician logging the service report","phases":[["ExplainMeter","LogMeterReading"],["PointToMeter","ReviewServiceNote"],["ConfirmMeterWork","CloseServiceReport"]]},
	{"id":"market_purchase","a":14,"b":9,"a_occupation":"market stocker","b_occupation":"local resident","block":"r2c4","edge":6,"along":8.0,"prop":"box","hand_item":"market_goods","item_owners":[0,1,1],"turns":[0,1,0],"title":"market purchase","a_role":"market stocker presenting the requested goods","b_role":"resident checking and accepting the order","phases":[["PresentGoods","InspectGoods"],["DescribeGoods","ChooseGoods"],["CompletePurchase","AcceptPurchase"]]},
	{"id":"street_service_request","a":10,"b":18,"a_occupation":"sanitation worker","b_occupation":"parcel courier","block":"r3c1","edge":2,"prop":"trash","hand_item":"document","item_owners":[0,1,1],"turns":[0,1,0],"title":"street-cleaning service review","a_role":"sanitation worker demonstrating the completed cleanup","b_role":"courier confirming the path is clear","phases":[["ShowCleanedArea","ReviewCleanedArea"],["ExplainServiceWork","ConfirmClearPath"],["CloseServiceRequest","AcceptServiceReport"]]},
	{"id":"route_assistance","a":7,"b":20,"a_occupation":"transit steward","b_occupation":"local resident","block":"r4c3","edge":0,"along":-8.0,"prop":"bus_stop","hand_item":"document","item_owners":[0,1,1],"turns":[0,1,0],"title":"transit route assistance","a_role":"transit steward explaining the next route","b_role":"traveller checking and acknowledging directions","phases":[["ExplainRoute","FollowRouteExplanation"],["ClarifyTransfer","CheckRouteDetails"],["ConfirmDirections","AcknowledgeDirections"]]}
]

var _graph: Dictionary = {}
var _links: Dictionary = {}
var _stations: Dictionary = {}
var _agents: Array[Dictionary] = []
var _pairs: Array[Dictionary] = []
var _elapsed := 0.0
var _planner_http: HTTPRequest
var _planner_port := 0
var _planner_active_index := -1
var _planner_next_at := 1.0
var _planner_cursor := 0
var _hand_tools_calibrated := false
var _building_bounds: Array[AABB] = []
var _static_obstacles: Array[Dictionary] = []
var _activity_prop_obstacles: Array[Dictionary] = []
var _walk_segments: Array = []
var _walk_segment_grid: Dictionary = {}
var _building_obstacle_grid: Dictionary = {}
var _static_obstacle_grid: Dictionary = {}
var _activity_prop_obstacle_grid: Dictionary = {}
var _agent_spatial_grid: Dictionary = {}
var _tree_safety := {"original_intersections":0,"relocated":0,"hidden":0}
var _urban_prop_safety := {"original_building_intersections":0,"original_road_intersections":0,"original_prop_intersections":0,"relocated":0,"hidden":0,"grounded":0,"maximum_ground_correction":0.0}
var _minimum_observed_prop_workers := 999
var _minimum_observed_meter_workers := 999
var _minimum_observed_power_meter_workers := 999
var _minimum_observed_water_meter_workers := 999
var _society: Node
var _society_ready := false
var _presentation_worker_indices: Array[int] = []
var _interaction_assignments: Array = []
var _next_transport_sample_at := 0.0
var _custody: RefCounted
var _furniture_report := {}

func _ready() -> void:
	# Navigation/assignment runs early in physics. In idle, the explicitly early
	# AnimationPlayers run first, this grounding pass runs second, and default
	# camera/controllers run last against the final grounded transform.
	process_physics_priority = -50
	process_priority = POSE_GROUNDING_PRIORITY
	_build_sidewalk_graph()
	_building_bounds = CitySpatialSafety.building_bounds(get_tree())
	var furniture_parent := Node3D.new()
	furniture_parent.name = "StreetFurniture"
	add_child(furniture_parent)
	if OS.get_environment("CITY_NO_FURNITURE") == "":
		_furniture_report = StreetFurniturePlanner.populate(get_tree(), furniture_parent, _building_bounds)
	call_deferred("_prune_furniture")
	_tree_safety = CitySpatialSafety.deconflict_trees(get_tree(), _building_bounds)
	_urban_prop_safety = CitySpatialSafety.deconflict_urban_props(get_tree(), _building_bounds)
	for value in get_tree().get_nodes_in_group("backdrop_pedestrians"):
		if value is Node3D:
			# The obsolete backdrop loop stays hidden; stop its skeletons from
			# animating unseen every frame as well.
			(value as Node3D).visible = false
			(value as Node3D).process_mode = Node.PROCESS_MODE_DISABLED
	_society = SocietyKernel.new()
	_society.name = "SocietyKernel"
	add_child(_society)
	_society.call("bootstrap", _build_visible_roster(), 200, 80, 760911)
	_setup_agents()
	_interaction_assignments = _society.call("plan_visible_interactions", PAIR_INTERACTIONS) as Array
	_society.call("finalize_visible_day", _interaction_assignments, PRESENTATION_WORKER_TARGET)
	_refresh_presentation_worker_indices()
	_apply_society_opening_assignments()
	_separate_initial_positions()
	_setup_pair_interactions(_interaction_assignments)
	_rebuild_spatial_constraints()
	_enforce_guaranteed_prop_jobs()
	_society_ready = true
	var planner_text:=OS.get_environment("CITY_NPC_PLANNER_PORT")
	if planner_text.is_valid_int():
		_planner_port=int(planner_text); _planner_http=HTTPRequest.new(); _planner_http.timeout=7.0; add_child(_planner_http); _planner_http.request_completed.connect(_on_planner_response)

## Footprints of everything a person cannot walk through (street lights, poles, trees, signals, street furniture).
var _scene_boxes: Array[AABB] = []

func _scene_obstacle_boxes() -> Array[AABB]:
	if _scene_boxes.is_empty():
		# Poles, lamps, signals and trees block only where they stand (their overhead arms and canopies do
		# not), so they are footprints around their base; street furniture uses its real footprint.
		for group_name in ["smart_city_devices", "power_poles", "city_trees", "traffic_signals", "water_valves", "power_switches"]:
			var radius := 0.72 if group_name == "city_trees" else 0.40
			for value in get_tree().get_nodes_in_group(group_name):
				if value is Node3D and (value as Node3D).visible and not (value as Node3D).is_queued_for_deletion():
					var p := (value as Node3D).global_position
					var size_hint := CitySpatialSafety.world_bounds(value as Node3D).size
					if size_hint.x > 6.0 and size_hint.z > 6.0:
						continue
					_scene_boxes.append(AABB(Vector3(p.x - radius, 0.0, p.z - radius), Vector3(radius * 2.0, 2.0, radius * 2.0)))
		for group_name2 in ["urban_props", "npc_activity_props"]:
			for value in get_tree().get_nodes_in_group(group_name2):
				if value is Node3D and (value as Node3D).visible and not (value as Node3D).is_queued_for_deletion():
					var box := CitySpatialSafety.world_bounds(value as Node3D)
					if box.size != Vector3.ZERO:
						_scene_boxes.append(box)
	return _scene_boxes

## True when a person can stand at `stand` and walk there in a straight line from `origin`.
## `own_box` is the footprint of the prop being used (it is allowed to be close, not walked through).
func _stand_clear(stand: Vector3, origin: Vector3, own_box := AABB()) -> bool:
	var boxes := _scene_obstacle_boxes()
	for i in 6:
		var point := origin.lerp(stand, float(i) / 5.0)
		var probe := AABB(Vector3(point.x - 0.34, 0.0, point.z - 0.34), Vector3(0.68, 2.0, 0.68))
		for box in boxes:
			if own_box.size != Vector3.ZERO and box.position.distance_to(own_box.position) < 0.01 and box.size.distance_to(own_box.size) < 0.01:
				continue
			if CitySpatialSafety.horizontal_intersects(probe, box, 0.0):
				if OS.get_environment("STAND_DEBUG") != "":
					print("STANDBLOCK point=", point, " box=", box)
				return false
	return true

func _prune_furniture() -> void:
	_furniture_report["pruned_for_devices"] = StreetFurniturePlanner.prune_device_overlaps(get_tree())
	await get_tree().process_frame
	_scene_boxes.clear()
	if _society_ready:
		_rebuild_spatial_constraints()

func _physics_process(delta: float) -> void:
	_elapsed += delta
	if _society_ready:
		_society.call("advance", delta)
		if _elapsed >= _next_transport_sample_at:
			_society.call("record_transport_snapshot", _live_transport_snapshot())
			_next_transport_sample_at = _elapsed + 1.0
	# BoneAttachment3D receives the imported skeleton's final pose only after the
	# first animation evaluation.  Calibrate once at that point so hand-held props
	# are measured from the real wrist transform, never from an import-time pose.
	if not _hand_tools_calibrated and _elapsed >= 0.25:
		_calibrate_hand_tools()
		_hand_tools_calibrated = true
	_update_pair_interactions()
	_rebuild_agent_spatial_grid()
	_update_street_chats(delta)
	for index in _agents.size(): _update_agent(index,delta)
	# Enforcement can start a low work clip and reset the actor to its station.
	# Do that before foot locking so a newly assigned crouch is grounded in this
	# same rendered frame instead of visibly hovering until the next pass.
	_enforce_guaranteed_prop_jobs()
	_update_planner()


func _process(delta: float) -> void:
	# Rocketbox AnimationPlayers use idle evaluation. This node has a later idle
	# priority, so these are the exact poses that will be rendered—not the prior
	# physics frame's pose. Navigation remains authoritative for X/Z.
	for index in _agents.size():
		var live_agent := _agents[index]
		var live_person := live_agent.node as Node3D
		if is_instance_valid(live_person) and not bool(live_agent.get("at_home", false)):
			# One constant height for the whole session: the feet are kept on the pavement by the
			# IK layer inside the skeleton, so nothing here moves the character up or down.
			live_person.global_position.y = float(live_agent.ground_y)
			live_person.set_meta("npc_surface_y", float(live_agent.ground_y))
		(live_agent.director as RefCounted).call("update", delta)
	# Objects follow the final grounded hands of this same frame.
	_update_item_custody(delta)


func _build_visible_roster() -> Array:
	var roster: Array = []
	for value in get_tree().get_nodes_in_group("pedestrians"):
		if not value is Node3D:
			continue
		var person := value as Node3D
		var player := person.find_child("AnimationPlayer", true, false) as AnimationPlayer
		if player == null or not ROCKETBOX_MODEL_GENDERS.has(person.scene_file_path):
			continue
		var display_index := roster.size()
		var workflow_index := int(WARDROBE_WORKFLOW_BY_CHARACTER[display_index % WARDROBE_WORKFLOW_BY_CHARACTER.size()])
		var workflow := WORKFLOWS[workflow_index] as Dictionary
		roster.append({
			"display_index": display_index,
			"display_name": str(CITIZEN_NAMES[display_index % CITIZEN_NAMES.size()]),
			"occupation_key": str(workflow.role),
			"occupation_title": str(workflow.role),
			"personality": str(workflow.personality),
			"model_path": person.scene_file_path
		})
	return roster


func _workflow_index_for_role(role: String) -> int:
	for workflow_index in WORKFLOWS.size():
		if str((WORKFLOWS[workflow_index] as Dictionary).role) == role:
			return workflow_index
	return 7

func _setup_agents() -> void:
	var citizens := get_tree().get_nodes_in_group("pedestrians")
	var standard_count := 0
	for index in citizens.size():
		if not citizens[index] is Node3D: continue
		var person := citizens[index] as Node3D
		var player := person.find_child("AnimationPlayer",true,false) as AnimationPlayer
		if player == null: continue
		player.process_priority = ANIMATION_EVALUATION_PRIORITY
		if not ROCKETBOX_MODEL_GENDERS.has(person.scene_file_path):
			push_error("NPC %s is not a selected Rocketbox model: %s" % [person.name, person.scene_file_path])
			continue
		if not player.has_animation_library("rocketbox") and not RocketboxAnimationLibrary.install_on(person, player):
			push_error("NPC %s could not install its Rocketbox motion library" % person.name)
			continue
		_install_interaction_animations(player)
		var display_index := standard_count
		standard_count += 1
		var citizen_profile := _society.call("citizen_for_visible_index", display_index) as Dictionary
		var workflow_index := _workflow_index_for_role(str(citizen_profile.get("occupation", "local resident")))
		var workflow := WORKFLOWS[workflow_index] as Dictionary
		var citizen_id := str(citizen_profile.get("citizen_id", "citizen_%04d" % (display_index + 1)))
		var skeleton := person.find_child("Skeleton3D", true, false) as Skeleton3D
		var foot_bone_indices := _find_foot_bone_indices(skeleton)
		# Ground speed of this rig's walk clip, so playback keeps feet planted.
		var walk_natural := WalkStride.natural_speed(person, player, skeleton, "rocketbox/Walking")
		var foot_reference_y := _calibrate_foot_reference(person, player, skeleton, foot_bone_indices)
		var station_ids: Array[String] = []
		for stop_index in 2:
			station_ids.append(_create_station(display_index, stop_index, workflow.stops[stop_index] as Dictionary, citizen_id, str(workflow.role)))
		var equipment: Dictionary = {}
		var handled_items: Dictionary = {}
		# Solo job tools and carried items belong to the InteractionDirector now: it builds them
		# at real size, puts them in a real grip and hands them to the prop.
		var citizen_ik := CitizenIK.new()
		citizen_ik.name = "CitizenIK"
		skeleton.add_child(citizen_ik)
		citizen_ik.setup(skeleton)
		citizen_ik.ground_y = person.global_position.y
		citizen_ik.calibrate(person.global_position.y)
		var director := InteractionDirector.new()
		director.setup(person, skeleton, citizen_ik)
		# Job props are semantic scene objects, not inverse-kinematics targets.
		# The original character clips retain full ownership of the anatomy.
		var right_hand_rig:Dictionary={}
		var left_hand_rig:Dictionary={}
		var opening_stop := display_index % 2
		var agent := {"node":person,"animation":player,"skeleton":skeleton,"foot_bone_indices":foot_bone_indices,"foot_reference_y":foot_reference_y,"pose_ground_offset":0.0,"equipment":equipment,"handled_items":handled_items,"ik":citizen_ik,"director":director,"right_hand_rig":right_hand_rig,"left_hand_rig":left_hand_rig,"contact_weight":0.0,"contact_goal":0.0,"contact_kind":"none","index":display_index,"citizen_id":citizen_id,"household_id":str(citizen_profile.get("household_id","")),"name":str(citizen_profile.get("display_name",CITIZEN_NAMES[display_index % CITIZEN_NAMES.size()])),"role":str(workflow.role),"personality":str(citizen_profile.get("personality",workflow.personality)),"stations":station_ids,"stop_index":opening_stop,"preferred_stop":-1,"station_id":"","work_order_id":"","social_event_id":"","trip_id":"","decision_reason":"","society_activity":{},"task":"","action":"","action_target":"","work_stage":"","route":PackedStringArray(),"route_start":"","route_entry_pending":false,"route_entry_target":Vector3.ZERO,"route_index":0,"ground_y":person.global_position.y,"lane_sign":-1.0 if display_index%2==0 else 1.0,"motion_state":"starting_shift","action_until":0.0,"gesture_until":0.0,"next_gesture_at":0.0,"departure_hold_until":0.0,"selected_clip":"","action_clips":[],"action_clip_index":0,"last_speed":0.0,"stuck_seconds":0.0,"last_block_reason":"","previous_move_direction":Vector3.ZERO,"avoidance_reversals":0,"yield_events":0,"plan_source":"society_kernel:awaiting_causal_assignment","planner_payload":{},"planner_response":{},"completed_actions":0,"pair_id":"","pair_partner":"","pair_role":"","pair_phase":-1,"interaction_turn":"","active_pair_item_kind":"","guaranteed_prop_job":false}
		person.set_meta("npc_ai_controlled",true); person.set_meta("npc_animation_source","Microsoft Rocketbox motion capture on its shared Bip01 production rig"); person.set_meta("npc_model_source","Microsoft Rocketbox high-definition avatar library (MIT)"); person.set_meta("npc_visual_height",1.82); person.set_meta("npc_display_index",display_index)
		person.set_meta("citizen_id", citizen_id)
		person.set_meta("citizen_household_id", str(agent.household_id))
		person.set_meta("npc_guaranteed_prop_job", false)
		person.set_meta("npc_surface_y", person.global_position.y)
		person.set_meta("npc_pose_ground_offset", 0.0)
		person.set_meta("npc_foot_reference_y", foot_reference_y)
		agent.walk_natural = walk_natural
		director.set("walk_natural", walk_natural)
		_agents.append(agent)

func _install_interaction_animations(player:AnimationPlayer)->void:
	# Fix 73's Rocketbox library already exposes every synchronized interaction
	# phase as a direct alias to a deliberately selected motion-capture clip.
	# Duplicating generic Interact/Yes clips here would erase that assignment.
	if player.has_animation_library("rocketbox"): return
	if player.has_animation_library("city_interactions"): return
	var library:=AnimationLibrary.new()
	for clip_name_value in INTERACTION_ANIMATION_PROFILES:
		var clip_name:=str(clip_name_value)
		var source_suffix:=str(INTERACTION_ANIMATION_PROFILES[clip_name])
		var source_name:="city_actions/CityAction|%s"%source_suffix
		if not player.has_animation(source_name): source_name=_find_clip(player,source_suffix)
		var source:=player.get_animation(source_name) if not source_name.is_empty() else null
		if source!=null:
			var animation:=source.duplicate(true) as Animation; animation.loop_mode=Animation.LOOP_LINEAR
			library.add_animation("CityInteraction|%s"%clip_name,animation)
	player.add_animation_library("city_interactions",library)

func _create_station(agent_index: int, stop_index: int, stop: Dictionary, citizen_id: String, role: String) -> String:
	var block_index := agent_index%16
	var row := block_index/4+1; var column := block_index%4+1
	# Four agents use one block, but each receives its own side midpoint. Corners
	# are intentionally excluded because that is where traffic poles and trees sit.
	var edge_options := [0,2,4,6]
	var edge := int(edge_options[int(agent_index/16)*2+stop_index])
	var block := "r%dc%d"%[row,column]
	var station_id := "npc_%02d_%s"%[agent_index+1,str(stop.id)]
	var station := {"id":station_id,"name":str(stop.id).replace("_"," ").capitalize(),"block":block,"edge":edge,"node":_node_key(block,edge),"prop":str(stop.prop),"tool":str(stop.get("tool","")),"item":str(stop.get("item","")),"clips":stop.clips,"words":stop.words,"owner":agent_index,"owner_citizen_id":citizen_id,"role":role}
	_stations[station_id]=station; _place_station_prop(station)
	_society.call("register_station", {
		"station_id": station_id,
		"owner_citizen_id": citizen_id,
		"owner_index": agent_index,
		"role": role,
		"prop_kind": str(stop.prop),
		"tool_kind": str(stop.get("tool", "")),
		"item_kind": str(stop.get("item", "")),
		"clips": (stop.clips as Array).duplicate(),
		"block": block,
		"edge": edge,
		"graph_node": str(station.node)
	})
	return station_id

func _place_station_prop(station: Dictionary) -> void:
	var prop: Node3D = PropFactory.build(str(station.prop))
	if prop==null: return
	prop.name="ActivityProp_%s"%str(station.id); prop.add_to_group("npc_activity_props"); prop.set_meta("activity_station",station.id); prop.set_meta("activity_prop_kind",station.prop); prop.set_meta("activity_owner_index",int(station.owner))
	if str(station.prop) in ["power_meter", "water_meter"]:
		prop.add_to_group("npc_meter_work_props")
		prop.set_meta("meter_kind", str(station.prop).trim_suffix("_meter"))
		prop.set_meta("meter_work_only", true)
		prop.set_meta("power_pole_job_eligible", false)
	add_child(prop)
	_normalize_prop(prop,str(station.prop))
	if not _resolve_station_prop_placement(station, prop):
		push_error("No collision-safe placement found for job station %s" % str(station.id))
	station["prop_node"]=prop
	_stations[str(station.id)] = station
	# A reserved activity site must be readable, not buried inside an ambient
	# planter/tree/bin already occupying the same patch of sidewalk.
	for ambient in get_tree().get_nodes_in_group("urban_props"):
		if ambient is Node3D and (ambient as Node3D).global_position.distance_to(prop.global_position)<3.2:
			(ambient as Node3D).visible=false

func _resolve_station_prop_placement(station: Dictionary, prop: Node3D) -> bool:
	var base := _graph[str(station.node)] as Vector3
	var block_center := _block_center(str(station.block), 0.16)
	var inward := (block_center - base).normalized()
	var tangent := _pair_tangent(int(station.edge))
	var original_actor := Vector3(base.x, 0.16, base.z) + inward * 1.15
	var along_offsets := [0.0, -4.0, 4.0, -8.0, 8.0, -12.0, 12.0, -16.0, 16.0, -20.0, 20.0, -24.0, 24.0]
	for along_value in along_offsets:
		var actor_position := original_actor + tangent * float(along_value)
		if not _position_outside_buildings(actor_position, 0.60):
			continue
		# The prop's front faces the person: it sits straight ahead at the prop kind's own
		# comfortable working distance from their feet, never closer or farther by guesswork.
		var prop_size := prop.get_meta("prop_size", Vector3(0.6, 1.0, 0.6)) as Vector3
		var prop_reach := 0.4
		if ActivityLibrary.is_kerb_kind(str(station.prop)):
			# Kerb furniture stands 0.55 m from the kerb edge with its front toward the footpath;
			# the person stands on the footpath in front of it.
			var kerb_point := Vector3(base.x, 0.16, base.z) + tangent * float(along_value) - inward * 1.45
			prop.rotation.y = atan2(inward.x, inward.z)
			prop.global_position = kerb_point
			actor_position = kerb_point + inward * (prop_size.z * 0.5 + prop_reach)
		else:
			prop.rotation.y = atan2(-inward.x, -inward.z)
			prop.global_position = actor_position + inward * (prop_size.z * 0.5 + prop_reach)
		_ground_prop(prop, 0.16)
		# The motion-capture performance decides where the person stands (where the actor's hands were on the
		# prop, or where the capture's step-back lands the pelvis on a seat): move the prop so that spot is here.
		var activity_stand := ActivityLibrary.stand_transform(prop, str(station.prop), ActivityLibrary.scene_class(str(station.prop)))
		var shift := actor_position - (activity_stand.position as Vector3)
		shift.y = 0.0
		prop.global_position += shift
		_ground_prop(prop, 0.16)
		_scene_boxes.clear()
		if not _stand_clear(actor_position, Vector3(base.x, 0.16, base.z), CitySpatialSafety.world_bounds(prop)):
			continue
		# The previous 0.98 m surface gap placed cones, planters, panels, and
		# crates outside a normal adult reach. 0.66 m keeps the rendered item in
		# the authored Rocketbox hand-work envelope without body/prop overlap.
		_place_prop_surface_at_reach(prop, actor_position, inward, float(PROP_SURFACE_REACH.get(str(station.prop), 0.58)))
		var bounds := _model_world_bounds(prop)
		if bounds.size == Vector3.ZERO or CitySpatialSafety.overlaps_road(bounds, 0.10) or _bounds_overlap_buildings(bounds, 0.20) or _bounds_overlap_existing_job_props(bounds, prop, 0.35) or _bounds_overlap_registered_devices(bounds, 0.28):
			continue
		station["resolved_position"] = actor_position
		station["placement_offset"] = float(along_value)
		prop.set_meta("collision_safe_placement", true)
		prop.set_meta("placement_offset_m", float(along_value))
		return true
	return false

func _position_outside_buildings(position: Vector3, clearance: float) -> bool:
	var indices := _obstacle_indices_at(_building_obstacle_grid, position)
	if _building_obstacle_grid.is_empty():
		for bounds in _building_bounds:
			if CitySpatialSafety.point_inside_horizontal(position, bounds, clearance):
				return false
		return true
	for index_value in indices:
		var bounds := _building_bounds[int(index_value)] as AABB
		if CitySpatialSafety.point_inside_horizontal(position, bounds, clearance):
			return false
	return true

func _bounds_overlap_buildings(bounds: AABB, margin: float) -> bool:
	for building in _building_bounds:
		if CitySpatialSafety.horizontal_intersects(bounds, building, margin):
			return true
	return false

func _bounds_overlap_existing_job_props(bounds: AABB, ignored: Node3D, margin: float) -> bool:
	for value in get_tree().get_nodes_in_group("npc_activity_props"):
		if not value is Node3D or value == ignored or not (value as Node3D).visible:
			continue
		if CitySpatialSafety.horizontal_intersects(bounds, _model_world_bounds(value as Node3D), margin):
			return true
	return false

func _bounds_overlap_registered_devices(bounds: AABB, margin: float) -> bool:
	for value in get_tree().get_nodes_in_group("smart_city_devices"):
		if not value is Node3D or not (value as Node3D).visible:
			continue
		if CitySpatialSafety.horizontal_intersects(bounds, _model_world_bounds(value as Node3D), margin):
			return true
	return false

func _place_prop_surface_at_reach(prop:Node3D,actor_position:Vector3,direction:Vector3,reach:float)->void:
	if prop.has_meta("prop_kind"): return
	var bounds:=_model_world_bounds(prop)
	if bounds.size==Vector3.ZERO: return
	var horizontal:=Vector3(direction.x,0.0,direction.z).normalized()
	var projected_half_extent:=absf(horizontal.x)*bounds.size.x*0.5+absf(horizontal.z)*bounds.size.z*0.5
	var desired_center:=actor_position+horizontal*(reach+projected_half_extent)
	var current_center:=bounds.get_center()
	prop.global_position+=Vector3(desired_center.x-current_center.x,0.0,desired_center.z-current_center.z)
	_ground_prop(prop,actor_position.y)

func _normalize_prop(prop: Node3D, kind: String) -> void:
	# Catalogued props are authored at real-world size; nothing is ever rescaled to "fit".
	if prop.has_meta("prop_kind"): return
	var bounds:=AABB(); var found:=false
	var inverse:=prop.global_transform.affine_inverse()
	for value in prop.find_children("*","MeshInstance3D",true,false):
		var mesh_node:=value as MeshInstance3D
		if mesh_node.mesh==null: continue
		var local_bounds: AABB=(inverse*mesh_node.global_transform)*mesh_node.mesh.get_aabb()
		bounds=local_bounds if not found else bounds.merge(local_bounds); found=true
	if not found: return
	var largest:=maxf(bounds.size.x,maxf(bounds.size.y,bounds.size.z))
	if largest<=0.001: return
	var factor:=float(PROP_WORLD_SIZE.get(kind,2.0))/largest
	prop.scale=Vector3.ONE*factor

func _ground_prop(prop:Node3D,ground_y:float)->void:
	if prop.has_meta("prop_kind"):
		# The factory puts the lowest visible point (top, for flush covers) on the origin.
		prop.global_position.y=ground_y
		prop.set_meta("spatial_safety_grounded", true); prop.set_meta("spatial_safety_surface_y", ground_y); prop.set_meta("spatial_safety_ground_correction", 0.0)
		return
	prop.force_update_transform()
	var bounds:=_model_world_bounds(prop)
	if bounds.size!=Vector3.ZERO:
		var correction := ground_y - bounds.position.y
		prop.global_position.y += correction
		prop.set_meta("spatial_safety_grounded", true)
		prop.set_meta("spatial_safety_surface_y", ground_y)
		prop.set_meta("spatial_safety_ground_correction", correction)

func _create_hand_tool(person: Node3D, kind: String) -> Node3D:
	var packed:=load(str(TOOL_PATHS.get(kind,""))) as PackedScene
	var skeleton:=person.find_child("Skeleton3D",true,false) as Skeleton3D
	if packed==null or skeleton==null: return null
	var hand_bone := "Bip01 R Hand" if skeleton.find_bone("Bip01 R Hand") >= 0 else "Wrist.R"
	var attachment:=BoneAttachment3D.new(); attachment.name="JobTool_%s"%kind; attachment.bone_name=hand_bone; skeleton.add_child(attachment)
	var grip_anchor := _create_hand_grip_anchor(skeleton, attachment, hand_bone, kind)
	var tool:=packed.instantiate() as Node3D; grip_anchor.add_child(tool); tool.add_to_group("npc_job_tools"); tool.set_meta("job_tool",kind)
	tool.rotation_degrees=TOOL_ROTATION_DEGREES.get(kind,Vector3(0.0,90.0,-90.0)) as Vector3; tool.visible=false
	_normalize_world_model(tool,float(TOOL_WORLD_SIZE.get(kind,0.4)))
	_align_handheld_grip(tool,grip_anchor,kind)
	tool.set_meta("npc_grip_anchor", "right_finger_cradle")
	return tool


func _create_hand_item(person: Node3D, kind: String) -> Node3D:
	var packed:=load(str(HAND_ITEM_PATHS.get(kind,""))) as PackedScene
	var skeleton:=person.find_child("Skeleton3D",true,false) as Skeleton3D
	if packed==null or skeleton==null: return null
	var hand_bone := "Bip01 R Hand" if skeleton.find_bone("Bip01 R Hand") >= 0 else "Wrist.R"
	var attachment:=BoneAttachment3D.new()
	attachment.name="HandledItemAttachment_%s_%d"%[kind,skeleton.get_child_count()]
	attachment.bone_name=hand_bone
	skeleton.add_child(attachment)
	var grip_anchor := _create_hand_grip_anchor(skeleton, attachment, hand_bone, kind)
	var item:=packed.instantiate() as Node3D
	grip_anchor.add_child(item)
	item.name="HandledItem_%s"%kind
	item.add_to_group("npc_handled_items")
	item.set_meta("handled_item_kind",kind)
	item.rotation_degrees=Vector3(0.0,90.0,-90.0)
	item.visible=false
	_normalize_world_model(item,float(HAND_ITEM_WORLD_SIZE.get(kind,0.30)))
	_align_handheld_grip(item,grip_anchor,kind)
	item.set_meta("npc_grip_anchor", "right_finger_cradle")
	return item


func _align_handheld_grip(model: Node3D, grip_anchor: Node3D, kind: String) -> void:
	var bounds := _model_bounds_in_space(model, grip_anchor)
	if bounds.size == Vector3.ZERO:
		return
	var ratio := HANDHELD_GRIP_POINTS.get(kind, Vector3(0.06,0.50,0.50)) as Vector3
	var authored_grip_point := bounds.position + Vector3(bounds.size.x * ratio.x, bounds.size.y * ratio.y, bounds.size.z * ratio.z)
	model.position -= authored_grip_point
	model.set_meta("npc_grip_point_ratio", ratio)
	model.set_meta("npc_grip_mode", "edge_or_handle")


func _create_hand_grip_anchor(skeleton: Skeleton3D, attachment: BoneAttachment3D, hand_bone: String, kind: String) -> Node3D:
	var anchor := Node3D.new()
	anchor.name = "FingerGrip_%s" % kind
	attachment.add_child(anchor)
	var hand_index := skeleton.find_bone(hand_bone)
	if hand_index < 0:
		return anchor
	var hand_pose := skeleton.get_bone_global_pose(hand_index)
	var knuckle_center := Vector3.ZERO
	var roots_found := 0
	for bone_name in RIGHT_GRIP_FINGER_JOINTS:
		var finger_index := skeleton.find_bone(str(bone_name))
		if finger_index >= 0:
			knuckle_center += skeleton.get_bone_global_pose(finger_index).origin
			roots_found += 1
	if roots_found == 0:
		return anchor
	knuckle_center /= float(roots_found)
	# Place the prop's authored edge/handle at the first finger joints.  The
	# object body therefore sits beyond the palm instead of crossing its centre.
	# This attachment-space offset follows authored hand motion and never changes
	# the skeleton pose.
	var grip_in_skeleton := knuckle_center
	anchor.position = hand_pose.affine_inverse() * grip_in_skeleton
	anchor.set_meta("npc_grip_anchor", "right_finger_cradle")
	anchor.set_meta("npc_wrist_to_grip_m", hand_pose.origin.distance_to(grip_in_skeleton))
	return anchor

func _calibrate_hand_tools() -> void:
	for value in get_tree().get_nodes_in_group("npc_job_tools"):
		var tool:=value as Node3D
		if tool==null: continue
		var kind:=str(tool.get_meta("job_tool",""))
		_normalize_world_model(tool,float(TOOL_WORLD_SIZE.get(kind,0.4)))
	for value in get_tree().get_nodes_in_group("npc_handled_items"):
		var item:=value as Node3D
		if item==null: continue
		var kind:=str(item.get_meta("handled_item_kind",""))
		_normalize_world_model(item,float(HAND_ITEM_WORLD_SIZE.get(kind,0.30)))

func _find_foot_bone_indices(skeleton: Skeleton3D) -> PackedInt32Array:
	var indices := PackedInt32Array()
	if skeleton == null:
		return indices
	for bone_name in FOOT_GROUND_BONES:
		var bone_index := skeleton.find_bone(str(bone_name))
		if bone_index >= 0:
			indices.append(bone_index)
	return indices


func _calibrate_foot_reference(person: Node3D, player: AnimationPlayer, skeleton: Skeleton3D, foot_bone_indices: PackedInt32Array) -> float:
	if skeleton == null or player == null or not player.has_animation("rocketbox/Idle_Neutral"):
		return 0.0
	var animation := player.get_animation("rocketbox/Idle_Neutral")
	player.play("rocketbox/Idle_Neutral")
	player.seek(animation.length * 0.5, true)
	player.advance(0.0)
	return _lowest_foot_local_y(person, skeleton, foot_bone_indices)

func _lowest_foot_local_y(person: Node3D, skeleton: Skeleton3D, foot_bone_indices: PackedInt32Array) -> float:
	if skeleton == null:
		return 0.0
	var person_inverse := person.global_transform.affine_inverse()
	var lowest := INF
	for bone_index in foot_bone_indices:
		var world_position := skeleton.global_transform * skeleton.get_bone_global_pose(bone_index).origin
		lowest = minf(lowest, (person_inverse * world_position).y)
	return 0.0 if lowest == INF else lowest

func _apply_pose_grounding(agent: Dictionary, _delta: float) -> void:
	return
	var person := agent.node as Node3D
	var skeleton := agent.skeleton as Skeleton3D
	if not is_instance_valid(person) or not is_instance_valid(skeleton):
		return
	var live_foot_y := _lowest_foot_local_y(person, skeleton, agent.foot_bone_indices as PackedInt32Array)
	var target_offset := clampf(float(agent.foot_reference_y) - live_foot_y, -0.68, 0.18)
	# The animation already blends continuously over 0.16 s. A second delayed
	# root blend makes the feet trail that pose and visibly float during crouch
	# entry/exit. Lock the root to the evaluated foot contact in the same frame;
	# this moves only the character root and cannot stretch or rotate the rig.
	agent.pose_ground_offset = target_offset
	person.global_position.y = float(agent.ground_y) + float(agent.pose_ground_offset)
	person.set_meta("npc_surface_y", float(agent.ground_y))
	person.set_meta("npc_pose_ground_offset", float(agent.pose_ground_offset))

func _normalize_world_model(model:Node3D,target_size:float)->void:
	var bounds:=_model_world_bounds(model)
	if bounds.size==Vector3.ZERO: return
	var largest:=maxf(bounds.size.x,maxf(bounds.size.y,bounds.size.z))
	if largest>0.001: model.scale*=target_size/largest

func _model_world_bounds(model:Node3D)->AABB:
	var bounds:=AABB(); var found:=false
	for value in model.find_children("*","MeshInstance3D",true,false):
		var mesh_node:=value as MeshInstance3D
		if mesh_node.mesh==null: continue
		var world_bounds:=mesh_node.global_transform*mesh_node.mesh.get_aabb()
		bounds=world_bounds if not found else bounds.merge(world_bounds); found=true
	return bounds if found else AABB()


func _model_bounds_in_space(model: Node3D, space: Node3D) -> AABB:
	var bounds := AABB()
	var found := false
	var inverse_space := space.global_transform.affine_inverse()
	for value in model.find_children("*", "MeshInstance3D", true, false):
		var mesh_node := value as MeshInstance3D
		if mesh_node.mesh == null:
			continue
		var local_bounds := (inverse_space * mesh_node.global_transform) * mesh_node.mesh.get_aabb()
		bounds = local_bounds if not found else bounds.merge(local_bounds)
		found = true
	return bounds if found else AABB()

func _set_active_tool(agent:Dictionary,station_id:String,clip:String="")->void:
	for key in (agent.equipment as Dictionary):
		var tool:Node3D=(agent.equipment as Dictionary)[key]
		if not is_instance_valid(tool): continue
		if tool.has_meta("npc_carry"): continue
		var tool_clips := TOOL_ACTION_CLIPS.get(str(tool.get_meta("job_tool","")), []) as Array
		tool.visible=str(key)==station_id and tool_clips.has(clip)


func _set_active_hand_item(agent:Dictionary,station_id:String,clip:String="")->void:
	for key in (agent.handled_items as Dictionary):
		var item:Node3D=(agent.handled_items as Dictionary)[key]
		if not is_instance_valid(item): continue
		if item.has_meta("npc_carry"): continue
		var item_clips := HAND_ITEM_ACTION_CLIPS.get(str(item.get_meta("handled_item_kind","")), []) as Array
		item.visible=str(key)==station_id and item_clips.has(clip)

func _set_rig_target(rig:Dictionary,position:Vector3)->void:
	if rig.is_empty(): return
	var target:=rig.get("target") as Node3D
	if is_instance_valid(target): target.global_position=position

func _set_contact_goal(agent:Dictionary,goal:float,kind:String)->void:
	agent.contact_goal=clampf(goal,0.0,1.0); agent.contact_kind=kind

func _configure_job_contact(agent:Dictionary,clip:String)->void:
	if not CONTACT_CLIPS.has(clip): _set_contact_goal(agent,0.0,"authored_free_motion"); return
	var station:=_stations.get(str(agent.station_id),{}) as Dictionary
	var prop:=station.get("prop_node") as Node3D
	if not is_instance_valid(prop): _set_contact_goal(agent,0.0,"no_contact_surface"); return
	var bounds:=_model_world_bounds(prop)
	if bounds.size==Vector3.ZERO: _set_contact_goal(agent,0.0,"no_contact_surface"); return
	var actor:=agent.node as Node3D; var center:=bounds.get_center()
	var toward_actor:=actor.global_position-center; toward_actor.y=0.0
	if toward_actor.length_squared()<0.001: toward_actor=-actor.global_transform.basis.z
	toward_actor=toward_actor.normalized()
	var front_extent:=absf(toward_actor.x)*bounds.size.x*0.5+absf(toward_actor.z)*bounds.size.z*0.5
	var height_ratio:=float(PROP_CONTACT_HEIGHT.get(str(station.prop),0.65))
	var contact:=center+toward_actor*front_extent; contact.y=bounds.position.y+bounds.size.y*height_ratio
	var actor_right:=actor.global_transform.basis.x.normalized(); var two_hands:=TWO_HAND_CONTACT.has(str(station.prop)) and clip in ["Chest_Open","PickUp_Table","Farm_Watering","Interact"]
	_set_rig_target(agent.right_hand_rig as Dictionary,contact+actor_right*(0.18 if two_hands else 0.0))
	_set_rig_target(agent.left_hand_rig as Dictionary,contact-actor_right*0.18)
	_set_contact_goal(agent,0.60 if two_hands else 0.54,"two_hand_%s_contact"%str(station.prop) if two_hands else "right_hand_%s_contact"%str(station.prop))

func _configure_pair_contact(agent:Dictionary,partner:Dictionary,clip:String,pair:Dictionary)->void:
	var actor:=agent.node as Node3D; var other:=partner.node as Node3D
	var midpoint:=(actor.global_position+other.global_position)*0.5+Vector3.UP*1.08
	if clip in ["OfferParcel","ReceiveParcel","PresentGoods","InspectGoods"]:
		_set_rig_target(agent.right_hand_rig as Dictionary,midpoint)
		_set_contact_goal(agent,0.52,"shared_handoff_contact"); return
	if clip in ["PointToMeter","ReviewServiceNote","ShowCleanedArea","ReviewCleanedArea"]:
		var prop:=pair.get("prop") as Node3D
		if is_instance_valid(prop):
			var bounds:=_model_world_bounds(prop); var contact:=bounds.get_center(); contact.y=bounds.position.y+bounds.size.y*0.68
			_set_rig_target(agent.right_hand_rig as Dictionary,contact); _set_contact_goal(agent,0.42,"shared_prop_reference"); return
	if clip in ["ExplainRoute","FollowRouteExplanation"]:
		var road_direction:=actor.global_transform.basis.x.normalized()
		_set_rig_target(agent.right_hand_rig as Dictionary,actor.global_position+Vector3.UP*1.55+road_direction*0.85)
		_set_contact_goal(agent,0.36,"route_direction_reference"); return
	_set_contact_goal(agent,0.0,"authored_conversation_motion")

func _update_contact_rig(agent:Dictionary,delta:float)->void:
	# Contact metadata drives intent/UI only. The exact authored source clip owns
	# every joint; no post-animation skeletal correction is allowed here.
	agent.contact_weight=0.0

func _setup_pair_interactions(assignments: Array) -> void:
	for assignment_value in assignments:
		var interaction_assignment := assignment_value as Dictionary
		var definition_index := int(interaction_assignment.get("definition_index", -1))
		if definition_index < 0 or definition_index >= PAIR_INTERACTIONS.size():
			continue
		var definition:=PAIR_INTERACTIONS[definition_index] as Dictionary
		var a_index:=int(interaction_assignment.a_index); var b_index:=int(interaction_assignment.b_index)
		if a_index>=_agents.size() or b_index>=_agents.size(): continue
		var tangent:=_pair_tangent(int(definition.edge))
		var center:=_pair_site_position(str(definition.block),int(definition.edge))+tangent*float(definition.get("along",0.0)); var spacing:=0.81 if _passes_object(definition) else 0.90
		var prop:=_create_interaction_prop(definition,center)
		if is_instance_valid(prop):
			center += prop.get_meta("interaction_site_offset", Vector3.ZERO) as Vector3
		var a:=_agents[a_index]; var b:=_agents[b_index]
		var a_person:=a.node as Node3D; var b_person:=b.node as Node3D
		a_person.global_position=Vector3(center.x-tangent.x*spacing,float(a.ground_y),center.z-tangent.z*spacing)
		b_person.global_position=Vector3(center.x+tangent.x*spacing,float(b.ground_y),center.z+tangent.z*spacing)
		_face_target(a_person,b_person.global_position); _face_target(b_person,a_person.global_position)
		var pair_id:=str(definition.id); var title:=str(definition.title)
		var social_event_id := str(interaction_assignment.get("event_id", ""))
		var a_society := _society.call("get_citizen_public_state", str(a.citizen_id)) as Dictionary
		var b_society := _society.call("get_citizen_public_state", str(b.citizen_id)) as Dictionary
		a.station_id = str((a.stations as Array)[int(a.stop_index)])
		b.station_id = str((b.stations as Array)[int(b.stop_index)])
		a.pair_id=pair_id; a.social_event_id=social_event_id; a.pair_partner=str(b.name); a.pair_role=str(definition.a_role); a.task=pair_id; a.action=str(definition.a_role); a.action_target=str(b.name); a.motion_state="interacting_%s"%pair_id; a.action_until=0.0; a.last_speed=0.0; a.plan_source="society_kernel:causal_shared_event"; a.decision_reason=str(a_society.get("decision_reason","a shared obligation scheduled both participants"))
		b.pair_id=pair_id; b.social_event_id=social_event_id; b.pair_partner=str(a.name); b.pair_role=str(definition.b_role); b.task=pair_id; b.action=str(definition.b_role); b.action_target=str(a.name); b.motion_state="interacting_%s"%pair_id; b.action_until=0.0; b.last_speed=0.0; b.plan_source="society_kernel:causal_shared_event"; b.decision_reason=str(b_society.get("decision_reason","a shared obligation scheduled both participants"))
		var shared_payload:={"social_event_id":social_event_id,"interaction_id":pair_id,"activity":title,"cause":str(a.decision_reason),"participants":[str(a.name),str(b.name)],"participant_ids":[str(a.citizen_id),str(b.citizen_id)],"households":[str(a.household_id),str(b.household_id)],"handled_item":str(definition.get("hand_item", "")),"reserved_slots":2,"route":"reserved_two_lane_sidewalk_graph","constraints":["reciprocal_facing","distinct_physical_slots","shared_world_prop","one_speaker_one_listener","single_item_custody","finite_exchange","complementary_native_animations"]}
		a.planner_payload=shared_payload.duplicate(true); b.planner_payload=shared_payload.duplicate(true)
		a.planner_response={"decision":a.action,"source":a.plan_source,"partner":b.name}; b.planner_response={"decision":b.action,"source":b.plan_source,"partner":a.name}
		_set_active_tool(a,""); _set_active_tool(b,"")
		_set_active_hand_item(a,""); _set_active_hand_item(b,"")
		_apply_pair_meta(a,b,title); _apply_pair_meta(b,a,title)
		_agents[a_index]=a; _agents[b_index]=b
		var pair_item_kind := str(definition.get("hand_item", ""))
		# One real object per exchange: it rests on the shared prop and passes
		# between the two people's hands (never two copies swapping visibility).
		var a_item := _create_hand_item(a_person, pair_item_kind) if not pair_item_kind.is_empty() else null
		var b_item: Node3D = null
		_pairs.append({"definition":definition,"a":a_index,"b":b_index,"phase":-1,"next_phase_at":0.0,"prop":prop,"a_item":a_item,"b_item":b_item,"social_event_id":social_event_id,"status":"active","ends_at":_elapsed+13.2})
	_update_pair_interactions()

func _pair_site_position(block:String,edge:int)->Vector3:
	var base:=_graph[_node_key(block,edge)] as Vector3
	var inward:=(_block_center(block,0.16)-base).normalized()
	return Vector3(base.x,0.16,base.z)+inward*1.2

func _pair_tangent(edge:int)->Vector3:
	return Vector3.RIGHT if edge==0 or edge==4 else Vector3.FORWARD

func _create_interaction_prop(definition:Dictionary,center:Vector3)->Node3D:
	var prop: Node3D = PropFactory.build(str(definition.prop))
	if prop==null: return null
	prop.name="InteractionProp_%s"%str(definition.id); prop.add_to_group("npc_interaction_props"); prop.set_meta("interaction_id",definition.id); prop.set_meta("activity_prop_kind",definition.prop); add_child(prop)
	var inward:=(_block_center(str(definition.block),0.16)-center).normalized()
	var tangent := _pair_tangent(int(definition.edge))
	var intended_position := center + inward * 1.40
	prop.rotation.y=atan2(-inward.x,-inward.z)
	_normalize_prop(prop,str(definition.prop))
	var resolved := false
	for along_value in [0.0, -3.5, 3.5, -7.0, 7.0, -10.5, 10.5, -14.0, 14.0]:
		prop.global_position = intended_position + tangent * float(along_value)
		_ground_prop(prop, 0.16)
		var bounds := _model_world_bounds(prop)
		if bounds.size == Vector3.ZERO or CitySpatialSafety.overlaps_road(bounds, 0.08) or _bounds_overlap_buildings(bounds, 0.15) or _bounds_overlap_visible_scene_props(bounds, prop, 0.20):
			continue
		var site_offset := tangent * float(along_value)
		prop.set_meta("interaction_site_offset", site_offset)
		prop.set_meta("collision_safe_placement", true)
		prop.set_meta("placement_offset_m", float(along_value))
		resolved = true
		break
	if not resolved:
		push_error("No grounded collision-safe placement found for interaction prop %s" % str(definition.id))
		prop.visible = false
	for ambient in get_tree().get_nodes_in_group("urban_props"):
		if ambient is Node3D and (ambient as Node3D).global_position.distance_to(prop.global_position)<3.5: (ambient as Node3D).visible=false
	# The interaction prop was already resolved against every job prop above.
	# Hiding all stations within an arbitrary 3 m radius left citizens assigned
	# to invisible objects; retain every collision-safe station as readable work.
	return prop

func _bounds_overlap_visible_scene_props(bounds: AABB, ignored: Node3D, margin: float) -> bool:
	for group_name in ["urban_props", "smart_city_devices", "npc_activity_props", "npc_interaction_props"]:
		for value in get_tree().get_nodes_in_group(group_name):
			if not value is Node3D or value == ignored or not (value as Node3D).visible:
				continue
			if CitySpatialSafety.horizontal_intersects(bounds, _model_world_bounds(value as Node3D), margin):
				return true
	return false

func _apply_pair_meta(agent:Dictionary,partner:Dictionary,title:String)->void:
	var person:=agent.node as Node3D
	person.set_meta("npc_name",agent.name); person.set_meta("npc_role",agent.role); person.set_meta("npc_personality",agent.personality)
	person.set_meta("npc_task",agent.task); person.set_meta("npc_action",agent.action); person.set_meta("npc_action_target",partner.name)
	person.set_meta("npc_interaction_id",agent.pair_id); person.set_meta("npc_interaction_partner",partner.name); person.set_meta("npc_interaction_title",title); person.set_meta("npc_intent","coordinate_with_character")

func _face_target(person:Node3D,target:Vector3)->void:
	var direction:=target-person.global_position; direction.y=0.0
	if direction.length_squared()>0.001: person.rotation.y=atan2(direction.x,direction.z)

func _update_pair_interactions()->void:
	for pair_index in _pairs.size():
		var pair:=_pairs[pair_index]
		if str(pair.get("status", "active")) != "active":
			continue
		if _elapsed >= float(pair.get("ends_at", INF)):
			_finish_pair_interaction(pair_index)
			continue
		if _elapsed<float(pair.next_phase_at): continue
		var definition:=pair.definition as Dictionary; var phases:=definition.phases as Array
		pair.phase=(int(pair.phase)+1)%phases.size(); pair.next_phase_at=_elapsed+4.4
		var clips:=phases[int(pair.phase)] as Array
		var turns := definition.get("turns", [0, 1, 0]) as Array
		var speaking_side := int(turns[int(pair.phase) % turns.size()]) if not turns.is_empty() else 0
		var item_owners := definition.get("item_owners", []) as Array
		var item_owner := int(item_owners[int(pair.phase) % item_owners.size()]) if not item_owners.is_empty() else -1
		pair.item_holder = item_owner
		pair.phase_started = _elapsed
		var speaker_index := int(pair.a) if speaking_side == 0 else int(pair.b)
		var holder_index := int(pair.a) if item_owner == 0 else (int(pair.b) if item_owner == 1 else -1)
		var speaker_id := str((_agents[speaker_index] as Dictionary).citizen_id)
		var holder_id := str((_agents[holder_index] as Dictionary).citizen_id) if holder_index >= 0 else ""
		_society.call("update_social_event_phase", str(pair.social_event_id), int(pair.phase), speaker_id, holder_id, str(definition.get("hand_item", "")))
		for side in 2:
			var agent_index:=int(pair.a) if side==0 else int(pair.b); var agent:=_agents[agent_index]
			var player:=agent.animation as AnimationPlayer
			var clip:=str(clips[side]); agent.selected_clip=_play_candidates(player,[clip],true)
			# Conversational gestures need room to read; slightly slower playback keeps
			# both beats visible without changing the synchronized phase duration.
			player.speed_scale=0.82
			agent.action=str(INTERACTION_ACTION_TEXT.get(clip,agent.pair_role))
			agent.pair_phase=int(pair.phase)
			agent.interaction_turn = "speaking" if side == speaking_side else "listening"
			agent.active_pair_item_kind = str(definition.get("hand_item", "")) if side == item_owner else ""
			agent.motion_state="interacting_%s_%s_phase_%d"%[str(definition.id),str(agent.interaction_turn),int(pair.phase)+1]
			agent.last_speed=0.0
			var partner_index:=int(pair.b) if side==0 else int(pair.a); var partner:=_agents[partner_index]
			_face_target(agent.node as Node3D,(partner.node as Node3D).global_position); _configure_pair_contact(agent,partner,clip,pair)
			(agent.node as Node3D).set_meta("npc_interaction_phase",int(pair.phase)+1); (agent.node as Node3D).set_meta("npc_action",agent.action); _agents[agent_index]=agent
		_pairs[pair_index]=pair


func _finish_pair_interaction(pair_index: int) -> void:
	if pair_index < 0 or pair_index >= _pairs.size():
		return
	var pair := _pairs[pair_index]
	if str(pair.get("status", "active")) != "active":
		return
	pair.status = "completed"
	# The object is set down on the shared prop; both are cleared away once
	# the participants have left the spot (never while they stand there).
	pair.item_holder = -1
	pair.cleanup_at = _elapsed + 8.0
	_restore_all_safe_station_props()
	_society.call("complete_social_event", str(pair.get("social_event_id", "")))
	for side in 2:
		var agent_index := int(pair.a) if side == 0 else int(pair.b)
		var agent := _agents[agent_index]
		agent.pair_id = ""
		agent.pair_partner = ""
		agent.pair_role = ""
		agent.pair_phase = -1
		agent.interaction_turn = ""
		agent.active_pair_item_kind = ""
		agent.social_event_id = ""
		agent.motion_state = "shared_event_completed"
		agent.action_until = 0.0
		agent.selected_clip = "Idle_Neutral"
		_set_contact_goal(agent, 0.0, "shared_event_complete")
		_set_active_tool(agent, "")
		_set_active_hand_item(agent, "")
		# The supplied capture showed both completed participants choosing the
		# same centerline entry simultaneously. Give them opposite pedestrian
		# lanes and let the second participant finish a short acknowledgement
		# before departing, removing the symmetric face/sidestep/reverse loop.
		agent.lane_sign = -1.0 if side == 0 else 1.0
		agent.departure_hold_until = _elapsed + (0.0 if side == 0 else 0.85)
		var person := agent.node as Node3D
		person.set_meta("npc_interaction_id", "")
		person.set_meta("npc_interaction_partner", "")
		person.set_meta("npc_interaction_title", "")
		person.set_meta("npc_intent", "continue_daily_agenda")
		_agents[agent_index] = agent
		# Dispatch to the farthest registered work site from the completed event.
		# This is a spatial preference passed into the authoritative scheduler, not
		# an embodiment-side reassignment, and prevents a new task being visibly
		# restaged beside the conversation that just ended.
		var preferred_station_id := ""
		var farthest_distance := -1.0
		for station_id_value in agent.stations as Array:
			var candidate_station_id := str(station_id_value)
			var candidate_station := _stations.get(candidate_station_id, {}) as Dictionary
			if candidate_station.is_empty():
				continue
			var candidate_position := _station_position(candidate_station, float(agent.ground_y))
			var candidate_distance := Vector2(person.global_position.x, person.global_position.z).distance_to(Vector2(candidate_position.x, candidate_position.z))
			if candidate_distance > farthest_distance:
				farthest_distance = candidate_distance
				preferred_station_id = candidate_station_id
		var assignment := _society.call("request_next_assignment", str(agent.citizen_id), str(agent.station_id), preferred_station_id) as Dictionary
		if not assignment.is_empty():
			_apply_society_assignment(agent_index, assignment)
			_assign_stop(agent_index, false)
			if side == 1:
				var departing_agent := _agents[agent_index]
				departing_agent.departure_hold_until = _elapsed + 0.85
				departing_agent.selected_clip = _play_candidates(departing_agent.animation as AnimationPlayer, ["Yes"], false)
				departing_agent.motion_state = "closing_shared_event_before_departure"
				_agents[agent_index] = departing_agent
	_pairs[pair_index] = pair


func _restore_all_safe_station_props() -> void:
	for station_value in _stations.values():
		var station := station_value as Dictionary
		var station_prop := station.get("prop_node") as Node3D
		if not is_instance_valid(station_prop) or not bool(station_prop.get_meta("collision_safe_placement", false)):
			continue
		station_prop.visible = true
		var bounds := _model_world_bounds(station_prop)
		if bounds.size == Vector3.ZERO or _bounds_overlap_visible_scene_props(bounds, station_prop, 0.20):
			station_prop.visible = false


func _apply_society_opening_assignments() -> void:
	for index in _agents.size():
		var agent := _agents[index]
		if _interaction_index_reserved(index):
			continue
		var assignment := _society.call("get_visible_assignment", str(agent.citizen_id)) as Dictionary
		if assignment.is_empty():
			push_error("SocietyKernel supplied no opening assignment for %s" % str(agent.citizen_id))
			continue
		_apply_society_assignment(index, assignment)
		agent = _agents[index]
		_assign_stop(index, bool(assignment.get("start_active", false)))


func _interaction_index_reserved(index: int) -> bool:
	for assignment_value in _interaction_assignments:
		var assignment := assignment_value as Dictionary
		if int(assignment.get("a_index", -1)) == index or int(assignment.get("b_index", -1)) == index:
			return true
	return false


func _refresh_presentation_worker_indices() -> void:
	_presentation_worker_indices.clear()
	for index in _agents.size():
		var society_state := _society.call("get_citizen_public_state", str(_agents[index].citizen_id)) as Dictionary
		if bool(society_state.get("presentation_worker", false)):
			_presentation_worker_indices.append(index)


func _apply_society_assignment(index: int, assignment: Dictionary) -> void:
	if index < 0 or index >= _agents.size():
		return
	var agent := _agents[index]
	var station_id := str(assignment.get("station_id", ""))
	var station_index := (agent.stations as Array).find(station_id)
	if station_index < 0:
		push_error("SocietyKernel assigned %s to an unowned station %s" % [str(agent.citizen_id), station_id])
		return
	agent.stop_index = station_index
	agent.station_id = station_id
	agent.work_order_id = str(assignment.get("work_order_id", ""))
	agent.decision_reason = str(assignment.get("reason", ""))
	agent.society_activity = (assignment.get("activity", {}) as Dictionary).duplicate(true)
	agent.guaranteed_prop_job = bool(assignment.get("presentation_worker", false))
	agent.plan_source = "society_kernel:institution_work_order"
	var person := agent.node as Node3D
	person.set_meta("npc_guaranteed_prop_job", bool(agent.guaranteed_prop_job))
	person.set_meta("citizen_work_order_id", str(agent.work_order_id))
	person.set_meta("citizen_decision_reason", str(agent.decision_reason))
	_agents[index] = agent


func _assign_stop(index: int, start_active := false) -> void:
	var agent := _agents[index]
	(agent.director as RefCounted).call("stop")
	_set_active_tool(agent,""); _set_active_hand_item(agent,""); _set_contact_goal(agent,0.0,"travelling")
	var station_id := str((agent.stations as Array)[int(agent.stop_index)])
	var station := _stations[station_id] as Dictionary
	# A stationary assignment must read as an activity in the scene. Keep only
	# clips that either touch the station prop or visibly use the station's hand
	# tool. Generic talking, pointing and confirmation poses are reserved for
	# finite social events and can no longer strand a lone citizen by scenery.
	var action_clips := _readable_station_clips(station)
	if action_clips.is_empty():
		action_clips = (station.clips as Array).duplicate()
	var ordered_clips := action_clips.duplicate()
	if bool(agent.guaranteed_prop_job) and ordered_clips.size() > 1:
		var clip_offset := posmod(int(agent.index) + absi(hash(str(agent.work_order_id))), ordered_clips.size())
		ordered_clips.clear()
		for clip_index in action_clips.size():
			ordered_clips.append(action_clips[(clip_index + clip_offset) % action_clips.size()])
		# Open on two visibly distinct, planted low-work poses whenever the
		# selected physical stations support them. This is part of the authored
		# presentation schedule, not a teleport or a permanent animation loop;
		# both workers continue through their complete station clip sets.
		var presentation_rank := _presentation_worker_indices.find(index)
		if presentation_rank >= 0 and presentation_rank < 2 and ordered_clips.has("InspectLow"):
			ordered_clips.erase("InspectLow")
			ordered_clips.push_front("InspectLow")
	var clip := _select_supported_clip(agent.animation as AnimationPlayer,ordered_clips)
	agent.station_id=station_id; agent.task=str(station.id).trim_prefix("npc_%02d_"%[int(agent.index)+1]); agent.action_clips=action_clips.duplicate(); agent.action_clip_index=action_clips.find(clip); agent.selected_clip=clip
	agent.action=str((station.words as Dictionary).get(clip,"waiting at the assigned activity point")); agent.action_target=str(station.name)
	var person := agent.node as Node3D
	person.set_meta("npc_name",agent.name); person.set_meta("npc_role",agent.role); person.set_meta("npc_personality",agent.personality); person.set_meta("npc_task",agent.task); person.set_meta("npc_action",agent.action); person.set_meta("npc_action_target",agent.action_target)
	var society_state := _society.call("get_citizen_public_state", str(agent.citizen_id)) as Dictionary
	var workflow_stages: Array[String] = []
	for workflow_clip_value in action_clips:
		workflow_stages.append(str(WORK_STAGE_BY_CLIP.get(str(workflow_clip_value), "physical_task")))
	agent.planner_payload={"citizen_id":agent.citizen_id,"household_id":agent.household_id,"role":agent.role,"personality":agent.personality,"agenda_activity":society_state.get("current_activity",{}),"legal_activity":agent.task,"work_order_id":agent.work_order_id,"work_order":society_state.get("active_work_order",{}),"station":station.id,"world_prop":str(station.prop),"job_tool":str(station.get("tool", "")),"handled_item":str(station.get("item", "")),"workflow_stages":workflow_stages,"decision_reason":agent.decision_reason,"route":"reserved_two_lane_sidewalk_graph","constraints":["daily_agenda","employer_assignment","exclusive_activity_slot","matching_world_prop","visible_item_or_surface_contact","multi_stage_workflow","native_supported_animation"]}
	agent.planner_response={"decision":agent.action,"source":"society_kernel","reason":agent.decision_reason,"animation":clip,"work_order_id":agent.work_order_id}
	if start_active:
		person.global_position=_station_position(station,float(agent.ground_y)); _begin_action(agent,18.0+float(int(agent.index)%9)*4.0)
	else:
		var start_node := _nearest_safe_graph_node(person.global_position)
		agent.route_start = start_node
		var initial_spawn := str(agent.motion_state) == "starting_shift"
		agent.route_entry_pending = not initial_spawn
		# Only the untrusted imported spawn is snapped once. Later job changes use
		# the already-validated station connector and visibly walk back to the graph.
		if initial_spawn:
			person.global_position = _graph[start_node] as Vector3
			person.global_position.y = float(agent.ground_y)
		agent.route=_shortest_path(start_node,str(station.node)); agent.route_index=0; agent.motion_state="travelling_to_%s"%str(agent.task); _play_candidates(agent.animation as AnimationPlayer,["Walking","Walk"],true)
		agent.route_entry_target = _select_route_entry_position(agent) if bool(agent.route_entry_pending) else Vector3.ZERO
		_society.call("record_transport_snapshot", _live_transport_snapshot())
		var expected_minutes := maxf(0.5, _route_length(start_node, agent.route as PackedStringArray, _station_position(station, float(agent.ground_y))) / maxf(walking_speed, 0.1) * 2.0 / 60.0)
		agent.trip_id = str(_society.call("begin_trip", str(agent.citizen_id), start_node, str(station.id), "walk", expected_minutes))
		var trip_state := _society.call("get_citizen_public_state", str(agent.citizen_id)) as Dictionary
		agent.decision_reason = str(trip_state.get("decision_reason", agent.decision_reason))
		(agent.planner_payload as Dictionary)["decision_reason"] = agent.decision_reason
		(agent.planner_payload as Dictionary)["trip_id"] = agent.trip_id
		(agent.planner_response as Dictionary)["reason"] = agent.decision_reason
		person.set_meta("citizen_trip_id", str(agent.trip_id))
		person.set_meta("citizen_decision_reason", str(agent.decision_reason))
	_agents[index]=agent


func _readable_station_clips(station: Dictionary) -> Array:
	var output: Array = []
	var tool_kind := str(station.get("tool", ""))
	var item_kind := str(station.get("item", ""))
	var visible_tool_clips := TOOL_ACTION_CLIPS.get(tool_kind, []) as Array
	var visible_item_clips := HAND_ITEM_ACTION_CLIPS.get(item_kind, []) as Array
	for clip_value in station.get("clips", []) as Array:
		var clip := str(clip_value)
		if PROP_JOB_CLIPS.has(clip) or visible_tool_clips.has(clip) or visible_item_clips.has(clip):
			output.append(clip)
	return output


# --- Needs, breaks and street life ------------------------------------------------------------------
## Citizens are not only workers. Needs fall with simulated time; when one gets pressing between two
## jobs, the citizen walks to a public amenity that satisfies it (fountain, vending machine, coffee
## cart, bench, payphone ...), uses it with the real interaction, and goes back to the shift. People
## who meet on the pavement sometimes stop and talk.

const BREAK_PLACES := {
	"thirst": ["drinking_fountain", "vending_machine", "coffee_cart"],
	"hunger": ["coffee_cart", "picnic_table", "vending_machine"],
	"energy": ["bench", "bench", "coffee_cart"],
	"social": ["payphone", "bench", "coffee_cart"],
	"fun": ["bench", "payphone", "sandwich_board"],
}
const BREAK_STAGE := {"trash": "Interact", "garbage_container": "Interact", "drinking_fountain": "Interact", "vending_machine": "Interact", "coffee_cart": "Consume", "bench": "PickUp_Table", "payphone": "Interact", "picnic_table": "Consume", "sandwich_board": "InspectLabel"}
const BREAK_EFFECT := {
	"trash": {"fun": 0.04}, "garbage_container": {"fun": 0.04},
	"drinking_fountain": {"thirst": 0.55}, "vending_machine": {"thirst": 0.45, "hunger": 0.12, "cost": 1.5},
	"coffee_cart": {"thirst": 0.3, "hunger": 0.15, "energy": 0.28, "cost": 2.2}, "bench": {"energy": 0.45, "fun": 0.12},
	"payphone": {"social": 0.5, "cost": 0.5}, "picnic_table": {"hunger": 0.5, "social": 0.2}, "sandwich_board": {"fun": 0.2},
}
const CHAT_COOLDOWN := 80.0
var _break_reserved: Dictionary = {}
var _break_counter := 0
var _chat_check_at := 1.0

func _maybe_start_break(index: int) -> void:
	var agent := _agents[index]
	if bool(agent.guaranteed_prop_job) or not str(agent.pair_id).is_empty() or bool(agent.get("break_active", false)):
		return
	if _daypart_decision(index):
		return
	if _elapsed < float(agent.get("next_break_at", 0.0)):
		return
	var need := str(_society.call("most_pressing_need", str(agent.citizen_id), 0.5))
	if need.is_empty() or not BREAK_PLACES.has(need):
		return
	_begin_amenity_visit(index, BREAK_PLACES[need] as Array, need)

## Walk to the nearest free amenity of one of `place_kinds` and use it (one visit = one real interaction).
func _begin_amenity_visit(index: int, place_kinds: Array, need: String, max_distance := 160.0, litter := "") -> bool:
	var agent := _agents[index]
	var person := agent.node as Node3D
	var best: Node3D = null
	var best_distance := max_distance
	var last_kind := str(agent.get("last_amenity", ""))
	var ordered := place_kinds.duplicate()
	# Different people (and the same person on different visits) pick different places in a different order.
	var rotation := int(hash("%s:%d" % [str(agent.citizen_id), int(agent.get("visits", 0))])) % maxi(ordered.size(), 1)
	for i in rotation:
		ordered.push_back(ordered.pop_front())
	for kind_candidate in ordered:
		if str(kind_candidate) == last_kind and ordered.size() > 1:
			continue
		var candidates := get_tree().get_nodes_in_group("garbage_bins" if str(kind_candidate) == "garbage_container" else "urban_props")
		for value in candidates:
			if not value is Node3D or (value as Node3D).is_queued_for_deletion() or not (value as Node3D).visible:
				continue
			if str((value as Node3D).get_meta("urban_prop_kind", "")) != str(kind_candidate) or _break_reserved.has((value as Node3D).get_instance_id()):
				continue
			if str(kind_candidate) in ActivityLibrary.BIN_KINDS and not BinWeight.has_room(value as Node, 2.6):
				continue
			var distance := person.global_position.distance_to((value as Node3D).global_position)
			if distance < best_distance:
				var candidate_stand := ActivityLibrary.stand_transform(value as Node3D, str(kind_candidate), ActivityLibrary.scene_class(str(kind_candidate))).position as Vector3
				var access := _graph[_nearest_safe_graph_node(candidate_stand)] as Vector3
				if not _stand_clear(candidate_stand, Vector3(access.x, 0.16, access.z), CitySpatialSafety.world_bounds(value as Node3D)):
					continue
				best_distance = distance
				best = value as Node3D
		if best != null:
			break
	agent.next_break_at = _elapsed + 45.0
	if best == null:
		_agents[index] = agent
		return false
	var kind := str(best.get_meta("urban_prop_kind"))
	var stand := ActivityLibrary.stand_transform(best, kind, ActivityLibrary.scene_class(kind))
	_break_counter += 1
	var station_id := "break_%03d_%s" % [_break_counter, kind]
	var node_key := _nearest_safe_graph_node(stand.position as Vector3)
	_stations[station_id] = {"id": station_id, "name": kind.replace("_", " ").capitalize(), "block": "r1c1", "edge": -1, "node": node_key, "prop": kind, "tool": "", "item": litter if kind in ActivityLibrary.BIN_KINDS else "",
		"clips": [BREAK_STAGE.get(kind, "Interact")], "words": {}, "prop_node": best, "resolved_position": stand.position, "owner": index, "transient": true}
	_break_reserved[best.get_instance_id()] = index
	(agent.director as RefCounted).call("stop")
	agent.break_active = true
	agent.break_need = need if BREAK_PLACES.has(need) else "fun"
	agent.break_place = kind
	agent.last_amenity = kind
	agent.visits = int(agent.get("visits", 0)) + 1
	agent.break_prop_id = best.get_instance_id()
	agent.station_id = station_id
	agent.task = "%s_%s" % [need, kind]
	agent.action_clips = [BREAK_STAGE.get(kind, "Interact")]
	agent.action_clip_index = 0
	agent.selected_clip = str(BREAK_STAGE.get(kind, "Interact"))
	agent.work_order_id = ""
	agent.action = "%s: going to the %s" % [need.replace("_", " "), kind.replace("_", " ")]
	agent.action_target = kind.replace("_", " ")
	agent.decision_reason = "%s (%s)" % [str(agent.get("daypart_reason", "a need became pressing")), kind.replace("_", " ")]
	person.set_meta("npc_task", agent.task); person.set_meta("npc_action", agent.action); person.set_meta("npc_action_target", agent.action_target); person.set_meta("citizen_decision_reason", agent.decision_reason)
	var start_node := _nearest_safe_graph_node(person.global_position)
	agent.route_start = start_node
	agent.route_entry_pending = true
	agent.route = _shortest_path(start_node, node_key)
	agent.route_index = 0
	agent.motion_state = "travelling_to_break"
	_play_candidates(agent.animation as AnimationPlayer, ["Walking", "Walk"], true)
	agent.route_entry_target = _select_route_entry_position(agent)
	_agents[index] = agent
	return true

# --- Daily rhythm: commute, work, lunch, leisure, home, sleep -------------------------------------
## The society kernel owns each citizen's agenda. The visible citizen follows it: off-shift people spend
## the day's free hours at benches, fountains, payphones and the coffee cart, lunch is eaten at an amenity,
## and at night everybody who is not on a guaranteed late shift goes home and is no longer in the street.

const HOME_PURPOSES := ["sleep", "household_morning", "home_evening", "home_midday", "home_and_leisure", "education", "school_trip"]
const LEISURE_PURPOSES := ["errand_or_social", "services_and_errands", "social_or_leisure", "commute_home"]
const LEISURE_PLACES := ["bench", "payphone", "vending_machine", "drinking_fountain", "coffee_cart", "picnic_table", "bench", "sandwich_board", "trash", "garbage_container"]
const LUNCH_PLACES := ["coffee_cart", "picnic_table", "vending_machine", "bench"]
var _daypart_check_at := 0.0

func _current_purpose(agent: Dictionary) -> String:
	var state := _society.call("get_citizen_public_state", str(agent.citizen_id)) as Dictionary
	return str((state.get("current_activity", {}) as Dictionary).get("purpose", ""))

## Returns true when the citizen was sent somewhere because of the time of day.
func _daypart_decision(index: int) -> bool:
	var agent := _agents[index]
	if bool(agent.guaranteed_prop_job):
		return false
	var purpose := _current_purpose(agent)
	agent.daypart_reason = "the agenda says: %s" % purpose.replace("_", " ")
	_agents[index] = agent
	if purpose in HOME_PURPOSES:
		return _go_home(index)
	if purpose in LEISURE_PURPOSES:
		return _begin_amenity_visit(index, LEISURE_PLACES, "leisure")
	if purpose == "meal_break":
		return _begin_amenity_visit(index, LUNCH_PLACES, "lunch")
	return false

func _go_home(index: int) -> bool:
	var agent := _agents[index]
	var person := agent.node as Node3D
	var citizen := _society.call("get_citizen_public_state", str(agent.citizen_id)) as Dictionary
	var home_block := str(citizen.get("home_block", "r1c1"))
	if home_block.is_empty() or not home_block.begins_with("r"):
		home_block = "r1c1"
	var door_key := _node_key(home_block, 2 * (int(hash(str(agent.citizen_id))) % 4))
	if not _graph.has(door_key):
		return false
	_break_counter += 1
	var station_id := "home_%03d" % _break_counter
	_stations[station_id] = {"id": station_id, "name": "Home", "block": home_block, "edge": 0, "node": door_key, "prop": "", "tool": "", "item": "", "clips": ["Idle_Neutral"], "words": {}, "prop_node": null,
		"resolved_position": (_graph[door_key] as Vector3), "owner": index, "transient": true, "home": true}
	(agent.director as RefCounted).call("stop")
	agent.break_active = true
	agent.break_need = "rest"
	agent.break_place = "home"
	agent.station_id = station_id
	agent.task = "going_home"
	agent.action_clips = ["Idle_Neutral"]
	agent.selected_clip = "Idle_Neutral"
	agent.work_order_id = ""
	agent.action = "heading home for the %s" % ("night" if _society.get("simulation_minute") != null and (int(fposmod(float(_society.get("simulation_minute")), 1440.0)) > 1200 or int(fposmod(float(_society.get("simulation_minute")), 1440.0)) < 420) else "day")
	agent.action_target = "home in %s" % home_block
	agent.decision_reason = str(agent.get("daypart_reason", "end of the day"))
	person.set_meta("npc_task", agent.task); person.set_meta("npc_action", agent.action); person.set_meta("npc_action_target", agent.action_target); person.set_meta("citizen_decision_reason", agent.decision_reason)
	var start_node := _nearest_safe_graph_node(person.global_position)
	agent.route_start = start_node
	agent.route_entry_pending = true
	agent.route = _shortest_path(start_node, door_key)
	agent.route_index = 0
	agent.motion_state = "travelling_to_home"
	_play_candidates(agent.animation as AnimationPlayer, ["Walking", "Walk"], true)
	agent.route_entry_target = _select_route_entry_position(agent)
	_agents[index] = agent
	return true

func _enter_home(index: int) -> void:
	var agent := _agents[index]
	var person := agent.node as Node3D
	var station := _stations.get(str(agent.station_id), {}) as Dictionary
	agent.at_home = true
	agent.home_position = person.global_position
	agent.break_active = false
	agent.action_until = 0.0
	agent.motion_state = "at_home"
	agent.last_speed = 0.0
	person.visible = false
	person.global_position = Vector3(agent.home_position.x, -60.0, agent.home_position.z)
	person.set_meta("npc_action", "at home")
	_stations.erase(str(agent.station_id))
	agent.station_id = ""
	_agents[index] = agent

func _leave_home(index: int) -> void:
	var agent := _agents[index]
	var person := agent.node as Node3D
	agent.at_home = false
	person.visible = true
	person.global_position = Vector3(agent.home_position.x, float(agent.ground_y), agent.home_position.z)
	_agents[index] = agent
	_assign_stop(index, false)
	_maybe_start_break(index)

func _tick_home(index: int, delta: float) -> bool:
	var agent := _agents[index]
	if not bool(agent.get("at_home", false)):
		return false
	_daypart_check_at -= delta
	if int(Engine.get_physics_frames()) % 120 == index % 120:
		var purpose := _current_purpose(agent)
		if not purpose in HOME_PURPOSES:
			_leave_home(index)
	return true

func _finish_break(index: int) -> void:
	var agent := _agents[index]
	var effect := BREAK_EFFECT.get(str(agent.break_place), {}) as Dictionary
	for need_name in effect:
		if need_name == "cost":
			continue
		_society.call("record_need_satisfied", str(agent.citizen_id), str(need_name), float(effect[need_name]), str(agent.break_place), float(effect.get("cost", 0.0)) if need_name == str(agent.break_need) else 0.0)
	_break_reserved.erase(agent.get("break_prop_id", 0))
	_stations.erase(str(agent.station_id))
	agent.break_active = false
	agent.station_id = ""
	agent.next_break_at = _elapsed + 70.0
	_agents[index] = agent
	if str(agent.break_place) == "home":
		_enter_home(index)
		return
	# What was bought is thrown away in the nearest bin that has room.
	var leftover: String = {"vending_machine": "can", "coffee_cart": "cup"}.get(str(agent.break_place), "")
	if not leftover.is_empty() and _begin_amenity_visit(index, ["trash", "garbage_container"], "tidy", 70.0, leftover):
		return
	_assign_stop(index, false)
	_maybe_start_break(index)

func _update_street_chats(delta: float) -> void:
	_chat_check_at -= delta
	if _chat_check_at > 0.0:
		return
	_chat_check_at = 0.6
	for index in _agents.size():
		var a := _agents[index]
		if not _chat_eligible(a):
			continue
		for other_index in range(index + 1, _agents.size()):
			var b := _agents[other_index]
			if not _chat_eligible(b):
				continue
			var pa := (a.node as Node3D).global_position
			var pb := (b.node as Node3D).global_position
			if Vector2(pa.x, pa.z).distance_to(Vector2(pb.x, pb.z)) > 2.6:
				continue
			var affinity := float((_society.call("get_citizen_public_state", str(a.citizen_id)) as Dictionary).get("traits", {}).get("extraversion", 0.5)) + float((_society.call("get_citizen_public_state", str(b.citizen_id)) as Dictionary).get("traits", {}).get("extraversion", 0.5))
			if affinity < 0.7:
				continue
			_start_street_chat(index, other_index)
			break

func _chat_eligible(agent: Dictionary) -> bool:
	return not bool(agent.guaranteed_prop_job) and str(agent.pair_id).is_empty() and not bool(agent.get("break_active", false)) \
		and str(agent.motion_state).begins_with("travelling") and float(agent.get("chat_until", 0.0)) <= _elapsed and _elapsed >= float(agent.get("next_chat_at", 0.0)) and float(agent.action_until) <= 0.0

func _start_street_chat(a_index: int, b_index: int) -> void:
	var a := _agents[a_index]
	var b := _agents[b_index]
	var length := 7.0 + float((a_index * 7 + b_index * 3) % 6)
	for pair in [[a, b, a_index], [b, a, b_index]]:
		var me := pair[0] as Dictionary
		var other := pair[1] as Dictionary
		var person := me.node as Node3D
		var toward := (other.node as Node3D).global_position - person.global_position
		toward.y = 0.0
		me.face_yaw = atan2(toward.x, toward.z)
		me.chat_until = _elapsed + length
		me.chat_partner = int(pair[2]) ^ 0
		me.chat_started = _elapsed
		me.next_chat_at = _elapsed + CHAT_COOLDOWN
		me.last_speed = 0.0
		_agents[int(pair[2])] = me
	_society.call("record_street_chat", str(a.citizen_id), str(b.citizen_id), "neighbourhood news")
	(a.node as Node3D).set_meta("npc_action", "stopping to talk with %s" % str(b.name))
	(b.node as Node3D).set_meta("npc_action", "stopping to talk with %s" % str(a.name))

## Returns true while the agent is standing in conversation (so navigation stays paused).
func _run_chat(index: int, agent: Dictionary, delta: float) -> bool:
	if float(agent.get("chat_until", 0.0)) <= _elapsed:
		if float(agent.get("chat_started", 0.0)) > 0.0:
			agent.chat_started = 0.0
			_play_candidates(agent.animation as AnimationPlayer, ["Walking", "Walk"], true)
			_agents[index] = agent
		return false
	var person := agent.node as Node3D
	var phase := int((_elapsed - float(agent.chat_started)) / 2.4)
	var speaking := (phase + index) % 2 == 0
	_play_candidates(agent.animation as AnimationPlayer, ["Idle_Talking"] if speaking else ["Idle_Neutral"], true)
	agent.last_speed = 0.0
	agent.motion_state = "travelling_chatting"
	_agents[index] = agent
	return true

func _update_agent(index: int, delta: float) -> void:
	var agent := _agents[index]; var person := agent.node as Node3D
	if not is_instance_valid(person): return
	if _tick_home(index, delta):
		return
	person.global_position.y=float(agent.ground_y); _update_contact_rig(agent,delta)
	_turn_toward_heading(agent, person, delta)
	if _run_chat(index, agent, delta):
		return
	agent = _agents[index]
	if agent.has("pending_stage") and not agent.has("face_yaw"):
		var pending_clip := str(agent.pending_stage)
		agent.erase("pending_stage")
		_start_stage(agent, pending_clip, true)
		_agents[index] = agent
	if not str(agent.pair_id).is_empty():
		agent.last_speed=0.0; _agents[index]=agent; return
	if float(agent.departure_hold_until) > _elapsed:
		agent.last_speed = 0.0
		agent.motion_state = "closing_shared_event_before_departure"
		_agents[index] = agent
		return
	if float(agent.departure_hold_until) > 0.0:
		agent.departure_hold_until = 0.0
		_play_candidates(agent.animation as AnimationPlayer,["Walking","Walk"],true)
	if float(agent.action_until)>_elapsed:
		_update_action_animation(agent); agent.last_speed=0.0; _agents[index]=agent; return
	if float(agent.action_until)>0.0 and bool(agent.get("break_active", false)):
		(agent.director as RefCounted).call("stop")
		agent.action_until=0.0
		_agents[index]=agent
		_finish_break(index)
		return
	if float(agent.action_until)>0.0:
		(agent.director as RefCounted).call("stop")
		agent.action_until=0.0; agent.completed_actions=int(agent.completed_actions)+1
		_complete_work_scene_state(agent)
		agent.preferred_stop=-1
		_agents[index]=agent
		var next_assignment := _society.call("complete_visible_work", str(agent.citizen_id), str(agent.work_order_id), str(agent.selected_clip)) as Dictionary
		if not next_assignment.is_empty():
			_apply_society_assignment(index, next_assignment)
			_assign_stop(index, bool(next_assignment.get("start_active", false)))
		else:
			agent = _agents[index]
			agent.stop_index = (int(agent.stop_index) + 1) % 2
			_agents[index] = agent
			_assign_stop(index, false)
		_maybe_start_break(index)
		return
	if bool(agent.route_entry_pending):
		var entry := _route_entry_position(agent)
		var entry_delta := entry - person.global_position
		entry_delta.y = 0.0
		if entry_delta.length() <= 0.58 or _walk_toward(agent, entry, delta):
			agent.route_entry_pending = false
			agent.route_entry_target = Vector3.ZERO
		elif float(agent.stuck_seconds) > 0.55:
			# A work station or another departure may occupy a previously valid
			# connector target. Choose another legal forward slot instead of
			# waiting indefinitely for a stationary worker to move.
			agent.route_entry_target = _select_route_entry_position(agent, entry)
			agent.stuck_seconds = 0.0
		_agents[index] = agent
		return
	var route := agent.route as PackedStringArray
	if int(agent.route_index)<route.size():
		var route_index := int(agent.route_index)
		var segment_start_key := str(agent.route_start) if route_index == 0 else str(route[route_index - 1])
		var segment_start := _graph[segment_start_key] as Vector3
		var segment_end := _graph[route[route_index]] as Vector3
		if _walk_toward(agent,_lane_target(segment_start,segment_end,float(agent.lane_sign)),delta):
			agent.route_index=route_index+1
			if not str(agent.trip_id).is_empty():
				_society.call("update_trip_progress", str(agent.citizen_id), str(agent.trip_id), float(agent.route_index) / float(maxi(1, route.size() + 1)))
		_agents[index]=agent; return
	var station := _stations[str(agent.station_id)] as Dictionary
	if _walk_toward(agent,_station_position(station,float(agent.ground_y)),delta,true):
		_complete_agent_trip(agent)
		_begin_action(agent,action_seconds+float(int(agent.index)%5))
	_agents[index]=agent


func _turn_toward_heading(agent: Dictionary, person: Node3D, delta: float) -> void:
	## Stationary turns (facing a work station) are eased over ~0.5 s rather
	## than snapped in one frame.
	if not agent.has("face_yaw"):
		return
	var heading := float(agent.face_yaw)
	person.rotation.y = lerp_angle(person.rotation.y, heading, minf(delta * 6.0, 1.0))
	if absf(angle_difference(person.rotation.y, heading)) < 0.01:
		person.rotation.y = heading
		agent.erase("face_yaw")


func _walk_toward(agent: Dictionary, target: Vector3, delta: float, exact := false) -> bool:
	## exact: the target is a work spot the body must stand on; route
	## waypoints only need passing near.
	var person := agent.node as Node3D; var offset:=target-person.global_position; offset.y=0.0
	agent.erase("face_yaw")
	if agent.has("escape_to"):
		# Boxed in by a pole or street furniture: walk to the nearest clear spot, then carry on.
		var escape := agent.escape_to as Vector3
		var to_escape := escape - person.global_position
		to_escape.y = 0.0
		if to_escape.length() <= 0.08 or float(agent.get("escape_until", 0.0)) < _elapsed:
			agent.erase("escape_to")
			agent.stuck_seconds = 0.0
		else:
			var heading := to_escape.normalized()
			person.global_position += heading * minf(walking_speed * delta, to_escape.length())
			person.global_position.y = float(agent.ground_y)
			person.rotation.y = lerp_angle(person.rotation.y, atan2(heading.x, heading.z), minf(delta * 7.0, 1.0))
			agent.last_speed = walking_speed
			agent.motion_state = "walking_clear_of_obstacle_%s" % str(agent.task)
			_play_candidates(agent.animation as AnimationPlayer, ["Walking", "Walk"], true)
			(agent.animation as AnimationPlayer).speed_scale = WalkStride.playback_scale(walking_speed, float(agent.get("walk_natural", 1.3)))
			return false
	var remaining := offset.length()
	if remaining<=0.24:
		if not exact:
			# Close enough to a waypoint: carry on to the next one from here
			# (no pop onto it, no step back to it).
			agent.last_speed=0.0; agent.stuck_seconds=0.0; return true
		# The last few centimetres onto a work spot are walked as part of the
		# stride instead of popping the body onto it in one frame.
		var glide := minf(walking_speed*delta, remaining)
		if remaining-glide<=0.02:
			person.global_position=Vector3(target.x,float(agent.ground_y),target.z); agent.last_speed=0.0; agent.stuck_seconds=0.0; return true
		person.global_position+=offset/remaining*glide
		agent.last_speed=walking_speed
		return false
	var desired:=offset.normalized()
	var lateral := Vector3(-desired.z, 0.0, desired.x)
	var separation := _separation_force(agent)
	# Separation may change the lane but may never create a backwards desired
	# velocity. This removes the rotate/re-approach cycle visible in the capture.
	var lateral_weight := clampf(separation.dot(lateral) * 1.05, -0.82, 0.82)
	var direction := (desired + lateral * lateral_weight).normalized()
	var step:=minf(walking_speed*delta,offset.length())
	var candidate:=person.global_position+direction*step; candidate.y=float(agent.ground_y)
	if not _position_clear(agent,candidate,personal_space*0.90):
		var block_reason := _position_block_reason(agent, candidate, personal_space * 0.90)
		# Head-on pedestrians need to create lateral clearance before advancing.
		# A mostly-forward diagonal still reduced their distance and made both
		# agents yield forever. Both now try their own right-hand side first, so
		# opposite-direction walkers choose opposite world-space sides.
		var forward_component := 0.10 if block_reason.begins_with("citizen_") else 0.72
		var side_component := 0.995 if block_reason.begins_with("citizen_") else 0.70
		var first_bypass := (desired * forward_component + lateral * side_component).normalized()
		var second_bypass := (desired * forward_component - lateral * side_component).normalized()
		var first_candidate := person.global_position + first_bypass * step
		first_candidate.y = float(agent.ground_y)
		var second_candidate := person.global_position + second_bypass * step
		second_candidate.y = float(agent.ground_y)
		# Once a side is chosen it is kept for a moment: alternating sides tick
		# by tick made people jitter back and forth in front of each other.
		var prefer_second := float(agent.get("bypass_until", 0.0)) > _elapsed and float(agent.get("bypass_side", 1.0)) < 0.0
		var options := [[second_candidate, second_bypass, -1.0], [first_candidate, first_bypass, 1.0]] if prefer_second else [[first_candidate, first_bypass, 1.0], [second_candidate, second_bypass, -1.0]]
		for option in options:
			if _position_clear(agent, option[0] as Vector3, personal_space * 0.86):
				candidate = option[0]
				direction = option[1]
				agent.bypass_side = float(option[2])
				agent.bypass_until = _elapsed + 1.2
				break
	if _position_clear(agent,candidate,personal_space*0.90):
		_set_contact_goal(agent,0.0,"walking")
		var previous_direction := agent.previous_move_direction as Vector3
		if previous_direction.length_squared() > 0.01 and direction.dot(previous_direction) < -0.35:
			agent.avoidance_reversals = int(agent.avoidance_reversals) + 1
		agent.previous_move_direction = direction
		person.global_position=candidate; person.rotation.y=lerp_angle(person.rotation.y,atan2(direction.x,direction.z),minf(delta*7.0,1.0)); agent.stuck_seconds=0.0; agent.last_speed=walking_speed; agent.motion_state="walking_to_%s"%str(agent.task)
		_play_candidates(agent.animation as AnimationPlayer,["Walking","Walk"],true)
		# Cadence matched to ground speed: planted feet do not skate.
		(agent.animation as AnimationPlayer).speed_scale = WalkStride.playback_scale(walking_speed, float(agent.get("walk_natural", 1.3)))
	else:
		if float(agent.stuck_seconds) <= 0.0:
			agent.yield_events = int(agent.yield_events) + 1
		agent.last_block_reason = _position_block_reason(agent, candidate, personal_space * 0.90)
		agent.stuck_seconds=float(agent.stuck_seconds)+delta; agent.last_speed=0.0; agent.motion_state="yielding_for_crossing_citizen"; _play_candidates(agent.animation as AnimationPlayer,["Idle_Neutral","Idle"],true)
		if float(agent.stuck_seconds)>0.55:
			_recover_from_deadlock(agent,desired,step)
	return false

## Nearest clear footpath spot around a boxed-in person, biased toward where they want to go.
func _find_escape_spot(agent: Dictionary, desired: Vector3) -> Vector3:
	var person := agent.node as Node3D
	var here := person.global_position
	for radius in [0.7, 1.1, 1.6, 2.3, 3.2]:
		var best := Vector3.INF
		var best_score := -INF
		for k in 24:
			var angle := TAU * float(k) / 24.0
			var candidate := here + Vector3(sin(angle), 0.0, cos(angle)) * float(radius)
			candidate.y = float(agent.ground_y)
			if not _position_clear(agent, candidate, personal_space * 0.7) or not _within_walk_network(candidate, 1.7):
				continue
			var score := Vector3(sin(angle), 0.0, cos(angle)).dot(desired)
			if score > best_score:
				best_score = score
				best = candidate
		if best != Vector3.INF:
			return best
	return Vector3.INF

func _recover_from_deadlock(agent: Dictionary, desired: Vector3, step: float) -> void:
	var person:=agent.node as Node3D
	if float(agent.stuck_seconds) > 2.5 and not agent.has("escape_to"):
		var spot := _find_escape_spot(agent, desired)
		if spot != Vector3.INF:
			agent.escape_to = spot
			agent.escape_until = _elapsed + 4.0
			return
	var lateral:=Vector3(-desired.z,0.0,desired.x)
	for side_sign in [float(agent.lane_sign), -float(agent.lane_sign)]:
		var bypass_direction: Vector3 = (desired * 0.58 + lateral * float(side_sign) * 0.82).normalized()
		var candidate: Vector3 = person.global_position+bypass_direction*maxf(step,0.04); candidate.y=float(agent.ground_y)
		if _position_clear(agent,candidate,personal_space*0.78):
			person.global_position=candidate
			person.rotation.y=lerp_angle(person.rotation.y,atan2(bypass_direction.x,bypass_direction.z),0.32)
			agent.previous_move_direction = bypass_direction
			agent.last_speed = walking_speed * 0.65
			agent.motion_state = "walking_bypass_to_%s" % str(agent.task)
			agent.stuck_seconds=0.0
			_play_candidates(agent.animation as AnimationPlayer,["Walking","Walk"],true)
			return

func _separation_force(agent: Dictionary) -> Vector3:
	var person:=agent.node as Node3D; var force:=Vector3.ZERO
	for other_index_value in _nearby_agent_indices(person.global_position):
		var other := _agents[int(other_index_value)] as Dictionary
		if other.node==person: continue
		var away:=person.global_position-(other.node as Node3D).global_position; away.y=0.0; var distance:=away.length()
		if distance<personal_space*2.1:
			if distance<0.01: away=Vector3(float(agent.lane_sign),0.0,0.0); distance=0.01
			force+=away.normalized()*(1.0-distance/(personal_space*2.1))
	return force

func _position_clear(agent: Dictionary, candidate: Vector3, clearance: float) -> bool:
	return _position_block_reason(agent, candidate, clearance).is_empty()


func _position_block_reason(agent: Dictionary, candidate: Vector3, clearance: float) -> String:
	# `clearance` is the social envelope for other people, not the character's
	# physical radius. Reusing 1+ m around every wall, tree and prop turned legal
	# sidewalk passages into invisible barriers. Keep a safe human-body margin
	# for geometry and the larger comfort distance only for citizen/citizen flow.
	if not _position_outside_buildings(candidate, 0.52):
		return "building"
	if not _within_walk_network(candidate, 1.70):
		return "outside_walk_network"
	for obstacle_index_value in _obstacle_indices_at(_static_obstacle_grid, candidate):
		var obstacle := _static_obstacles[int(obstacle_index_value)] as Dictionary
		if not is_instance_valid(obstacle.get("node")):
			continue
		if str(obstacle.kind) in ["tree", "pole"]:
			var offset := candidate - (obstacle.position as Vector3)
			offset.y = 0.0
			if offset.length() < 0.45 + float(obstacle.radius):
				var current_offset := (agent.node as Node3D).global_position - (obstacle.position as Vector3)
				current_offset.y = 0.0
				if offset.length() <= current_offset.length() + 0.002:
					return "%s_%s" % [str(obstacle.kind), str((obstacle.node as Node3D).name)]
		elif CitySpatialSafety.point_inside_horizontal(candidate, obstacle.bounds as AABB, float(obstacle.get("margin", 0.42))):
			var obstacle_bounds := obstacle.bounds as AABB
			var here := (agent.node as Node3D).global_position
			if CitySpatialSafety.point_inside_horizontal(here, obstacle_bounds, float(obstacle.get("margin", 0.42))):
				# Already inside the zone (a lamp's arm widens its bounds): any step that leads outward is
				# allowed, so nobody is trapped; only going deeper toward the middle is refused.
				var centre := obstacle_bounds.get_center()
				if Vector2(candidate.x - centre.x, candidate.z - centre.z).length() < Vector2(here.x - centre.x, here.z - centre.z).length() - 0.001:
					return "static_%s" % str((obstacle.node as Node3D).name)
			elif _horizontal_distance_to_bounds(candidate, obstacle_bounds) <= _horizontal_distance_to_bounds(here, obstacle_bounds) + 0.002:
				return "static_%s" % str((obstacle.node as Node3D).name)
	# Vehicles crossing the sidewalk (emergency units leaving/entering their
	# station) and incident responders are moving obstacles. Only a step that
	# would bring the citizen closer is refused, so nobody can deadlock.
	var current_position := (agent.node as Node3D).global_position
	for dynamic_value in get_tree().get_nodes_in_group("npc_dynamic_obstacles") + get_tree().get_nodes_in_group("incident_responders"):
		var dynamic := dynamic_value as Node3D
		if dynamic == null or not dynamic.visible:
			continue
		var reach := float(dynamic.get_meta("npc_dynamic_obstacle_radius", 0.55)) + 0.45
		var dynamic_offset := candidate - dynamic.global_position
		dynamic_offset.y = 0.0
		if dynamic_offset.length() < reach:
			var current_offset := current_position - dynamic.global_position
			current_offset.y = 0.0
			if dynamic_offset.length() <= current_offset.length() + 0.002:
				return "dynamic_%s" % str(dynamic.name)
	var own_prop: Node3D = null
	if _stations.has(str(agent.station_id)):
		own_prop = (_stations[str(agent.station_id)] as Dictionary).get("prop_node") as Node3D
	for obstacle_index_value in _obstacle_indices_at(_activity_prop_obstacle_grid, candidate):
		var obstacle := _activity_prop_obstacles[int(obstacle_index_value)] as Dictionary
		var value := obstacle.node as Node3D
		if not is_instance_valid(value) or value == own_prop or not value.visible:
			continue
		if CitySpatialSafety.point_inside_horizontal(candidate, obstacle.bounds as AABB, 0.34):
			var prop_bounds := obstacle.bounds as AABB
			if _horizontal_distance_to_bounds(candidate, prop_bounds) <= _horizontal_distance_to_bounds((agent.node as Node3D).global_position, prop_bounds) + 0.002:
				return "activity_prop_%s" % str(value.name)
	for other_index_value in _nearby_agent_indices(candidate):
		var other := _agents[int(other_index_value)] as Dictionary
		if other.node == agent.node:
			continue
		var other_position := (other.node as Node3D).global_position
		var candidate_distance := candidate.distance_to(other_position)
		# Agents are advanced sequentially. At high simulation speed the first
		# mover could consume most of the separation before the other agent's
		# turn, even when its own candidate passed the nominal check. Reserve one
		# additional local step so their resulting distance cannot cross the
		# physical 1.35 m floor. The separating exception below still lets an
		# already-close pair escape instead of deadlocking.
		var movement_buffer := candidate.distance_to((agent.node as Node3D).global_position)
		var required_separation := maxf(clearance, MIN_CITIZEN_SEPARATION + movement_buffer)
		if candidate_distance >= required_separation:
			continue
		# If two people are already inside the comfort envelope, blocking every
		# candidate also blocks the step that would separate them. Permit only a
		# strictly separating move until normal clearance is restored.
		var current_distance := (agent.node as Node3D).global_position.distance_to(other_position)
		if candidate_distance <= current_distance + 0.002:
			return "citizen_%s" % str(other.citizen_id)
	return ""


func _horizontal_distance_to_bounds(point: Vector3, bounds: AABB) -> float:
	var delta_x := maxf(maxf(bounds.position.x - point.x, point.x - bounds.end.x), 0.0)
	var delta_z := maxf(maxf(bounds.position.z - point.z, point.z - bounds.end.z), 0.0)
	return Vector2(delta_x, delta_z).length()

func _rebuild_spatial_constraints() -> void:
	_static_obstacles.clear()
	_activity_prop_obstacles.clear()
	_walk_segments.clear()
	_walk_segment_grid.clear()
	_building_obstacle_grid.clear()
	_static_obstacle_grid.clear()
	_activity_prop_obstacle_grid.clear()
	var linked := {}
	for key_value in _links:
		var key := str(key_value)
		for other_value in _links[key] as PackedStringArray:
			var other := str(other_value)
			var edge_id := key + "|" + other if key < other else other + "|" + key
			if linked.has(edge_id):
				continue
			linked[edge_id] = true
			_walk_segments.append([_graph[key] as Vector3, _graph[other] as Vector3])
	for station_value in _stations.values():
		var station := station_value as Dictionary
		_walk_segments.append([_graph[str(station.node)] as Vector3, _station_position(station, 0.16)])
	_rebuild_walk_segment_grid()
	var seen := {}
	for group_name in ["city_trees", "urban_props", "smart_city_devices", "power_poles", "traffic_signals", "water_valves", "power_switches"]:
		for value in get_tree().get_nodes_in_group(group_name):
			if not value is Node3D:
				continue
			var node := value as Node3D
			if node.is_queued_for_deletion() or not node.visible or node.is_in_group("npc_activity_props") or node.is_in_group("npc_interaction_props") or seen.has(node.get_instance_id()):
				continue
			seen[node.get_instance_id()] = true
			if node.is_in_group("city_trees"):
				_static_obstacles.append({"node":node,"kind":"tree","position":node.global_position,"radius":0.72})
			elif node.is_in_group("power_poles"):
				# The pole model's world AABB includes its elevated crossarm. Treating
				# that full box as a ground obstacle blocked several metres of clear
				# sidewalk and trapped paired citizens after social events ended.
				_static_obstacles.append({"node":node,"kind":"pole","position":node.global_position,"radius":0.30})
			elif node.is_in_group("traffic_signals"):
				# Signal heads and arms overhang the road above head height; pedestrian
				# avoidance only needs the support-post footprint at ground level.
				_static_obstacles.append({"node":node,"kind":"pole","position":node.global_position,"radius":0.28})
			elif str(node.name).begins_with("SmartStreetLight") or str(node.name).begins_with("StreetLight"):
				# Lamp arms and heads hang over the footpath; only the mast base is in the way.
				_static_obstacles.append({"node":node,"kind":"pole","position":node.global_position,"radius":0.30})
			else:
				var bounds := _model_world_bounds(node)
				if bounds.size != Vector3.ZERO:
					# Street furniture is solid but small: a person only needs to clear its footprint by a
					# stride's width, not the wider zone used for big devices.
					_static_obstacles.append({"node":node,"kind":"bounds","bounds":bounds,"margin":0.20 if node.is_in_group("urban_props") else 0.42})
	# Job props are static after placement. Cache their rendered bounds once;
	# recomputing full descendant mesh AABBs for every candidate step caused the
	# large FPS collapse when the twelve social-event participants started walking.
	for value in get_tree().get_nodes_in_group("npc_activity_props"):
		if not value is Node3D:
			continue
		var node := value as Node3D
		var bounds := _model_world_bounds(node)
		if bounds.size != Vector3.ZERO:
			_activity_prop_obstacles.append({"node":node,"bounds":bounds})
	_rebuild_obstacle_grids()


func _spatial_grid_key(cell_x: int, cell_z: int) -> String:
	return "%d:%d" % [cell_x, cell_z]


func _index_bounds_in_grid(grid: Dictionary, obstacle_index: int, bounds: AABB, margin: float) -> void:
	var minimum_x := floori((bounds.position.x - margin) / OBSTACLE_SPATIAL_CELL_SIZE)
	var maximum_x := floori((bounds.end.x + margin) / OBSTACLE_SPATIAL_CELL_SIZE)
	var minimum_z := floori((bounds.position.z - margin) / OBSTACLE_SPATIAL_CELL_SIZE)
	var maximum_z := floori((bounds.end.z + margin) / OBSTACLE_SPATIAL_CELL_SIZE)
	for cell_x in range(minimum_x, maximum_x + 1):
		for cell_z in range(minimum_z, maximum_z + 1):
			var key := _spatial_grid_key(cell_x, cell_z)
			var indices := grid.get(key, []) as Array
			indices.append(obstacle_index)
			grid[key] = indices


func _rebuild_obstacle_grids() -> void:
	for building_index in _building_bounds.size():
		_index_bounds_in_grid(_building_obstacle_grid, building_index, _building_bounds[building_index], 0.70)
	for obstacle_index in _static_obstacles.size():
		var obstacle := _static_obstacles[obstacle_index] as Dictionary
		if str(obstacle.kind) in ["tree", "pole"]:
			var radius := float(obstacle.radius) + 0.55
			var position := obstacle.position as Vector3
			_index_bounds_in_grid(_static_obstacle_grid, obstacle_index, AABB(position - Vector3(radius, 0.0, radius), Vector3(radius * 2.0, 0.1, radius * 2.0)), 0.0)
		else:
			_index_bounds_in_grid(_static_obstacle_grid, obstacle_index, obstacle.bounds as AABB, 0.55)
	for obstacle_index in _activity_prop_obstacles.size():
		_index_bounds_in_grid(_activity_prop_obstacle_grid, obstacle_index, (_activity_prop_obstacles[obstacle_index] as Dictionary).bounds as AABB, 0.50)


func _obstacle_indices_at(grid: Dictionary, point: Vector3) -> Array:
	if grid.is_empty():
		return []
	return grid.get(_spatial_grid_key(floori(point.x / OBSTACLE_SPATIAL_CELL_SIZE), floori(point.z / OBSTACLE_SPATIAL_CELL_SIZE)), []) as Array


func _rebuild_agent_spatial_grid() -> void:
	_agent_spatial_grid.clear()
	for agent_index in _agents.size():
		var position := (_agents[agent_index].node as Node3D).global_position
		var key := _spatial_grid_key(floori(position.x / AGENT_SPATIAL_CELL_SIZE), floori(position.z / AGENT_SPATIAL_CELL_SIZE))
		var indices := _agent_spatial_grid.get(key, []) as Array
		indices.append(agent_index)
		_agent_spatial_grid[key] = indices


func _nearby_agent_indices(point: Vector3) -> Array:
	if _agent_spatial_grid.is_empty():
		var all_indices: Array = []
		for agent_index in _agents.size(): all_indices.append(agent_index)
		return all_indices
	var output: Array = []
	var center_x := floori(point.x / AGENT_SPATIAL_CELL_SIZE)
	var center_z := floori(point.z / AGENT_SPATIAL_CELL_SIZE)
	for cell_x in range(center_x - 1, center_x + 2):
		for cell_z in range(center_z - 1, center_z + 2):
			output.append_array(_agent_spatial_grid.get(_spatial_grid_key(cell_x, cell_z), []) as Array)
	return output


func _walk_grid_key(cell_x: int, cell_z: int) -> String:
	return "%d:%d" % [cell_x, cell_z]


func _rebuild_walk_segment_grid() -> void:
	for segment_index in _walk_segments.size():
		var segment := _walk_segments[segment_index] as Array
		var start := segment[0] as Vector3
		var finish := segment[1] as Vector3
		var minimum_x := floori((minf(start.x, finish.x) - 2.0) / WALK_SPATIAL_CELL_SIZE)
		var maximum_x := floori((maxf(start.x, finish.x) + 2.0) / WALK_SPATIAL_CELL_SIZE)
		var minimum_z := floori((minf(start.z, finish.z) - 2.0) / WALK_SPATIAL_CELL_SIZE)
		var maximum_z := floori((maxf(start.z, finish.z) + 2.0) / WALK_SPATIAL_CELL_SIZE)
		for cell_x in range(minimum_x, maximum_x + 1):
			for cell_z in range(minimum_z, maximum_z + 1):
				var key := _walk_grid_key(cell_x, cell_z)
				var indices := _walk_segment_grid.get(key, []) as Array
				indices.append(segment_index)
				_walk_segment_grid[key] = indices

func _within_walk_network(point: Vector3, clearance: float) -> bool:
	if _walk_segments.is_empty():
		return true
	var key := _walk_grid_key(floori(point.x / WALK_SPATIAL_CELL_SIZE), floori(point.z / WALK_SPATIAL_CELL_SIZE))
	for segment_index_value in _walk_segment_grid.get(key, []) as Array:
		var segment := _walk_segments[int(segment_index_value)] as Array
		if _point_segment_distance_xz(point, segment[0] as Vector3, segment[1] as Vector3) <= clearance:
			return true
	return false

func _point_segment_distance_xz(point: Vector3, start: Vector3, finish: Vector3) -> float:
	var flat_point := Vector2(point.x, point.z)
	var flat_start := Vector2(start.x, start.z)
	var flat_finish := Vector2(finish.x, finish.z)
	var segment := flat_finish - flat_start
	if segment.length_squared() < 0.0001:
		return flat_point.distance_to(flat_start)
	var fraction := clampf((flat_point - flat_start).dot(segment) / segment.length_squared(), 0.0, 1.0)
	return flat_point.distance_to(flat_start + segment * fraction)

func _is_prop_job_active(agent: Dictionary) -> bool:
	if not str(agent.pair_id).is_empty() or not str(agent.motion_state).begins_with("performing_") or not PROP_JOB_CLIPS.has(str(agent.selected_clip)):
		return false
	var station := _stations.get(str(agent.station_id), {}) as Dictionary
	var prop := station.get("prop_node") as Node3D
	if not is_instance_valid(prop) or not prop.visible or not bool(prop.get_meta("collision_safe_placement", false)):
		return false
	var person := agent.node as Node3D
	var station_position := _station_position(station, float(agent.ground_y))
	# The director may step up to ~0.7 m along the prop to reach the part it works on.
	return Vector2(person.global_position.x, person.global_position.z).distance_to(Vector2(station_position.x, station_position.z)) <= 0.9

func _enforce_guaranteed_prop_jobs() -> void:
	var active := 0
	var active_meters := 0
	var active_power_meters := 0
	var active_water_meters := 0
	for index in _agents.size():
		var agent := _agents[index]
		if bool(agent.guaranteed_prop_job) and not _is_prop_job_active(agent):
			var station := _stations.get(str(agent.station_id), {}) as Dictionary
			if not station.is_empty() and str(agent.pair_id).is_empty():
				var clips: Array = []
				for clip_value in station.clips as Array:
					if PROP_JOB_CLIPS.has(str(clip_value)):
						clips.append(clip_value)
				if not clips.is_empty():
					agent.action_clips = clips
					agent.action_clip_index = 0
					agent.selected_clip = _select_supported_clip(agent.animation as AnimationPlayer, clips)
					(agent.node as Node3D).global_position = _station_position(station, float(agent.ground_y))
					_begin_action(agent, action_seconds + 12.0 + float(index % 5))
					_agents[index] = agent
		var is_active := _is_prop_job_active(agent)
		(agent.node as Node3D).set_meta("npc_prop_interaction_active", is_active)
		if is_active:
			active += 1
			var active_station := _stations.get(str(agent.station_id), {}) as Dictionary
			var active_prop_kind := str(active_station.get("prop", ""))
			if active_prop_kind == "power_meter":
				active_meters += 1
				active_power_meters += 1
			elif active_prop_kind == "water_meter":
				active_meters += 1
				active_water_meters += 1
			elif active_prop_kind.contains("pole"):
				push_error("A power pole was incorrectly assigned as a citizen work prop")
	_minimum_observed_prop_workers = mini(_minimum_observed_prop_workers, active)
	_minimum_observed_meter_workers = mini(_minimum_observed_meter_workers, active_meters)
	_minimum_observed_power_meter_workers = mini(_minimum_observed_power_meter_workers, active_power_meters)
	_minimum_observed_water_meter_workers = mini(_minimum_observed_water_meter_workers, active_water_meters)
	if active < MINIMUM_PROP_JOB_WORKERS:
		push_error("Prop-job invariant violated: %d active, minimum %d" % [active, MINIMUM_PROP_JOB_WORKERS])
	if active_meters < MINIMUM_METER_JOB_WORKERS or active_power_meters < 2 or active_water_meters < 2:
		push_error("Meter-work invariant violated: %d active (%d power, %d water)" % [active_meters, active_power_meters, active_water_meters])

func _separate_initial_positions() -> void:
	for index in _agents.size():
		var person:=_agents[index].node as Node3D
		for previous in index:
			var other:=_agents[previous].node as Node3D
			if person.global_position.distance_to(other.global_position)<personal_space: person.global_position+=Vector3(float(index%3-1)*1.3,0.0,float(index%2*2-1)*1.3)

func _begin_action(agent: Dictionary, duration: float) -> void:
	var station:=_stations[str(agent.station_id)] as Dictionary; var person:=agent.node as Node3D
	if bool(station.get("home", false)):
		agent.action_until = _elapsed + 0.05; agent.motion_state = "entering_home"; agent.last_speed = 0.0; agent.gesture_until = agent.action_until
		return
	var facing:=(_block_center(str(station.block),float(agent.ground_y))-person.global_position).normalized(); agent.face_yaw=atan2(facing.x,facing.z)
	var station_prop := station.get("prop_node") as Node3D
	if is_instance_valid(station_prop) and station_prop.has_meta("prop_kind"):
		agent.face_yaw = float(ActivityLibrary.stand_transform(station_prop, str(station.prop), ActivityLibrary.scene_class(str(station.prop))).yaw)
	var clip:=str(agent.selected_clip)
	var stage_seconds := _start_stage(agent, clip)
	# Throwing something away is one performance, not a loop: the visit ends when the item is in the bin.
	if bool(station.get("transient", false)) and str(station.prop) in ActivityLibrary.BIN_KINDS:
		duration = stage_seconds
	agent.work_stage = str(WORK_STAGE_BY_CLIP.get(clip,"physical_task"))
	_update_work_scene_state(agent)
	if not str(agent.work_order_id).is_empty():
		_society.call("begin_work_order", str(agent.citizen_id), str(agent.work_order_id))
	agent.gesture_until=_elapsed+stage_seconds; agent.next_gesture_at=agent.gesture_until; agent.action_until=_elapsed+duration; agent.motion_state="performing_%s"%str(agent.task); agent.last_speed=0.0; person.set_meta("npc_intent","perform_supported_activity")
	person.set_meta("npc_prop_interaction_active", _is_prop_job_active(agent))

## Runs one stage ("Chest_Open", "InspectLow" ...) at the station's real prop: idle motion underneath,
## hands, posture, hinged parts and carried items driven by the director. Returns its length in seconds.
func _start_stage(agent: Dictionary, clip: String, turn_done := false) -> float:
	var player := agent.animation as AnimationPlayer
	var director := agent.director as RefCounted
	if not turn_done and agent.has("face_yaw"):
		# Still turning to face the prop: the feet are pinned only once the body is settled, so they can
		# never end up crossed or twisted by a turn that happens after they were planted.
		agent.pending_stage = clip
		_play_candidates(player, ["Idle_Neutral", "Idle"], true)
		director.call("stop")
		var estimate := 0.0
		var station_pending := _stations.get(str(agent.station_id), {}) as Dictionary
		var prop_pending := station_pending.get("prop_node") as Node3D
		if is_instance_valid(prop_pending) and prop_pending.has_meta("prop_kind"):
			for beat_value in ActivityLibrary.beats_for(str(station_pending.prop), int(agent.action_clip_index), _activity_seed(agent)):
				var pending_beat := beat_value as Dictionary
				estimate += float(pending_beat.hold) if float(pending_beat.hold) > 0.0 else ActivityLibrary.clip_length(str(pending_beat.clip))
		return maxf(estimate, 1.5) + 0.8
	var station := _stations.get(str(agent.station_id), {}) as Dictionary
	var prop := station.get("prop_node") as Node3D
	_play_candidates(player, ["Idle_Neutral", "Idle"], true)
	director.call("stop")
	if not is_instance_valid(prop) or not prop.has_meta("prop_kind"):
		return 3.0
	var kind := str(station.prop)
	var carried := str(station.get("item", ""))
	var beats := ActivityLibrary.beats_for(kind, int(agent.action_clip_index), _activity_seed(agent), carried)
	director.call("start", beats, prop, float(agent.ground_y))
	var total := 0.0
	for beat_value in beats:
		var beat := beat_value as Dictionary
		total += float(beat.hold) if float(beat.hold) > 0.0 else ActivityLibrary.clip_length(str(beat.clip))
	return maxf(total, 1.5)

## Deterministic per-person, per-visit variety so no two people (or two visits) play the same idle sequence.
func _activity_seed(agent: Dictionary) -> int:
	return absi(int(hash("%s:%d" % [str(agent.citizen_id), int(agent.completed_actions)])))

func _update_action_animation(agent: Dictionary) -> void:
	if _elapsed<float(agent.gesture_until): return
	var clips:=agent.action_clips as Array
	if clips.is_empty(): clips=[agent.selected_clip]
	agent.action_clip_index=(int(agent.action_clip_index)+1)%clips.size()
	var clip:=str(clips[int(agent.action_clip_index)])
	agent.selected_clip=clip
	var stage_seconds := _start_stage(agent, clip)
	agent.work_stage = str(WORK_STAGE_BY_CLIP.get(clip,"physical_task"))
	_update_work_scene_state(agent)
	var station:=_stations[str(agent.station_id)] as Dictionary
	agent.action=str((station.words as Dictionary).get(clip,agent.action)); (agent.node as Node3D).set_meta("npc_action",agent.action)
	agent.gesture_until=_elapsed+stage_seconds
	agent.next_gesture_at=agent.gesture_until

func _select_supported_clip(player: AnimationPlayer, candidates: Array) -> String:
	for candidate in candidates:
		if not _find_clip(player,str(candidate)).is_empty(): return str(candidate)
	return "Idle_Neutral" if not _find_clip(player,"Idle_Neutral").is_empty() else "Idle"

func _play_candidates(player: AnimationPlayer, candidates: Array, loop_clip: bool) -> String:
	if player==null: return ""
	var suffix:=_select_supported_clip(player,candidates); var selected:=_find_clip(player,suffix)
	if selected.is_empty(): return ""
	var animation:=player.get_animation(selected)
	# Writing loop_mode emits changed on the shared clip, which makes every
	# AnimationPlayer using that library rebuild its track caches: only write it
	# when the mode actually differs.
	var loop_mode := Animation.LOOP_LINEAR if loop_clip else Animation.LOOP_NONE
	if animation!=null and animation.loop_mode!=loop_mode: animation.loop_mode=loop_mode
	if player.current_animation!=selected or not player.is_playing(): player.play(selected,0.16)
	player.speed_scale=1.0; return suffix

func _find_clip(player: AnimationPlayer, suffix: String) -> String:
	if player==null: return ""
	var rocketbox_name := "rocketbox/%s" % suffix
	if player.has_animation(rocketbox_name): return rocketbox_name
	var model_specific:="city_actions/CityAction|%s"%suffix
	if player.has_animation(model_specific): return model_specific
	for animation_name in player.get_animation_list():
		if str(animation_name).ends_with("|%s"%suffix): return str(animation_name)
	return ""

func _clip_length(player: AnimationPlayer, suffix: String) -> float:
	var name:=_find_clip(player,suffix); var animation:=player.get_animation(name) if not name.is_empty() else null
	return maxf(0.8,animation.length if animation!=null else 1.0)


func _work_stage_duration(player: AnimationPlayer, suffix: String) -> float:
	# Some source takes include a long neutral lead-out. A job beat must remain
	# readable but cannot monopolize the whole work order while the citizen
	# appears planted. The authored take still plays at 1x; this only schedules
	# the transition to the next semantic stage.
	return clampf(_clip_length(player, suffix), 1.6, 4.0)


func _update_work_scene_state(agent: Dictionary) -> void:
	var person := agent.node as Node3D
	person.set_meta("npc_work_stage", str(agent.work_stage))
	person.set_meta("npc_work_stage_index", int(agent.action_clip_index))
	person.set_meta("npc_work_stage_count", (agent.action_clips as Array).size())
	var station := _stations.get(str(agent.station_id), {}) as Dictionary
	var prop := station.get("prop_node") as Node3D
	if not is_instance_valid(prop):
		return
	prop.set_meta("activity_state", str(agent.work_stage))
	prop.set_meta("active_citizen_id", str(agent.citizen_id))
	prop.set_meta("active_work_order_id", str(agent.work_order_id))
	prop.set_meta("last_action", str(agent.action))
	prop.set_meta("stage_index", int(agent.action_clip_index))
	prop.set_meta("stage_count", (agent.action_clips as Array).size())


func _complete_work_scene_state(agent: Dictionary) -> void:
	var station := _stations.get(str(agent.station_id), {}) as Dictionary
	var prop := station.get("prop_node") as Node3D
	if is_instance_valid(prop):
		prop.set_meta("activity_state", "serviced")
		prop.set_meta("active_citizen_id", "")
		prop.set_meta("last_completed_work_order_id", str(agent.work_order_id))
		prop.set_meta("last_serviced_simulation_second", _elapsed)

func _update_planner() -> void:
	if _planner_port<=0 or _planner_http==null or _planner_active_index>=0 or _elapsed<_planner_next_at or _agents.is_empty(): return
	_planner_active_index=_planner_cursor%_agents.size(); _planner_cursor+=1
	var agent:=_agents[_planner_active_index]
	var legal_actions:Array[String]=[]
	for station_id in agent.stations:
		var station:=_stations[str(station_id)] as Dictionary; legal_actions.append(str(station.id).trim_prefix("npc_%02d_"%[int(agent.index)+1]))
	var society_state := _society.call("get_citizen_public_state", str(agent.citizen_id)) as Dictionary
	var payload={"npc_id":str((agent.node as Node3D).name),"citizen_id":agent.citizen_id,"household_id":agent.household_id,"role":agent.role,"personality":agent.personality,"active_task":agent.task,"task_target":agent.action_target,"work_order_id":agent.work_order_id,"decision_reason":agent.decision_reason,"agenda_activity":society_state.get("current_activity",{}),"current_state":{"intent":"perform_job"},"available_destinations":["north","east","south","west"],"legal_intents":["express_current_intent","summarize_memory","suggest_future_activity"],"legal_actions":legal_actions,"authority":"proposal_only_society_kernel_remains_authoritative"}
	agent.planner_payload=payload; _agents[_planner_active_index]=agent
	var error:=_planner_http.request("http://127.0.0.1:%d/plan"%_planner_port,["Content-Type: application/json"],HTTPClient.METHOD_POST,JSON.stringify(payload))
	if error!=OK: _planner_active_index=-1; _planner_next_at=_elapsed+10.0

func _on_planner_response(_result:int,response_code:int,_headers:PackedStringArray,body:PackedByteArray)->void:
	if _planner_active_index<0 or _planner_active_index>=_agents.size(): return
	var agent:=_agents[_planner_active_index]
	var response: Variant = JSON.parse_string(body.get_string_from_utf8()) if response_code==200 else null
	if response is Dictionary:
		agent.planner_response=response
		agent.plan_source="society_kernel+optional_expression"
	_agents[_planner_active_index]=agent; _planner_active_index=-1
	# First eight calls establish one inexpensive template per role. Thereafter
	# refresh one citizen every 100 seconds (36 calls/hour maximum).
	_planner_next_at=_elapsed+(1.0 if _planner_cursor<8 else 100.0)

func _build_sidewalk_graph() -> void:
	for row in range(1,5):
		for column in range(1,5):
			# 38 m follows the clear sidewalk band: outside the largest building
			# footprints but still 2 m inside the road edge at every 95 m block.
			var block:="r%dc%d"%[row,column]; var center:=_block_center(block,0.16); var h:=38.0
			var points:=[Vector3(center.x,center.y,center.z-h),Vector3(center.x+h,center.y,center.z-h),Vector3(center.x+h,center.y,center.z),Vector3(center.x+h,center.y,center.z+h),Vector3(center.x,center.y,center.z+h),Vector3(center.x-h,center.y,center.z+h),Vector3(center.x-h,center.y,center.z),Vector3(center.x-h,center.y,center.z-h)]
			for edge in points.size(): var key:=_node_key(block,edge); _graph[key]=points[edge]; _links[key]=PackedStringArray()
			for edge in points.size(): _connect(_node_key(block,edge),_node_key(block,(edge+1)%points.size()))
	for row in range(1,5):
		for column in range(1,5):
			var block:="r%dc%d"%[row,column]
			if column<4:
				var east:="r%dc%d"%[row,column+1]; _connect(_node_key(block,1),_node_key(east,7)); _connect(_node_key(block,3),_node_key(east,5))
			if row<4:
				var south:="r%dc%d"%[row+1,column]; _connect(_node_key(block,5),_node_key(south,7)); _connect(_node_key(block,3),_node_key(south,1))

func _station_position(station: Dictionary, y: float) -> Vector3:
	if station.has("resolved_position"):
		var resolved := station.resolved_position as Vector3
		return Vector3(resolved.x, y, resolved.z)
	var base:=_graph[str(station.node)] as Vector3; var inward:=(_block_center(str(station.block),y)-base).normalized()
	return Vector3(base.x,y,base.z)+inward*1.15


func parking_citizen_plan(citizen_id: String, origin: Vector3) -> Dictionary:
	## Gives an embodied parking citizen the same real destinations and reserved
	## sidewalk graph used by the normal city population.  The SocietyKernel
	## remains authoritative for who they are and why they are travelling.
	if not is_instance_valid(_society):
		return {}
	var state := _society.call("get_citizen_public_state", citizen_id) as Dictionary
	if state.is_empty() or _stations.is_empty():
		return {}
	var activity := state.get("current_activity", {}) as Dictionary
	var occupation := str(state.get("occupation", ""))
	var candidates: Array[Dictionary] = []
	for station_value in _stations.values():
		var station := station_value as Dictionary
		if str(station.get("role", "")) == occupation:
			candidates.append(station)
	if candidates.is_empty():
		for station_value in _stations.values():
			candidates.append(station_value as Dictionary)
	var selected := candidates[posmod(absi(hash("%s:%s" % [citizen_id, str(activity.get("activity_id", ""))])), candidates.size())] as Dictionary
	var destination := _station_position(selected, 0.17)
	var start_node := _nearest_safe_graph_node(origin)
	var goal_node := str(selected.get("node", _nearest_safe_graph_node(destination)))
	var route := _shortest_path(start_node, goal_node)
	var graph_start := _graph[start_node] as Vector3 if _graph.has(start_node) else origin
	var points := _sidewalk_connector(origin, graph_start)
	if _graph.has(start_node):
		if points.is_empty() or points[points.size() - 1].distance_to(graph_start) > 0.05:
			points.append(graph_start)
	for node_key_value in route:
		if _graph.has(str(node_key_value)):
			points.append(_graph[str(node_key_value)] as Vector3)
	points.append(destination)
	var clips := selected.get("clips", ["Interact"]) as Array
	return {
		"citizen": state,
		"activity": activity.duplicate(true),
		"station_id": str(selected.get("id", "")),
		"destination": destination,
		"outbound_path": points,
		"activity_clip": str(clips[0]) if not clips.is_empty() else "Interact",
	}


func _sidewalk_connector(origin: Vector3, destination: Vector3) -> PackedVector3Array:
	## Join outer-city pavement to the core graph with axis-aligned sidewalk
	## segments. A direct diagonal here cut across carriageways and blocks.
	var origin_axis := _nearest_sidewalk_axis(origin)
	var destination_axis := _nearest_sidewalk_axis(destination)
	var points := PackedVector3Array([Vector3(origin.x, 0.17, origin.z)])
	if origin_axis == "horizontal" and destination_axis == "vertical":
		points.append(Vector3(destination.x, 0.17, origin.z))
	elif origin_axis == "vertical" and destination_axis == "horizontal":
		points.append(Vector3(origin.x, 0.17, destination.z))
	elif origin_axis == "horizontal":
		var turn_x := _nearest_sidewalk_coordinate((origin.x + destination.x) * 0.5)
		points.append(Vector3(turn_x, 0.17, origin.z))
		points.append(Vector3(turn_x, 0.17, destination.z))
	else:
		var turn_z := _nearest_sidewalk_coordinate((origin.z + destination.z) * 0.5)
		points.append(Vector3(origin.x, 0.17, turn_z))
		points.append(Vector3(destination.x, 0.17, turn_z))
	points.append(Vector3(destination.x, 0.17, destination.z))
	var clean := PackedVector3Array()
	for point in points:
		if clean.is_empty() or clean[clean.size() - 1].distance_to(point) > 0.05:
			clean.append(point)
	return clean


func _nearest_sidewalk_axis(point: Vector3) -> String:
	return "vertical" if absf(point.x - _nearest_sidewalk_coordinate(point.x)) <= absf(point.z - _nearest_sidewalk_coordinate(point.z)) else "horizontal"


func _nearest_sidewalk_coordinate(value: float) -> float:
	var best := 0.0
	var distance := INF
	for junction in 9:
		var centre := -380.0 + float(junction) * 95.0
		for side in [-9.4, 9.4]:
			var candidate: float = centre + float(side)
			if absf(value - candidate) < distance:
				best = candidate
				distance = absf(value - candidate)
	return best


func _route_length(start_node: String, route: PackedStringArray, station_position: Vector3) -> float:
	if not _graph.has(start_node):
		return 0.0
	var length := 0.0
	var previous := _graph[start_node] as Vector3
	for node_key_value in route:
		var node_key := str(node_key_value)
		if not _graph.has(node_key):
			continue
		var current := _graph[node_key] as Vector3
		length += previous.distance_to(current)
		previous = current
	length += previous.distance_to(station_position)
	return length


func _live_transport_snapshot() -> Dictionary:
	var city_life := get_parent().get_node_or_null("CityLife") as Node
	if city_life != null and city_life.has_method("get_transport_snapshot"):
		return city_life.call("get_transport_snapshot") as Dictionary
	return {
		"sumo_connected": false,
		"simulated_vehicle_count": 0,
		"displayed_vehicle_count": 0,
		"stopped_vehicle_count": 0,
		"turning_vehicle_count": 0,
		"average_speed_mps": 0.0,
		"sample_msec": Time.get_ticks_msec()
	}


func _complete_agent_trip(agent: Dictionary) -> void:
	if str(agent.trip_id).is_empty():
		return
	_society.call("record_transport_snapshot", _live_transport_snapshot())
	_society.call("complete_trip", str(agent.citizen_id), str(agent.trip_id), "arrived")
	agent.trip_id = ""
	var society_state := _society.call("get_citizen_public_state", str(agent.citizen_id)) as Dictionary
	agent.decision_reason = str(society_state.get("decision_reason", agent.decision_reason))
	var planner_payload := agent.planner_payload as Dictionary
	planner_payload["decision_reason"] = agent.decision_reason
	planner_payload["trip_outcome"] = {
		"status": "arrived",
		"last_trip_minutes": society_state.get("last_trip_minutes", 0.0),
		"transport_source": (society_state.get("transport_snapshot", {}) as Dictionary).get("source", "local traffic fallback")
	}
	agent.planner_payload = planner_payload
	var planner_response := agent.planner_response as Dictionary
	planner_response["reason"] = agent.decision_reason
	agent.planner_response = planner_response
	var person := agent.node as Node3D
	person.set_meta("citizen_trip_id", "")
	person.set_meta("citizen_decision_reason", agent.decision_reason)


func _route_entry_position(agent: Dictionary) -> Vector3:
	var assigned_target := agent.get("route_entry_target", Vector3.ZERO) as Vector3
	if assigned_target != Vector3.ZERO:
		return assigned_target
	return _select_route_entry_position(agent)


func _select_route_entry_position(agent: Dictionary, rejected := Vector3.ZERO) -> Vector3:
	var entry := _graph[str(agent.route_start)] as Vector3
	entry.y = float(agent.ground_y)
	var route := agent.route as PackedStringArray
	var outbound_target := _station_position(_stations[str(agent.station_id)] as Dictionary, float(agent.ground_y))
	if not route.is_empty() and _graph.has(str(route[0])):
		outbound_target = _graph[str(route[0])] as Vector3
	var direction := outbound_target - entry
	direction.y = 0.0
	if direction.length_squared() < 0.01:
		return entry
	var perpendicular := Vector3(-direction.z, 0.0, direction.x).normalized()
	# Every traveller keeps right relative to their own direction. Using an
	# agent-parity sign here put opposite travellers on the same world-space
	# side of the pavement and created head-on stand-offs.
	# Stagger simultaneous departures only *into* the outbound segment. A
	# centred offset produced negative slots behind the junction, making some
	# citizens turn back through the work station they had just left.
	var longitudinal_slot := 0.75 + float(posmod(int(agent.index), 4)) * 0.55
	var forward := direction.normalized()
	var candidates := [
		entry + perpendicular * 0.68 + forward * longitudinal_slot,
		entry + perpendicular * 0.68 + forward * (longitudinal_slot + 1.50),
		entry - perpendicular * 0.68 + forward * longitudinal_slot,
		entry + perpendicular * 1.28 + forward * longitudinal_slot,
		entry - perpendicular * 1.28 + forward * (longitudinal_slot + 0.75)
	]
	for candidate_value in candidates:
		var candidate := candidate_value as Vector3
		candidate.y = float(agent.ground_y)
		if rejected != Vector3.ZERO and candidate.distance_to(rejected) < 0.10:
			continue
		if _route_entry_candidate_clear(agent, candidate):
			return candidate
	# Geometry and person avoidance still guard every actual step. This fallback
	# preserves a route even in the unlikely case that all connector slots are
	# momentarily occupied.
	return candidates[1] as Vector3


func _route_entry_candidate_clear(agent: Dictionary, candidate: Vector3) -> bool:
	if not _position_outside_buildings(candidate, 0.52) or not _within_walk_network(candidate, 1.70):
		return false
	for obstacle_index_value in _obstacle_indices_at(_static_obstacle_grid, candidate):
		var obstacle := _static_obstacles[int(obstacle_index_value)] as Dictionary
		if not is_instance_valid(obstacle.get("node")):
			continue
		if str(obstacle.kind) in ["tree", "pole"]:
			var offset := candidate - (obstacle.position as Vector3)
			offset.y = 0.0
			if offset.length() < 0.45 + float(obstacle.radius):
				return false
		elif CitySpatialSafety.point_inside_horizontal(candidate, obstacle.bounds as AABB, 0.42):
			return false
	var own_prop: Node3D = null
	if _stations.has(str(agent.station_id)):
		own_prop = (_stations[str(agent.station_id)] as Dictionary).get("prop_node") as Node3D
	for obstacle_index_value in _obstacle_indices_at(_activity_prop_obstacle_grid, candidate):
		var obstacle := _activity_prop_obstacles[int(obstacle_index_value)] as Dictionary
		var prop := obstacle.node as Node3D
		if is_instance_valid(prop) and prop != own_prop and prop.visible and CitySpatialSafety.point_inside_horizontal(candidate, obstacle.bounds as AABB, 0.34):
			return false
	for other_index_value in _nearby_agent_indices(candidate):
		var other := _agents[int(other_index_value)] as Dictionary
		if other.node != agent.node and candidate.distance_to((other.node as Node3D).global_position) < 1.65:
			return false
	return true


func _lane_target(segment_start: Vector3, target: Vector3, _lane_sign: float) -> Vector3:
	var direction := target - segment_start
	direction.y = 0.0
	if direction.length_squared() < 0.01:
		return target
	var perpendicular := Vector3(-direction.z, 0.0, direction.x).normalized()
	return target + perpendicular * 0.68
func _block_center(block: String, y: float) -> Vector3:
	var pieces:=block.split("c"); return Vector3(-142.5+float(int(pieces[1])-1)*95.0,y,-142.5+float(int(pieces[0].trim_prefix("r"))-1)*95.0)
func _connect(a: String, b: String) -> void:
	var a_links:=_links[a] as PackedStringArray; var b_links:=_links[b] as PackedStringArray
	if not a_links.has(b): a_links.append(b)
	if not b_links.has(a): b_links.append(a)
	_links[a]=a_links; _links[b]=b_links
func _shortest_path(start: String, goal: String) -> PackedStringArray:
	if start==goal: return PackedStringArray()
	var frontier:=PackedStringArray([start]); var visited:={start:true}; var previous:={}
	while not frontier.is_empty():
		var current:=frontier[0]; frontier.remove_at(0)
		if current==goal: break
		for next_value in _links[current] as PackedStringArray:
			var next:=str(next_value)
			if not visited.has(next): visited[next]=true; previous[next]=current; frontier.append(next)
	if not previous.has(goal): return PackedStringArray()
	var output:Array[String]=[goal]; var cursor:=goal
	while previous.has(cursor): cursor=str(previous[cursor]); output.push_front(cursor)
	output.pop_front(); return PackedStringArray(output)
func _nearest_graph_node(position: Vector3) -> String:
	var selected:=""; var best:=INF
	for key_value in _graph.keys():
		var distance:=position.distance_squared_to(_graph[key_value] as Vector3)
		if distance<best: selected=str(key_value); best=distance
	return selected
func _nearest_safe_graph_node(position: Vector3) -> String:
	var selected := ""
	var best := INF
	for key_value in _graph.keys():
		var graph_position := _graph[key_value] as Vector3
		if not _position_outside_buildings(graph_position, 0.65):
			continue
		var distance := position.distance_squared_to(graph_position)
		if distance < best:
			selected = str(key_value)
			best = distance
	return selected if not selected.is_empty() else _nearest_graph_node(position)
func _node_key(block: String, edge: int) -> String: return "%s:%d"%[block,edge]

var _agent_debug_frame:=-1
var _agent_debug_cache:Array[Dictionary]=[]
func get_agent_debug() -> Array[Dictionary]:
	# Several readers (sensor snapshot, citizen list, status) ask in the same frame: build it once per frame.
	var frame:=Engine.get_process_frames()
	if frame==_agent_debug_frame: return _agent_debug_cache
	var output:Array[Dictionary]=[]
	for agent in _agents: output.append(_public_state(agent))
	_agent_debug_frame=frame
	_agent_debug_cache=output
	return output
func get_agent_state(target: Node3D) -> Dictionary:
	for agent in _agents:
		if agent.node==target: return _public_state(agent)
	return {}
func get_spatial_debug() -> Dictionary:
	var active_prop_workers := 0
	var active_meter_workers := 0
	var active_power_meter_workers := 0
	var active_water_meter_workers := 0
	var active_pole_workers := 0
	var safe_station_props := 0
	for agent in _agents:
		if _is_prop_job_active(agent):
			active_prop_workers += 1
			var station := _stations.get(str(agent.station_id), {}) as Dictionary
			var prop_kind := str(station.get("prop", ""))
			if prop_kind == "power_meter":
				active_meter_workers += 1
				active_power_meter_workers += 1
			elif prop_kind == "water_meter":
				active_meter_workers += 1
				active_water_meter_workers += 1
			elif prop_kind.contains("pole"):
				active_pole_workers += 1
	for station_value in _stations.values():
		var prop := (station_value as Dictionary).get("prop_node") as Node3D
		if is_instance_valid(prop) and bool(prop.get_meta("collision_safe_placement", false)):
			safe_station_props += 1
	return {"active_prop_workers":active_prop_workers,"required_prop_workers":MINIMUM_PROP_JOB_WORKERS,"active_meter_workers":active_meter_workers,"required_meter_workers":MINIMUM_METER_JOB_WORKERS,"active_power_meter_workers":active_power_meter_workers,"active_water_meter_workers":active_water_meter_workers,"active_pole_workers":active_pole_workers,"minimum_observed_prop_workers":_minimum_observed_prop_workers,"minimum_observed_meter_workers":_minimum_observed_meter_workers,"minimum_observed_power_meter_workers":_minimum_observed_power_meter_workers,"minimum_observed_water_meter_workers":_minimum_observed_water_meter_workers,"safe_station_props":safe_station_props,"total_station_props":_stations.size(),"building_obstacles":_building_bounds.size(),"static_obstacles":_static_obstacles.size(),"walk_segments":_walk_segments.size(),"tree_safety":_tree_safety.duplicate(true),"urban_prop_safety":_urban_prop_safety.duplicate(true),"society":get_society_debug()}


func get_society_debug() -> Dictionary:
	return _society.call("get_debug_summary") as Dictionary if is_instance_valid(_society) else {}


func get_society_recent_events(limit := 20) -> Array[Dictionary]:
	return _society.call("get_recent_events", limit) as Array[Dictionary] if is_instance_valid(_society) else []


func snapshot_society_state() -> Dictionary:
	return _society.call("snapshot_state") as Dictionary if is_instance_valid(_society) else {}


func _public_state(agent: Dictionary) -> Dictionary:
	var paired:=not str(agent.pair_id).is_empty()
	var prop_job_active := _is_prop_job_active(agent)
	var job_prop_kind := ""
	if _stations.has(str(agent.station_id)):
		job_prop_kind = str((_stations[str(agent.station_id)] as Dictionary).get("prop", ""))
	var society_state := _society.call("get_citizen_public_state", str(agent.citizen_id)) as Dictionary if is_instance_valid(_society) else {}
	var route := agent.route as PackedStringArray
	var next_waypoint := Vector3.ZERO
	if bool(agent.route_entry_pending):
		next_waypoint = _route_entry_position(agent)
	elif int(agent.route_index) < route.size() and _graph.has(str(route[int(agent.route_index)])):
		next_waypoint = _graph[str(route[int(agent.route_index)])] as Vector3
	var active_tool_kind := ""
	var active_hand_item_kind_director := ""
	for held_kind in (agent.director as RefCounted).call("held_items") as Array:
		if str(held_kind) in ["phone", "meter_reader", "cup", "watering_can", "wrench", "chalk", "coin", "handset", "laptop", "cleaner", "broom", "can"]:
			active_tool_kind = str(held_kind)
		else:
			active_hand_item_kind_director = str(held_kind)
	for tool_value in (agent.equipment as Dictionary).values():
		var tool := tool_value as Node3D
		if is_instance_valid(tool) and tool.visible and str(tool.get_meta("npc_carry", "right_hand")) != "rest":
			active_tool_kind = str(tool.get_meta("job_tool", ""))
			break
	var active_hand_item_kind := active_hand_item_kind_director
	for item_value in (agent.handled_items as Dictionary).values():
		var item := item_value as Node3D
		if is_instance_valid(item) and item.visible and str(item.get_meta("npc_carry", "right_hand")) != "rest":
			active_hand_item_kind = str(item.get_meta("handled_item_kind", ""))
			break
	if active_hand_item_kind.is_empty():
		active_hand_item_kind = str(agent.active_pair_item_kind)
	# Working with the station's own object (resting on its crate or bench
	# between grips) is as readable as holding it.
	var works_station_object := false
	for entry_value in agent.get("custody", []):
		if str((entry_value as Dictionary).get("station_id", "")) == str(agent.station_id) and float(agent.action_until) > _elapsed:
			works_station_object = true
	var stationary_action_readable := paired or prop_job_active or works_station_object or not active_tool_kind.is_empty() or not active_hand_item_kind.is_empty() or bool((agent.director as RefCounted).call("is_running"))
	var state := {"node":agent.node,"npc_id":(agent.node as Node3D).name,"citizen_id":agent.citizen_id,"display_name":agent.name,"role":agent.role,"personality":agent.personality,"household_id":agent.household_id,"task":agent.task,"intent":"coordinate_with_character" if paired else "perform_supported_activity","action":agent.action,"action_target":agent.action_target,"work_stage":agent.work_stage,"work_stage_index":int(agent.pair_phase) if paired else int(agent.action_clip_index),"work_stage_count":3 if paired else (agent.action_clips as Array).size(),"station_id":agent.station_id,"station_capacity":2 if paired else 1,"motion_state":agent.motion_state,"speed_mps":agent.last_speed,"animation_clip":agent.selected_clip,"contact_kind":agent.contact_kind,"contact_weight":agent.contact_weight,"pose_ground_offset":agent.pose_ground_offset,"surface_y":agent.ground_y,"prop_interaction_active":prop_job_active,"job_prop_kind":job_prop_kind,"active_tool_kind":active_tool_kind,"active_hand_item_kind":active_hand_item_kind,"stationary_action_readable":stationary_action_readable,"guaranteed_prop_job":bool(agent.guaranteed_prop_job),"presentation_worker":bool(agent.guaranteed_prop_job),"seconds_remaining_in_action":0.0 if paired else maxf(0.0,float(agent.action_until)-_elapsed),"plan_source":agent.plan_source,"planner_payload":agent.planner_payload,"planner_response":agent.planner_response,"completed_actions":agent.completed_actions,"work_order_id":agent.work_order_id,"social_event_id":agent.social_event_id,"trip_id":agent.trip_id,"decision_reason":agent.decision_reason,"society_activity":agent.society_activity,"avoidance_reversals":int(agent.avoidance_reversals),"yield_events":int(agent.yield_events),"last_block_reason":str(agent.last_block_reason),"next_waypoint":next_waypoint,"station_reservations":[(agent.node as Node3D).name,agent.pair_partner] if paired else [(agent.node as Node3D).name],"interaction_id":agent.pair_id,"interaction_partner":agent.pair_partner,"interaction_role":agent.pair_role,"interaction_phase":agent.pair_phase,"interaction_turn":agent.interaction_turn}
	for key_value in society_state.keys():
		var key := str(key_value)
		if not state.has(key):
			state[key] = society_state[key_value]
	return state


# --- Physical object custody ----------------------------------------------------

func _passes_object(definition: Dictionary) -> bool:
	## Exchanges where the object changes hands stand a little closer.
	var owners := definition.get("item_owners", []) as Array
	return not str(definition.get("hand_item", "")).is_empty() and owners.has(0) and owners.has(1)


func _custody_handler() -> RefCounted:
	if _custody == null:
		var container := Node3D.new()
		container.name = "HandledObjects"
		add_child(container)
		_custody = NpcItemHandling.new(container)
	return _custody


func _ensure_custody(agent: Dictionary, index: int) -> void:
	if agent.has("custody"):
		return
	var person := agent.node as Node3D
	var skeleton := agent.skeleton as Skeleton3D
	var attachment := BoneAttachment3D.new()
	attachment.name = "RightHandCradle"
	attachment.bone_name = "Bip01 R Hand"
	skeleton.add_child(attachment)
	agent.cradle = _create_hand_grip_anchor(skeleton, attachment, "Bip01 R Hand", "cradle")
	var entries: Array = []
	var handler := _custody_handler()
	for source in [agent.handled_items, agent.equipment]:
		for station_id in source as Dictionary:
			var node := (source as Dictionary)[station_id] as Node3D
			if not is_instance_valid(node):
				continue
			var kind := str(node.get_meta("handled_item_kind", node.get_meta("job_tool", "")))
			if node.is_in_group("npc_job_tools") and not kind in CUSTODY_TOOLS:
				continue
			var station := _stations.get(str(station_id), {}) as Dictionary
			if station.is_empty():
				continue
			var rest := _station_rest(station, node)
			var entry: Dictionary = handler.call("register", node, kind, index, rest.xform, float(rest.y))
			entry.station_id = str(station_id)
			entry.point = rest.point
			entry.inside = bool(rest.get("inside", false))
			entries.append(entry)
	agent.custody = entries
	person.set_meta("npc_custody_objects", entries.size())


func _station_rest(station: Dictionary, node: Node3D) -> Dictionary:
	## Where a station's object rests between uses, chosen by what the prop is:
	## on a work surface or bench seat, inside a mailbox compartment, pinned on
	## a notice board, or on the pavement at the worker's side.
	var prop := station.get("prop_node") as Node3D
	var work := _station_position(station, 0.16)
	var facing := Vector3.FORWARD
	var kind := str(station.prop)
	var item_kind := str(node.get_meta("handled_item_kind", node.get_meta("job_tool", "")))
	var right_side := Vector3(-facing.z, 0.0, facing.x)
	if is_instance_valid(prop):
		facing = prop.global_position - work
		facing.y = 0.0
		facing = facing.normalized() if facing.length_squared() > 0.0001 else Vector3.FORWARD
		right_side = Vector3(-facing.z, 0.0, facing.x)
		var bounds := _model_world_bounds(prop)
		var yaw := atan2(facing.x, facing.z)
		if bounds.size != Vector3.ZERO and item_kind != "waste_bag":
			var front := _front_point(bounds, work)
			if kind == "bench":
				var seat := front + facing * 0.22 + right_side * 0.35
				seat.y = 0.16 + 0.44
				return {"xform": NpcItemHandling.rest_on_surface(node, seat, yaw), "y": seat.y, "point": seat}
			if kind == "mailbox":
				var slot := front - facing * 0.05
				slot.y = 1.02
				return {"xform": NpcItemHandling.rest_on_surface(node, slot, yaw), "y": slot.y, "point": slot, "inside": true}
			if kind in ["poster", "billboard"]:
				var pin := front - facing * 0.03
				pin.y = 1.15
				return {"xform": Transform3D(Basis(Vector3.UP, yaw) * Basis(Vector3.RIGHT, -PI * 0.5).scaled(node.global_transform.basis.get_scale()), pin), "y": pin.y, "point": pin, "pinned": true}
			var top := bounds.end.y - 0.16
			if (kind in SURFACE_PROPS or kind == "planter") and top >= 0.35 and top <= 1.2:
				var margin := 0.16
				var point := Vector3(clampf(work.x, bounds.position.x + margin, bounds.end.x - margin), bounds.end.y, clampf(work.z, bounds.position.z + margin, bounds.end.z - margin))
				if bounds.size.x <= margin * 2.0: point.x = bounds.get_center().x
				if bounds.size.z <= margin * 2.0: point.z = bounds.get_center().z
				return {"xform": NpcItemHandling.rest_on_surface(node, point, yaw), "y": point.y, "point": point}
	# On the pavement inside the work spot, at the worker's right hand.
	var ground := work + right_side * 0.45 + facing * 0.3
	ground.y = 0.16
	return {"xform": NpcItemHandling.rest_on_surface(node, ground, atan2(facing.x, facing.z)), "y": 0.16, "point": ground}


func _front_point(bounds: AABB, from: Vector3) -> Vector3:
	## The point of a prop's footprint nearest to where the worker stands.
	return Vector3(clampf(from.x, bounds.position.x, bounds.end.x), bounds.position.y, clampf(from.z, bounds.position.z, bounds.end.z))


func _pair_rest(pair: Dictionary, node: Node3D) -> Dictionary:
	var prop := pair.get("prop") as Node3D
	var a := (_agents[int(pair.a)] as Dictionary).node as Node3D
	var b := (_agents[int(pair.b)] as Dictionary).node as Node3D
	var midpoint := (a.global_position + b.global_position) * 0.5
	if is_instance_valid(prop) and prop.visible:
		var bounds := _model_world_bounds(prop)
		var top := bounds.end.y - 0.16
		if top >= 0.3 and top <= 1.25:
			var point := Vector3(clampf(midpoint.x, bounds.position.x + 0.15, bounds.end.x - 0.15), bounds.end.y, clampf(midpoint.z, bounds.position.z + 0.15, bounds.end.z - 0.15))
			if bounds.size.x <= 0.3: point.x = bounds.get_center().x
			if bounds.size.z <= 0.3: point.z = bounds.get_center().z
			return {"xform": NpcItemHandling.rest_on_surface(node, point, a.rotation.y), "y": point.y, "point": point}
	# No usable top: the giver keeps it at hand (set down by their feet).
	var ground := a.global_position + (b.global_position - a.global_position).normalized() * 0.35
	ground.y = 0.16
	return {"xform": NpcItemHandling.rest_on_surface(node, ground, a.rotation.y), "y": 0.16, "point": ground}


func _update_item_custody(delta: float) -> void:
	if _agents.is_empty():
		return
	var handler := _custody_handler()
	for index in _agents.size():
		var agent := _agents[index]
		_ensure_custody(agent, index)
		var clip := str(agent.selected_clip)
		if str(agent.get("custody_clip", "")) != clip or str(agent.get("custody_station", "")) != str(agent.station_id):
			agent.custody_clip = clip
			agent.custody_station = str(agent.station_id)
			agent.custody_since = _elapsed
		var in_action := float(agent.action_until) > _elapsed and str(agent.pair_id).is_empty()
		var stage := maxf(float(agent.gesture_until) - float(agent.custody_since), 1.2)
		var progress := (_elapsed - float(agent.custody_since)) / stage
		var person := agent.node as Node3D
		for entry_value in agent.custody as Array:
			var entry := entry_value as Dictionary
			var node := entry.node as Node3D
			var held: bool = handler.call("is_held_by", entry, index)
			var at_station := in_action and str(entry.station_id) == str(agent.station_id)
			var station := _stations.get(str(entry.station_id), {}) as Dictionary
			var prop := station.get("prop_node") as Node3D
			if not held and str(entry.target) == "rest" and str(entry.state) == "rest":
				# Mail and parcels inside a mailbox are out of sight until taken.
				node.visible = (not is_instance_valid(prop) or prop.visible) and not bool(entry.get("inside", false))
			elif held:
				node.visible = true
			var kind := str(entry.kind)
			var clips := (HAND_ITEM_ACTION_CLIPS.get(kind, TOOL_ACTION_CLIPS.get(kind, [])) as Array)
			if not at_station:
				if held or str(entry.target) == "hand":
					# Leaving the work spot: set it back where it belongs first.
					handler.call("put", entry, _station_rest(station, node).xform, float(entry.surface_y))
				continue
			var reach_rest: float = handler.call("hand_reach", agent, entry.point as Vector3)
			if clip == "SetGroundItem" and clips.has(clip):
				if held:
					var target_point := entry.point as Vector3
					var ahead := person.global_position + person.global_transform.basis.z.normalized() * 0.42
					ahead.y = 0.16
					var station_near := Vector2((entry.point as Vector3).x - person.global_position.x, (entry.point as Vector3).z - person.global_position.z).length() < 1.0
					if not station_near:
						target_point = ahead
					var reach_target: float = handler.call("hand_reach", agent, target_point)
					if reach_target < NpcItemHandling.PUT_REACH or progress > NpcItemHandling.FALLBACK_FRACTION:
						var rest_xform := NpcItemHandling.rest_on_surface(node, target_point, person.rotation.y)
						handler.call("put", entry, rest_xform, target_point.y)
						entry.point = target_point
			elif clips.has(clip):
				if not held and str(entry.target) != "hand" and (reach_rest < NpcItemHandling.GRAB_REACH or progress > NpcItemHandling.FALLBACK_FRACTION):
					handler.call("take", entry, agent, index)
			elif held and (reach_rest < NpcItemHandling.PUT_REACH or progress > NpcItemHandling.FALLBACK_FRACTION):
				handler.call("put", entry, _station_rest(station, node).xform, float(entry.surface_y))
		_agents[index] = agent
	for pair_index in _pairs.size():
		var pair := _pairs[pair_index]
		var item := pair.get("a_item") as Node3D
		if not is_instance_valid(item):
			continue
		if not pair.has("custody"):
			var rest := _pair_rest(pair, item)
			var entry: Dictionary = handler.call("register", item, str(item.get_meta("handled_item_kind", "")), int(pair.a), rest.xform, float(rest.y))
			entry.point = rest.point
			pair.custody = entry
			pair.rest = rest
		var entry := pair.custody as Dictionary
		var holder := int(pair.get("item_holder", -1))
		if holder < 0 or str(pair.get("status", "active")) != "active":
			handler.call("put", entry, (pair.rest as Dictionary).xform, float((pair.rest as Dictionary).y))
			if pair.has("cleanup_at") and str(entry.state) == "rest":
				var spot := (pair.rest as Dictionary).point as Vector3
				var gone := true
				for side_index in [int(pair.a), int(pair.b)]:
					if ((_agents[side_index] as Dictionary).node as Node3D).global_position.distance_to(spot) < 5.0:
						gone = false
				if gone or _elapsed >= float(pair.cleanup_at):
					item.visible = false
					var shared_prop := pair.get("prop") as Node3D
					if is_instance_valid(shared_prop):
						shared_prop.visible = false
					pair.erase("cleanup_at")
			_pairs[pair_index] = pair
			continue
		var holder_index := int(pair.a) if holder == 0 else int(pair.b)
		if not handler.call("is_held_by", entry, holder_index):
			if str(entry.target) == "hand":
				# Hand-over between the two people's extended hands.
				handler.call("take", entry, _agents[holder_index], holder_index, false, NpcItemHandling.HANDOVER_SECONDS)
			else:
				var reach: float = handler.call("hand_reach", _agents[holder_index], entry.point as Vector3)
				if reach < NpcItemHandling.GRAB_REACH or _elapsed - float(pair.get("phase_started", _elapsed)) > 0.9:
					handler.call("take", entry, _agents[holder_index], holder_index)
		_pairs[pair_index] = pair
	handler.call("update", delta, _agents)
