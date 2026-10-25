"""Stable identifier generation."""
import secrets

PREFIXES = ("src", "doc", "res", "find", "act", "ev")


def new_id(prefix):
    """Return a stable-style id like 'src_8f12a1'."""
    if prefix not in PREFIXES:
        raise ValueError(f"unknown id prefix: {prefix}")
    return f"{prefix}_{secrets.token_hex(3)}"
