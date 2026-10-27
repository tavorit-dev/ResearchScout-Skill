"""User authorisation policies for ``REVIEW_REQUIRED`` sources (spec 6.5, V13).

``REVIEW_REQUIRED`` never becomes ``ALLOWED`` by itself. A human decides, and
the program records that decision; hand-editing the source record or the
registry is not the path. This module owns both halves of that record:

* ``save_policy`` validates a user authorisation and writes it to
  ``archive/registry/source_policies.jsonl``, replacing any earlier record for
  the same source so the latest decision wins.
* ``resolve_policy`` answers the gate's question -- "is there a live, in-scope
  authorisation for exactly this source, address and access method?" -- and
  returns ``None`` for anything expired, out of scope or not ``ALLOWED``.

Two safety rules shape the code:

1. **A malformed authorisation must not be read as permission.** Reading the
   registry validates every record and raises ``ValueError`` rather than
   skipping a corrupt line: a damaged authorization has to be visible, because
   the alternative is silently dropping or widening a permission the user
   gave. Callers that cannot afford to stop (the research round) catch the
   error and proceed with no policies, which is the fail-closed direction.
2. **An authorisation is scoped and time-limited.** The record names the
   access method, the paths it covers and an ``expires_at``. Outside that
   scope, and after that moment, the source returns to ``REVIEW_REQUIRED``.

Nothing here fetches, resolves or dereferences an address: policies are data,
and the gate is the only module that consults them. Standard library only.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.lib.jsonl import read_records, upsert_by_id

# The registry lives at the archive root, shared by every research project
# (spec 9.1, spec 6.5). It is not inside a single project directory.
POLICIES_FILE = Path("registry") / "source_policies.jsonl"

# The two outcomes a user may record. A BLOCKED policy is a user decision that
# a source must not be collected from; it never yields permission. The gate
# only consults policies when it would otherwise return REVIEW_REQUIRED, so a
# BLOCKED policy leaves that status in place rather than overriding anything.
RESOLVED_ALLOWED = "ALLOWED"
RESOLVED_BLOCKED = "BLOCKED"
RESOLVED_STATUSES = (RESOLVED_ALLOWED, RESOLVED_BLOCKED)

# The only principal allowed to record a policy. A model-authored file with
# resolved_by "agent" is not a user authorisation.
RESOLVED_BY_USER = "user"


def _require_non_empty_string(policy, field, label=None):
    """Return ``policy[field]`` when it is a non-empty string, else raise."""
    value = policy.get(field)
    if not isinstance(value, str) or not value.strip():
        name = label or field
        raise ValueError(f"policy field {name!r} must be a non-empty string")
    return value


def _parse_timestamp(value, field):
    """Return the datetime for an ISO 8601 string, or raise a clear error."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"policy field {field!r} must be an ISO 8601 string")
    text = value.strip()
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(
            f"policy field {field!r} is not an ISO 8601 timestamp: {text!r}"
        ) from exc
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def validate_policy(policy):
    """Raise ``ValueError`` unless ``policy`` is a usable authorisation record.

    Required, per spec 6.5: ``source_id`` (non-empty string), ``resolved_status``
    (``ALLOWED`` or ``BLOCKED``), ``resolved_by`` (``"user"``), ``resolved_at``
    (ISO 8601), and ``scope`` with ``paths`` (a list of non-empty strings),
    ``access_method`` (non-empty string) and ``expires_at`` (ISO 8601).
    ``reason`` and ``check_record_ref`` are optional but, when present, must be
    strings.

    Unknown extra keys are left alone: the record is stored as the user wrote
    it, and an unknown field cannot grant anything on its own. The fields the
    resolver reads are all checked here.
    """
    if not isinstance(policy, dict):
        raise ValueError(f"policy must be a JSON object, got {type(policy).__name__}")
    _require_non_empty_string(policy, "source_id")
    status = policy.get("resolved_status")
    if status not in RESOLVED_STATUSES:
        raise ValueError(
            f"policy field 'resolved_status' must be one of "
            f"{', '.join(RESOLVED_STATUSES)}, got {status!r}"
        )
    if policy.get("resolved_by") != RESOLVED_BY_USER:
        raise ValueError(
            "policy field 'resolved_by' must be 'user': only an explicit user "
            "authorisation releases a REVIEW_REQUIRED source"
        )
    _parse_timestamp(policy.get("resolved_at"), "resolved_at")

    for optional in ("reason", "check_record_ref"):
        if optional in policy and not isinstance(policy[optional], str):
            raise ValueError(f"policy field {optional!r} must be a string when present")

    scope = policy.get("scope")
    if not isinstance(scope, dict):
        raise ValueError("policy field 'scope' must be a JSON object")
    paths = scope.get("paths")
    if not isinstance(paths, list) or not paths:
        raise ValueError(
            "policy field 'scope.paths' must be a non-empty list of absolute paths"
        )
    for entry in paths:
        if not isinstance(entry, str) or not entry.strip():
            raise ValueError(
                "policy field 'scope.paths' must contain non-empty strings"
            )
        if not entry.startswith("/"):
            raise ValueError(
                "policy field 'scope.paths' entries must be absolute paths "
                "starting with '/'"
            )
    _require_non_empty_string(scope, "access_method", label="scope.access_method")
    _parse_timestamp(scope.get("expires_at"), "scope.expires_at")


