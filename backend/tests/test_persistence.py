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


@pytest.mark.asyncio
async def test_persistence_across_restart():
    """Verify accepted events survive complete database engine disposal and re-initialization."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from sqlalchemy import select
    from sqlalchemy.pool import NullPool
    from app.db.models import EventModel
    from app.core.config import settings
    from app.db.session import engine

    priv_pem, pub_hex, dev_id = generate_key_pair()
    event = make_test_event(dev_id, priv_pem, description="Persisted across restart")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/events/ingest",
            json={"events": [event], "public_keys": {dev_id: pub_hex}},
        )
        assert resp.status_code == 200
        assert resp.json()["persisted_count"] == 1

    # Simulate process shutdown: dispose current engine
    await engine.dispose()

    # Re-initialize new engine pointing to the same persistent store
    restarted_engine = create_async_engine(
        settings.DATABASE_URL,
        poolclass=NullPool,
        connect_args={"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {},
    )
    RestartedSession = async_sessionmaker(bind=restarted_engine, class_=AsyncSession, expire_on_commit=False)

    async with RestartedSession() as session:
        stored_event = await session.scalar(
            select(EventModel).where(EventModel.event_id == event["event_id"])
        )
        assert stored_event is not None
        assert stored_event.event_id == event["event_id"]
        assert stored_event.device_id == dev_id
        assert stored_event.payload["description"] == "Persisted across restart"

    await restarted_engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_event_ingestion():
    """Verify atomic idempotency and transaction safety under concurrent submissions."""
    import asyncio

    priv_pem, pub_hex, dev_id = generate_key_pair()
    event = make_test_event(dev_id, priv_pem, description="Concurrent ingestion test")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async def submit():
            return await client.post(
                "/api/v1/events/ingest",
                json={"events": [event], "public_keys": {dev_id: pub_hex}},
            )

        responses = await asyncio.gather(*[submit() for _ in range(5)])
        assert all(r.status_code == 200 for r in responses)

        persisted_counts = [r.json()["persisted_count"] for r in responses]
        assert sum(persisted_counts) == 1

        duplicate_counts = [r.json()["duplicate_count"] for r in responses]
        assert sum(duplicate_counts) == 4

