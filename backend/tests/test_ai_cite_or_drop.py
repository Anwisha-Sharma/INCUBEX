from app.ai.cite_or_drop import CiteOrDropVerifier
from app.algorithms.grouping import IncidentGroup
from app.algorithms.reconciliation import reconcile_incident_group
from app.models.events import HybridLogicalClock, RescueMeshEvent

MOCK_EVENT_ID_1 = "1111111111111111111111111111111111111111111111111111111111110001"
MOCK_FAKE_EVENT_ID = "9999999999999999999999999999999999999999999999999999999999990001"


def make_mock_event(event_id: str, event_type: str, severity: int, people_affected: int, description: str) -> RescueMeshEvent:
    return RescueMeshEvent(
        event_id=event_id,
        event_type=event_type,
        device_id="dev_0001",
        clock=HybridLogicalClock(wall_time_ms=1000000, logical_counter=0, node_id="dev_0001"),
        payload={
            "incident_type": "flood",
            "latitude": 19.0760,
            "longitude": 72.8777,
            "severity": severity,
            "people_affected": people_affected,
            "description": description,
        },
        signature="mock_sig_000000000000000000000000000000000000000000000000000000000000",
    )


def test_cite_or_drop_accepts_valid_citations():
    ev1 = make_mock_event(MOCK_EVENT_ID_1, "report", severity=2, people_affected=5, description="Flood at Main St")
    group = IncidentGroup("inc_test", ev1)
    reconciled = reconcile_incident_group(group)

    verifier = CiteOrDropVerifier(group, reconciled)

    raw_claims = [
        {"statement": "Water level rising on Main St affecting 5 people.", "cited_event_ids": [ev1.event_id]}
    ]

    accepted, dropped = verifier.verify_and_filter(raw_claims)
    assert len(accepted) == 1
    assert accepted[0].statement == raw_claims[0]["statement"]
    assert accepted[0].cited_event_ids == [ev1.event_id]
    assert len(dropped) == 0


def test_cite_or_drop_rejects_missing_or_fake_citations():
    ev1 = make_mock_event(MOCK_EVENT_ID_1, "report", severity=2, people_affected=5, description="Flood at Main St")
    group = IncidentGroup("inc_test", ev1)
    reconciled = reconcile_incident_group(group)

    verifier = CiteOrDropVerifier(group, reconciled)

    raw_claims = [
        # Claim without any citation -> MUST DROP
        {"statement": "Hospital evacuated completely.", "cited_event_ids": []},
        # Claim with hallucinated non-existent event ID -> MUST DROP
        {"statement": "Road closed by police.", "cited_event_ids": [MOCK_FAKE_EVENT_ID]},
    ]

    accepted, dropped = verifier.verify_and_filter(raw_claims)
    assert len(accepted) == 0
    assert len(dropped) == 2
    assert "no citation provided" in dropped[0]
    assert "hallucinated citations" in dropped[1]


def test_cite_or_drop_rejects_hallucinated_figures():
    ev1 = make_mock_event(MOCK_EVENT_ID_1, "report", severity=2, people_affected=5, description="Small landslide")
    group = IncidentGroup("inc_test", ev1)
    reconciled = reconcile_incident_group(group)

    verifier = CiteOrDropVerifier(group, reconciled)

    raw_claims = [
        # AI hallucinated 45 casualties when recorded event only reports 5
        {"statement": "Over 45 casualties reported at the landslide site.", "cited_event_ids": [ev1.event_id]}
    ]

    accepted, dropped = verifier.verify_and_filter(raw_claims)
    assert len(accepted) == 0
    assert len(dropped) == 1
    assert "unsupported figure" in dropped[0]
