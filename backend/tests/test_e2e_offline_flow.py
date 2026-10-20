import json
import time
import pytest
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

from app.main import app
from app.core.canonical import compute_event_canonical_bytes, compute_canonical_hash
from app.core.security import generate_key_pair, sign_canonical_content
from app.sync.qr import QRChunker, QRReassembler


@pytest.mark.asyncio
async def test_complete_end_to_end_offline_lifecycle():
    """
    Phase 10: Full End-to-End Multi-Device Offline Scenario Verification.
    Validates offline reporting, QR exchange, quarantine of forgery,
    hotspot WebSocket sync, explainable priority scoring, and AI Cite-or-Drop briefing.
    """
    # 1. Device Identities
    alice_priv, alice_pub, alice_dev = generate_key_pair()
    bob_priv, bob_pub, bob_dev = generate_key_pair()
    charlie_priv, charlie_pub, charlie_dev = generate_key_pair()

    now_ms = int(time.time() * 1000)

    # 2. Alice creates a signed offline report (Flood, critical severity, people trapped)
    alice_clock = {"wall_time_ms": now_ms, "logical_counter": 0, "node_id": alice_dev}
    alice_payload = {
        "incident_type": "flood",
        "latitude": 19.0760,
        "longitude": 72.8777,
        "severity": 3,
        "people_affected": 12,
        "people_trapped": True,
        "description": "Flash flood entered residential basement; families trapped",
        "evidence_hashes": ["hash_flooded_basement_photo"],
    }
    alice_canonical = compute_event_canonical_bytes("report", alice_dev, alice_clock, alice_payload)
    alice_event_id = compute_canonical_hash(alice_canonical)
    alice_sig = sign_canonical_content(alice_priv, alice_canonical)

    alice_event = {
        "event_id": alice_event_id,
        "event_type": "report",
        "device_id": alice_dev,
        "clock": alice_clock,
        "payload": alice_payload,
        "signature": alice_sig,
    }

    # 3. Alice exports event as animated chunked QR frames
    qr_frames = QRChunker.encode_to_frames([alice_event], chunk_size=120)
    assert len(qr_frames) >= 2

    # 4. Bob scans QR frames in reverse order (offline camera exchange)
    reassembler = QRReassembler()
    for frame in reversed(qr_frames[:-1]):
        status = reassembler.add_frame(frame)
        assert status["is_complete"] is False

    final_status = reassembler.add_frame(qr_frames[-1])
    assert final_status["is_complete"] is True
    reassembled_events = final_status["payload"]
    assert len(reassembled_events) == 1
    assert reassembled_events[0]["event_id"] == alice_event_id

    # 5. Malicious Charlie attempts to submit forged/tampered event
    forged_event = {
        "event_id": "0000000000000000000000000000000000000000000000000000000000000000",
        "event_type": "report",
        "device_id": charlie_dev,
        "clock": {"wall_time_ms": now_ms, "logical_counter": 0, "node_id": charlie_dev},
        "payload": {"incident_type": "flood", "latitude": 19.0760, "longitude": 72.8777, "severity": 0, "description": "Fake all clear"},
        "signature": alice_sig,
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Ingest Alice's authentic event + Charlie's forged event
        ingest_resp = await client.post(
            "/api/v1/events/ingest",
            json={
                "events": [alice_event, forged_event],
                "public_keys": {alice_dev: alice_pub, charlie_dev: charlie_pub},
                "transport": "qr",
                "client_device_id": bob_dev,
            },
        )
        assert ingest_resp.status_code == 200
        ingest_data = ingest_resp.json()
        assert ingest_data["persisted_count"] == 1
        assert ingest_data["quarantined_count"] == 1
        assert alice_event_id in ingest_data["persisted_ids"]
        assert forged_event["event_id"] in ingest_data["quarantined_ids"]

        # 6. Bob records an on-scene observation (Responder corroboration)
        bob_clock = {"wall_time_ms": now_ms + 1000, "logical_counter": 0, "node_id": bob_dev}
        bob_payload = {
            "incident_id": f"inc_{alice_event_id[:16]}",
            "observer_role": "responder",
            "severity": 3,
            "people_affected": 12,
            "people_trapped": True,
            "description": "On scene with technical rescue team; pumping equipment deployed",
            "evidence_hashes": ["hash_responder_rescue_photo"],
        }
        bob_canonical = compute_event_canonical_bytes("observation", bob_dev, bob_clock, bob_payload)
        bob_event_id = compute_canonical_hash(bob_canonical)
        bob_sig = sign_canonical_content(bob_priv, bob_canonical)

        bob_event = {
            "event_id": bob_event_id,
            "event_type": "observation",
            "device_id": bob_dev,
            "clock": bob_clock,
            "payload": bob_payload,
            "signature": bob_sig,
        }

        # Ingest Bob's observation
        await client.post(
            "/api/v1/events/ingest",
            json={"events": [bob_event], "public_keys": {bob_dev: bob_pub}},
        )

        # 7. Check Reconciled Incident Dossier
        inc_id = f"inc_{alice_event_id[:16]}"
        incident_resp = await client.get(f"/api/v1/incidents/{inc_id}")
        assert incident_resp.status_code == 200
        incident_data = incident_resp.json()

        assert incident_data["incident_type"] == "flood"
        assert incident_data["current_severity"] == 3
        assert incident_data["current_people_trapped"] is True
        assert incident_data["priority"]["level"] == "HIGH"
        # Exact formula: S = 5(3) + 3(3) + 2(3) + 2(3) - 3(0) = 15 + 9 + 6 + 6 = 36
        assert incident_data["priority"]["score"] == 36
        assert len(incident_data["timeline_event_ids"]) == 2

        # 8. Check AI Grounded Briefing
        briefing_resp = await client.get(f"/api/v1/incidents/{inc_id}/briefing?force_offline_template=true")
        assert briefing_resp.status_code == 200
        briefing_data = briefing_resp.json()

        assert briefing_data["incident_id"] == inc_id
        assert "PEOPLE TRAPPED" in briefing_data["executive_summary"]
        assert len(briefing_data["verified_facts"]) >= 1
        assert alice_event_id in briefing_data["evidence_event_ids"]
        assert bob_event_id in briefing_data["evidence_event_ids"]

    # 9. Verify Hotspot WebSocket Gossip Relay
    sync_client = TestClient(app)
    with sync_client.websocket_connect("/ws/sync") as ws:
        # Peer asks for missing events
        ws.send_text(json.dumps({"type": "HELLO", "device_id": "peer_unit_5", "known_event_ids": []}))
        offer = json.loads(ws.receive_text())
        assert offer["type"] == "EVENTS_OFFER"
        assert alice_event_id in offer["server_missing_for_peer"]
        assert bob_event_id in offer["server_missing_for_peer"]
