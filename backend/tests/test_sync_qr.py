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
