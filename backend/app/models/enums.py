from enum import Enum


class EventType(str, Enum):
    REPORT = "report"
    OBSERVATION = "observation"
    LINK = "link"
    UNLINK = "unlink"
    ROLE_GRANT = "roleGrant"
    ASSIGN = "assign"
    STATUS = "status"


class IncidentType(str, Enum):
    FLOOD = "flood"
    FIRE = "fire"
    BUILDING_COLLAPSE = "building_collapse"
    MEDICAL_EMERGENCY = "medical_emergency"
    OTHER = "other"


class ResponderStatus(str, Enum):
    AVAILABLE = "available"
    EN_ROUTE = "en_route"
    ON_SCENE = "on_scene"
    DONE = "done"


class QuarantineReason(str, Enum):
    INVALID_SIGNATURE = "invalid_signature"
    HASH_MISMATCH = "hash_mismatch"
    MALFORMED_PAYLOAD = "malformed_payload"
    CLOCK_SKEW_EXCESSIVE = "clock_skew_excessive"
    UNAUTHORIZED_ROLE = "unauthorized_role"
    MISSING_FIELDS = "missing_fields"


class PriorityLevel(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
