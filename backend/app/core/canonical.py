import hashlib
import json
from typing import Any


def to_canonical_json_bytes(data: Any) -> bytes:
    """
    Produce canonical, deterministic JSON bytes:
    - Keys sorted recursively at all dictionary levels
    - Minimal separators (',', ':') with no extra whitespace
    - Strict UTF-8 encoding
    - Ensures identical byte representation across Python, TypeScript, and Web Crypto API.
    """
    def _normalize(item: Any) -> Any:
        if isinstance(item, dict):
            return {k: _normalize(item[k]) for k in sorted(item.keys())}
        elif isinstance(item, (list, tuple)):
            return [_normalize(x) for x in item]
        elif hasattr(item, "model_dump"):
            return _normalize(item.model_dump(mode="json"))
        return item

    normalized = _normalize(data)
    json_str = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return json_str.encode("utf-8")


def compute_canonical_hash(canonical_bytes: bytes) -> str:
    """Compute SHA-256 hexadecimal digest of canonical bytes."""
    return hashlib.sha256(canonical_bytes).hexdigest()


def compute_event_canonical_bytes(
    event_type: str,
    device_id: str,
    clock: dict[str, Any] | Any,
    payload: dict[str, Any] | Any,
) -> bytes:
    """
    Build the canonical serialization of an event's immutable identity content.
    Excludes event_id and signature so that event_id is strictly content-addressed.
    """
    clock_dict = clock.model_dump(mode="json") if hasattr(clock, "model_dump") else clock
    payload_dict = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload

    content = {
        "clock": clock_dict,
        "device_id": device_id,
        "event_type": event_type,
        "payload": payload_dict,
    }
    return to_canonical_json_bytes(content)


def compute_event_id(
    event_type: str,
    device_id: str,
    clock: dict[str, Any] | Any,
    payload: dict[str, Any] | Any,
) -> str:
    """Compute the SHA-256 content-addressed Event ID."""
    canonical_bytes = compute_event_canonical_bytes(event_type, device_id, clock, payload)
    return compute_canonical_hash(canonical_bytes)
