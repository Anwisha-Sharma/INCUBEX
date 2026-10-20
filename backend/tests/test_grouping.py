from app.algorithms.grouping import group_incidents
from app.algorithms.reconciliation import reconcile_incident_group
from app.core.canonical import compute_event_canonical_bytes, compute_canonical_hash
from app.core.security import generate_key_pair, sign_canonical_content
from app.models.events import HybridLogicalClock, RescueMeshEvent


def create_event(
    event_type: str,
    device_id: str,
    priv_pem: str,
    wall_time_ms: int,
    payload: dict,
    counter: int = 0,
) -> RescueMeshEvent:
    clock = HybridLogicalClock(wall_time_ms=wall_time_ms, logical_counter=counter, node_id=device_id)
    canonical_bytes = compute_event_canonical_bytes(event_type, device_id, clock, payload)
    event_id = compute_canonical_hash(canonical_bytes)
    sig = sign_canonical_content(priv_pem, canonical_bytes)
    return RescueMeshEvent(
        event_id=event_id,
        event_type=event_type,
        device_id=device_id,
        clock=clock,
        payload=payload,
        signature=sig,
    )


def test_reports_within_150m_and_2h_group_together():
    priv, pub, dev = generate_key_pair()

    # Lat 19.0760, Lon 72.8777
    # Second location is ~80m away: Lat 19.0765, Lon 72.8781
    r1 = create_event(
        "report", dev, priv, wall_time_ms=1000000,
        payload={"incident_type": "flood", "latitude": 19.0760, "longitude": 72.8777, "severity": 2, "description": "Flood 1"}
    )
    r2 = create_event(
        "report", dev, priv, wall_time_ms=1000000 + 1800000,  # 30 mins later
        payload={"incident_type": "flood", "latitude": 19.0765, "longitude": 72.8781, "severity": 3, "description": "Flood 2"}
    )

    groups = group_incidents([r1, r2])
    assert len(groups) == 1

    group = next(iter(groups.values()))
    assert len(group.reports) == 2
    reconciled = reconcile_incident_group(group)
    assert reconciled.incident_type == "flood"
    assert reconciled.has_conflict is True  # severity 2 vs 3


def test_reports_outside_distance_or_time_remain_separate():
    priv, pub, dev = generate_key_pair()

    # Distance > 2 km apart
    r1 = create_event(
        "report", dev, priv, wall_time_ms=1000000,
        payload={"incident_type": "fire", "latitude": 19.0760, "longitude": 72.8777, "severity": 2, "description": "Fire A"}
    )
    r2 = create_event(
        "report", dev, priv, wall_time_ms=1000000,
        payload={"incident_type": "fire", "latitude": 19.1000, "longitude": 72.9000, "severity": 2, "description": "Fire B"}
    )

    groups = group_incidents([r1, r2])
    assert len(groups) == 2


def test_coordinator_link_merges_incidents():
    priv, pub, dev = generate_key_pair()

    r1 = create_event(
        "report", dev, priv, wall_time_ms=1000000,
        payload={"incident_type": "building_collapse", "latitude": 19.0760, "longitude": 72.8777, "severity": 3, "description": "Collapse North"}
    )
    r2 = create_event(
        "report", dev, priv, wall_time_ms=1000000,
        payload={"incident_type": "building_collapse", "latitude": 19.1000, "longitude": 72.9000, "severity": 3, "description": "Collapse South"}
    )

    # Coordinator links the two separate reports
    link_event = create_event(
        "link", dev, priv, wall_time_ms=1005000,
        payload={"incident_ids": [r1.event_id, r2.event_id], "target_incident_id": f"inc_{r1.event_id[:16]}", "reason": "Same building complex"}
    )

    groups = group_incidents([r1, r2, link_event])
    assert len(groups) == 1


def test_deterministic_grouping_order_invariance():
    priv, pub, dev = generate_key_pair()

    r1 = create_event("report", dev, priv, wall_time_ms=1000000, payload={"incident_type": "flood", "latitude": 19.0760, "longitude": 72.8777, "severity": 1, "description": "A"})
    r2 = create_event("report", dev, priv, wall_time_ms=1001000, payload={"incident_type": "flood", "latitude": 19.0762, "longitude": 72.8779, "severity": 2, "description": "B"})

    # Forward order
    groups_fwd = group_incidents([r1, r2])
    # Reverse order
    groups_rev = group_incidents([r2, r1])

    assert list(groups_fwd.keys()) == list(groups_rev.keys())
    assert len(groups_fwd) == 1
