from app.ai.models import GroundedClaim, ResponderBriefing
from app.algorithms.grouping import IncidentGroup
from app.algorithms.priority import PriorityResult
from app.algorithms.reconciliation import ReconciledIncidentState


def generate_deterministic_briefing(
    group: IncidentGroup,
    state: ReconciledIncidentState,
    priority: PriorityResult,
) -> ResponderBriefing:
    """
    Deterministic rule-based briefing generator.
    Runs 100% offline when network or LLM APIs (Groq/Claude) are unavailable.
    Distinguishes verified facts, unverified reports, and conflicting observations with full citations.
    """
    primary = group.primary_report
    all_events = group.all_events
    primary_id = primary.event_id

    # Executive Summary
    trapped_text = "PEOPLE TRAPPED" if state.current_people_trapped else "no individuals reported trapped"
    summary = (
        f"Incident {state.incident_id} ({state.incident_type.upper()}): "
        f"Priority {priority.level.value} (Score {priority.score}). "
        f"Location: Lat {state.latitude}, Lon {state.longitude}. "
        f"Current severity assessed at {state.current_severity}/3 with {state.current_people_affected} people affected ({trapped_text}). "
        f"Total of {len(all_events)} append-only events recorded across {len(state.reporting_devices)} devices."
    )

    verified_facts: list[GroundedClaim] = []
    unverified_reports: list[GroundedClaim] = []
    conflicting_observations: list[GroundedClaim] = []

    # 1. Primary event claim
    verified_facts.append(
        GroundedClaim(
            statement=(
                f"Initial report filed for {state.incident_type} with severity {primary.payload.get('severity', 0)} "
                f"at coordinates ({state.latitude}, {state.longitude}). Description: '{primary.payload.get('description', '')}'"
            ),
            cited_event_ids=[primary_id],
        )
    )

    # 2. Corroborated observations vs unverified single reports
    for obs in group.observations:
        p = obs.payload
        role = p.get("observer_role", "civilian")
        desc = p.get("description") or f"Observation from {role}"
        claim_text = f"Observation by {role} (Device {obs.device_id[:8]}...): {desc}"

        if role in ("responder", "coordinator") or len(state.reporting_devices) >= 2:
            verified_facts.append(GroundedClaim(statement=claim_text, cited_event_ids=[obs.event_id]))
        else:
            unverified_reports.append(GroundedClaim(statement=claim_text, cited_event_ids=[obs.event_id]))

    # 3. Conflicts
    if state.has_conflict:
        for detail in state.conflict_details:
            conflicting_observations.append(
                GroundedClaim(
                    statement=f"DISCREPANCY: {detail}",
                    cited_event_ids=[e.event_id for e in all_events],
                )
            )

    # 4. Recommended operational actions
    actions: list[str] = []
    if state.current_severity >= 2:
        actions.append("Dispatch primary search and rescue squad with medical response capability.")
    if state.current_people_trapped:
        actions.append("Deploy extraction and technical rescue equipment immediately for trapped occupants.")
    if state.has_conflict:
        actions.append("Direct first arriving unit to establish on-scene ground truth and resolve reported discrepancies.")
    if len(state.evidence_hashes) > 0:
        actions.append(f"Review {len(state.evidence_hashes)} attached cryptographic evidence hash(es) for visual confirmation.")
    if not actions:
        actions.append("Monitor local sector and maintain radio contact.")

    return ResponderBriefing(
        incident_id=state.incident_id,
        briefing_type="deterministic_template",
        executive_summary=summary,
        verified_facts=verified_facts,
        unverified_reports=unverified_reports,
        conflicting_observations=conflicting_observations,
        recommended_actions=actions,
        dropped_claims=[],
        evidence_event_ids=[e.event_id for e in all_events],
        priority_score=priority.score,
        priority_level=priority.level.value,
        priority_explanation=priority.explanation,
    )
