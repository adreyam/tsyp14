"""Deterministic 2-D geometry and impact physics for incident choreography.

All coordinates are SUMO metres (x east, y north). Headings follow SUMO:
degrees clockwise from north, so the unit vector is (sin a, cos a). A body's
position is its geometric centre.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math

FRICTION_DECELERATION = 6.8   # m/s², tyres skidding on dry asphalt
ANGULAR_DAMPING = 3.2         # rad/s² equivalent spin decay
MAX_SLIDE_SECONDS = 3.0
MAX_SLIDE_DISTANCE = 5.5
RESTITUTION = 0.12
SURFACE_FRICTION = 0.35


def heading_vector(angle_degrees: float) -> tuple[float, float]:
    radians = math.radians(angle_degrees)
    return math.sin(radians), math.cos(radians)


def body_centre(front: tuple[float, float], angle_degrees: float, length: float) -> tuple[float, float]:
    hx, hy = heading_vector(angle_degrees)
    return front[0] - hx * length * 0.5, front[1] - hy * length * 0.5


@dataclass
class Box:
    x: float
    y: float
    angle: float  # degrees, SUMO convention
    length: float
    width: float

    def axes(self) -> tuple[tuple[float, float], tuple[float, float]]:
        hx, hy = heading_vector(self.angle)
        return (hx, hy), (hy, -hx)

    def corners(self) -> list[tuple[float, float]]:
        (fx, fy), (rx, ry) = self.axes()
        half_length = self.length * 0.5
        half_width = self.width * 0.5
        output = []
        for along, side in ((1, 1), (1, -1), (-1, -1), (-1, 1)):
            output.append((self.x + fx * half_length * along + rx * half_width * side, self.y + fy * half_length * along + ry * half_width * side))
        return output

    def contains(self, point: tuple[float, float], margin: float = 0.0) -> bool:
        (fx, fy), (rx, ry) = self.axes()
        dx, dy = point[0] - self.x, point[1] - self.y
        return abs(dx * fx + dy * fy) <= self.length * 0.5 + margin and abs(dx * rx + dy * ry) <= self.width * 0.5 + margin

    def grown(self, margin: float) -> "Box":
        return Box(self.x, self.y, self.angle, self.length + margin * 2.0, self.width + margin * 2.0)


def _project(corners: list[tuple[float, float]], axis: tuple[float, float]) -> tuple[float, float]:
    values = [corner[0] * axis[0] + corner[1] * axis[1] for corner in corners]
    return min(values), max(values)


def overlap(a: Box, b: Box) -> tuple[bool, tuple[float, float], float]:
    """Separating-axis test. Returns (overlapping, normal from a to b, depth)."""
    corners_a = a.corners()
    corners_b = b.corners()
    best_depth = math.inf
    best_axis = (1.0, 0.0)
    for axis in (*a.axes(), *b.axes()):
        min_a, max_a = _project(corners_a, axis)
        min_b, max_b = _project(corners_b, axis)
        depth = min(max_a, max_b) - max(min_a, min_b)
        if depth <= 0.0:
            return False, (0.0, 0.0), 0.0
        if depth < best_depth:
            best_depth = depth
            best_axis = axis
    direction = (b.x - a.x, b.y - a.y)
    if direction[0] * best_axis[0] + direction[1] * best_axis[1] < 0.0:
        best_axis = (-best_axis[0], -best_axis[1])
    return True, best_axis, best_depth


def segment_hits_box(start: tuple[float, float], end: tuple[float, float], box: Box, margin: float, samples: int = 24) -> bool:
    for index in range(samples + 1):
        t = index / samples
        if box.contains((start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t), margin):
            return True
    return False


def polyline_hits_box(points: list[tuple[float, float]], box: Box, margin: float) -> bool:
    return any(segment_hits_box(points[index], points[index + 1], box, margin) for index in range(len(points) - 1))


@dataclass
class Body:
    """A vehicle sliding after impact; offsets are relative to its impact pose."""

    vehicle_id: str
    mass: float
    inertia: float
    vx: float
    vy: float
    spin: float  # rad/s, positive = clockwise (SUMO angle increases)
    dx: float = 0.0
    dy: float = 0.0
    dyaw: float = 0.0
    resting: bool = False
    elapsed: float = 0.0
    trail: list = field(default_factory=list)

    @staticmethod
    def for_vehicle(vehicle_id: str, length: float, width: float, speed: float, angle: float) -> "Body":
        mass = 1450.0 * (length / 4.7) ** 2
        inertia = mass * (length * length + width * width) / 12.0
        hx, hy = heading_vector(angle)
        return Body(vehicle_id, mass, inertia, hx * speed, hy * speed, 0.0)

    def step(self, dt: float) -> None:
        if self.resting:
            return
        self.elapsed += dt
        speed = math.hypot(self.vx, self.vy)
        if speed > 0.0:
            reduced = max(0.0, speed - FRICTION_DECELERATION * dt)
            self.vx *= reduced / speed
            self.vy *= reduced / speed
        if self.spin != 0.0:
            reduced_spin = max(0.0, abs(self.spin) - ANGULAR_DAMPING * dt)
            self.spin = math.copysign(reduced_spin, self.spin)
        self.dx += self.vx * dt
        self.dy += self.vy * dt
        self.dyaw += self.spin * dt
        distance = math.hypot(self.dx, self.dy)
        if distance > MAX_SLIDE_DISTANCE:
            self.dx *= MAX_SLIDE_DISTANCE / distance
            self.dy *= MAX_SLIDE_DISTANCE / distance
            self.vx = self.vy = 0.0
        if (math.hypot(self.vx, self.vy) < 0.05 and abs(self.spin) < 0.03) or self.elapsed >= MAX_SLIDE_SECONDS:
            self.vx = self.vy = self.spin = 0.0
            self.resting = True


def _cross(ax: float, ay: float, bx: float, by: float) -> float:
    return ax * by - ay * bx


def resolve_impact(a: Body, box_a: Box, b: Body, box_b: Box, normal: tuple[float, float], contact: tuple[float, float]) -> float:
    """Applies an inelastic impulse with friction at the contact point.

    Returns the impact severity as the change of velocity (m/s) of the
    lighter body, which drives damage level and injuries.
    """
    nx, ny = normal
    rax, ray = contact[0] - box_a.x, contact[1] - box_a.y
    rbx, rby = contact[0] - box_b.x, contact[1] - box_b.y
    # Velocity of each body at the contact point (spin is clockwise-positive,
    # i.e. negative counter-clockwise; ω × r for CW spin is (ω ry, -ω rx)).
    vax = a.vx + a.spin * ray
    vay = a.vy - a.spin * rax
    vbx = b.vx + b.spin * rby
    vby = b.vy - b.spin * rbx
    relative_x, relative_y = vax - vbx, vay - vby
    approach = relative_x * nx + relative_y * ny
    if approach <= 0.0:
        return 0.0
    ra_n = _cross(rax, ray, nx, ny)
    rb_n = _cross(rbx, rby, nx, ny)
    denominator = 1.0 / a.mass + 1.0 / b.mass + ra_n * ra_n / a.inertia + rb_n * rb_n / b.inertia
    impulse = (1.0 + RESTITUTION) * approach / denominator
    before_a = (a.vx, a.vy)
    before_b = (b.vx, b.vy)
    a.vx -= impulse * nx / a.mass
    a.vy -= impulse * ny / a.mass
    b.vx += impulse * nx / b.mass
    b.vy += impulse * ny / b.mass
    # CW-positive spin: torque sign is the negative of the 2-D cross product.
    a.spin += impulse * ra_n / a.inertia
    b.spin -= impulse * rb_n / b.inertia
    # Tangential friction impulse (bounded by Coulomb).
    tx, ty = -ny, nx
    tangential = relative_x * tx + relative_y * ty
    friction = max(-SURFACE_FRICTION * impulse, min(SURFACE_FRICTION * impulse, tangential / (1.0 / a.mass + 1.0 / b.mass)))
    a.vx -= friction * tx / a.mass
    a.vy -= friction * ty / a.mass
    b.vx += friction * tx / b.mass
    b.vy += friction * ty / b.mass
    for body in (a, b):
        body.spin = max(-2.4, min(2.4, body.spin))
    delta_a = math.hypot(a.vx - before_a[0], a.vy - before_a[1])
    delta_b = math.hypot(b.vx - before_b[0], b.vy - before_b[1])
    return max(delta_a, delta_b)


def contact_point(a: Box, b: Box) -> tuple[float, float]:
    """Average of the corners of either box that lie inside the other."""
    inside = [corner for corner in a.corners() if b.contains(corner, 0.05)] + [corner for corner in b.corners() if a.contains(corner, 0.05)]
    if not inside:
        return (a.x + b.x) * 0.5, (a.y + b.y) * 0.5
    return sum(point[0] for point in inside) / len(inside), sum(point[1] for point in inside) / len(inside)


def damage_zone(box: Box, contact: tuple[float, float]) -> str:
    (fx, fy), (rx, ry) = box.axes()
    dx, dy = contact[0] - box.x, contact[1] - box.y
    along = (dx * fx + dy * fy) / (box.length * 0.5)
    side = (dx * rx + dy * ry) / (box.width * 0.5)
    if abs(along) >= abs(side) * 0.8:
        return "front" if along > 0 else "rear"
    return "right" if side > 0 else "left"
