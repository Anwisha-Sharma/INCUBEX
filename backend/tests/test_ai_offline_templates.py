from app.ai.offline_templates import generate_deterministic_briefing
from app.algorithms.grouping import IncidentGroup
from app.algorithms.priority import PriorityEngine
from app.algorithms.reconciliation import reconcile_incident_group
from app.models.events import HybridLogicalClock, RescueMeshEvent


def test_deterministic_offline_briefing_structure():
    ev1 = RescueMeshEvent(
        event_id="1111111111111111111111111111111111111111111111111111111111110001",
        event_type="report",
        device_id="dev_alpha1",
        clock=HybridLogicalClock(wall_time_ms=1000000, logical_counter=0, node_id="dev_alpha1"),
        payload={
            "incident_type": "fire",
            "latitude": 19.0760,
            "longitude": 72.8777,
            "severity": 3,
            "people_affected": 8,
            "people_trapped": True,
            "description": "Commercial building fire",
            "evidence_hashes": ["hash_evidence_1"],
        },
        signature="mock_sig_000000000000000000000000000000000000000000000000000000000000",
    )
    group = IncidentGroup("inc_fire_test", ev1)
    reconciled = reconcile_incident_group(group)
    priority = PriorityEngine().evaluate(reconciled, current_time_ms=1000000 + 1000)

    briefing = generate_deterministic_briefing(group, reconciled, priority)

    assert briefing.incident_id == "inc_fire_test"
    assert briefing.briefing_type == "deterministic_template"
    assert "PEOPLE TRAPPED" in briefing.executive_summary
    assert len(briefing.verified_facts) >= 1
    assert briefing.verified_facts[0].cited_event_ids == [ev1.event_id]
    assert len(briefing.recommended_actions) >= 1
    assert "technical rescue" in " ".join(briefing.recommended_actions)
    assert briefing.priority_score == priority.score
    assert briefing.priority_level == priority.level.value
