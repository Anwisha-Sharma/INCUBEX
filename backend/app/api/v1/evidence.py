import hashlib
import os
from pathlib import Path
import base64
from fastapi import APIRouter, HTTPException, Request, Response, status
from app.core.privacy import strip_image_exif

router = APIRouter(prefix="/evidence", tags=["Evidence & Media Privacy"])

MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB limit for mobile mesh uploads
EVIDENCE_DIR = Path("./data/evidence")


def get_evidence_storage_path(evidence_hash: str) -> Path:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    return EVIDENCE_DIR / f"{evidence_hash}.bin"


@router.post(
    "/upload",
    summary="Upload image evidence with automatic EXIF metadata stripping",
)
async def upload_evidence(request: Request):
    """
    Evidence Image Upload Endpoint:
    - Accepts direct binary image streams or JSON Base64 payloads (zero external multipart dependency).
    - Strips privacy-sensitive EXIF metadata (GPS coordinates, camera model, timestamps).
    - Computes content-addressed SHA-256 hash of cleaned bytes.
    - Saves sanitized image for local offline storage or mesh transmission.
    - Returns the content-addressed evidence_hash ready for inclusion in event payload.evidence_hashes.
    """
    content_type_header = request.headers.get("content-type", "")
    filename = request.headers.get("x-filename", "evidence.jpg")

    if "application/json" in content_type_header:
        try:
            body = await request.json()
            raw_b64 = body.get("data", "")
            raw_bytes = base64.b64decode(raw_b64)
            filename = body.get("filename", filename)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JSON or Base64 evidence payload",
            )
    else:
        raw_bytes = await request.body()

    if not raw_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )

    if len(raw_bytes) > MAX_IMAGE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Image exceeds maximum allowable size ({len(raw_bytes)} > {MAX_IMAGE_SIZE_BYTES} bytes)",
        )

    cleaned_bytes, content_type = strip_image_exif(raw_bytes)
    evidence_hash = hashlib.sha256(cleaned_bytes).hexdigest()
    exif_stripped = len(cleaned_bytes) < len(raw_bytes)

    # Persist sanitized bytes on disk for mesh serving
    storage_path = get_evidence_storage_path(evidence_hash)
    with open(storage_path, "wb") as f:
        f.write(cleaned_bytes)

    return {
        "evidence_hash": evidence_hash,
        "content_type": content_type,
        "original_size_bytes": len(raw_bytes),
        "cleaned_size_bytes": len(cleaned_bytes),
        "exif_stripped": exif_stripped,
        "filename": filename,
    }


@router.get(
    "/{evidence_hash}",
    summary="Retrieve sanitized evidence image by SHA-256 hash",
)
async def get_evidence(evidence_hash: str):
    """Serve sanitized evidence file by content address."""
    storage_path = get_evidence_storage_path(evidence_hash)
    if not storage_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Evidence with hash '{evidence_hash}' not found",
        )

    with open(storage_path, "rb") as f:
        data = f.read()

    media_type = "image/jpeg" if data.startswith(b"\xff\xd8") else "application/octet-stream"
    return Response(content=data, media_type=media_type)
