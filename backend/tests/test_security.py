from app.core.canonical import compute_event_canonical_bytes
from app.core.security import (
    generate_key_pair,
    derive_device_id_from_public_key_bytes,
    sign_canonical_content,
    verify_signature,
)
from app.models.events import HybridLogicalClock, ReportPayload


def test_key_pair_generation_and_device_id():
    private_pem, public_key_hex, device_id = generate_key_pair()

    assert private_pem.startswith("-----BEGIN PRIVATE KEY-----")
    assert len(public_key_hex) == 130  # 65 bytes uncompressed point (04 + 32 byte X + 32 byte Y)
    assert public_key_hex.startswith("04")
    assert len(device_id) == 64  # SHA-256 hex


def test_signature_verification_success():
    private_pem, public_key_hex, device_id = generate_key_pair()

    clock = HybridLogicalClock(wall_time_ms=1771400000000, logical_counter=0, node_id=device_id)
    payload = ReportPayload(
        incident_type="medical_emergency",
        latitude=18.5204,
        longitude=73.8567,
        severity=2,
        people_affected=3,
        people_trapped=False,
        description="First aid supplies needed at shelter",
        evidence_hashes=[],
    )

    canonical_bytes = compute_event_canonical_bytes("report", device_id, clock, payload)
    sig_hex = sign_canonical_content(private_pem, canonical_bytes)

    assert len(sig_hex) == 128  # 64 bytes hex (r || s IEEE P1363)
    is_valid = verify_signature(public_key_hex, sig_hex, canonical_bytes)
    assert is_valid is True


def test_tampered_payload_signature_fails():
    private_pem, public_key_hex, device_id = generate_key_pair()
    clock = HybridLogicalClock(wall_time_ms=1771400000000, logical_counter=0, node_id=device_id)
    payload_original = {"description": "Normal situation", "severity": 0}
    payload_tampered = {"description": "Severe panic", "severity": 3}

    canonical_orig = compute_event_canonical_bytes("report", device_id, clock, payload_original)
    canonical_tamp = compute_event_canonical_bytes("report", device_id, clock, payload_tampered)

    sig_hex = sign_canonical_content(private_pem, canonical_orig)

    # Verifying original passes
    assert verify_signature(public_key_hex, sig_hex, canonical_orig) is True
    # Verifying tampered fails
    assert verify_signature(public_key_hex, sig_hex, canonical_tamp) is False


def test_wrong_public_key_fails():
    priv1, pub1, _ = generate_key_pair()
    priv2, pub2, _ = generate_key_pair()

    test_bytes = b"RescueMesh canonical payload test"
    sig = sign_canonical_content(priv1, test_bytes)

    assert verify_signature(pub1, sig, test_bytes) is True
    assert verify_signature(pub2, sig, test_bytes) is False
