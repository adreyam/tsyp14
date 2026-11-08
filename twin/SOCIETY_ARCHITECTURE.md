# Fix 78 society and embodiment architecture

## Purpose

Fix 78 separates *being a citizen* from *currently having a model on screen*. `SocietyKernel` is the authoritative simulation layer. `NpcAi` is an embodiment adapter: it turns a kernel assignment into a validated route, multi-stage prop/item workflow, Rocketbox animation and visible pose, then reports completion back to the kernel. `CityLife` remains the traffic renderer/bridge consumer, and SUMO remains authoritative for vehicle movement.

This boundary prevents animation convenience from becoming simulation truth. A citizen does not acquire a job because a model happens to stand next to a cone; an institutional work order causes the citizen to travel to the cone, perform an eligible motion, and commit an outcome.

## Runtime ownership

| Concern | Authority | Persistent result |
| --- | --- | --- |
| identity, age, health, fatigue, funds and traits | `SocietyKernel` | citizen record |
| household, members, home block, rent and shared balance | `SocietyKernel` | household record |
| employer, occupation, wage and shift | `SocietyKernel` | citizen + institution records |
| daily activity choice | `SocietyKernel` agenda/state decision | current activity + reason |
| backlog, budget, inventory, employees and assets | `SocietyKernel` | institution/asset/work-order records |
| relationships, communication turns and item custody | `SocietyKernel` | typed edge/event/inventory records |
| trip origin, destination, mode, progress and arrival | `SocietyKernel` | trip ledger |
| vehicle position, route and signals | SUMO/TraCI | live transport snapshot |
| model, rig, clip, route following and contact pose | `NpcAi` | embodied presentation only |
| traffic meshes and interpolation | `CityLife` | embodied vehicle presentation only |
| selection and explanation UI | `UnifiedControlRoom` | read-only view of public state |

## Population model

The deterministic bootstrap creates 200 citizens in 80 households and 13 institutions. Household sizes are generated to sum exactly to the population. Adult household heads are created first, which gives the 32 visible Rocketbox models stable citizen records without making those visible residents a separate class of simulated entity. Remaining household members receive age/life-stage, attributes, employment and agendas from keyed deterministic rolls.

The initial multiplex relationship graph has 443 typed edges across household, colleague and neighbourhood contexts. Relationship strength and recent contact are stored independently of whether either participant is visible. A completed social event can create or strengthen an edge; monthly maintenance applies bounded decay.

## Daily decision model

Each citizen receives an explicit day agenda containing sleep/home, household, commute, work or school, meal, errands/social and return-home windows appropriate to age and employment. The current activity comes from simulation day/time plus citizen and household state. The kernel records a concise causal reason alongside the decision so the UI can explain *why now*.

The clock advances continuously for the live scene and can be accelerated for testing. At day close, wages and household expenses are applied, institution budgets and market inventory advance, age and agendas update, and monthly health/employment/household/relationship processes run when due. Off-screen citizens use the same records and rules; they do not need a `Node3D` to remain alive.

## Work-order lifecycle

1. A registered world station becomes a semantic institutional asset with role, prop kind, clips, condition and capacity.
2. The institution exposes an open service order caused by asset/backlog state.
3. A compatible employed citizen is assigned; one citizen and one exclusive station are reserved.
4. `NpcAi` receives station, order, activity and decision reason, then uses only the collision-validated sidewalk graph.
5. On arrival it starts a supported multi-stage workflow and reports `begin_work_order`.
6. It advances through bounded access, handling, operation/repair, verification, recording and placement beats while keeping prop, tool and held-item state synchronized.
7. On action completion it reports citizen, order and actual final clip to `complete_visible_work`.
8. The kernel closes the order, changes asset condition, backlog/budget and citizen wage/fatigue/funds state, records an event, and selects the next legal assignment.

The ten presentation workers are ordinary work-order assignees selected for physical pose coverage and readable props. The selector reserves at least five dedicated meter jobs, including at least two water and two power meters; poles are never eligible. Presentation status never bypasses work-order completion or grants authority to animation code.

The invariant counts a worker only when all of these are true: non-social assignment, `performing_*` state, visible collision-safe matching prop, within 0.35 m of the reserved station position, and a whitelisted physical clip. It requires ten total physical-prop workers and five meter workers at all times. Walking, carrying while walking, conversation, pointing, phone/note recording and passive proximity are excluded. Separately, the activity-quality gate rejects *any* solo stationary stage unless it has a matching physical-prop clip, visible compatible tool or visible handled item. Generic neutral, talking and confirmation poses are not legal solo work.

## Social-event lifecycle

The opening social events are chosen from eligible citizens by role constraints; participant indices are not hardcoded. An event creates a shared obligation and causal description, reserves two naturally spaced positions and one collision-safe prop, and binds three complementary clip phases. Every phase has one speaker, one listener and one visible item custodian. `NpcAi` reports the phase, speaker and holder to the kernel as it updates the authored scene. The kernel stores a three-entry custody history and, on completion, adds the event item to the final owner's inventory. It then updates the relationship graph, releases both participants, hides the temporary shared prop/items and supplies their next assignments. Participants visibly walk away; no phase or interaction loops.

