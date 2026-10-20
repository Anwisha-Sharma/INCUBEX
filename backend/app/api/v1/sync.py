from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.events import ingest_events
from app.db.session import get_db
from app.db.models import EventModel
from app.models.events import (
    EventDetailResponse,
    EventIngestRequest,
    EventIngestResponse,
    HybridLogicalClock,
    RescueMeshEvent,
    SyncSummaryResponse,
)
from app.sync.qr import QRChunker, QRReassembler

router = APIRouter(prefix="/sync", tags=["Peer Synchronization"])


class MissingEventsRequest(BaseModel):
    """Peer's known event IDs to compute missing set difference."""
    known_event_ids: list[str] = Field(..., description="List of event IDs already present on client/peer")


class MissingEventsResponse(BaseModel):
    missing_count: int
    missing_events: list[EventDetailResponse]


class SyncExportRequest(BaseModel):
    event_ids: list[str] | None = Field(default=None, description="Optional specific IDs to export; if None exports all")
    format: str = Field(default="chunked_qr", description="'chunked_qr' or 'json'")
    chunk_size: int = Field(default=220, ge=50, le=1000, description="Max character length per QR frame")


class SyncExportResponse(BaseModel):
    format: str
    total_events: int
    json_events: list[dict[str, Any]] | None = None
    qr_frames: list[str] | None = None
    frame_count: int = 0


class SyncImportRequest(BaseModel):
    format: str = Field(default="chunked_qr", description="'chunked_qr' or 'json'")
    qr_frames: list[str] | None = Field(default=None, description="List of scanned RM1:... QR frame strings")
    json_events: list[dict[str, Any]] | None = Field(default=None, description="Array of raw event objects")
    public_keys: dict[str, str] = Field(default_factory=dict, description="Public keys for verification")
    client_device_id: str | None = None


class SyncImportProgressResponse(BaseModel):
    is_complete: bool
    received_frames: int
    total_frames: int
    progress_percent: float
    ingest_result: EventIngestResponse | None = None


@router.get(
    "/summary",
    response_model=SyncSummaryResponse,
    summary="Get compact event-ID summary of the local event store",
)
async def get_sync_summary(db: AsyncSession = Depends(get_db)):
    """
    Returns compact summary of all persisted event IDs and the highest HLC wall time.
    Used by offline peers to immediately compute which events need to be exchanged.
    """
    event_ids_res = await db.scalars(select(EventModel.event_id))
    event_ids = list(event_ids_res)

    latest_wall_time = await db.scalar(select(func.max(EventModel.wall_time_ms))) or 0

    return SyncSummaryResponse(
        total_events=len(event_ids),
        event_ids=event_ids,
        latest_wall_time_ms=latest_wall_time,
    )


@router.post(
    "/missing",
    response_model=MissingEventsResponse,
    summary="Retrieve missing events based on peer's known event ID set",
)
async def get_missing_events(
    request: MissingEventsRequest,
    db: AsyncSession = Depends(get_db),
):
    r"""
    Computes set difference (Server Events \ Peer Events) and returns missing full event records.
    Transports can bundle this into JSON export or chunked QR codes.
    """
    all_events = await db.scalars(select(EventModel).order_by(EventModel.wall_time_ms.asc()))
    known_set = set(request.known_event_ids)

    missing: list[EventDetailResponse] = []
    for e in all_events:
        if e.event_id not in known_set:
            missing.append(
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
            )

    return MissingEventsResponse(
        missing_count=len(missing),
        missing_events=missing,
    )


@router.post(
    "/export",
    response_model=SyncExportResponse,
    summary="Export events as standard JSON or Deflate-compressed animated QR frames",
)
async def export_sync_payload(
    request: SyncExportRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Exports events for offline transfer:
    - 'chunked_qr': compresses into numbered Base64url QR frame sequence (Tier 0).
    - 'json': produces raw export for file transfer or USB exchange.
    """
    stmt = select(EventModel)
    if request.event_ids:
        stmt = stmt.where(EventModel.event_id.in_(request.event_ids))
    stmt = stmt.order_by(EventModel.wall_time_ms.asc())

    db_events = await db.scalars(stmt)
    records = [
        {
            "event_id": e.event_id,
            "event_type": e.event_type,
            "device_id": e.device_id,
            "clock": {
                "wall_time_ms": e.wall_time_ms,
                "logical_counter": e.logical_counter,
                "node_id": e.node_id,
            },
            "payload": e.payload,
            "signature": e.signature,
        }
        for e in db_events
    ]

    if request.format == "chunked_qr":
        frames = QRChunker.encode_to_frames(records, chunk_size=request.chunk_size)
        return SyncExportResponse(
            format="chunked_qr",
            total_events=len(records),
            qr_frames=frames,
            frame_count=len(frames),
        )
    else:
        return SyncExportResponse(
            format="json",
            total_events=len(records),
            json_events=records,
            frame_count=0,
        )


@router.post(
    "/import",
    response_model=SyncImportProgressResponse,
    summary="Import events from scanned QR frames or JSON payload",
)
async def import_sync_payload(
    request: SyncImportRequest,
    raw_request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Imports events via chunked QR or JSON:
    - Progressively reassembles QR frames.
    - If all frames are received, verifies integrity hash and idempotently persists events.
    """
    events_to_ingest: list[dict[str, Any]] = []

    if request.format == "chunked_qr":
        if not request.qr_frames:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="qr_frames list cannot be empty for chunked_qr format",
            )

        reassembler = QRReassembler()
        last_status = None
        for frame in request.qr_frames:
            last_status = reassembler.add_frame(frame)

        if not last_status or not last_status["is_complete"]:
            return SyncImportProgressResponse(
                is_complete=False,
                received_frames=last_status["received_frames"] if last_status else 0,
                total_frames=last_status["total_frames"] if last_status else 0,
                progress_percent=last_status["progress_percent"] if last_status else 0.0,
                ingest_result=None,
            )

        events_to_ingest = last_status["payload"]
        received_frames = last_status["received_frames"]
        total_frames = last_status["total_frames"]
    else:
        events_to_ingest = request.json_events or []
        received_frames = 1
        total_frames = 1

    # Ingest the validated events
    pydantic_events = [
        RescueMeshEvent(
            event_id=item["event_id"],
            event_type=item["event_type"],
            device_id=item["device_id"],
            clock=HybridLogicalClock(**item["clock"]),
            payload=item["payload"],
            signature=item["signature"],
        )
        for item in events_to_ingest
    ]

    ingest_req = EventIngestRequest(
        events=pydantic_events,
        public_keys=request.public_keys,
        transport=request.format,
        client_device_id=request.client_device_id,
    )
    ingest_result = await ingest_events(ingest_req, raw_request, db)

    return SyncImportProgressResponse(
        is_complete=True,
        received_frames=received_frames,
        total_frames=total_frames,
        progress_percent=100.0,
        ingest_result=ingest_result,
    )
