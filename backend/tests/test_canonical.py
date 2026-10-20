import json
from app.core.canonical import (
    to_canonical_json_bytes,
    compute_canonical_hash,
    compute_event_canonical_bytes,
    compute_event_id,
)
from app.models.events import HybridLogicalClock, ReportPayload


def test_canonical_json_key_sorting_and_compactness():
    # Dict with reverse-ordered keys and whitespace
    data1 = {"z": 1, "a": {"d": 4, "b": 2}, "m": [3, 2, 1]}
    data2 = {"a": {"b": 2, "d": 4}, "m": [3, 2, 1], "z": 1}

    bytes1 = to_canonical_json_bytes(data1)
    bytes2 = to_canonical_json_bytes(data2)

    assert bytes1 == bytes2
    # Ensure no space after ':' or ','
    assert b": " not in bytes1
    assert b", " not in bytes1
    assert bytes1 == b'{"a":{"b":2,"d":4},"m":[3,2,1],"z":1}'


def test_canonical_event_id_determinism():
    clock = HybridLogicalClock(wall_time_ms=1771400000000, logical_counter=0, node_id="node_alpha")
    payload = ReportPayload(
        incident_type="flood",
        latitude=19.0760,
        longitude=72.8777,
        severity=3,
        people_affected=15,
        people_trapped=True,
        description="Flash flood near main bridge",
        evidence_hashes=["e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"],
    )

    id1 = compute_event_id(
        event_type="report",
        device_id="dev_001",
        clock=clock,
        payload=payload,
    )
    id2 = compute_event_id(
        event_type="report",
        device_id="dev_001",
        clock=clock,
        payload=payload,
    )

    assert id1 == id2
    assert len(id1) == 64  # SHA-256 hex string


def test_tampering_changes_event_id():
    clock = HybridLogicalClock(wall_time_ms=1771400000000, logical_counter=0, node_id="node_alpha")
    payload1 = {"description": "Water level at 2 feet", "severity": 1}
    payload2 = {"description": "Water level at 2 feet", "severity": 2}

    id1 = compute_event_id("report", "dev_001", clock, payload1)
    id2 = compute_event_id("report", "dev_001", clock, payload2)

    assert id1 != id2
