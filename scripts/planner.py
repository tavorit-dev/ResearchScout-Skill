"""Research project initialization and scope suggestion (spec 7.1, spec 7.8).

The planner is the first stage of the pipeline: it creates one research
project on disk and states what that project covers. Everything later --
discovery, the compliance gate, collection, the research loop -- reads the
record written here, so the shape of this record is the contract the rest of
the Skill is built on.

Scope is a decision, not an omission (spec 7.8, invariant 9). The record
always carries explicit ``core`` / ``related`` / ``excluded`` lists, and it
carries whether a human has confirmed them. A scope the Skill proposed itself
is recorded as unconfirmed and must not be used for the hard scope check until
the user accepts it: an unconfirmed suggestion that silently acquires the
authority of a confirmed scope would turn a guess into a constraint on what
the research is allowed to look at.

The record written here is the one ``schemas/research.schema.json`` defines.
It carries no ``confirmed`` flag of its own, because the schema forbids
unknown properties (``additionalProperties: false``); the confirmation state
lives in ``state/scope.json`` next to the scope itself.

Nothing in this module opens a socket, so there is nothing here that could
bypass an access restriction.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from jsonschema import ValidationError

from scripts.lib.ids import new_id
from scripts.lib.paths import ensure_research_layout, research_dir
from scripts.lib.validate import validate_record

# The lifecycle state a freshly created project is in. The loop states in the
# schema describe a finished round, and no round has run yet.
STATUS_RUNNING = "RUNNING"

SCOPE_LISTS = ("core", "related", "excluded")

# ``research.yaml`` holds the record the schemas define; the sidecar holds only
# the confirmation state, which the schema has no room for.
RECORD_FILE = "research.yaml"
SCOPE_FILE = "scope.json"


def suggest_scope(objective, keywords):
    """Return an unconfirmed scope suggestion for a project's seed keywords.

    This is a deterministic placeholder, not a scope judgement. The seed
    keywords already state what the user wants covered, so they become the
    core list unchanged; deciding what is adjacent to them and what is
    deliberately out of range needs semantic judgement, which belongs to the
    host agent -- a script cannot call a model (spec 7.8, spec 11.3). The host
    agent writes its own suggestion through this same shape.

    A project created without a scope has no seed keywords to turn into a
    suggestion, so its core list stays empty. That is deliberate: the
    unconfirmed record says "no scope is established yet", and the host agent
    fills in a real suggestion from the objective. Filling the lists in here
    from the objective would be the semantic inference this placeholder exists
    to avoid.

    ``confirmed`` is always False. The suggestion is labelled "建议，请确认"
    and only a user confirmation may turn it into a scope the hard check
    enforces. ``objective`` is accepted so the host agent has somewhere to
    attach a semantic suggestion, and is not read here.
    """
    return {
        "core": list(keywords or []),
        "related": [],
        "excluded": [],
        "confirmed": False,
    }


def normalize_scope(scope):
    """Return scope with all three lists present, and its confirmation state.

    An absent or empty scope is an unconfirmed suggestion built from the seed
    keywords, never an empty scope treated as confirmed (spec 7.8).

    ``confirmed`` is True only when the caller says so explicitly. It is never
    inferred from the lists: a host agent backfilling its own semantic
    suggestion supplies lists too, and that is the very suggestion spec 7.8
    requires the user to confirm. Reading "lists were supplied" as "a human
    confirmed them" would make the unconfirmed path -- the common one --
    impossible to express, so every path defaults to unconfirmed and the
    confirming call site passes the flag.
    """
    supplied = scope or {}
    if not any(supplied.get(name) for name in SCOPE_LISTS):
        suggested = suggest_scope("", supplied.get("core"))
        suggested["confirmed"] = supplied.get("confirmed") is True
        return suggested
    normalized = {
        name: list(supplied.get(name) or []) for name in SCOPE_LISTS
    }
    normalized["confirmed"] = supplied.get("confirmed") is True
    return normalized


def _utc_now():
    """Return the current UTC time; a timestamp is read, never guessed."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def init_research(archive_root, *, domain, topic, objective, keywords,
                  time_window, scope=None):
    """Create a research project on disk and return its record.

    The project directory is ``archive/<domain>/<topic>/`` and both names are
    slugs: paths stay ASCII while the content inside them does not (spec 3.1
    C6), so a non-ASCII name raises here rather than creating a directory the
    rest of the archive cannot address.

    Three files are written. ``research.yaml`` is the record the schemas
    define -- the project's objective, its explicit scope and the period it
    targets. ``state/current.json`` is the same record plus the fields a
    resumed run needs (where the project lives, when it was created, which
    round it is on), and ``state/scope.json`` holds only the scope
    confirmation state, which the schema has no room for.

    ``research_id`` is an opaque ``res_`` id. The creation date is recorded as
    a field rather than encoded into the id, so the id stays a stable handle
    that no calendar change can invalidate.

    The returned record is the one callers can act on: it is the validated
    schema record with the scope confirmation state attached, matching what
    ``load_research`` reads back.

    This is a create operation. Initializing a project that already exists
    raises FileExistsError instead of overwriting it: the archive is a
    resumable, long-lived project (spec 9.3), so a second call -- a host agent
    re-running its first-run flow, or a user re-running init -- would mint a
    new ``research_id`` and reset the objective and the confirmation state
    while leaving ``documents/``, ``findings/`` and ``iterations/`` in place,
    orphaning findings under an id that no longer exists. Resuming a project
    means loading it, not initializing it again.
    """
    root = research_dir(archive_root, domain, topic)
    if (root / RECORD_FILE).exists():
        raise FileExistsError(
            f"research project already exists: {root}; "
            f"load it instead of initializing it again"
        )
    ensure_research_layout(root)
    resolved = normalize_scope(scope)
    created_at = _utc_now()

    record = {
        "research_id": new_id("res"),
        "domain": domain,
        "topic": topic,
        "objective": objective,
        "keywords": list(keywords or []),
        "scope": {name: list(resolved[name]) for name in SCOPE_LISTS},
        "time_window": _normalize_time_window(time_window),
        "status": STATUS_RUNNING,
    }
    validate_record("research", record)

    scope_state = {
        "confirmed": resolved["confirmed"],
        "lists": {name: list(resolved[name]) for name in SCOPE_LISTS},
    }

    current = dict(record)
    current.update({
        "archive_root": str(Path(archive_root)),
        "path": str(root),
        "created_at": created_at,
        "round": 0,
    })

    (root / RECORD_FILE).write_text(
        yaml.safe_dump(record, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    _write_json(root / "state" / "current.json", current)
    _write_json(root / "state" / SCOPE_FILE, scope_state)

    record["scope"] = _scope_view(record["scope"], resolved["confirmed"])
    return record


def load_research(root):
    """Return the record written by ``init_research`` for a project root.

    ``research.yaml`` is the record, and it is authoritative for all three
    scope lists: it is the file spec 7.8 says the confirmed scope is written
    to, and the file the design expects a user to edit, so an edit there has
    to be what the rest of the Skill sees. Only the confirmation state, which
    the schema has no room for, is read from ``state/scope.json`` and merged
    back under ``scope`` -- because a caller reading the scope is the caller
    that must know whether it may enforce it.

    Everything read here is checked before it is returned, so a caller can
    trust the shape. A record that fails ``research.schema.json``, or a
    sidecar that is not a JSON object, raises ValueError naming the offending
    path rather than handing back a malformed record the caller will trip over
    later. A missing file still raises FileNotFoundError, also naming the path.

    A sidecar with no ``confirmed`` key means no confirmation was ever
    recorded, so the scope reads as unconfirmed. The key is always present in
    the result and is always a bool: a caller must never be able to read an
    absent field as permission.
    """
    root = Path(root)
    record = _load_record(root / RECORD_FILE)
    confirmed = _load_confirmed(root / "state" / SCOPE_FILE)
    record["scope"] = _scope_view(record["scope"], confirmed)
    return record


def _scope_view(lists, confirmed):
    """Return the scope a caller sees: the three lists plus a bool flag."""
    scope = {name: list(lists.get(name) or []) for name in SCOPE_LISTS}
    scope["confirmed"] = confirmed is True
    return scope


def _load_record(path):
    """Read and schema-check ``research.yaml``, naming the path on failure."""
    try:
        record = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read research record {path}: {exc}") from exc
    try:
        validate_record("research", record)
    except ValidationError as exc:
        raise ValueError(
            f"research record {path} does not match research.schema.json: {exc.message}"
        ) from exc
    return record


def _load_confirmed(path):
    """Return the confirmation flag from the scope sidecar; absent means False.

    A sidecar that cannot be read or parsed, or that is not an object, is a
    corrupted record rather than an unconfirmed one: treating it as
    unconfirmed would silently discard a confirmation the user did give, so it
    raises with the path in the message.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read scope state {path}: {exc}") from exc
    try:
        state = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"scope state {path} is not valid JSON: {exc}") from exc
    if not isinstance(state, dict):
        raise ValueError(
            f"scope state {path} must be a JSON object, got {type(state).__name__}"
        )
    return state.get("confirmed") is True


def _normalize_time_window(time_window):
    """Return a schema-shaped time window from whatever the caller passed.

    The schema describes a window as ``{"start": ..., "end": ...}`` with either
    end open, but a caller naturally says "the last 30 days". Both spellings
    are accepted and the relative one is resolved against the current UTC time
    into a real start date: the schema's shape is a boundary the rest of the
    pipeline can compare, and no date is invented -- a missing or unreadable
    window leaves both ends null rather than guessing a period.

    A bool is not a day count even though Python says it is an int, so
    ``recent_days=True`` leaves the window open rather than silently becoming a
    one-day window.
    """
    window = time_window or {}
    start = window.get("start")
    end = window.get("end")
    if start is None and end is None:
        recent_days = window.get("recent_days")
        if _is_day_count(recent_days):
            now = datetime.now(timezone.utc)
            start = (now - timedelta(days=recent_days)).date().isoformat()
            end = now.date().isoformat()
    return {"start": start, "end": end}


def _is_day_count(value):
    """Return True when value is a usable positive number of days."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _write_json(path, payload):
    """Write JSON with a trailing newline, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
