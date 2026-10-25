"""Archive path construction. Paths are ASCII-only (spec 3.1 C6)."""
import re
from pathlib import Path

RESEARCH_SUBDIRS = (
    "state", "sources", "documents", "findings",
    "iterations", "summaries", "reports", "index",
)


def slugify(text):
    """Lowercase ASCII slug. Raises ValueError on non-ASCII input."""
    if not text.isascii():
        raise ValueError(f"slug must be ASCII: {text!r}")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    if not slug:
        raise ValueError(f"slug is empty: {text!r}")
    return slug


def research_dir(archive_root, domain, topic):
    """Return archive/<domain>/<topic>/, validating both are ASCII slugs."""
    return Path(archive_root) / slugify(domain) / slugify(topic)


def ensure_research_layout(root):
    """Create the standard research project layout; return the root."""
    root = Path(root)
    for name in RESEARCH_SUBDIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / "documents" / "content").mkdir(parents=True, exist_ok=True)
    (root / "summaries" / "snapshots").mkdir(parents=True, exist_ok=True)
    return root
