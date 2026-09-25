"""Raw event hashing.

Hashing the exact raw bytes as received (before any parsing/normalization)
is what guarantees traceability back to the original log line, regardless
of whether parsing later succeeds, partially succeeds, or fails.
"""
import hashlib


def sha256_hex(raw_event: str) -> str:
    """Compute the SHA-256 hex digest of a raw event string (UTF-8 encoded)."""
    return hashlib.sha256(raw_event.encode("utf-8")).hexdigest()
