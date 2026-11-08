# Incident & Emergency-Response Architecture

This document is the contract for every incident, emergency vehicle and
responder in City Ground Zero. It exists because the previous attempts broke
the simulated world: dispatch cars appeared and vanished, responders floated
and walked through cars, traffic drove through crash scenes and fire/water
effects were blocky. Each rule below removes one of those failure classes at
its root instead of hiding it.

## 1. Authority — who owns what

| Concern | Authority | Notes |
| --- | --- | --- |
| Position/speed of every road vehicle (civilian **and** emergency) | SUMO via `sumo_bridge.py` | Godot never moves a road vehicle on its own |
| Crash choreography (approach, impact, post-impact slide, rest pose) | `incident_engine.py` inside the bridge | Uses TraCI only; outputs a deterministic rest pose |
| Lane closures, overtaking permission, junction police control, rerouting | `incident_engine.py` | Applied to SUMO, so traffic *physically* respects the scene |
| Emergency fleet: depots, patrols, dispatch, response mode, return | `incident_engine.EmergencyFleet` | A unit is always in exactly one state (§3) |
| Civilian vehicle meshes, interpolation, lane-change smoothing | `CityLife` | Renders exactly what SUMO reports |
| Emergency vehicle meshes, depot bays, driveway pull-out/in, lightbars | `EmergencyFleetPresenter` | Keyed by **unit id**, not SUMO id |
| Wreck damage, debris, fire/smoke/water FX, scene props, responder crews | `IncidentScenePresenter` | Presentation only; reacts to bridge state |
| Replay recording/playback | `IncidentRecorder` + `ReplayTheatre` | Separate render layer, never touches live nodes |
| Operator UI | `UnifiedControlRoom` → *Incidents* tab | Sends commands, shows read-only state |

## 2. Traffic foundation contract

These are prerequisites; without them no incident can look right.

* **Every simulated vehicle is rendered.** The civilian population is a closed,
  fixed-size fleet that enters at the city perimeter and then continues
  forever with legal `changeTarget` reroutes. There is no invisible traffic for
  rendered cars to queue behind, and no teleporting (`time-to-teleport = -1`;
  the bridge watchdog resolves genuine gridlock legally).
* **One geometry for physics and pixels.** SUMO `length/width` per vType equal
  the rendered model's normalized dimensions, and Godot places the model's
  centre at SUMO's body centre (`front − heading · length/2`). A visible contact
  is therefore a simulated contact.
* **Typed pools.** The packet carries a visual class (`car`, `taxi`, `bus`,
  `police`, `ambulance`, `fire`, `tow`, `utility`). Buses render as buses and
  civilian traffic never wears a police or ambulance body.
* **Opposite-lane overtaking.** The network is built with `--opposites.guess`.
  Civilians never overtake (`lcOpposite = 0`) except when the incident engine
  explicitly permits it for cars queued behind a blocked lane; emergency units in
  response mode may overtake. SUMO only overtakes when the opposite lane is
  clear for the whole manoeuvre. SUMO performs the lateral move in one step, so
  `CityLife` blends any lane-sized lateral jump over ~1 s instead of rejecting
  it.

## 3. Emergency unit lifecycle

```
 GARAGED ─dispatch→ MOBILIZING (crew walks out of the station)
    ↑                    │
    │              EXITING_DEPOT (Godot drives bay → curb hold point)
    │                    │  bridge inserts the SUMO vehicle at the curb
    │              EN_ROUTE (SUMO, response vType: lights, red-light
    │                    │   clearance driving, opposite-lane overtaking)
    │              ON_SCENE (SUMO stop at the planned scene position)
    │                    │
    │              DEPARTING (released; stays stopped until Godot reports
    │                    │   the whole crew aboard: `crew_aboard`)
    │              RETURNING (SUMO, normal vType, lights off)
    │                    │  SUMO arrival at the depot curb position
 ENTERING_DEPOT (Godot drives curb → bay) ──────────────┘
 PATROL (police only): on the network permanently, law-abiding
```

A unit's visual node never disappears and never pops: in the garage and on the
driveway it is posed by Godot; on the network it is posed by SUMO; the hand-off
blends between the curb hold point and the SUMO lane position. Response mode is
a live vType swap (`police` → `police_response`), validated to pass red lights
with zero collisions (the SUMO bluelight device alone produced collisions in the
same test and is not used).

Special cases, each a legal path rather than a shortcut:

