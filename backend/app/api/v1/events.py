import time
import uuid
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.canonical import compute_event_canonical_bytes, compute_canonical_hash
from app.core.security import verify_signature
from app.db.session import get_db
from app.db.models import (
    DeviceModel,
    EventModel,
    IncidentEventLinkModel,
    IncidentModel,
    QuarantinedEventModel,
    SyncRecordModel,
)
from app.models.enums import QuarantineReason
from app.models.events import (
    EventDetailResponse,
    EventIngestRequest,
    EventIngestResponse,
    EventValidationDetail,
    EventValidationRequest,
    EventValidationResponse,
    HybridLogicalClock,
    QuarantinedEventResponse,
    RescueMeshEvent,
    SyncSummaryResponse,
)

router = APIRouter(prefix="/events", tags=["Events, Persistence & Sync"])


def validate_single_event(
    event: RescueMeshEvent,
    public_keys: dict[str, str],
) -> tuple[EventValidationDetail, bytes]:
    """
    Validate a single event according to RescueMesh C1/E1 rules:
    1. Recompute canonical bytes and verify SHA-256 content address against event_id.
    2. Check clock skew against allowable threshold.
    3. Verify ECDSA signature against public key (if known or provided).
    4. Quarantine invalid events with explicit reasons.
    """
    quarantine_reasons: list[QuarantineReason] = []
    message = "Event validated successfully"

    # 1. Canonical Hash Check
    canonical_bytes = compute_event_canonical_bytes(
        event_type=event.event_type.value,
        device_id=event.device_id,
        clock=event.clock,
        payload=event.payload,
    )
    expected_event_id = compute_canonical_hash(canonical_bytes)
    hash_matches = expected_event_id == event.event_id

    if not hash_matches:
        quarantine_reasons.append(QuarantineReason.HASH_MISMATCH)
        message = f"Event ID does not match canonical hash. Expected {expected_event_id}"

    # 2. Clock Skew Check
    now_ms = int(time.time() * 1000)
    skew = abs(event.clock.wall_time_ms - now_ms)
    if skew > settings.MAX_CLOCK_SKEW_MS:
        quarantine_reasons.append(QuarantineReason.CLOCK_SKEW_EXCESSIVE)
        message = f"Event wall clock skew exceeds threshold ({skew}ms > {settings.MAX_CLOCK_SKEW_MS}ms)"

    # 3. Signature Verification
    signature_verified: bool | None = None
    pub_key_hex = public_keys.get(event.device_id)

    if pub_key_hex:
        sig_ok = verify_signature(
            public_key_hex=pub_key_hex,
            signature_str=event.signature,
            canonical_bytes=canonical_bytes,
        )
        signature_verified = sig_ok
        if not sig_ok:
            quarantine_reasons.append(QuarantineReason.INVALID_SIGNATURE)
            message = "ECDSA signature verification failed"
    else:
        signature_verified = None

    is_valid = len(quarantine_reasons) == 0

    detail = EventValidationDetail(
        event_id=event.event_id,
        is_valid=is_valid,
        canonical_hash_matches=hash_matches,
        signature_verified=signature_verified,
        quarantine_reasons=quarantine_reasons,
        message=message if not is_valid else "Event is valid and verified",
    )
    return detail, canonical_bytes


@router.post(
    "/validate",
    response_model=EventValidationResponse,
    summary="Validate and verify a batch of RescueMesh events in memory",
)
async def validate_events(request: EventValidationRequest):
    """
    In-memory validation of append-only events:
    - Verifies SHA-256 canonical hash integrity.
    - Inspects physical clock skew.
    - Verifies ECDSA digital signature if public keys are supplied.
    - Marks malformed or tampered events for quarantine without discarding them.
    """
    results: list[EventValidationDetail] = []
    valid_count = 0
    quarantined_count = 0

    for event in request.events:
        detail, _ = validate_single_event(event, request.public_keys)
        if detail.is_valid:
            valid_count += 1
        else:
            quarantined_count += 1
        results.append(detail)

    return EventValidationResponse(
        total_events=len(request.events),
        valid_count=valid_count,
        quarantined_count=quarantined_count,
        results=results,
    )


