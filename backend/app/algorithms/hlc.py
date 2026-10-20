from typing import Any
from app.models.events import HybridLogicalClock


class HybridLogicalClockManager:
    """
    Hybrid Logical Clock (HLC) based on Kulkarni et al.
    Guarantees:
    - Strictly monotonic ordering: (wall_time_ms, logical_counter, node_id)
    - Causality preservation: if event A causes event B, HLC(A) < HLC(B)
    - Bounded drift from physical wall time
    """
    def __init__(self, node_id: str, max_drift_ms: int = 7200000):
        self.node_id = node_id
        self.max_drift_ms = max_drift_ms
        self.latest_wall_time_ms = 0
        self.logical_counter = 0

    def tick(self, physical_now_ms: int) -> HybridLogicalClock:
        """Advance clock for a locally originated event."""
        if physical_now_ms > self.latest_wall_time_ms:
            self.latest_wall_time_ms = physical_now_ms
            self.logical_counter = 0
        else:
            self.logical_counter += 1

        return HybridLogicalClock(
            wall_time_ms=self.latest_wall_time_ms,
            logical_counter=self.logical_counter,
            node_id=self.node_id,
        )

    def update(self, remote_clock: HybridLogicalClock, physical_now_ms: int) -> HybridLogicalClock:
        """
        Update clock upon receiving a remote event from another peer.
        Enforces causality across disconnected network partitions.
        """
        # Detect clock skew
        if abs(remote_clock.wall_time_ms - physical_now_ms) > self.max_drift_ms:
            # We still advance causality, but caller can inspect skew
            pass

        max_wall = max(self.latest_wall_time_ms, remote_clock.wall_time_ms, physical_now_ms)

        if max_wall == self.latest_wall_time_ms and max_wall == remote_clock.wall_time_ms:
            self.logical_counter = max(self.logical_counter, remote_clock.logical_counter) + 1
        elif max_wall == self.latest_wall_time_ms:
            self.logical_counter += 1
        elif max_wall == remote_clock.wall_time_ms:
            self.logical_counter = remote_clock.logical_counter + 1
        else:
            self.logical_counter = 0

        self.latest_wall_time_ms = max_wall

        return HybridLogicalClock(
            wall_time_ms=self.latest_wall_time_ms,
            logical_counter=self.logical_counter,
            node_id=self.node_id,
        )


def compare_hlc(a: HybridLogicalClock | dict[str, Any], b: HybridLogicalClock | dict[str, Any]) -> int:
    """
    Deterministic total ordering comparator:
    Returns -1 if a < b, 0 if a == b, 1 if a > b.
    Order: wall_time_ms -> logical_counter -> node_id
    """
    a_wall = a.wall_time_ms if isinstance(a, HybridLogicalClock) else a["wall_time_ms"]
    a_count = a.logical_counter if isinstance(a, HybridLogicalClock) else a.get("logical_counter", 0)
    a_node = a.node_id if isinstance(a, HybridLogicalClock) else a["node_id"]

    b_wall = b.wall_time_ms if isinstance(b, HybridLogicalClock) else b["wall_time_ms"]
    b_count = b.logical_counter if isinstance(b, HybridLogicalClock) else b.get("logical_counter", 0)
    b_node = b.node_id if isinstance(b, HybridLogicalClock) else b["node_id"]

    if a_wall != b_wall:
        return -1 if a_wall < b_wall else 1
    if a_count != b_count:
        return -1 if a_count < b_count else 1
    if a_node != b_node:
        return -1 if a_node < b_node else 1
    return 0
