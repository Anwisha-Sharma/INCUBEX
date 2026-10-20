import io
from typing import Tuple


def strip_image_exif(image_bytes: bytes) -> Tuple[bytes, str]:
    """
    Security & Privacy (Module E2):
    Strips EXIF metadata (GPS location coordinates, camera serial numbers, device names)
    from binary image uploads before storage or mesh transmission.
    Returns cleaned bytes and format type.
    """
    # Look for JPEG SOI marker (0xFFD8)
    if image_bytes.startswith(b"\xff\xd8"):
        # Scan and strip APP1 (EXIF: 0xFFE1) segments
        clean_buf = bytearray(b"\xff\xd8")
        idx = 2
        n = len(image_bytes)

        while idx < n:
            if image_bytes[idx] != 0xff:
                idx += 1
                continue

            marker = image_bytes[idx:idx + 2]
            if marker == b"\xff\xda":  # Start of Scan (SOS) - rest is compressed image data
                clean_buf.extend(image_bytes[idx:])
                break

            if idx + 4 > n:
                break

            seg_len = int.from_bytes(image_bytes[idx + 2:idx + 4], byteorder="big")
            # If marker is APP1 (0xFFE1, EXIF) or APP2 (0xFFE2), skip it
            if marker in (b"\xff\xe1", b"\xff\xe2"):
                idx += 2 + seg_len
            else:
                clean_buf.extend(image_bytes[idx:idx + 2 + seg_len])
                idx += 2 + seg_len

        return bytes(clean_buf), "image/jpeg"

    # For PNG or other binary data, return original bytes (PNG doesn't use JPEG APP1 EXIF)
    return image_bytes, "application/octet-stream"
