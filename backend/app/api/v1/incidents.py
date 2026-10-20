from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.algorithms.grouping import group_incidents
from app.algorithms.priority import PriorityEngine
from app.algorithms.reconciliation import reconcile_incident_group
from app.ai.models import ResponderBriefing
from app.ai.service import BriefingService
from app.db.session import get_db
from app.db.models import EventModel
from app.models.events import HybridLogicalClock, RescueMeshEvent

router = APIRouter(prefix="/incidents", tags=["Incidents & Priority Reconciliation"])


class IncidentSummaryResponse(BaseModel):
    incident_id: str
    incident_type: str
    latitude: float
    longitude: float
    current_severity: int
    current_people_affected: int
    current_people_trapped: bool
    current_description: str
    has_conflict: bool
    conflict_details: list[str]
    priority_score: int
    priority_level: str
    needs_verification: bool
    reporting_device_count: int
    created_at_ms: int
    latest_update_ms: int


class IncidentDetailResponse(BaseModel):
    incident_id: str
    incident_type: str
    latitude: float
    longitude: float
    current_severity: int
    current_people_affected: int
    current_people_trapped: bool
    current_description: str
    evidence_hashes: list[str]
    has_conflict: bool
    conflict_details: list[str]
    severity_observations: list[dict[str, Any]]
    location_observations: list[dict[str, Any]]
    assigned_responder_id: str | None
    responder_status: str | None
    priority: dict[str, Any]
    timeline_event_ids: list[str]


async def _load_and_reconcile_all(db: AsyncSession) -> dict[str, tuple[Any, Any]]:
    """Helper: load all stored events, run deterministic grouping and priority scoring."""
    db_events = await db.scalars(
        select(EventModel).order_by(EventModel.wall_time_ms.asc(), EventModel.logical_counter.asc())
    )
    pydantic_events = [
        RescueMeshEvent(
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
        )
        for e in db_events
    ]

    groups = group_incidents(pydantic_events)
    engine = PriorityEngine()

    result = {}
    for inc_id, grp in groups.items():
        reconciled = reconcile_incident_group(grp)
        priority = engine.evaluate(reconciled)
        result[inc_id] = (grp, reconciled, priority)
    return result


@router.get(
    "",
    response_model=list[IncidentSummaryResponse],
    summary="List deterministically grouped incidents with priority scores",
)
async def list_incidents(
    priority_level: str | None = None,
    incident_type: str | None = None,
    has_conflict: bool | None = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Returns deterministically reconciled incident list.
    Every priority score is calculated using S = 5V + 3P + 2C + 2R - 3U with safety clamp applied.
    """
    reconciled_map = await _load_and_reconcile_all(db)
    results: list[IncidentSummaryResponse] = []

    for inc_id, (grp, rec, pri) in reconciled_map.items():
        if priority_level and pri.level.value != priority_level.upper():
            continue
        if incident_type and rec.incident_type != incident_type:
            continue
        if has_conflict is not None and rec.has_conflict != has_conflict:
            continue

        results.append(
            IncidentSummaryResponse(
                incident_id=inc_id,
                incident_type=rec.incident_type,
                latitude=rec.latitude,
                longitude=rec.longitude,
                current_severity=rec.current_severity,
                current_people_affected=rec.current_people_affected,
                current_people_trapped=rec.current_people_trapped,
                current_description=rec.current_description,
                has_conflict=rec.has_conflict,
                conflict_details=rec.conflict_details,
                priority_score=pri.score,
                priority_level=pri.level.value,
                needs_verification=pri.needs_verification,
                reporting_device_count=len(rec.reporting_devices),
                created_at_ms=rec.created_at_ms,
                latest_update_ms=rec.latest_update_ms,
            )
        )

    # Sort incidents descending by priority score
    results.sort(key=lambda x: x.priority_score, reverse=True)
    return results


@router.get(
    "/{incident_id}",
    response_model=IncidentDetailResponse,
    summary="Get full incident detail, conflict history, and priority explanation",
)
async def get_incident_detail(
    incident_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Returns full incident dossier:
    - Current values selected by latest HLC.
    - Preserved conflict history across all observers.
    - Transparent factor breakdown (V, P, C, R, U) and explanation.
    """
    reconciled_map = await _load_and_reconcile_all(db)
    if incident_id not in reconciled_map:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{incident_id}' not found",
        )

    grp, rec, pri = reconciled_map[incident_id]

    return IncidentDetailResponse(
        incident_id=rec.incident_id,
        incident_type=rec.incident_type,
        latitude=rec.latitude,
        longitude=rec.longitude,
        current_severity=rec.current_severity,
        current_people_affected=rec.current_people_affected,
        current_people_trapped=rec.current_people_trapped,
        current_description=rec.current_description,
        evidence_hashes=rec.evidence_hashes,
        has_conflict=rec.has_conflict,
        conflict_details=rec.conflict_details,
        severity_observations=rec.severity_observations,
        location_observations=rec.location_observations,
        assigned_responder_id=rec.assigned_responder_id,
        responder_status=rec.responder_status,
        priority=pri.to_dict(),
        timeline_event_ids=[e.event_id for e in grp.all_events],
    )


@router.get(
    "/{incident_id}/sensitivity",
    summary="Sensitivity analysis varying each weight by ±1",
)
async def get_incident_sensitivity(
    incident_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Performs sensitivity analysis on the priority formula for this incident:
    Varies weights by ±1 and reports whether the priority classification level remains stable.
    """
    reconciled_map = await _load_and_reconcile_all(db)
    if incident_id not in reconciled_map:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{incident_id}' not found",
        )

    _, rec, _ = reconciled_map[incident_id]
    engine = PriorityEngine()
    return engine.sensitivity_analysis(rec)


@router.get(
    "/{incident_id}/briefing",
    response_model=ResponderBriefing,
    summary="Get evidence-grounded responder briefing with Cite-or-Drop verification",
)
async def get_incident_briefing(
    incident_id: str,
    force_offline_template: bool = Query(False, description="Force deterministic rule-based template generation"),
    db: AsyncSession = Depends(get_db),
):
    """
    Generates an evidence-grounded responder briefing:
    - Enforces Cite-or-Drop verification: uncorroborated claims and figures are dropped.
    - Seamlessly falls back to deterministic rule-based template if offline or unconfigured.
    """
    reconciled_map = await _load_and_reconcile_all(db)
    if incident_id not in reconciled_map:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{incident_id}' not found",
        )

    grp, rec, pri = reconciled_map[incident_id]
    return await BriefingService.generate_briefing(
        grp, rec, pri, force_offline=force_offline_template
    )
