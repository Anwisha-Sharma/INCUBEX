import time
import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.core.canonical import compute_event_canonical_bytes, compute_canonical_hash
from app.core.security import generate_key_pair, sign_canonical_content
from app.db.session import init_db


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()


@pytest.mark.asyncio
async def test_incidents_api_endpoints():
    priv, pub, dev = generate_key_pair()
    now_ms = int(time.time() * 1000)

    # Ingest a severe report
    clock = {"wall_time_ms": now_ms, "logical_counter": 0, "node_id": dev}
    payload = {
        "incident_type": "fire",
        "latitude": 19.0760,
        "longitude": 72.8777,
        "severity": 3,
        "people_affected": 15,
        "people_trapped": True,
        "description": "Large apartment fire",
        "evidence_hashes": ["e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"],
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

        # 1. List incidents
        list_resp = await client.get("/api/v1/incidents")
        assert list_resp.status_code == 200
        incidents = list_resp.json()
        assert len(incidents) >= 1

        target_inc = next((i for i in incidents if i["incident_id"] == f"inc_{event_id[:16]}"), None)
        assert target_inc is not None
        assert target_inc["incident_type"] == "fire"
        assert target_inc["current_severity"] == 3
        assert target_inc["current_people_trapped"] is True
        assert target_inc["priority_level"] == "HIGH"
        assert target_inc["priority_score"] >= 24

        # 2. Get incident detail
        inc_id = target_inc["incident_id"]
        detail_resp = await client.get(f"/api/v1/incidents/{inc_id}")
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        assert detail["incident_id"] == inc_id
        assert detail["priority"]["level"] == "HIGH"
        assert "S = 5V + 3P + 2C + 2R - 3U" in detail["priority"]["explanation"]

        # 3. Sensitivity analysis
        sens_resp = await client.get(f"/api/v1/incidents/{inc_id}/sensitivity")
        assert sens_resp.status_code == 200
        sens = sens_resp.json()
        assert "baseline_score" in sens
        assert sens["min_score"] <= sens["baseline_score"] <= sens["max_score"]
