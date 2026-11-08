"""Roads closed to traffic: the street of an accident until the tow truck has taken the car, and the four entrances of
a zone hit by an earthquake or a flood.

A closed street is closed for real, not only "slower":
* the traffic lights at the junction before it show red for every movement into it (a "closure" copy of the
  junction's program, see smart_signals.py), so nobody turns in;
* every car whose route still goes through it is sent another way, checked every 2 s (the city keeps extending trips,
  and those extensions are corrected too);
* responders (police, ambulance, fire, tow) still get through: emergency priority gives them their green.

A zone lockdown closes the four streets that lead into the zone (each zone is a 2 x 2 block square: its four inner
streets meet the boundary roads at four entrances) the same way; police cars (and a fire engine when one can be
spared) park across them, and Godot draws barriers there (zone_roadblocks.gd, from the "lockdowns" list of the bridge
state). New cars entering the city do not use a gate that leads into a closed zone.
"""

from __future__ import annotations

import math

from incident_types import WORLD
from road_control import CLOSED_TRAVEL_TIME, DEFAULT_TRAVEL_TIME

REROUTE_EVERY = 2.0
BARRIER_POS = 4.0          # metres into the closed street, from the boundary junction
UNIT_STOP_POS = 16.0       # the police car parks just inside the barrier
EVACUATION_GREEN_DISTANCE = 60.0   # a red light this close ahead of an evacuating car turns green for it
EVACUATION_SPEED_FACTOR = 1.35   # cars leaving a closed zone drive faster than usual (up to their vehicle's top speed)
ZONE_SIZE = 190.0
ZONE_IDS = "ABCDEFGHIJKLMNOP"


def zone_centre_sumo(zone: str) -> tuple[float, float]:
    index = ZONE_IDS.index(zone)
    column, row = index % 4, index // 4
    return column * ZONE_SIZE + ZONE_SIZE / 2.0, row * ZONE_SIZE + ZONE_SIZE / 2.0


