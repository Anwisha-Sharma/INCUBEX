import json
from typing import Any
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.canonical import compute_event_canonical_bytes, compute_canonical_hash
from app.db.models import EventModel
from app.db.session import AsyncSessionLocal
from app.models.events import HybridLogicalClock, RescueMeshEvent

hub_router = APIRouter(tags=["Local WebSocket Hub"])


class LocalHubManager:
    """
    Tier 1 Local Network WebSocket Hub:
    Relays gossip messages across local peers connected to the same Wi-Fi router or phone hotspot.
    Zero internet dependency.
    """
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.peer_device_ids: dict[WebSocket, str] = {}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        self.peer_device_ids.pop(websocket, None)

    async def broadcast(self, message: dict[str, Any], sender: WebSocket | None = None):
        """Broadcast message to all connected peers except sender."""
        payload = json.dumps(message)
        for conn in list(self.active_connections):
            if conn != sender:
                try:
                    await conn.send_text(payload)
                except Exception:
                    self.disconnect(conn)


hub_manager = LocalHubManager()


@hub_router.websocket("/ws/sync")
async def websocket_sync_endpoint(websocket: WebSocket):
    """
    Real-time local gossip synchronization endpoint.
    Operates without internet connection on local hotspot or LAN.
    """
    await hub_manager.connect(websocket)

    try:
        while True:
            raw_text = await websocket.receive_text()
            try:
                msg = json.loads(raw_text)
            except Exception:
                await websocket.send_text(json.dumps({"type": "ERROR", "message": "Invalid JSON format"}))
                continue

            msg_type = msg.get("type", "").upper()

            if msg_type == "HELLO":
                # Peer handshake with known event IDs
                peer_device_id = msg.get("device_id", "unknown")
                hub_manager.peer_device_ids[websocket] = peer_device_id
                known_ids = set(msg.get("known_event_ids", []))

                async with AsyncSessionLocal() as session:
                    db_events = await session.scalars(select(EventModel.event_id))
                    server_ids = set(db_events)

                server_has_missing = list(server_ids - known_ids)
                peer_has_missing = list(known_ids - server_ids)

                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "EVENTS_OFFER",
                            "server_missing_for_peer": server_has_missing,
                            "peer_can_upload": peer_has_missing,
                            "total_server_events": len(server_ids),
                        }
                    )
                )

            elif msg_type == "GET_EVENTS":
                # Peer requests specific event records
                requested_ids = msg.get("event_ids", [])
                async with AsyncSessionLocal() as session:
                    events = await session.scalars(
                        select(EventModel).where(EventModel.event_id.in_(requested_ids))
                    )
                    records = [
                        {
                            "event_id": e.event_id,
                            "event_type": e.event_type,
                            "device_id": e.device_id,
                            "clock": {
                                "wall_time_ms": e.wall_time_ms,
                                "logical_counter": e.logical_counter,
                                "node_id": e.node_id,
                            },
                            "payload": e.payload,
                            "signature": e.signature,
                        }
                        for e in events
                    ]

                await websocket.send_text(
                    json.dumps({"type": "EVENTS_BATCH", "events": records})
                )

            elif msg_type == "PUBLISH_EVENTS":
                # Peer pushes new events to hub; persist and gossip broadcast to all other peers
                events_data = msg.get("events", [])
                persisted_events = []

                async with AsyncSessionLocal() as session:
                    for ev_dict in events_data:
                        ev_id = ev_dict.get("event_id")
                        existing = await session.scalar(
                            select(EventModel).where(EventModel.event_id == ev_id)
                        )
                        if not existing and ev_id:
                            clock_data = ev_dict.get("clock", {})
                            new_event = EventModel(
                                event_id=ev_id,
                                event_type=ev_dict.get("event_type", "report"),
                                device_id=ev_dict.get("device_id", "unknown"),
                                wall_time_ms=clock_data.get("wall_time_ms", 0),
                                logical_counter=clock_data.get("logical_counter", 0),
                                node_id=clock_data.get("node_id", "unknown"),
                                payload=ev_dict.get("payload", {}),
                                signature=ev_dict.get("signature", ""),
                                canonical_bytes_len=len(raw_text),
                            )
                            session.add(new_event)
                            persisted_events.append(ev_dict)

                    if persisted_events:
                        await session.commit()

                # Acknowledge to sender
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "PUBLISH_ACK",
                            "ingested_count": len(persisted_events),
                            "total_received": len(events_data),
                        }
                    )
                )

                # Broadcast newly published events to all other connected peers
                if persisted_events:
                    await hub_manager.broadcast(
                        {"type": "EVENTS_BROADCAST", "events": persisted_events},
                        sender=websocket,
                    )

            elif msg_type == "PING":
                await websocket.send_text(json.dumps({"type": "PONG"}))

    except WebSocketDisconnect:
        hub_manager.disconnect(websocket)
    except Exception:
        hub_manager.disconnect(websocket)
