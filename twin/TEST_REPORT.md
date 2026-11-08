# Verification report — Fix 80 Smart-City Asset Map and Vehicle Emissions

Verified on 2026-09-13 using the Godot 4.7.2, SUMO 1.25.0, TraCI and Python 3.14 runtimes bundled in this directory. Fix 80 is a new versioned folder; Fix 79 and every older deliverable remain preserved.

## Fix 80 acceptance

- The new Asset Map has 19 color-coded category buttons, live counts, explanatory tooltips and exact scene-group mappings. Automated activation sampled traffic signals, air monitors, every power/water meter, hospitals, police vehicles, ambulances and high-emission cars; frame count matched target count every time, and selecting a category twice cleared it.
- Sensor coverage is now 12 traffic radar detectors, 8 air-quality stations, 4 noise monitors, 4 weather stations, 4 flood-level radar sensors, 8 utility telemetry gateways and 16 separately highlighted physical street cameras. The unrelated bus-stop and air-conditioner placeholders are hidden.
- Actuator coverage is 49 traffic controllers, 5 water valves, 3 power switches, 12 imported Kenney adaptive street lights and 4 irrigation valves. Complete meter categories union the live utility meters with every technician-job and citizen-interaction cabinet.
- Civic response coverage is 7 external hospitals, 1 externally modeled and clearly identified police headquarters, 8 police vehicles and 2 moving Kenney ambulances. The 68-slot traffic fleet remains unchanged in size, lane-fitted and grounded.
- Six deterministic non-emergency cars emit procedural, rear-mounted black exhaust. Each emitter has 24 soft billboard particles, buoyant rise, velocity/spread variation and an opacity/color lifetime ramp. Each car exposes excessive-emissions, PM severity, sensor-detectable and repair-interaction metadata; no emergency vehicle can receive the condition.
- All new devices and facilities expose typed operational metadata and stable service markers for future physical citizen interaction. Purpose-built sensor assemblies contain at least four meaningful visible 3D components rather than a label-only placeholder.
- Every registered device passed rendered-bounds grounding at 0.17 m and cleared road corridors, all buildings, other devices and all 70 job/interaction props. The pedestrian spatial grid now includes smart devices; 150 live samples found zero building/road/prop/device/tree collisions.
- Device mesh detail distance-LODs at 360 m. Each outline's twelve cylinders are rendered through one `MultiMesh`, keeping the 49-signal selection at 49 frame draws instead of 588 geometry nodes. The matched 1,000-frame diagnostic recorded 15.89 ms p50 / 23.16 ms p95, compared with Fix 79 at 14.93 / 24.00.
- Eight hardware-rendered QA frames cover the complete panel, traffic radar, air monitor, weather station, smart light, police facility, ambulance and visible black exhaust in `screenshots/fix80_asset_map_smoke_qa/`.
- The final `./test_headless.sh` run exited 0 from deterministic SUMO behavior through live TraCI integration. It retained 68 stable traffic identities, zero mid-turn signal stops, 1.0 m lead-red p95, 16.5 m queue-tail p95, 0.0000 m crouch contact error and the complete prior rig/grip/camera/utility acceptance.

Fix 79's camera work and Fix 78's skeleton, animation, grounding, jobs, grip and society implementation were not structurally changed by this pass. Their acceptance evidence remains below as regression context.

## Fix 79 camera and infrastructure acceptance

- First-person view captures ordinary mouse motion for citizens and vehicles, allows full 360-degree yaw, and clamps pitch to -82/+78 degrees.
- Camera origin placement uses the target's projected world AABB plus a hard exterior clearance. The exhaustive gate audited 102 person assets and 68 vehicle assets, with 0.180 m minimum clearance and no camera inside a grown model envelope.
- General navigation uses MMB orbit, Shift+MMB pan, Ctrl+MMB dolly, bounded wheel zoom, RMB free-look, and the existing keyboard translation.
- Escape from each of the sixteen direct roadside feeds restores the establishing camera at a nearby three-quarter exterior position rather than resetting to an unrelated overview or leaving the viewport empty.
- Physical camera rigs now select the nearest collision-free site while retaining their assigned junction aim. All 16 cleared 214 building/tree obstacle assets, and all 16 Escape views passed the static-occluder gate.
- Power highlighting frames every pole, all sixteen live service-zone meters, and every power-meter cabinet used by technician job scenes. Cable overlays remain live. Meter frame margin and thickness now scale to the model instead of hiding compact cabinets inside oversized geometry.
- Eight fresh rendered QA frames cover vehicle forward/look-back, citizen look-back, selected/released street feed, technician meter highlight, live zone-meter highlight, and the shared utility-briefing meter in `screenshots/fix79_camera_power_qa/`.
- The complete `./test_headless.sh` rerun exited 0 after all Fix 79 changes, from the first deterministic SUMO test through the final live TraCI integration. The camera changes introduced no per-frame geometry scan; street-device collision resolution occurs once at startup. A matched runtime diagnostic measured Fix 79 at 15.01 ms p50 / 25.48 ms p95 versus Fix 78 at 15.31 ms p50 / 26.27 ms p95 in the same comparison, so no performance regression was detected.

Fix 78's skeleton, animation, grounding, job, grip, society and traffic implementation was not altered by this pass. The acceptance evidence below is retained and rerun as regression coverage.

## Acceptance outcome

The complete `./test_headless.sh` suite was run from its first SUMO test after the final Fix 79 camera, placement and highlight corrections and exited 0. The run covered traffic generation and signals, TraCI continuity, Godot scene startup, the society kernel, citizen activity and movement, rigs and animations, prop contact and grounding, spatial safety, all-target first-person bounds, all-feed street-camera release, selection/cameras, and water/power systems.

