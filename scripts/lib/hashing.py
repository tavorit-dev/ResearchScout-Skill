"""Content fingerprinting for the second deduplication layer (spec 10.1)."""
import hashlib
import re


def content_hash(text):
    """SHA-256 of whitespace-normalized text."""
    normalized = re.sub(r"\s+", " ", text or "").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