def save_policy(archive_root, policy):
    """Validate ``policy`` and write it to the archive's policy registry.

    The registry is ``<archive_root>/registry/source_policies.jsonl`` and the
    directory is created when needed (it sits at the archive root, not inside a
    research project). Upsert by ``source_id`` means the latest record for a
    source wins instead of accumulating contradictory authorisations. Returns
    ``"inserted"`` or ``"updated"`` from the JSONL writer.
    """
    validate_policy(policy)
    path = Path(archive_root) / POLICIES_FILE
    return upsert_by_id(path, policy, "source_id")


def load_policies(archive_root):
    """Return every policy record, or ``[]`` when the registry does not exist.

    Raises
    ------
    ValueError
        A line is not valid JSON, or a record fails ``validate_policy``. The
        path is named in the message. A corrupt authorization is not skipped:
        silently reading it as absent or as permission would be the wrong
        direction, and a caller that can continue without policies (the round)
        catches this error and does exactly that.
    """
    path = Path(archive_root) / POLICIES_FILE
    try:
        records = read_records(path)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"policy registry {path} is not valid JSONL: {exc}"
        ) from exc
    for index, record in enumerate(records):
        try:
            validate_policy(record)
        except ValueError as exc:
            raise ValueError(
                f"policy record {index} in {path} is invalid: {exc}"
            ) from exc
    return records


def resolve_policy(policies, *, source_id, path, access_method, now=None):
    """Return the live authorisation for this source/path/method, or ``None``.

    The latest record for ``source_id`` wins (file order; ``save_policy``
    replaces in place). It is returned only when all of the following hold:

    * ``resolved_status`` is ``ALLOWED`` -- a ``BLOCKED`` policy never yields
      permission;
    * ``scope.access_method`` equals ``access_method`` exactly;
    * ``path`` equals one of ``scope.paths`` or lies beneath it, compared by
      path segment;
    * ``now`` is strictly earlier than ``scope.expires_at``. A missing or
      unparseable expiry fails closed.

    ``path`` is expected to be the normalised absolute path the gate already
    decided on (``collector.normalise_path``), and ``scope.paths`` to be plain
    absolute paths. Both are compared as-is; a policy that names a path the
    gate never asks about is simply out of scope, which is the safe answer.
    """
    if not isinstance(source_id, str) or not source_id:
        return None

    latest = None
    for record in policies or []:
        if isinstance(record, dict) and record.get("source_id") == source_id:
            latest = record
    if latest is None:
        return None
    if latest.get("resolved_status") != RESOLVED_ALLOWED:
        return None

    scope = latest.get("scope")
    if not isinstance(scope, dict):
        return None
    if scope.get("access_method") != access_method:
        return None

    paths = scope.get("paths")
    if not isinstance(paths, list) or not isinstance(path, str):
        return None
    if not any(
        _path_matches(path, entry)
        for entry in paths
        if isinstance(entry, str) and entry.strip()
    ):
        return None

    expires_at = _parsed_timestamp(scope.get("expires_at"))
    clock = _utc(now)
    if expires_at is None or not clock < expires_at:
        return None
    return latest


def _path_matches(path, prefix):
    """Return True when ``path`` equals ``prefix`` or lies beneath it.

    A segment boundary is required, so ``/public`` does not cover
    ``/publicity``; ``/`` covers the whole tree. The conservative direction is
    the one used when the answer is unclear: no match means the source stays
    ``REVIEW_REQUIRED``.
    """
    prefix = prefix.strip()
    if not prefix.startswith("/"):
        return False
    if prefix == "/" or prefix.rstrip("/") == "":
        return path.startswith("/")
    bare = prefix.rstrip("/")
    bare_path = path.rstrip("/") or "/"
    return bare_path == bare or bare_path.startswith(bare + "/")


def _parsed_timestamp(value):
    """Return a timezone-aware datetime for an ISO 8601 string, else ``None``."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _utc(now):
    """Return ``now`` as a timezone-aware UTC datetime, defaulting to the clock."""
    clock = now if isinstance(now, datetime) else datetime.now(timezone.utc)
    if clock.tzinfo is None:
        return clock.replace(tzinfo=timezone.utc)
    return clock.astimezone(timezone.utc)
