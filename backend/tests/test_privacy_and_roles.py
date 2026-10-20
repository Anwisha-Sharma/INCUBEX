import time
from app.core.privacy import strip_image_exif
from app.core.roles import RoleRegistry
from app.models.events import HybridLogicalClock, RescueMeshEvent


def test_exif_stripping_removes_app1_markers():
    # Construct a synthetic JPEG with an APP1 marker (0xFFE1) followed by image data
    # SOI (0xFFD8) + APP1 marker (0xFFE1, length 8, 'EXIF\0\0') + SOS (0xFFDA) + dummy data
    exif_segment = b"\xff\xe1\x00\x08EXIF\x00\x00"
    raw_jpeg = b"\xff\xd8" + exif_segment + b"\xff\xda\x00\x02\x12\x34\xff\xd9"

    cleaned_bytes, mime = strip_image_exif(raw_jpeg)

    assert mime == "image/jpeg"
    assert b"EXIF" not in cleaned_bytes
    assert cleaned_bytes.startswith(b"\xff\xd8")
    assert b"\xff\xda" in cleaned_bytes


def test_coordinator_role_grants_and_lease():
    coordinator_dev = "coord_dev_000000000000000000000001"
    responder_dev = "resp_dev_000000000000000000000002"
    now_ms = int(time.time() * 1000)

    registry = RoleRegistry()
    registry.bootstrap_coordinator(coordinator_dev)

    assert registry.get_effective_role(coordinator_dev) == "coordinator"
    assert registry.get_effective_role(responder_dev) == "civilian"

    # Grant responder role for 1 hour
    role_event = RescueMeshEvent(
        event_id="3333333333333333333333333333333333333333333333333333333333330001",
        event_type="roleGrant",
        device_id=coordinator_dev,
        clock=HybridLogicalClock(wall_time_ms=now_ms, logical_counter=0, node_id=coordinator_dev),
        payload={
            "target_device_id": responder_dev,
            "role": "responder",
            "lease_until_ms": now_ms + 3600000,
            "grantor_id": coordinator_dev,
        },
        signature="mock_sig_000000000000000000000000000000000000000000000000000000000000",
    )

    processed = registry.process_role_grant_event(role_event)
    assert processed is True
    assert registry.get_effective_role(responder_dev, current_time_ms=now_ms) == "responder"

    # After lease expires -> drops back to civilian
    expired_time = now_ms + 3600001
    assert registry.get_effective_role(responder_dev, current_time_ms=expired_time) == "civilian"
