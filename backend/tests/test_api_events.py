import time
import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.core.canonical import compute_event_canonical_bytes, compute_canonical_hash
from app.core.security import generate_key_pair, sign_canonical_content


@pytest.mark.asyncio
async def test_validate_event_success():
    priv_pem, pub_hex, device_id = generate_key_pair()
    now_ms = int(time.time() * 1000)

    clock = {"wall_time_ms": now_ms, "logical_counter": 0, "node_id": device_id}
    payload = {
        "incident_type": "fire",
        "latitude": 19.0760,
        "longitude": 72.8777,
        "severity": 3,
        "people_affected": 8,
        "people_trapped": True,
        "description": "Commercial building fire with trapped occupants",
        "evidence_hashes": [],
    }

    canonical_bytes = compute_event_canonical_bytes("report", device_id, clock, payload)
    event_id = compute_canonical_hash(canonical_bytes)
    sig_hex = sign_canonical_content(priv_pem, canonical_bytes)

    valid_event = {
        "event_id": event_id,
        "event_type": "report",
        "device_id": device_id,
        "clock": clock,
        "payload": payload,
        "signature": sig_hex,
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/events/validate",
            json={
                "events": [valid_event],
                "public_keys": {device_id: pub_hex},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total_events"] == 1
        assert data["valid_count"] == 1
        assert data["quarantined_count"] == 0
        assert data["results"][0]["is_valid"] is True
        assert data["results"][0]["canonical_hash_matches"] is True
        assert data["results"][0]["signature_verified"] is True
        assert len(data["results"][0]["quarantine_reasons"]) == 0


@pytest.mark.asyncio
async def test_validate_event_quarantines_tampered_event_id():
    priv_pem, pub_hex, device_id = generate_key_pair()
    now_ms = int(time.time() * 1000)

    clock = {"wall_time_ms": now_ms, "logical_counter": 0, "node_id": device_id}
    payload = {
        "incident_type": "fire",
        "latitude": 19.0760,
        "longitude": 72.8777,
        "severity": 1,
        "people_affected": 0,
        "people_trapped": False,
        "description": "Small fire controlled",
        "evidence_hashes": [],
    }

    canonical_bytes = compute_event_canonical_bytes("report", device_id, clock, payload)
    sig_hex = sign_canonical_content(priv_pem, canonical_bytes)

    # Forged event ID
    tampered_event = {
        "event_id": "0000000000000000000000000000000000000000000000000000000000000000",
        "event_type": "report",
        "device_id": device_id,
        "clock": clock,
        "payload": payload,
        "signature": sig_hex,
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/events/validate",
            json={
                "events": [tampered_event],
                "public_keys": {device_id: pub_hex},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["valid_count"] == 0
        assert data["quarantined_count"] == 1
        assert data["results"][0]["is_valid"] is False
        assert data["results"][0]["canonical_hash_matches"] is False
        assert "hash_mismatch" in data["results"][0]["quarantine_reasons"]


@pytest.mark.asyncio
async def test_validate_event_quarantines_invalid_signature():
    priv_pem1, pub_hex1, dev_id1 = generate_key_pair()
    _, pub_hex2, _ = generate_key_pair()
    now_ms = int(time.time() * 1000)

    clock = {"wall_time_ms": now_ms, "logical_counter": 0, "node_id": dev_id1}
    payload = {
        "incident_type": "flood",
        "latitude": 19.0760,
        "longitude": 72.8777,
        "severity": 2,
        "people_affected": 2,
        "people_trapped": False,
        "description": "Water logging",
        "evidence_hashes": [],
    }

    canonical_bytes = compute_event_canonical_bytes("report", dev_id1, clock, payload)
    event_id = compute_canonical_hash(canonical_bytes)
    sig_hex = sign_canonical_content(priv_pem1, canonical_bytes)

    event_with_mismatched_key = {
        "event_id": event_id,
        "event_type": "report",
        "device_id": dev_id1,
        "clock": clock,
        "payload": payload,
        "signature": sig_hex,
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Pass dev_id1 mapped to wrong public key pub_hex2
        response = await client.post(
            "/api/v1/events/validate",
            json={
                "events": [event_with_mismatched_key],
                "public_keys": {dev_id1: pub_hex2},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["valid_count"] == 0
        assert data["quarantined_count"] == 1
        assert "invalid_signature" in data["results"][0]["quarantine_reasons"]