## Movement and performance boundary

Movement uses a connected, lane-offset sidewalk graph. Route-entry slots are forward-only, staggered and validated against current geometry and citizens. Opposite walkers keep right relative to their direction; a head-on conflict first creates lateral clearance and steering never gains a backward component. A predictive movement buffer preserves a 1.35 m citizen floor even when agents update sequentially at accelerated time. If actors begin inside that envelope, only a strictly separating step is allowed. Recovery is a legal lateral walk step, never a teleport.

Static building, street-object and activity-prop bounds are cached and indexed into 16 m spatial cells. Live citizen positions are rebuilt in a small dynamic grid once per physics tick, while sidewalk segments have their own index. Pole and signal collision use the ground post, excluding elevated crossarms. `SocietyKernel` refreshes embodied agenda dictionaries only when the integer simulation minute changes. These constraints keep the implementation causal and collision-safe without repeating full-scene scans for every candidate step.

## Trips and traffic feedback

Every embodied movement assignment starts a trip record. It contains stable citizen ID, origin, destination, mode, expected minutes, normalized progress, status and current transport context. Waypoint progress updates the ledger, and arrival closes it before work begins. The continuity test accelerates this cycle and rejects implausible sampled position jumps.

Once per second the kernel records `CityLife.get_transport_snapshot()`: bridge connection state, simulated/displayed/stopped/turning counts, average speed and timestamp. If SUMO is unavailable, the source is explicitly marked as local fallback. This is contextual feedback, not a fabricated one-car-per-citizen mapping. Future demand coupling should create genuine SUMO person/vehicle demand or a documented aggregate demand adapter before claiming tighter integration.

## Embodiment safety boundary

The successful Rocketbox model/rig layer remains intact. Fix 78 does not procedurally rotate limbs or change skeleton scale. The shared importer keeps valid rotations and bounded pelvis motion while rejecting scale, root motion, non-pelvis translations and missing bones. The standing forward-inspection semantic clip retains authored upper-body motion but substitutes the neutral planted transforms for pelvis, structural spine base and both leg/foot/toe chains. Navigation runs in physics. Rocketbox players evaluate at early idle priority, live grounding then samples cached foot/toe indices and corrects only the character root vertically, and cameras read the final transform afterward. This ordering prevents a newly evaluated crouch from rendering with the preceding pose's ground offset.

Held objects are children of a right-hand bone attachment through a `FingerGrip_*` transform derived from the first articulated finger joints. Each asset has an orientation-aware contact point: devices and papers use a true major-axis side edge, boxes use an outer edge, hanging items use their top, and the watering can uses its handle. The grip gate rejects wrist penetration and flat assets that merely touch the hand through the middle of their broad face. Prop placement uses rendered bounds and rejects roads, buildings and other props before a station becomes eligible.

The optional language-model planner has no state authority. It receives a constrained summary and may return expression or future-activity suggestions. It cannot set transforms, choose unsupported clips, reassign employment, complete an order, mutate a trip, or alter a citizen record.

## Public inspection API

- `NpcAi.get_agent_state(node)` returns the combined embodied and authoritative citizen state for the selected model.
- `NpcAi.get_society_debug()` returns population, household, institution, relationship, work-order, social-event, trip, clock, transport and invariant counts.
- `NpcAi.get_society_recent_events(limit)` exposes the causal event stream.
- `NpcAi.snapshot_society_state()` returns a restorable authoritative snapshot.
- `CityLife.get_transport_snapshot()` returns the aggregate live/fallback transport context.

The **Citizen Society** control-room tab consumes these read-only views. It shows stable identity and household, current action, causal purpose, agenda, work order, trip and authority without becoming a second source of truth.

## Acceptance gates

`test_society_kernel.gd` checks determinism, exact population/households, institutions, relationships, all 64 assets, work effects, trip completion, snapshot/restore, 30 days of evolution and invariants. `test_citizen_society_integration.gd` checks real scene binding, causal assignments, finite social completion and relationship changes. `test_citizen_trip_continuity.gd` checks repeated departures/arrivals, bounded embodied motion and continuous prop-work coverage. `test_citizen_activity_quality.gd` checks all 64 multi-stage definitions, runtime stage/item/tool evidence, speaker/listener turns, item transfers and persistent custody history, post-event dispersal, reversals, yields and the separation floor. `test_active_meter_workers_and_random_selection.gd` enforces meter balance and non-walking random selection. `test_crouch_transition_grounding.gd` samples the real late render-frame order plus all 32 rigs across crouch entry, hold and exit. `test_grounded_worktable_and_palm_grip.gd` samples every rig and all 81 held objects for planted legs, retained upper-body motion, finger-joint placement, major-axis edge contact and palm exclusion. The inherited rig, animation, reach, grounding, route, collision, prop, traffic, vehicle, camera, UI and utility tests remain mandatory in `test_headless.sh`.

Measured results for the accepted files are in [TEST_REPORT.md](TEST_REPORT.md).