* **Station staging** — a garaged unit whose bay is within 45 m of a scene on
  its own street works it from the bay (crew on foot, hose from the parked
  engine). Without it the scene block can cover the station's exit points and
  the engines could never pull out.
* **Queued arrival** — a unit held for 20 s in a stationary queue within 70 m
  of its stop is on scene where it stands; its crew walks in along the kerb.
  The scene block only counts units contiguous with the wrecks, so traffic
  queued between them is still released by the flagger.
* **Return from the oncoming lane** — a unit halted for 6 s in the oncoming
  lane changes back (a SUMO lane change, never a teleport) to the same spot of
  its own lane once that spot is clear beyond the minimum gap.
* **Depot arrival** — each bay's arrival point (where a returning unit waits
  to turn in) leaves the whole vehicle on the edge, never its rear in the
  junction box (enforced by `tools/compute_emergency_depots.gd`).
* **Mutual aid** — a request the local fleet cannot serve for 90 s (all units
  busy, or trapped behind the very scene that needs them) is answered by a
  contracted `AID-*` unit that enters through the inbound perimeter gate
  nearest the scene and leaves through an outbound gate (`left_city`
  hand-off releases any wreck on its flatbed). At most four aid units are
  active at once, each request calls at most one, and a unit enters only
  through a gate whose first 20 m are clear.

## 4. Incident lifecycle

```
ARMED → (choreography) → IMPACT/ONSET → DISPATCHED → RESPONDING → ON_SCENE
      → WORKING → CLEARING → RESOLVED
```

* **Armed**: the engine selects participants that satisfy the type's physical
  preconditions (both rendered, correct relative geometry, enough room to stop
  responders). Selection retries with new candidates and relaxed constraints;
  a pinned car or junction that offers no safe geometry for 75 s is released
  and the nearest feasible one used, so a trigger always produces an incident.
* **Scene window**: midblock scenes come to rest 26–48 m into their edge
  (selection bounds for crashes, a target-based coast for breakdowns, fires
  and lost loads), so the response block fits behind the scene on the same
  edge and the flagger line and tow slots fit ahead of it.
* **Choreography**: TraCI speed control produces the real manoeuvre (late
  braking, red-light run, unsafe overtake). The unsafe overtaker is a
  speeding driver (slow car + 9 m/s, at most 72 km/h); a pair is chosen only
  when the predicted clip point lies inside the scene window and the whole
  oncoming lane, the far junction and its approaches are empty. Oncoming
  traffic appearing, a cut-in that does not take within 2 s, or the junction
  closing in abandons the attempt; the overtaker falls back behind the car
  beside it before pulling in (never stopping in the oncoming lane). Impact is detected from oriented
  vehicle boxes, contact is snapped to 2 cm, and a deterministic 2-D impulse
  solver produces the post-impact slide which is streamed as a bounded pose
  offset (yaw/lateral), clamped so the wreck never leaves the closed area.
* **Scene control**: the engine holds wrecks with SUMO stops (so following cars
  treat them as real obstacles), closes the lane for routing, runs a stop/go
  flagger for the queue, and turns a blocked junction into a
  police-controlled program that only releases movements whose paths do not
  cross the wreck footprint. The officer also waves the first vehicle of an
  approach whose movement is closed onto an open one and it is re-routed from
  there, so no lane freezes behind a vehicle wanting a closed turn. When the
  scene clears, signals return to their program through a 4 s all-red so
  nothing still crossing meets the program's first green. One lane
  closure (one stop/go flagger) per closed lane; crash choreography keeps the
  impact mid-block so the queue always has room to merge back.
