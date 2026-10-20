import random
import time
from typing import Any
from app.algorithms.grouping import group_incidents
from app.algorithms.hlc import HybridLogicalClockManager
from app.algorithms.priority import PriorityEngine
from app.algorithms.reconciliation import reconcile_incident_group
from app.core.canonical import compute_event_canonical_bytes, compute_canonical_hash
from app.core.security import generate_key_pair, sign_canonical_content
from app.models.events import HybridLogicalClock, RescueMeshEvent


class SimulatedDevice:
    """A virtual edge device with local ECDSA keys, HLC clock, and event store."""
    def __init__(self, device_name: str, clock_skew_ms: int = 0):
        self.device_name = device_name
        self.clock_skew_ms = clock_skew_ms
        self.priv_pem, self.pub_hex, self.device_id = generate_key_pair()
        self.clock_mgr = HybridLogicalClockManager(node_id=self.device_id)
        self.event_store: dict[str, RescueMeshEvent] = {}

    def get_physical_time(self) -> int:
        return int(time.time() * 1000) + self.clock_skew_ms

    def create_event(self, event_type: str, payload: dict[str, Any]) -> RescueMeshEvent:
        """Create, sign, and locally store an append-only event."""
        hlc = self.clock_mgr.tick(self.get_physical_time())
        canonical_bytes = compute_event_canonical_bytes(event_type, self.device_id, hlc, payload)
        event_id = compute_canonical_hash(canonical_bytes)
        sig = sign_canonical_content(self.priv_pem, canonical_bytes)

        event = RescueMeshEvent(
            event_id=event_id,
            event_type=event_type,
            device_id=self.device_id,
            clock=hlc,
            payload=payload,
            signature=sig,
        )
        self.event_store[event_id] = event
        return event

    def receive_event(self, event: RescueMeshEvent) -> bool:
        """Receive remote event: advance local HLC and store event idempotently."""
        self.clock_mgr.update(event.clock, self.get_physical_time())
        if event.event_id in self.event_store:
            return False  # Duplicate
        self.event_store[event.event_id] = event
        return True

    def derive_state(self) -> dict[str, Any]:
        """Derive incident groups, reconciled states, and priority scores from local event store."""
        events = list(self.event_store.values())
        groups = group_incidents(events)
        engine = PriorityEngine()

        derived = {}
        for inc_id, group in groups.items():
            reconciled = reconcile_incident_group(group)
            priority = engine.evaluate(reconciled, current_time_ms=self.get_physical_time())
            derived[inc_id] = {
                "incident_type": reconciled.incident_type,
                "current_severity": reconciled.current_severity,
                "current_people_affected": reconciled.current_people_affected,
                "current_people_trapped": reconciled.current_people_trapped,
                "has_conflict": reconciled.has_conflict,
                "score": priority.score,
                "level": priority.level.value,
                "factors": priority.factors,
            }
        return derived


class MultiDeviceSimulator:
    """
    Simulates opportunistic mesh exchange across disconnected devices
    subject to dropped packets, reordered delivery, clock skew, and duplicate transmissions.
    """
    def __init__(self, devices: list[SimulatedDevice]):
        self.devices = devices

    def broadcast_with_chaos(
        self,
        event: RescueMeshEvent,
        drop_rate: float = 0.2,
        duplicate_rate: float = 0.4,
    ) -> None:
        """Distribute event to peers with random drops and duplicates."""
        for dev in self.devices:
            if dev.device_id == event.device_id:
                continue
            if random.random() < drop_rate:
                continue  # Packet dropped

            dev.receive_event(event)
            if random.random() < duplicate_rate:
                # Transmit duplicate delivery
                dev.receive_event(event)

    def full_reconciliation_sync(self) -> None:
        """
        Simulate complete gossip sync where devices exchange missing events
        until all devices hold the union of all valid events.
        """
        all_events: dict[str, RescueMeshEvent] = {}
        for dev in self.devices:
            all_events.update(dev.event_store)

        # Shuffle delivery order to simulate out-of-order arrival
        event_list = list(all_events.values())
        random.shuffle(event_list)

        for dev in self.devices:
            for ev in event_list:
                dev.receive_event(ev)

    def verify_convergence(self) -> bool:
        """
        Verify that all devices produce the EXACT same derived state
        after syncing all events, regardless of initial message arrival order.
        """
        states = [d.derive_state() for d in self.devices]
        if not states:
            return True

        first_state = states[0]
        for idx, state in enumerate(states[1:], start=1):
            if state != first_state:
                return False
        return True