@router.post(
    "/ingest",
    response_model=EventIngestResponse,
    summary="Ingest, validate, and idempotently persist a batch of events",
)
async def ingest_events(
    request: EventIngestRequest,
    raw_request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Durable event ingestion endpoint:
    - Performs cryptographic and canonical integrity verification.
    - Idempotently persists valid events (duplicate events are silently skipped).
    - Preserves historical observations (append-only, never overwriting).
    - Quarantines invalid or tampered events in isolated storage.
    - Automatically updates device registry and records synchronization ledger.
    """
    client_ip = raw_request.client.host if raw_request.client else None

    # Load stored public keys to corroborate signatures
    known_devices = await db.scalars(select(DeviceModel))
    known_pub_keys = {d.device_id: d.public_key_hex for d in known_devices}
    # Merge with any keys provided in the request payload
    all_pub_keys = {**known_pub_keys, **request.public_keys}

    # Register/update any new public keys in the device registry
    for dev_id, pub_hex in request.public_keys.items():
        if dev_id not in known_pub_keys:
            try:
                async with db.begin_nested():
                    new_device = DeviceModel(
                        device_id=dev_id,
                        public_key_hex=pub_hex,
                        role="civilian",
                    )
                    db.add(new_device)
                    await db.flush()
            except IntegrityError:
                pass  # Concurrently registered by another worker

    persisted_ids: list[str] = []
    duplicate_ids: list[str] = []
    quarantined_ids: list[str] = []
    quarantine_details: list[EventValidationDetail] = []

    for event in request.events:
        detail, canonical_bytes = validate_single_event(event, all_pub_keys)

        if not detail.is_valid:
            # Quarantine invalid or tampered event
            try:
                async with db.begin_nested():
                    quarantined_entry = QuarantinedEventModel(
                        event_id=event.event_id,
                        device_id=event.device_id,
                        raw_data=event.model_dump(mode="json"),
                        quarantine_reasons=[r.value for r in detail.quarantine_reasons],
                        remote_addr=client_ip,
                    )
                    db.add(quarantined_entry)
                    await db.flush()
            except IntegrityError:
                pass
            quarantined_ids.append(event.event_id)
            quarantine_details.append(detail)
            continue

        # Check for existing event (Idempotency / Event-Set Union)
        existing = await db.scalar(
            select(EventModel).where(EventModel.event_id == event.event_id)
        )
        if existing:
            duplicate_ids.append(event.event_id)
            continue

        try:
            async with db.begin_nested():
                # Persist new valid event
                new_event = EventModel(
                    event_id=event.event_id,
                    event_type=event.event_type.value,
                    device_id=event.device_id,
                    wall_time_ms=event.clock.wall_time_ms,
                    logical_counter=event.clock.logical_counter,
                    node_id=event.clock.node_id,
                    payload=event.payload,
                    signature=event.signature,
                    canonical_bytes_len=len(canonical_bytes),
                )
                db.add(new_event)
                await db.flush()
                persisted_ids.append(event.event_id)

                # If event is a fresh report, initialize incident view
                if event.event_type.value == "report":
                    incident_id = f"inc_{event.event_id[:16]}"
                    payload = event.payload
                    existing_inc = await db.scalar(
                        select(IncidentModel).where(IncidentModel.incident_id == incident_id)
                    )
                    if not existing_inc:
                        new_incident = IncidentModel(
                            incident_id=incident_id,
                            incident_type=payload.get("incident_type", "other"),
                            primary_event_id=event.event_id,
                            created_at_ms=event.clock.wall_time_ms,
                            status="active",
                            current_severity=payload.get("severity", 0),
                            current_people_affected=payload.get("people_affected", 0),
                            current_people_trapped=payload.get("people_trapped", False),
                            latitude=payload.get("latitude", 0.0),
                            longitude=payload.get("longitude", 0.0),
                            updated_at_ms=event.clock.wall_time_ms,
                        )
                        db.add(new_incident)

                    # Link incident to event
                    link = IncidentEventLinkModel(
                        incident_id=incident_id,
                        event_id=event.event_id,
                    )
                    db.add(link)
                    await db.flush()
        except IntegrityError:
            # Concurrent submission race: another worker persisted the same event
            duplicate_ids.append(event.event_id)
            if event.event_id in persisted_ids:
                persisted_ids.remove(event.event_id)

    # Record sync session ledger
    try:
        async with db.begin_nested():
            sync_record = SyncRecordModel(
                sync_id=uuid.uuid4().hex,
                device_id=request.client_device_id or (request.events[0].device_id if request.events else "unknown"),
                transport=request.transport,
                events_received=len(request.events),
                events_persisted=len(persisted_ids),
                events_quarantined=len(quarantined_ids),
            )
            db.add(sync_record)
            await db.flush()
    except IntegrityError:
        pass

    await db.commit()

    return EventIngestResponse(
        total_received=len(request.events),
        persisted_count=len(persisted_ids),
        duplicate_count=len(duplicate_ids),
        quarantined_count=len(quarantined_ids),
        persisted_ids=persisted_ids,
        duplicate_ids=duplicate_ids,
        quarantined_ids=quarantined_ids,
        quarantine_details=quarantine_details,
    )


@router.get(
    "/quarantined",
    response_model=list[QuarantinedEventResponse],
    summary="List quarantined events for coordinator audit",
)
async def list_quarantined_events(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve isolated quarantined events with forensic reasons and client IP."""
    stmt = (
        select(QuarantinedEventModel)
        .order_by(desc(QuarantinedEventModel.quarantined_at))
        .offset(offset)
        .limit(limit)
    )
    quarantined = await db.scalars(stmt)
    return [
        QuarantinedEventResponse(
            id=q.id,
            event_id=q.event_id,
            device_id=q.device_id,
            raw_data=q.raw_data,
            quarantine_reasons=q.quarantine_reasons,
            quarantined_at_iso=q.quarantined_at.isoformat(),
            remote_addr=q.remote_addr,
        )
        for q in quarantined
    ]


@router.get(
    "/{event_id}",
    response_model=EventDetailResponse,
    summary="Retrieve single stored event by ID",
)
async def get_event_by_id(
    event_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Retrieve a single immutable event from the event store."""
    event = await db.scalar(select(EventModel).where(EventModel.event_id == event_id))
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Event '{event_id}' not found in event store",
        )
    return EventDetailResponse(
        event_id=event.event_id,
        event_type=event.event_type,
        device_id=event.device_id,
        clock=HybridLogicalClock(
            wall_time_ms=event.wall_time_ms,
            logical_counter=event.logical_counter,
            node_id=event.node_id,
        ),
        payload=event.payload,
        signature=event.signature,
        received_at_iso=event.received_at.isoformat(),
        canonical_bytes_len=event.canonical_bytes_len,
    )


@router.get(
    "",
    response_model=list[EventDetailResponse],
    summary="List stored events with optional filtering",
)
async def list_events(
    event_type: str | None = None,
    device_id: str | None = None,
    since_wall_time_ms: int | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Query append-only events ordered by Hybrid Logical Clock wall time."""
    stmt = select(EventModel)
    if event_type:
        stmt = stmt.where(EventModel.event_type == event_type)
    if device_id:
        stmt = stmt.where(EventModel.device_id == device_id)
    if since_wall_time_ms is not None:
        stmt = stmt.where(EventModel.wall_time_ms >= since_wall_time_ms)

    stmt = stmt.order_by(EventModel.wall_time_ms.asc(), EventModel.logical_counter.asc()).offset(offset).limit(limit)
    events = await db.scalars(stmt)

    return [
        EventDetailResponse(
            event_id=e.event_id,
            event_type=e.event_type,
            device_id=e.device_id,
            clock=HybridLogicalClock(
                wall_time_ms=e.wall_time_ms,
                logical_counter=e.logical_counter,
                node_id=e.node_id,
            ),
            payload=e.payload,
            signature=e.signature,
            received_at_iso=e.received_at.isoformat(),
            canonical_bytes_len=e.canonical_bytes_len,
        )
        for e in events
    ]


@router.post(
    "/canonicalize",
    summary="Helper: Compute canonical bytes and SHA-256 event ID",
)
async def canonicalize_event(event_type: str, device_id: str, clock: dict[str, Any], payload: dict[str, Any]):
    """
    Utility endpoint allowing clients or test harnesses to compute the standard
    RescueMesh canonical JSON byte sequence and SHA-256 content-addressed ID.
    """
    try:
        canonical_bytes = compute_event_canonical_bytes(
            event_type=event_type,
            device_id=device_id,
            clock=clock,
            payload=payload,
        )
        canonical_str = canonical_bytes.decode("utf-8")
        event_id = compute_canonical_hash(canonical_bytes)
        return {
            "canonical_json": canonical_str,
            "canonical_bytes_len": len(canonical_bytes),
            "event_id": event_id,
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Canonicalization failed: {str(e)}",
        )
