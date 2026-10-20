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
async def test_qr_export_and_import_api_pipeline():
    priv, pub, dev = generate_key_pair()
    now_ms = int(time.time() * 1000)

    clock = {"wall_time_ms": now_ms, "logical_counter": 0, "node_id": dev}
    payload = {
        "incident_type": "building_collapse",
        "latitude": 19.0760,
        "longitude": 72.8777,
        "severity": 3,
        "people_affected": 10,
        "people_trapped": True,
        "description": "Multi-story building collapse with survivors trapped",
        "evidence_hashes": ["sha256_photo_evidence"],
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
        # Ingest initially on Device 1
        await client.post(
            "/api/v1/events/ingest",
            json={"events": [event], "public_keys": {dev: pub}},
        )

        # 1. Export as animated QR frames
        export_resp = await client.post(
            "/api/v1/sync/export",
            json={"format": "chunked_qr", "chunk_size": 150},
        )
        assert export_resp.status_code == 200
        export_data = export_resp.json()
        assert export_data["format"] == "chunked_qr"
        assert export_data["total_events"] >= 1
        assert len(export_data["qr_frames"]) >= 1

        qr_frames = export_data["qr_frames"]

        # 2. Simulate partial scan (first frame only)
        if len(qr_frames) > 1:
            partial_resp = await client.post(
                "/api/v1/sync/import",
                json={"format": "chunked_qr", "qr_frames": [qr_frames[0]]},
            )
            assert partial_resp.status_code == 200
            partial_data = partial_resp.json()
            assert partial_data["is_complete"] is False
            assert partial_data["received_frames"] == 1
            assert partial_data["total_frames"] == len(qr_frames)

        # 3. Complete scan of all frames
        import_resp = await client.post(
            "/api/v1/sync/import",
            json={
                "format": "chunked_qr",
                "qr_frames": qr_frames,
                "public_keys": {dev: pub},
            },
        )
        assert import_resp.status_code == 200
        import_data = import_resp.json()
        assert import_data["is_complete"] is True
        assert import_data["progress_percent"] == 100.0
        assert import_data["ingest_result"] is not None
        # It's already in DB, so duplicate count or persisted count handled idempotently
        assert import_data["ingest_result"]["total_received"] >= 1