class RoadBlocks:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.traci = engine.traci
        self.blocked: dict[str, set[str]] = {}          # owner -> edges no car may enter
        self.lockdowns: dict[str, dict] = {}            # zone -> {reason, since, entrances: [...]}
        self._next_reroute = 0.0
        self._junction_positions: dict[str, tuple[float, float]] | None = None
        self.evacuating: dict[str, tuple[str, float]] = {}   # vehicle -> (zone, its usual speed factor)
        self.evacuated = 0

    # -- closed streets ----------------------------------------------------------------------

    def block(self, owner: str, edge: str) -> None:
        edges = self.blocked.setdefault(owner, set())
        if edge in edges:
            return
        edges.add(edge)
        self.engine.smart_signals.close_into(owner, edge)
        if owner.startswith("LOCKDOWN-"):
            # (an accident's street already carries its closed travel time from road_control.close_lane)
            try:
                self.traci.edge.adaptTraveltime(edge, CLOSED_TRAVEL_TIME)
            except self.traci.exceptions.TraCIException:
                pass
        self._next_reroute = 0.0

    def unblock(self, owner: str) -> None:
        edges = self.blocked.pop(owner, set())
        if not edges:
            return
        self.engine.smart_signals.open_owner(owner)
        still = self.blocked_edges()
        if owner.startswith("LOCKDOWN-"):
            for edge in edges - still:
                try:
                    self.traci.edge.adaptTraveltime(edge, DEFAULT_TRAVEL_TIME)
                except self.traci.exceptions.TraCIException:
                    pass

    def blocked_edges(self) -> set[str]:
        return set().union(*self.blocked.values()) if self.blocked else set()

    def step(self, snapshot: dict) -> None:
        if self.lockdowns:
            self._turn_away_departures(snapshot)
        if not self.blocked or self.engine.now < self._next_reroute:
            return
        self._next_reroute = self.engine.now + REROUTE_EVERY
        self._evacuate()
        closed = self.blocked_edges()
        lockdown_edges = {entrance["edge"] for record in self.lockdowns.values() for entrance in record["entrances"]}
        if lockdown_edges:
            # A car still waiting to enter the simulation right on a closed zone entrance (not drawn yet) never
            # starts there; the city replaces it with a car through an open gate.
            for vehicle_id in self.traci.simulation.getPendingVehicles():
                try:
                    if vehicle_id.startswith("civ") and self.traci.vehicle.getRoute(vehicle_id)[0] in lockdown_edges:
                        self.traci.vehicle.remove(vehicle_id)
                except (self.traci.exceptions.TraCIException, IndexError):
                    continue
        exempt = set(self.engine.fleet.vehicle_to_unit) | self.engine.busy_vehicles()
        for vehicle_id, data in snapshot.items():
            if vehicle_id in exempt or "#" in vehicle_id or data["type"] not in ("car", "taxi", "bus"):
                continue
            road = data["road"]
            if road in closed:
                continue                                       # already inside: it drives out
            try:
                route = self.traci.vehicle.getRoute(vehicle_id)
                index = self.traci.vehicle.getRouteIndex(vehicle_id)
            except self.traci.exceptions.TraCIException:
                continue
            ahead = route[index + 1:] if index >= 0 else route
            if not any(edge in closed for edge in ahead):
                continue
            try:
                self.traci.vehicle.rerouteTraveltime(vehicle_id)
                route = self.traci.vehicle.getRoute(vehicle_id)
                index = self.traci.vehicle.getRouteIndex(vehicle_id)
                if any(edge in closed for edge in route[index + 1:]):
                    self._send_elsewhere(vehicle_id, road, closed)
            except self.traci.exceptions.TraCIException:
                continue

    def _turn_away_departures(self, snapshot: dict) -> None:
        """A scheduled car that has just started right on a closed zone entrance (at a city gate) is taken off before it
        is drawn; the city replaces it with a car through an open gate."""
        entrances = {entrance["edge"] for record in self.lockdowns.values() for entrance in record["entrances"]}
        for vehicle_id in self.traci.simulation.getDepartedIDList():
            if not vehicle_id.startswith("civ"):
                continue
            try:
                if self.traci.vehicle.getRoadID(vehicle_id) in entrances and self.traci.vehicle.getLanePosition(vehicle_id) < 12.0:
                    # (at the gate end of the street; a car leaving a car park there is let out, and evacuated)
                    self.traci.vehicle.remove(vehicle_id)
                    self.engine.fleet.removed_vehicle_ids.add(vehicle_id)
                    snapshot.pop(vehicle_id, None)
            except self.traci.exceptions.TraCIException:
                continue

    def _send_elsewhere(self, vehicle_id: str, road: str, closed: set[str]) -> None:
        """Its destination is behind the closure: it ends its trip somewhere reachable instead (the city then
        extends it as usual)."""
        if road.startswith(":"):
            return
        try:
            lane = self.traci.vehicle.getLaneID(vehicle_id)
            options = [link[0].rsplit("_", 1)[0] for link in self.traci.lane.getLinks(lane)]
        except self.traci.exceptions.TraCIException:
            return
        for edge in options:
            if edge.startswith(":") or edge in closed:
                continue
            try:
                self.traci.vehicle.setRoute(vehicle_id, [road, edge])
                return
            except self.traci.exceptions.TraCIException:
                continue

    # -- zone lockdown -----------------------------------------------------------------------

    def zone_streets(self, zone: str) -> tuple[set[str], list[tuple[str, list[str]]]]:
        """The zone's inner streets (into and out of its centre junction), and its ways out: each street out of the
        centre with the streets beyond it, outside the zone."""
        entrances = self.entrances(zone)
        if not entrances:
            return set(), []
        centre = self.traci.edge.getToJunction(entrances[0])
        outgoing = [edge for edge in self.traci.junction.getOutgoingEdges(centre) if not edge.startswith(":")]
        exits = []
        for edge in outgoing:
            boundary = self.traci.edge.getToJunction(edge)
            beyond = [after for after in self.traci.junction.getOutgoingEdges(boundary)
                      if not after.startswith(":") and self.traci.edge.getToJunction(after) != centre]
            exits.append((edge, beyond))
        return set(entrances) | set(outgoing), exits

    def _evacuate(self) -> None:
        """Every civilian car inside a closed zone leaves it at once by the nearest way out, a little faster than it
        usually drives; it gets its usual speed back once it is outside."""
        inside_now: dict[str, str] = {}
        for zone, record in self.lockdowns.items():
            streets = record.get("streets") or set()
            exits = record.get("exits") or []
            for street in streets:
                try:
                    vehicles = self.traci.edge.getLastStepVehicleIDs(street)
                except self.traci.exceptions.TraCIException:
                    continue
                for vehicle_id in vehicles:
                    if not vehicle_id.startswith("civ") or vehicle_id in self.engine.fleet.vehicle_to_unit:
                        continue
                    inside_now[vehicle_id] = zone
                    if vehicle_id not in self.evacuating:
                        self._lead_out(vehicle_id, zone, street, streets, exits)
        for vehicle_id in list(inside_now):
            if vehicle_id in self.evacuating:
                self._green_ahead(vehicle_id)
        for vehicle_id in [vehicle for vehicle in self.evacuating if vehicle not in inside_now]:
            zone, usual = self.evacuating.pop(vehicle_id)
            try:
                road = self.traci.vehicle.getRoadID(vehicle_id)
                if road.startswith(":"):
                    self.evacuating[vehicle_id] = (zone, usual)   # still crossing the boundary junction
                    continue
                self.traci.vehicle.setSpeedFactor(vehicle_id, usual)
            except self.traci.exceptions.TraCIException:
                pass                                             # it has left the city
            self.evacuated += 1

    def _green_ahead(self, vehicle_id: str) -> None:
        """A red light just ahead of a car leaving the zone turns green for it (SMART-SIG evacuation hold)."""
        try:
            upcoming = self.traci.vehicle.getNextTLS(vehicle_id)
        except self.traci.exceptions.TraCIException:
            return
        if not upcoming:
            return
        tls, link_index, distance, state = upcoming[0]
        if distance <= EVACUATION_GREEN_DISTANCE and state in "rRyYu":
            self.engine.smart_signals.evacuation_green(tls, link_index, self.engine.now)

    def _lead_out(self, vehicle_id: str, zone: str, street: str, streets: set[str], exits) -> None:
        try:
            usual = self.traci.vehicle.getSpeedFactor(vehicle_id)
        except self.traci.exceptions.TraCIException:
            return
        best = None
        closed = self.blocked_edges()
        for out_edge, beyond in exits:
            for after in beyond:
                if after in closed:
                    continue
                try:
                    route = self.traci.simulation.findRoute(street, after)
                except self.traci.exceptions.TraCIException:
                    continue
                edges = list(route.edges)
                if not edges or edges[0] != street or any(edge in closed for edge in edges[1:]):
                    continue
                if best is None or route.travelTime < best[0]:
                    best = (route.travelTime, edges)
        try:
            if best is not None:
                self.traci.vehicle.setRoute(vehicle_id, best[1])
            self.traci.vehicle.setSpeedFactor(vehicle_id, max(usual, EVACUATION_SPEED_FACTOR))
        except self.traci.exceptions.TraCIException:
            return
        self.evacuating[vehicle_id] = (zone, usual)

    def entrances(self, zone: str) -> list[str]:
        """The four streets into the zone: those that end at the junction in the middle of the zone."""
        cx, cy = zone_centre_sumo(zone)
        positions = self._junctions()
        centre = min(positions, key=lambda junction: math.hypot(positions[junction][0] - cx, positions[junction][1] - cy))
        if math.hypot(positions[centre][0] - cx, positions[centre][1] - cy) > 30.0:
            return []
        return [edge for edge in self.traci.junction.getIncomingEdges(centre) if not edge.startswith(":")]

    def _junctions(self) -> dict[str, tuple[float, float]]:
        if self._junction_positions is None:
            self._junction_positions = {junction: self.traci.junction.getPosition(junction)
                                        for junction in self.traci.junction.getIDList() if not junction.startswith(":")}
        return self._junction_positions

    def lock_zone(self, zone: str, reason: str) -> str:
        zone = zone.upper()
        if len(zone) != 1 or zone not in ZONE_IDS:
            return "bad zone"
        if zone in self.lockdowns:
            self.lockdowns[zone]["reason"] = reason or self.lockdowns[zone]["reason"]
            return f"zone {zone} already closed"
        owner = f"LOCKDOWN-{zone}"
        entrances = []
        for edge in self.entrances(zone):
            # (no lane permissions: SUMO quits outright when a car is scheduled to start on a forbidden lane)
            self.block(owner, edge)
            entrances.append(self._entrance_record(edge))
        streets, exits = self.zone_streets(zone)
        self.lockdowns[zone] = {"zone": zone, "reason": reason or "emergency", "since": round(self.engine.now, 1),
                                "entrances": entrances, "units": {}, "streets": streets, "exits": exits}
        self._post_units(zone)
        self._evacuate()
        self.engine.events.push(self.engine.now, {"kind": "zone_lockdown", "zone": zone, "reason": reason,
                                                   "text": f"Zone {zone} closed ({reason}): its {len(entrances)} entrances are blocked, "
                                                           f"{sum(1 for z, _ in self.evacuating.values() if z == zone)} cars inside are leaving"})
        return f"zone {zone} closed: {len(entrances)} entrances blocked"

    def reopen_zone(self, zone: str = "", reason: str = "") -> str:
        zones = [zone.upper()] if zone else [z for z, record in self.lockdowns.items() if not reason or record["reason"] == reason]
        opened = []
        for letter in zones:
            record = self.lockdowns.pop(letter, None)
            if record is None:
                continue
            owner = f"LOCKDOWN-{letter}"
            self.unblock(owner)
            for unit_id in record["units"].values():
                unit = self.engine.fleet.units.get(unit_id)
                if unit is not None and unit.incident_id == owner:
                    self.engine.fleet.release(unit, self.engine.now)
            for vehicle_id, (zone_of, usual) in list(self.evacuating.items()):
                if zone_of == letter:
                    self.evacuating.pop(vehicle_id)
                    try:
                        self.traci.vehicle.setSpeedFactor(vehicle_id, usual)
                    except self.traci.exceptions.TraCIException:
                        pass
            opened.append(letter)
            self.engine.events.push(self.engine.now, {"kind": "zone_reopened", "zone": letter, "text": f"Zone {letter} reopened"})
        return f"zone(s) {', '.join(opened)} reopened" if opened else "no zone was closed"

    def _entrance_record(self, edge: str) -> dict:
        try:
            x, y = self.traci.simulation.convert2D(edge, BARRIER_POS, 0)
            x2, y2 = self.traci.simulation.convert2D(edge, BARRIER_POS + 2.0, 0)
        except self.traci.exceptions.TraCIException:
            x = y = x2 = y2 = 0.0
        length = math.hypot(x2 - x, y2 - y) or 1.0
        return {"edge": edge, "x": round(x - WORLD, 2), "z": round(y - WORLD, 2),
                "dx": round((x2 - x) / length, 3), "dz": round((y2 - y) / length, 3), "unit": ""}

    def _post_units(self, zone: str) -> None:
        """A police car across each entrance while there are police cars to spare (one stays free for incidents);
        a fire engine on a remaining entrance when one can be spared too. The other entrances get barriers only."""
        record = self.lockdowns[zone]
        owner = f"LOCKDOWN-{zone}"
        for entrance in record["entrances"]:
            for kind in ("police", "fire"):
                free = [unit for unit in self.engine.fleet.units.values() if unit.kind == kind and unit.is_available
                        and not unit.id.startswith("AID-")]
                if len(free) <= 1:
                    continue
                target = (entrance["x"] + WORLD, entrance["z"] + WORLD)
                unit = self.engine.fleet.select_unit(kind, target)
                if unit is None or not unit.is_available:
                    continue
                self.engine.fleet.dispatch(unit, {"edge": entrance["edge"], "pos": UNIT_STOP_POS}, owner, self.engine.now)
                entrance["unit"] = unit.id
                record["units"][entrance["edge"]] = unit.id
                break

    def public(self) -> list[dict]:
        return [{"zone": record["zone"], "reason": record["reason"], "since": record["since"],
                 "entrances": record["entrances"],
                 "evacuating": sum(1 for zone, _ in self.evacuating.values() if zone == record["zone"])}
                for record in self.lockdowns.values()]
