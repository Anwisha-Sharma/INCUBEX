from typing import Annotated, Any, Literal
from pydantic import BaseModel, Field, field_validator
from app.models.enums import EventType, IncidentType, PriorityLevel, QuarantineReason, ResponderStatus


class HybridLogicalClock(BaseModel):
    """
    Hybrid Logical Clock representation:
    - wall_time_ms: Physical UTC timestamp in milliseconds
    - logical_counter: Tie-breaking sequence counter on the node
    - node_id: Unique identifier of the node (typically device_id)
    """
    wall_time_ms: int = Field(..., description="UTC physical wall time in milliseconds")
    logical_counter: int = Field(default=0, ge=0, description="Logical counter incremented on causality ties")
    node_id: str = Field(..., min_length=4, max_length=128, description="Unique node / device identifier")


class ReportPayload(BaseModel):
    """Payload for creating a fresh incident report."""
    incident_type: IncidentType
    latitude: float = Field(..., ge=-90.0, le=90.0, description="WGS84 latitude")
    longitude: float = Field(..., ge=-180.0, le=180.0, description="WGS84 longitude")
    severity: int = Field(..., ge=0, le=3, description="Severity score: 0 (minimal) to 3 (critical)")
    people_affected: int = Field(default=0, ge=0, description="Estimated number of people affected")
    people_trapped: bool = Field(default=False, description="Flag indicating people trapped or immediately endangered")
    description: str = Field(..., min_length=3, max_length=2000, description="Textual description of the incident")
    evidence_hashes: list[str] = Field(default_factory=list, description="SHA-256 hashes of attached photos/audio")


class ObservationPayload(BaseModel):
    """Payload for appending subsequent observations or updates to an incident."""
    incident_id: str = Field(..., description="Target incident identifier")
    observer_role: str = Field(default="civilian", description="Role of the observer (e.g. civilian, responder)")
    severity: int | None = Field(default=None, ge=0, le=3, description="Updated or observed severity")
    people_affected: int | None = Field(default=None, ge=0, description="Updated count of people affected")
    people_trapped: bool | None = Field(default=None, description="Updated people trapped flag")
    description: str | None = Field(default=None, max_length=2000, description="Additional observation details")
    evidence_hashes: list[str] = Field(default_factory=list, description="SHA-256 hashes of new attached evidence")


class LinkPayload(BaseModel):
    """Coordinator-signed event linking two or more incident clusters into one."""
    incident_ids: list[str] = Field(..., min_length=2, description="List of incident IDs to link together")
    target_incident_id: str = Field(..., description="Canonical target incident ID")
    reason: str = Field(..., min_length=3, max_length=500, description="Coordinator rationale for linking")


class UnlinkPayload(BaseModel):
    """Coordinator-signed event separating erroneously grouped reports."""
    incident_id: str = Field(..., description="Incident ID being split")
    split_event_ids: list[str] = Field(..., min_length=1, description="Event IDs to detach from this incident")
    reason: str = Field(..., min_length=3, max_length=500, description="Coordinator rationale for unlinking")


class RoleGrantPayload(BaseModel):
    """Coordinator role grant certifying another device as responder or coordinator."""
    target_device_id: str = Field(..., description="Device ID receiving the role")
    role: str = Field(..., description="Assigned role: responder, coordinator, or triage_officer")
    lease_until_ms: int = Field(..., description="Lease expiration timestamp in milliseconds")
    grantor_id: str = Field(..., description="Device ID of the authorizing coordinator")


class AssignPayload(BaseModel):
    """Assignment event dispatching a responder to an incident."""
    incident_id: str = Field(..., description="Incident ID assigned")
    responder_id: str = Field(..., description="Device ID of the responder")
    lease_duration_sec: int = Field(default=1800, ge=60, le=86400, description="Lease duration in seconds")


class StatusPayload(BaseModel):
    """Operational responder status update."""
    incident_id: str = Field(..., description="Target incident ID")
    status: ResponderStatus
    note: str | None = Field(default=None, max_length=500, description="Optional status note")


# Polymorphic Event Definition
EventPayloadType = (
    ReportPayload
    | ObservationPayload
    | LinkPayload
    | UnlinkPayload
    | RoleGrantPayload
    | AssignPayload
    | StatusPayload
    | dict[str, Any]
)


class RescueMeshEvent(BaseModel):
    """
    Immutable, content-addressed append-only RescueMesh event.
    - event_id: SHA-256 hex of canonical (clock, device_id, event_type, payload)
    - signature: ECDSA signature over the canonical content
    """
    event_id: str = Field(..., min_length=64, max_length=64, description="SHA-256 hash of canonical content")
    event_type: EventType = Field(..., description="Discriminator event type")
    device_id: str = Field(..., min_length=8, max_length=130, description="Origin device public identity hash")
    clock: HybridLogicalClock = Field(..., description="Hybrid Logical Clock of the event origin")
    payload: dict[str, Any] = Field(..., description="Validated event payload")
    signature: str = Field(..., min_length=10, description="ECDSA signature (P-256) over canonical content")


class EventValidationRequest(BaseModel):
    """Request model for batch or single event validation."""
    events: list[RescueMeshEvent] = Field(..., min_length=1, max_length=250)
    public_keys: dict[str, str] = Field(
        default_factory=dict,
        description="Optional map of device_id -> uncompressed hex or PEM public key"
    )


class EventValidationDetail(BaseModel):
    event_id: str
    is_valid: bool
    canonical_hash_matches: bool
    signature_verified: bool | None = None
    quarantine_reasons: list[QuarantineReason] = Field(default_factory=list)
    message: str | None = None


class EventValidationResponse(BaseModel):
    total_events: int
    valid_count: int
    quarantined_count: int
    results: list[EventValidationDetail]


class EventIngestRequest(BaseModel):
    """Batch ingestion request containing signed RescueMesh events and peer metadata."""
    events: list[RescueMeshEvent] = Field(..., min_length=1, max_length=500)
    public_keys: dict[str, str] = Field(
        default_factory=dict,
        description="Public keys mapping device_id -> uncompressed hex point",
    )
    transport: str = Field(default="http", description="Transport used: http, qr, websocket, or json_import")
    client_device_id: str | None = Field(default=None, description="Device ID initiating synchronization")


class EventIngestResponse(BaseModel):
    total_received: int
    persisted_count: int
    duplicate_count: int
    quarantined_count: int
    persisted_ids: list[str] = Field(default_factory=list)
    duplicate_ids: list[str] = Field(default_factory=list)
    quarantined_ids: list[str] = Field(default_factory=list)
    quarantine_details: list[EventValidationDetail] = Field(default_factory=list)


class EventDetailResponse(BaseModel):
    event_id: str
    event_type: str
    device_id: str
    clock: HybridLogicalClock
    payload: dict[str, Any]
    signature: str
    received_at_iso: str
    canonical_bytes_len: int


class QuarantinedEventResponse(BaseModel):
    id: int
    event_id: str | None
    device_id: str | None
    raw_data: dict[str, Any]
    quarantine_reasons: list[str]
    quarantined_at_iso: str
    remote_addr: str | None


class SyncSummaryResponse(BaseModel):
    """Compact summary of local event store state for peer set-difference reconciliation."""
    total_events: int
    event_ids: list[str]
    latest_wall_time_ms: int
