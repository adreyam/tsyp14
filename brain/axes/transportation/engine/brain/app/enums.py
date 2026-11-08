"""Status values (plain string constants, stored as text in the database)."""


class SessionStatus:
    PENDING = "PENDING"            # session created, no space assigned yet
    ASSIGNED = "ASSIGNED"          # space reserved, ticket shown, vehicle driving to it
    PARKED = "PARKED"              # magnetic sensor + local RFID confirmed the right space
    MISPLACED = "MISPLACED"        # vehicle identified in a space it was not assigned
    VERIFICATION = "VERIFICATION"  # inconsistency, needs the administrator
    EXITED = "EXITED"              # closed normally
    CANCELLED = "CANCELLED"        # closed by the administrator without an exit


ACTIVE_SESSION_STATUSES = (
    SessionStatus.PENDING,
    SessionStatus.ASSIGNED,
    SessionStatus.PARKED,
    SessionStatus.MISPLACED,
    SessionStatus.VERIFICATION,
)


class VerificationReason:
    SPACE_VACANT = "SPACE_VACANT"  # vehicle left its space and did not reach the exit


class SpaceStatus:
    AVAILABLE = "AVAILABLE"
    RESERVED = "RESERVED"          # assigned to a session, vehicle not there yet
    OCCUPIED = "OCCUPIED"
    RELEASING = "RELEASING"        # session closed, waiting for the magnetic sensor to confirm empty
    OUT_OF_SERVICE = "OUT_OF_SERVICE"


class CardStatus:
    AVAILABLE = "AVAILABLE"
    IN_USE = "IN_USE"
    LOST = "LOST"
    OUT_OF_SERVICE = "OUT_OF_SERVICE"


class VehicleCategory:
    REGISTERED = "REGISTERED"
    VISITOR = "VISITOR"


class LaneKind:
    ENTRANCE = "ENTRANCE"
    EXIT = "EXIT"


class LaneState:
    IDLE = "IDLE"
    WAITING_RFID = "WAITING_RFID"
    # entrance
    AWAITING_TICKET_REQUEST = "AWAITING_TICKET_REQUEST"
    AWAITING_CARD = "AWAITING_CARD"
    TICKET_SHOWN = "TICKET_SHOWN"
    BLOCKED = "BLOCKED"
    # exit
    AWAITING_CARD_RETURN = "AWAITING_CARD_RETURN"
    EXIT_AUTHORIZED = "EXIT_AUTHORIZED"
    MANUAL_VERIFICATION = "MANUAL_VERIFICATION"


class Severity:
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
