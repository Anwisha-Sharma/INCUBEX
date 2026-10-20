import base64
import hashlib
from typing import Tuple
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.exceptions import InvalidSignature
from app.core.canonical import compute_event_canonical_bytes


def generate_key_pair() -> Tuple[str, str, str]:
    """
    Generate a new ECDSA P-256 key pair.
    Returns:
        (private_key_pem, public_key_hex, device_id)
    where device_id is the SHA-256 hex digest of the raw uncompressed public key bytes.
    """
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    public_key = private_key.public_key()
    raw_public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    public_key_hex = raw_public_bytes.hex()
    device_id = hashlib.sha256(raw_public_bytes).hexdigest()

    return private_pem, public_key_hex, device_id


def derive_device_id_from_public_key_bytes(raw_pub_bytes: bytes) -> str:
    """Derive deterministic device_id from raw uncompressed public key bytes."""
    return hashlib.sha256(raw_pub_bytes).hexdigest()


def load_public_key_from_hex(public_key_hex: str) -> ec.EllipticCurvePublicKey:
    """Load EC public key from uncompressed hex string (65 bytes, starts with 04)."""
    raw_bytes = bytes.fromhex(public_key_hex.strip())
    return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw_bytes)


def sign_canonical_content(private_pem: str, canonical_bytes: bytes) -> str:
    """
    Sign canonical bytes using ECDSA P-256 + SHA-256.
    Returns standard raw r || s (64 bytes) hex string compatible with Web Crypto API.
    """
    private_key = serialization.load_pem_private_key(private_pem.encode("utf-8"), password=None)
    der_signature = private_key.sign(canonical_bytes, ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der_signature)
    raw_sig = r.to_bytes(32, byteorder="big") + s.to_bytes(32, byteorder="big")
    return raw_sig.hex()


def verify_signature(
    public_key_hex: str,
    signature_str: str,
    canonical_bytes: bytes,
) -> bool:
    """
    Verify ECDSA signature against canonical bytes.
    Accepts signature as either:
    1. 64-byte raw hex (IEEE P1363 standard from Web Crypto API export)
    2. Hex-encoded DER signature
    3. Base64-encoded raw/DER signature
    """
    try:
        pub_key = load_public_key_from_hex(public_key_hex)
        sig_str_clean = signature_str.strip()

        # Try decoding as hex, fallback to base64
        try:
            sig_bytes = bytes.fromhex(sig_str_clean)
        except ValueError:
            sig_bytes = base64.b64decode(sig_str_clean)

        # If exactly 64 bytes, convert IEEE P1363 (r || s) into DER
        if len(sig_bytes) == 64:
            r = int.from_bytes(sig_bytes[:32], byteorder="big")
            s = int.from_bytes(sig_bytes[32:], byteorder="big")
            der_signature = utils.encode_dss_signature(r, s)
        else:
            der_signature = sig_bytes

        pub_key.verify(der_signature, canonical_bytes, ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False
