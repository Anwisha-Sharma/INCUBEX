from app.algorithms.hlc import HybridLogicalClockManager, compare_hlc
from app.models.events import HybridLogicalClock


def test_hlc_tick_advances_monotonically():
    mgr = HybridLogicalClockManager("node_a")

    c1 = mgr.tick(1000)
    assert c1.wall_time_ms == 1000
    assert c1.logical_counter == 0
    assert c1.node_id == "node_a"

    # Same physical time -> increment logical counter
    c2 = mgr.tick(1000)
    assert c2.wall_time_ms == 1000
    assert c2.logical_counter == 1

    # Later physical time -> reset counter
    c3 = mgr.tick(1005)
    assert c3.wall_time_ms == 1005
    assert c3.logical_counter == 0


def test_hlc_update_advances_causality():
    mgr_a = HybridLogicalClockManager("node_a")
    mgr_b = HybridLogicalClockManager("node_b")

    ca1 = mgr_a.tick(1000)

    # Node B at physical time 950 receives message from Node A at 1000
    cb1 = mgr_b.update(ca1, 950)
    # Node B's clock must advance to at least 1000
    assert cb1.wall_time_ms == 1000
    assert cb1.logical_counter == 1
    assert compare_hlc(ca1, cb1) == -1


def test_hlc_compare_total_ordering():
    c1 = HybridLogicalClock(wall_time_ms=1000, logical_counter=0, node_id="node_a")
    c2 = HybridLogicalClock(wall_time_ms=1000, logical_counter=1, node_id="node_a")
    c3 = HybridLogicalClock(wall_time_ms=1000, logical_counter=1, node_id="node_b")
    c4 = HybridLogicalClock(wall_time_ms=1001, logical_counter=0, node_id="node_a")

    assert compare_hlc(c1, c2) == -1
    assert compare_hlc(c2, c3) == -1  # "node_a" < "node_b"
    assert compare_hlc(c3, c4) == -1
    assert compare_hlc(c1, c1) == 0
