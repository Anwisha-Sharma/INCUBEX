import pytest
from app.sync.qr import QRChunker, QRReassembler


def test_qr_encode_and_reassemble_roundtrip():
    original_payload = {
        "event_id": "test_event_12345",
        "description": "Flash flood evacuation ordered for Sector 4 with high urgency",
        "severity": 3,
        "coordinates": {"lat": 19.0760, "lon": 72.8777},
        "evidence_hashes": ["hash_a", "hash_b", "hash_c"],
    }

    # Split into small chunks to force multiple frames (e.g. chunk_size=50)
    frames = QRChunker.encode_to_frames(original_payload, chunk_size=50)
    assert len(frames) >= 2
    for f in frames:
        assert f.startswith("RM1:")
        parts = f.split(":")
        assert len(parts) == 6

    # Reassemble out-of-order
    reassembler = QRReassembler()
    # Feed frames in reverse order
    for f in reversed(frames[:-1]):
        status = reassembler.add_frame(f)
        assert status["is_complete"] is False
        assert status["payload"] is None

    # Feed the missing last frame
    final_status = reassembler.add_frame(frames[-1])
    assert final_status["is_complete"] is True
    assert final_status["progress_percent"] == 100.0
    assert final_status["payload"] == original_payload


def test_qr_tampered_frame_raises_integrity_error():
    payload = {"message": "Genuine emergency message"}
    frames = QRChunker.encode_to_frames(payload, chunk_size=80)

    # Tamper with the chunk data of the first frame
    parts = frames[0].split(":")
    tampered_chunk = "AAAA" + parts[5][4:]
    tampered_frame = f"{parts[0]}:{parts[1]}:{parts[2]}:{parts[3]}:{parts[4]}:{tampered_chunk}"

    reassembler = QRReassembler()
    with pytest.raises(Exception):
        for idx, f in enumerate(frames):
            if idx == 0:
                reassembler.add_frame(tampered_frame)
            else:
                reassembler.add_frame(f)


def test_qr_duplicate_frames_handled_safely():
    payload = {"status": "all_clear", "responders": 4}
    frames = QRChunker.encode_to_frames(payload, chunk_size=40)
    assert len(frames) >= 2

    reassembler = QRReassembler()
    # Add frame 0 twice
    s1 = reassembler.add_frame(frames[0])
    assert s1["received_frames"] == 1
    s2 = reassembler.add_frame(frames[0])
    assert s2["received_frames"] == 1  # Deduped in frames dict

    # Feed remaining frames
    for f in frames[1:]:
        final = reassembler.add_frame(f)

    assert final["is_complete"] is True
    assert final["payload"] == payload


def test_qr_invalid_frame_bounds_raise_value_error():
    reassembler = QRReassembler()

    # Out of range index: frame_idx 5 on total_frames 2
    with pytest.raises(ValueError, match="out of range"):
        reassembler.add_frame("RM1:abc12345:5:2:hashpref:chunkdata")

    # Zero total frames
    with pytest.raises(ValueError, match="greater than zero"):
        reassembler.add_frame("RM1:abc12345:0:0:hashpref:chunkdata")

    # Invalid non-numeric frames
    with pytest.raises(ValueError, match="valid integers"):
        reassembler.add_frame("RM1:abc12345:NaN:two:hashpref:chunkdata")


def test_qr_abandoned_transfer_cleanup():
    import time
    reassembler = QRReassembler()
    frame = "RM1:aband001:0:5:hash12345678:chunkdata"
    reassembler.add_frame(frame)
    assert "aband001" in reassembler.transfers

    # Backdate session updated_at by 700 seconds
    reassembler.transfers["aband001"]["updated_at"] = time.time() - 700

    pruned = reassembler.cleanup_expired_transfers(max_age_seconds=600)
    assert pruned == 1
    assert "aband001" not in reassembler.transfers

