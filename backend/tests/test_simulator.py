from app.algorithms.simulator import MultiDeviceSimulator, SimulatedDevice


def test_multi_device_convergence_under_network_chaos():
    # 4 virtual edge devices
    dev_a = SimulatedDevice("device_alpha", clock_skew_ms=0)
    dev_b = SimulatedDevice("device_beta", clock_skew_ms=120000)   # +2 minutes skew
    dev_c = SimulatedDevice("device_gamma", clock_skew_ms=-180000) # -3 minutes skew
    dev_d = SimulatedDevice("device_delta", clock_skew_ms=45000)   # +45 seconds skew

    devices = [dev_a, dev_b, dev_c, dev_d]
    sim = MultiDeviceSimulator(devices)

    # 1. Device A creates an initial report: Flood
    ev1 = dev_a.create_event(
        "report",
        {
            "incident_type": "flood",
            "latitude": 19.0760,
            "longitude": 72.8777,
            "severity": 2,
            "people_affected": 3,
            "people_trapped": False,
            "description": "Flooding near market square",
            "evidence_hashes": ["hash_market_photo"],
        },
    )
    sim.broadcast_with_chaos(ev1, drop_rate=0.25, duplicate_rate=0.5)

    # 2. Device B observes conflicting severity
    ev2 = dev_b.create_event(
        "observation",
        {
            "incident_id": f"inc_{ev1.event_id[:16]}",
            "observer_role": "civilian",
            "severity": 3,
            "people_affected": 8,
            "people_trapped": True,
            "description": "Water rising rapidly, people trapped in shop",
            "evidence_hashes": [],
        },
    )
    sim.broadcast_with_chaos(ev2, drop_rate=0.3, duplicate_rate=0.6)

    # 3. Device C reports completely separate incident: Fire 5km away
    ev3 = dev_c.create_event(
        "report",
        {
            "incident_type": "fire",
            "latitude": 19.1200,
            "longitude": 72.8500,
            "severity": 3,
            "people_affected": 12,
            "people_trapped": True,
            "description": "Transformer explosion and warehouse fire",
            "evidence_hashes": ["hash_fire_video"],
        },
    )
    sim.broadcast_with_chaos(ev3, drop_rate=0.2, duplicate_rate=0.4)

    # 4. Device D (Responder) dispatches and updates status
    ev4 = dev_d.create_event(
        "status",
        {
            "incident_id": f"inc_{ev3.event_id[:16]}",
            "status": "en_route",
            "note": "Unit 4 dispatched",
        },
    )
    sim.broadcast_with_chaos(ev4, drop_rate=0.1, duplicate_rate=0.3)

    # 5. Full opportunistic reconciliation gossip sync
    sim.full_reconciliation_sync()

    # 6. Verify property: ALL devices converge to the exact same derived state
    converged = sim.verify_convergence()
    assert converged is True

    # Validate state properties
    state = dev_a.derive_state()
    assert len(state) == 2  # Exactly 2 incidents: Flood and Fire

    flood_id = f"inc_{ev1.event_id[:16]}"
    assert flood_id in state
    assert state[flood_id]["has_conflict"] is True  # Preserved severity conflict (2 vs 3)
    assert state[flood_id]["current_severity"] == 3 # Latest observation won current value
    assert state[flood_id]["current_people_trapped"] is True

    fire_id = f"inc_{ev3.event_id[:16]}"
    assert fire_id in state
    assert state[fire_id]["level"] == "HIGH"
