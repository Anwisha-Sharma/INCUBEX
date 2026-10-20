import base64
import hashlib
import json
import math
import uuid
import zlib
from typing import Any, Tuple


class QRChunker:
    """
    Tier 0 Offline Synchronization Transport:
    Compresses events using Deflate, encodes in Base64url, and splits into numbered QR frames.
    Format:
        RM1:<transfer_id>:<frame_index>:<total_frames>:<sha256_prefix>:<chunk_data>
    """
    PREFIX = "RM1"
    DEFAULT_CHUNK_SIZE = 220  # Safe byte length for dense mobile QR scanning

    @classmethod
    def encode_to_frames(
        cls,
        payload_data: Any,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> list[str]:
        """
        Serializes JSON payload, applies Deflate compression, Base64url encodes,
        and splits into sequential frames.
        """
        # 1. Normalize and serialize payload
        if isinstance(payload_data, (dict, list)):
            json_bytes = json.dumps(payload_data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        elif isinstance(payload_data, str):
            json_bytes = payload_data.encode("utf-8")
        elif isinstance(payload_data, bytes):
            json_bytes = payload_data
        else:
            raise ValueError("Unsupported payload type for QR encoding")

        # 2. Compute full payload SHA-256 hash (12-char prefix used for frame validation)
        full_hash = hashlib.sha256(json_bytes).hexdigest()
        hash_prefix = full_hash[:12]

        # 3. Deflate compression (zlib standard, level 9)
        compressed = zlib.compress(json_bytes, level=9)

        # 4. Base64url encoding without padding
        b64url = base64.urlsafe_b64encode(compressed).decode("ascii").rstrip("=")

        # 5. Split into chunks
        total_len = len(b64url)
        total_frames = max(1, math.ceil(total_len / chunk_size))
        transfer_id = uuid.uuid4().hex[:8]

        frames = []
        for idx in range(total_frames):
            start = idx * chunk_size
            end = min(total_len, start + chunk_size)
            chunk = b64url[start:end]
            frame_str = f"{cls.PREFIX}:{transfer_id}:{idx}:{total_frames}:{hash_prefix}:{chunk}"
            frames.append(frame_str)

        return frames


class QRReassembler:
    """
    Progressively collects scanned QR frames, verifies integrity,
    and decompresses the complete transfer payload.
    """
    def __init__(self):
        # transfer_id -> { total_frames, hash_prefix, frames: {index: chunk} }
        self.transfers: dict[str, dict[str, Any]] = {}

    def add_frame(self, frame_str: str) -> dict[str, Any]:
        """
        Process a scanned frame string.
        Returns status dictionary with progress and completion state.
        """
        parts = frame_str.strip().split(":", 5)
        if len(parts) != 6 or parts[0] != QRChunker.PREFIX:
            raise ValueError("Invalid RescueMesh QR frame format")

        _, transfer_id, frame_idx_str, total_frames_str, hash_prefix, chunk = parts
        frame_idx = int(frame_idx_str)
        total_frames = int(total_frames_str)

        if transfer_id not in self.transfers:
            self.transfers[transfer_id] = {
                "total_frames": total_frames,
                "hash_prefix": hash_prefix,
                "frames": {},
            }

        session = self.transfers[transfer_id]
        session["frames"][frame_idx] = chunk

        received_count = len(session["frames"])
        is_complete = received_count == total_frames
        progress_pct = round((received_count / total_frames) * 100, 1)

        result: dict[str, Any] = {
            "transfer_id": transfer_id,
            "received_frames": received_count,
            "total_frames": total_frames,
            "progress_percent": progress_pct,
            "is_complete": is_complete,
            "payload": None,
        }

        if is_complete:
            # Reconstruct Base64url string in order
            ordered_chunks = [session["frames"][i] for i in range(total_frames)]
            full_b64 = "".join(ordered_chunks)

            # Re-add Base64url padding if needed
            pad_len = (4 - (len(full_b64) % 4)) % 4
            full_b64 += "=" * pad_len

            compressed_bytes = base64.urlsafe_b64decode(full_b64)
            decompressed_bytes = zlib.decompress(compressed_bytes)

            # Verify integrity prefix
            actual_hash = hashlib.sha256(decompressed_bytes).hexdigest()
            if not actual_hash.startswith(session["hash_prefix"]):
                raise ValueError("Integrity check failed: Reassembled payload hash mismatch")

            try:
                result["payload"] = json.loads(decompressed_bytes.decode("utf-8"))
            except UnicodeDecodeError:
                result["payload"] = decompressed_bytes

        return result
