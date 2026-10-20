import time
import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.core.canonical import compute_event_canonical_bytes, compute_canonical_hash
from app.core.security import generate_key_pair, sign_canonical_content


@pytest.mark.asyncio
async def test_incident_briefing_api_endpoint():
    priv, pub, dev = generate_key_pair()
    now_ms = int(time.time() * 1000)

    clock = {"wall_time_ms": now_ms, "logical_counter": 0, "node_id": dev}
    payload = {
        "incident_type": "flood",
        "latitude": 19.0760,
        "longitude": 72.8777,
        "severity": 2,
        "people_affected": 4,
        "people_trapped": False,
        "description": "Rising floodwaters blocking highway access",
        "evidence_hashes": ["sha256_water_photo"],
    }
    canonical = compute_event_canonical_bytes("report", dev, clock, payload)
    event_id = compute_canonical_hash(canonical)
    sig = sign_canonical_content(priv, canonical)

    event = {
        "event_id": event_id,
        "event_type": "report",
        "device_id": dev,
        "clock": clock,
        "payload": payload,
        "signature": sig,
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Ingest event
        await client.post(
            "/api/v1/events/ingest",
            json={"events": [event], "public_keys": {dev: pub}},
        )

        inc_id = f"inc_{event_id[:16]}"

        # Request briefing
        resp = await client.get(f"/api/v1/incidents/{inc_id}/briefing?force_offline_template=true")
        assert resp.status_code == 200
        data = resp.json()

        assert data["incident_id"] == inc_id
        assert data["briefing_type"] == "deterministic_template"
        assert "FLOOD" in data["executive_summary"]
        assert len(data["verified_facts"]) >= 1
        assert event_id in data["evidence_event_ids"]
        assert "recommended_actions" in data
        assert data["priority_score"] >= 12
        assert "priority_explanation" in data
