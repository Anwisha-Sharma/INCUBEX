import json
import time
from starlette.testclient import TestClient
from app.main import app
from app.core.canonical import compute_event_canonical_bytes, compute_canonical_hash
from app.core.security import generate_key_pair, sign_canonical_content


def test_websocket_hub_protocol():
    client = TestClient(app)

    priv, pub, dev = generate_key_pair()
    now_ms = int(time.time() * 1000)

    clock = {"wall_time_ms": now_ms, "logical_counter": 0, "node_id": dev}
    payload = {"incident_type": "flood", "latitude": 19.0760, "longitude": 72.8777, "severity": 2, "description": "Hub WS test"}
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

    with client.websocket_connect("/ws/sync") as websocket:
        # 1. PING / PONG
        websocket.send_text(json.dumps({"type": "PING"}))
        pong_data = json.loads(websocket.receive_text())
        assert pong_data["type"] == "PONG"

        # 2. HELLO handshake
        websocket.send_text(json.dumps({"type": "HELLO", "device_id": dev, "known_event_ids": []}))
        offer_data = json.loads(websocket.receive_text())
        assert offer_data["type"] == "EVENTS_OFFER"
        assert "server_missing_for_peer" in offer_data

        # 3. PUBLISH_EVENTS
        websocket.send_text(json.dumps({"type": "PUBLISH_EVENTS", "events": [event]}))
        ack_data = json.loads(websocket.receive_text())
        assert ack_data["type"] == "PUBLISH_ACK"
        assert ack_data["total_received"] == 1

        # 4. GET_EVENTS
        websocket.send_text(json.dumps({"type": "GET_EVENTS", "event_ids": [event_id]}))
        batch_data = json.loads(websocket.receive_text())
        assert batch_data["type"] == "EVENTS_BATCH"
        assert len(batch_data["events"]) == 1
        assert batch_data["events"][0]["event_id"] == event_id


def test_websocket_hub_quarantines_tampered_event():
    client = TestClient(app)

    priv, pub, dev = generate_key_pair()
    now_ms = int(time.time() * 1000)

    clock = {"wall_time_ms": now_ms, "logical_counter": 0, "node_id": dev}
    payload = {"incident_type": "fire", "latitude": 19.07, "longitude": 72.87, "severity": 2, "description": "Tampered WS test"}
    canonical = compute_event_canonical_bytes("report", dev, clock, payload)
    genuine_event_id = compute_canonical_hash(canonical)
    sig = sign_canonical_content(priv, canonical)

    # Tampered event: claim a fake event_id
    tampered_event = {
        "event_id": "000000000000000000000000000000000000000000000000000000000000fake",
        "event_type": "report",
        "device_id": dev,
        "clock": clock,
        "payload": payload,
        "signature": sig,
    }

    with client.websocket_connect("/ws/sync") as websocket:
        websocket.send_text(json.dumps({"type": "PUBLISH_EVENTS", "events": [tampered_event]}))
        ack_data = json.loads(websocket.receive_text())

        assert ack_data["type"] == "PUBLISH_ACK"
        assert ack_data["ingested_count"] == 0
        assert ack_data["quarantined_count"] == 1

    # Verify event is NOT in accepted event store
    resp = client.get(f"/api/v1/events/{tampered_event['event_id']}")
    assert resp.status_code == 404

    # Verify event appears in quarantined audit store
    q_resp = client.get("/api/v1/events/quarantined")
    assert q_resp.status_code == 200
    q_data = q_resp.json()
    assert any(item["event_id"] == tampered_event["event_id"] for item in q_data)

