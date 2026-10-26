"""JSON Schema validation for records crossing the agent/script boundary."""
import json
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"
SCHEMA_FILES = {
    kind: SCHEMA_DIR / f"{kind}.schema.json"
    for kind in ("research", "source", "document", "finding", "action")
}
_CACHE = {}


def _validator(kind):
    if kind not in SCHEMA_FILES:
        raise ValueError(f"unknown record kind: {kind}")
    if kind not in _CACHE:
        schema = json.loads(SCHEMA_FILES[kind].read_text(encoding="utf-8"))
        _CACHE[kind] = Draft202012Validator(schema)
    return _CACHE[kind]


def validate_record(kind, record):
    """Raise jsonschema.ValidationError if record does not match its schema."""
    _validator(kind).validate(record)
