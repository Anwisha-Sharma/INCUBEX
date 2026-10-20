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


def make_test_event(device_id: str, private_pem: str, event_type: str = "report", **payload_overrides):
    now_ms = int(time.time() * 1000)
    clock = {"wall_time_ms": now_ms, "logical_counter": 0, "node_id": device_id}

    payload = {
        "incident_type": "flood",
        "latitude": 19.0760,
        "longitude": 72.8777,
        "severity": 3,
        "people_affected": 5,
        "people_trapped": True,
        "description": "Rising water level on Main Street",
        "evidence_hashes": [],
    }
    payload.update(payload_overrides)

    canonical_bytes = compute_event_canonical_bytes(event_type, device_id, clock, payload)
    event_id = compute_canonical_hash(canonical_bytes)
    sig_hex = sign_canonical_content(private_pem, canonical_bytes)

    return {
        "event_id": event_id,
        "event_type": event_type,
        "device_id": device_id,
        "clock": clock,
        "payload": payload,
        "signature": sig_hex,
    }


@pytest.mark.asyncio
async def test_idempotent_event_ingestion_and_retrieval():
    priv_pem, pub_hex, dev_id = generate_key_pair()
    event = make_test_event(dev_id, priv_pem)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. First Ingestion -> Must persist
        resp1 = await client.post(
            "/api/v1/events/ingest",
            json={
                "events": [event],
                "public_keys": {dev_id: pub_hex},
                "transport": "http",
                "client_device_id": dev_id,
            },
        )
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1["total_received"] == 1
        assert data1["persisted_count"] == 1
        assert data1["duplicate_count"] == 0
        assert data1["quarantined_count"] == 0
        assert event["event_id"] in data1["persisted_ids"]

        # 2. Duplicate Ingestion -> Must be duplicate-safe (idempotent, no error)
        resp2 = await client.post(
            "/api/v1/events/ingest",
            json={
                "events": [event],
                "public_keys": {dev_id: pub_hex},
                "transport": "http",
                "client_device_id": dev_id,
            },
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["total_received"] == 1
        assert data2["persisted_count"] == 0
        assert data2["duplicate_count"] == 1
        assert data2["quarantined_count"] == 0
        assert event["event_id"] in data2["duplicate_ids"]

        # 3. Retrieve Single Event by ID
        resp3 = await client.get(f"/api/v1/events/{event['event_id']}")
        assert resp3.status_code == 200
        data3 = resp3.json()
        assert data3["event_id"] == event["event_id"]
        assert data3["device_id"] == dev_id
        assert data3["payload"]["severity"] == 3


@pytest.mark.asyncio
async def test_invalid_event_is_quarantined_in_db():
    priv_pem, pub_hex, dev_id = generate_key_pair()
    event = make_test_event(dev_id, priv_pem)

    # Corrupt the payload so hash does not match
    event["payload"]["description"] = "Corrupted text"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/events/ingest",
            json={
                "events": [event],
                "public_keys": {dev_id: pub_hex},
                "transport": "http",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["persisted_count"] == 0
        assert data["quarantined_count"] == 1
        assert event["event_id"] in data["quarantined_ids"]

        # Verify it appears in the quarantined events list
        resp_q = await client.get("/api/v1/events/quarantined")
        assert resp_q.status_code == 200
        q_list = resp_q.json()
        assert any(item["event_id"] == event["event_id"] for item in q_list)


@pytest.mark.asyncio
async def test_sync_summary_and_missing_events():
    priv_pem, pub_hex, dev_id = generate_key_pair()
    event1 = make_test_event(dev_id, priv_pem, description="Event 1")
    event2 = make_test_event(dev_id, priv_pem, description="Event 2")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Ingest both events
        await client.post(
            "/api/v1/events/ingest",
            json={"events": [event1, event2], "public_keys": {dev_id: pub_hex}},
        )

        # Query sync summary
        summary_resp = await client.get("/api/v1/sync/summary")
        assert summary_resp.status_code == 200
        summary = summary_resp.json()
        assert summary["total_events"] >= 2
        assert event1["event_id"] in summary["event_ids"]
        assert event2["event_id"] in summary["event_ids"]

        # Peer only has event1; query missing events
        missing_resp = await client.post(
            "/api/v1/sync/missing",
            json={"known_event_ids": [event1["event_id"]]},
        )
        assert missing_resp.status_code == 200
        missing_data = missing_resp.json()
        assert any(item["event_id"] == event2["event_id"] for item in missing_data["missing_events"])
