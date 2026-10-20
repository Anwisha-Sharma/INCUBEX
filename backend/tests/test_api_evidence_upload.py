"""
API tests for Evidence Image Upload and Privacy EXIF Stripping.
Module E2 & Phase 5 Requirement: Image upload path and GPS stripping verification.
"""

import base64
import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app


def build_synthetic_jpeg_with_exif_gps() -> bytes:
    """
    Construct a synthetic binary JPEG stream with:
    - SOI: 0xFFD8
    - APP1 (EXIF with GPS data): 0xFFE1
    - DQT: 0xFFDB
    - SOS: 0xFFDA (image compressed data)
    - EOI: 0xFFD9
    """
    buf = bytearray()
    buf.extend(b"\xff\xd8")  # SOI

    # APP1 segment with fake EXIF GPS payload
    exif_payload = b"Exif\x00\x00GPSLatitude=19.0760GPSLongitude=72.8777Camera=RescueMeshDrone"
    seg_len = len(exif_payload) + 2
    buf.extend(b"\xff\xe1")
    buf.extend(seg_len.to_bytes(2, byteorder="big"))
    buf.extend(exif_payload)

    # Standard image data segments
    buf.extend(b"\xff\xdb\x00\x04\x01\x02")  # DQT
    buf.extend(b"\xff\xda\x00\x02")          # SOS
    buf.extend(b"\x12\x34\x56\x78")          # Compressed image stream
    buf.extend(b"\xff\xd9")                  # EOI

    return bytes(buf)


@pytest.mark.asyncio
async def test_evidence_upload_binary_strips_exif_gps_metadata():
    raw_jpeg = build_synthetic_jpeg_with_exif_gps()
    assert b"\xff\xe1" in raw_jpeg
    assert b"GPSLatitude" in raw_jpeg

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/evidence/upload",
            content=raw_jpeg,
            headers={"content-type": "image/jpeg", "x-filename": "incident_evidence.jpg"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["exif_stripped"] is True
        assert data["cleaned_size_bytes"] < data["original_size_bytes"]
        assert len(data["evidence_hash"]) == 64

        evidence_hash = data["evidence_hash"]

        # Retrieve sanitized image by hash
        get_resp = await client.get(f"/api/v1/evidence/{evidence_hash}")
        assert get_resp.status_code == 200
        cleaned_bytes = get_resp.content

        # Verify GPS and APP1 markers are completely gone
        assert b"\xff\xe1" not in cleaned_bytes
        assert b"GPSLatitude" not in cleaned_bytes
        # Verify JPEG SOI header remains intact
        assert cleaned_bytes.startswith(b"\xff\xd8")


@pytest.mark.asyncio
async def test_evidence_upload_json_base64_strips_exif():
    raw_jpeg = build_synthetic_jpeg_with_exif_gps()
    b64_str = base64.b64encode(raw_jpeg).decode("ascii")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/evidence/upload",
            json={"data": b64_str, "filename": "b64_evidence.jpg"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["exif_stripped"] is True
        assert data["filename"] == "b64_evidence.jpg"


@pytest.mark.asyncio
async def test_evidence_upload_non_jpeg_preserves_bytes():
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/evidence/upload",
            content=png_bytes,
            headers={"content-type": "image/png"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["exif_stripped"] is False
        assert data["cleaned_size_bytes"] == len(png_bytes)


@pytest.mark.asyncio
async def test_evidence_upload_empty_file_rejected():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/evidence/upload", content=b"")
        assert resp.status_code == 400

