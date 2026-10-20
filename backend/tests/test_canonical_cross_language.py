"""
Cross-Language Canonical JSON & SHA-256 Content-Addressing Test Vectors.
Guarantees byte-for-byte parity between Python backend and Frontend TypeScript/Web Crypto API.
"""

from app.core.canonical import compute_event_canonical_bytes, compute_canonical_hash


def test_cross_language_vector_1_report():
    """
    Vector 1: Fresh flood incident report with coordinates, affected count,
    trapped flag, and evidence hashes.
    Expected canonical string and SHA-256 match Node.js/Web Crypto reference implementation.
    """
    clock = {"wall_time_ms": 1771400000000, "logical_counter": 0, "node_id": "dev_alpha_01"}
    payload = {
        "incident_type": "flood",
        "latitude": 19.076,
        "longitude": 72.8777,
        "severity": 3,
        "people_affected": 5,
        "people_trapped": True,
        "description": "Rising water level",
        "evidence_hashes": ["abc123hash", "def456hash"],
    }

    canonical_bytes = compute_event_canonical_bytes("report", "dev_alpha_01", clock, payload)
    expected_canonical = (
        '{"clock":{"logical_counter":0,"node_id":"dev_alpha_01","wall_time_ms":1771400000000},'
        '"device_id":"dev_alpha_01","event_type":"report",'
        '"payload":{"description":"Rising water level","evidence_hashes":["abc123hash","def456hash"],'
        '"incident_type":"flood","latitude":19.076,"longitude":72.8777,'
        '"people_affected":5,"people_trapped":true,"severity":3}}'
    )
    assert canonical_bytes.decode("utf-8") == expected_canonical

    event_id = compute_canonical_hash(canonical_bytes)
    assert event_id == "c5c2c6da71cf0864f82f7989ab47ed0c74576e3e2a1619236b8ecfcdee6b7a40"


def test_cross_language_vector_2_observation():
    """
    Vector 2: Follow-up observation referencing an incident ID.
    Expected canonical string and SHA-256 match Node.js/Web Crypto reference implementation.
    """
    clock = {"wall_time_ms": 1771403600000, "logical_counter": 1, "node_id": "dev_beta_02"}
    payload = {
        "incident_id": "inc_test_123",
        "severity": 2,
        "people_affected": 3,
        "people_trapped": False,
        "description": "Water receding on South side",
    }

    canonical_bytes = compute_event_canonical_bytes("observation", "dev_beta_02", clock, payload)
    expected_canonical = (
        '{"clock":{"logical_counter":1,"node_id":"dev_beta_02","wall_time_ms":1771403600000},'
        '"device_id":"dev_beta_02","event_type":"observation",'
        '"payload":{"description":"Water receding on South side","incident_id":"inc_test_123",'
        '"people_affected":3,"people_trapped":false,"severity":2}}'
    )
    assert canonical_bytes.decode("utf-8") == expected_canonical

    event_id = compute_canonical_hash(canonical_bytes)
    assert event_id == "580799e74c45ba1b65ab05de8ccb47482f9d7f1d72f4c16207d49210fd346725"


def test_cross_language_unicode_and_null_handling():
    """
    Verify Unicode characters (accents, emojis, non-Latin scripts) and None/null values
    are serialized deterministically.
    """
    clock = {"wall_time_ms": 1771405000000, "logical_counter": 0, "node_id": "dev_unicode"}
    payload = {
        "notes": "Emergency 🚨 evacuation in progress: São Paulo, München, 東京都",
        "optional_field": None,
    }

    canonical_bytes = compute_event_canonical_bytes("observation", "dev_unicode", clock, payload)
    canonical_str = canonical_bytes.decode("utf-8")
    assert "🚨" in canonical_str
    assert "São Paulo" in canonical_str
    assert "東京都" in canonical_str
    assert '"optional_field":null' in canonical_str

    event_id = compute_canonical_hash(canonical_bytes)
    assert len(event_id) == 64