## Physical work and meter coverage

The presentation requirement is now enforced by live eligibility rather than merely assigning a job in the larger agenda:

- at least 10 citizens must currently be stationary and performing a whitelisted physical clip on their matching job prop;
- at least 5 of those must operate a water or power meter, including at least 2 of each kind;
- poles are explicitly ineligible meter-work targets;
- walking, carrying while walking, conversation, pointing, phone/note-only recording and passive proximity never satisfy the invariant.

The full run measured 13–16 simultaneous valid prop workers across 10 roles, 12 prop kinds and 13 physical poses. The dedicated meter test observed minimums of 5 meter workers, 3 power-meter workers and 2 water-meter workers. The new water-meter cabinet is grounded and participates in the same collision-safe station placement as other work props.

## Random Citizen selection

`Random Citizen` now queries the citizen's current embodied state. It rejects walking, travelling and yielding actors, then requires a currently visible physical prop/tool/item or active reciprocal interaction plus a readable stationary action. The acceptance test made 48 selections and found 48 non-walking active citizens across 11 activity kinds.

## Rejected forward-bend pose

The supplied failure image showed the standing `PickUp_Table`/forward-inspection action rotating the lower chain and lifting the legs. The semantic clip is now rebuilt from authored motion with a planted neutral pelvis, structural spine base, thighs, calves, feet and toes. Upper-spine, head and arm motion remains authored; the fix does not rotate bones procedurally at runtime.

Across all 32 rigs and nine time samples:

- maximum planted-chain translation drift was 0.0102 m;
- maximum planted-chain rotation drift was 0.64 degrees;
- minimum retained hand motion was 0.489 m;
- minimum retained head motion was 0.119 m.

The initial settled-pose gate missed a live scheduling defect: it sampled after the crouch had converged, while live animation evaluation could occur after the physics grounding pass. That left the rendered pose using the previous frame's root correction and produced a visible 0.45–0.57 m hover during crouch entry or reassignment.

The final pipeline has explicit ordering: navigation and assignment remain in physics; each Rocketbox `AnimationPlayer` evaluates early in the render frame; root-only foot locking runs immediately afterward; default camera/control readers then consume the final grounded transform. The delayed exponential root blend was removed because animation blending already provides the transition and a second blend makes the root trail the feet.

The new transition gate observed 531 live rendered crouch frames plus 3,552 explicit entry/hold/exit samples across all 32 rigs. It exercised poses requiring as much as 0.570 m of root correction and measured 0.0000 m maximum foot-contact error. The wider grounding test also found all 32 citizens grounded, including three crouched workers, with 0.000 m maximum error.

## Finger, edge and handle grips

The earlier attachment used the wrist and then the knuckle/palm transition. Both were rejected. Fix 78 now derives a fixed attachment-space finger cradle from the first articulated joints of the four non-thumb fingers. The object origin is not treated as the contact point: normalized, orientation-aware per-asset anchors put the actual side edge, upper edge, top, or handle at that cradle.

Flat devices and papers receive an additional gate: contact with only their thin face is invalid; one of the two broad dimensions must also be at a side edge. This prevents a phone, meter reader, document or notice from being centered through the palm.

The final 81-object audit measured:

| Check | Result |
| --- | ---: |
| held tools/items checked | 81 |
| minimum wrist-to-finger-grip distance | 0.122 m |
| minimum solid-object wrist/palm clearance | 0.0337 m |
| maximum wrist penetration into a solid object | 0.0000 m |
| maximum one-frame attachment/contact gap | 0.0289 m |
| maximum grip contact-surface depth | 0.0160 m |

The fresh 1280×720 evidence set includes notice, document, watering can, market goods, waste bag, parcel, meter reader and phone. Manual inspection accepted the meter reader and phone at a side/bottom edge, papers at a sheet edge, boxes at an upper/side edge, the waste bag at its top, and the watering can at its handle. Evidence is in `screenshots/fix78_finger_edge_grips_verified_final/`; its generator is `tests/render_fix78_hand_grips.gd`.

## Society, movement and placement regressions

- 200 persistent citizens, 80 households and 13 institutions passed deterministic kernel checks.
- 16 complete runtime work sequences, 18 communication phases, four visible custody transfers and six persisted custody histories were observed.
- All 12 opening-event participants released and dispersed; minimum sampled citizen separation was 1.48 m.
- Trip continuity completed 71 of 85 observed trips with 14 still active and a 0.775 m maximum physics-step displacement.
- The 150-sample spatial gate found zero citizen/building/road/prop/tree conflicts.
- All 198 visible props were grounded with zero overlaps; all 70 activity/event props stayed outside traffic corridors.
- All 32 citizens used the validated Rocketbox set: 19 distinct models, the intact 81-bone `Bip01` hierarchy and 62 semantic clips, without foreign skeletons, scale tracks, runtime IK or limb stretching.

## Traffic and camera regressions

The complete SUMO/TraCI path passed after the embodiment change:

- 68 stable rendered vehicle identities, zero stream replacements and 29 legal continuations;
- zero mid-turn red-light stops, emergency stops or harsh braking;
- lead red-light stop p95 of 1.0 m and queue-tail p95 of 16.5 m;
- 1,630 turning samples, 98 internal junction lanes, all 49 controllers and 84 perimeter flows;
- camera detachment before a pooled car hides, with a visible 2.2 s coast interval.

## Reproduce

From this Fix 78 directory:

```bash
./test_headless.sh
```

Regenerate the close grip evidence with:

```bash
runtime/linux-x86_64/godot --path . --script tests/render_fix78_hand_grips.gd --rendering-method gl_compatibility
```