* **Guided pass**: SUMO's own opposite-lane overtaking is never enabled (every
  vType has `lcOpposite="0"`); it passes at full speed with no controlled
  return. The flagger holds oncoming traffic 16 m beyond the scene (never
  closer than 12 m to the junction it came from), then
  sends the first queued vehicle into the empty oncoming lane, one at a time:
  the whole manoeuvre area must be empty, the passer drives at 7 m/s with
  safe-speed checks on, and returns only where its own lane is clear beyond
  SUMO's 2.5 m minimum gap. SUMO refuses an opposite change for a vehicle
  with a stop ahead, a route that turns or ends on the edge, or a response
  vType, so for the pass the route is the edge plus its straight successor,
  the stop is lifted and a responder drives as its base type; all three are
  restored once it is back in lane. A vehicle that does not take the lane
  within 1.5 s stays queued. A car is waved only when there is room for it
  beyond the scene (own lane empty to the flagger line; a unit's slot free),
  nothing coming could fail to stop at the flagger line, and not in the last
  3 s of a go phase. Held oncoming traffic is never released while a passer
  is out, even when the scene itself is released (the closure retires only
  once its last passer is back). One tow slot per scene; tows use it in turn. Vehicles whose stop is at the scene are not
  waved: they join the block behind it. No vehicle is ever teleported out of
  an oncoming lane (moveTo of an opposite-driving vehicle corrupts SUMO).
* **Clearing**: wrecks either drive away damaged (minor) or are winched onto a
  flatbed — the SUMO vehicle is removed only after the visual car is attached
  to the tow truck, then carried to the tow yard. Closures lift, overtaking
  permission is revoked, units return to base. *Resolve now* fast-forwards the
  same path (wreck released or loaded, crews board, units return) — in any
  phase, including before the first unit arrives — and never deletes anything
  in view.
* **Red-light crossing**: a responding (or patient-carrying) unit commits to
  a red only when no green-light arrival could reach the junction within 8 s,
  then crosses without yielding at an internal waiting point (never standing
  in the box), and hands control back once it is through.
* **Last resort**: a non-drivable vehicle with no tow on scene 150 s after
  the police report, and no assigned tow still making progress, is driven
  clear under escort (the same path *Resolve* takes), so a scene always
  clears.
* **Police junction control** bans left turns and U-turns (a yielding
  left-turner would wait inside the box for the whole incident); that
  traffic is waved onto an open movement.
* **Watchdogs**: every phase has a timeout with a defined, legal fallback
  (re-route a stuck unit, release a wreck, open the junction). All TraCI
  calls tolerate vehicles that vanished between steps.

## 5. Responder crews

Crew members are Rocketbox avatars using the same Bip01 motion library,
root-only foot locking and finger-cradle grips as citizens. They exit from the
vehicle's door side, walk only inside the scene's safe zone (closed lane,
shoulder, sidewalk), path around oriented vehicle boxes and each other, face
their work target, perform authored clips (directing traffic, documenting,
crouched casualty care, hose handling, winch control) and board the vehicle
again — on the side they got out — before it leaves; the vehicle physically
waits for them (`departing`). Midblock, crews always exit on the kerb side, and
the walkable door strips reach 1.3 m past each end of a stopped vehicle so a
crew can always walk around it. The officer directing traffic stands upstream
of the whole block and steps back when another unit parks behind it. Citizens treat crew members as obstacles and
crews yield to citizens.

The Incidents tab's **Watch** frames the scene from the clearest of 32
candidate viewpoints, scored on five rays across the scene against every
building, tree, pole and prop (never from inside or against a wall). A
building fire is framed from across the street, looking at the burning
façade; the building itself is the subject, not an occluder.

## 6. Replay

`IncidentRecorder` keeps a rolling 20 s buffer of every vehicle transform at
the packet rate. On impact the window `[−10 s, +8 s]` around the contact point
is frozen into the incident. `ReplayTheatre` instantiates ghost copies on
render layer 20, which live cameras never draw; the replay camera draws the
static city plus layer 20 and excludes live traffic, people and FX. Playback
supports pause, scrub, slow motion and several camera rigs, with a HUD showing
the recorded speeds and the signal state the striking driver faced.

## 7. Bridge ↔ Godot protocol additions

Per vehicle: `x/z` (body centre), `len`, `wid`, `cls`, optional `unit`,
`lights` (bit 1 lightbar, 2 hazards, 4 siren), `role`, `pose` `[dx, dz, dyaw]`
and `dmg` `{zone, level}`.

Top level: `fleet` (unit states), `incidents` (id, type, phase, location,
participants, units, closure, impact time/point, timeline), `handoffs`
(SUMO vehicles leaving the network into Godot custody: towed wrecks, units
entering depots, mutual-aid units leaving the city) and `acks` for operator
requests. Godot reports `unit_at_curb`, `unit_parked`, `crew_aboard`,
`driver_ready`, `wreck_loaded` and `hold_junction`; every Godot-owned phase
falls back to a timeout only while Godot is silent (headless runs).

Commands on the existing control UDP port:
`{"command":"incident","action":"trigger|resolve|dispatch|escalate", ...}`
with an idempotent `request` id that is acknowledged in `acks`.
