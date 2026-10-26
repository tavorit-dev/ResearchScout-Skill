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

from scripts.lib.ids import new_id
from scripts.lib.paths import ensure_research_layout, research_dir
from scripts.lib.validate import validate_record

# The lifecycle state a freshly created project is in. The loop states in the
# schema describe a finished round, and no round has run yet.
STATUS_RUNNING = "RUNNING"

SCOPE_LISTS = ("core", "related", "excluded")


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
    keywords, never an empty scope treated as confirmed (spec 7.8). A caller
    that supplies lists is stating them, so those lists count as confirmed
    unless the caller says otherwise with an explicit ``confirmed`` flag.
    """
    supplied = scope or {}
    if not any(supplied.get(name) for name in SCOPE_LISTS):
        suggested = suggest_scope("", supplied.get("core"))
        suggested["confirmed"] = bool(supplied.get("confirmed"))
        return suggested
    normalized = {
        name: list(supplied.get(name) or []) for name in SCOPE_LISTS
    }
    normalized["confirmed"] = bool(supplied.get("confirmed", True))
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
    round it is on), and ``state/scope.json`` holds the scope together with
    its confirmation state, which the schema has no room for.

    ``research_id`` is an opaque ``res_`` id. The creation date is recorded as
    a field rather than encoded into the id, so the id stays a stable handle
    that no calendar change can invalidate.

    The returned record is the one callers can act on: it is the validated
    schema record with the scope confirmation state attached, matching what
    ``load_research`` reads back.
    """
    root = ensure_research_layout(research_dir(archive_root, domain, topic))
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

    scope_state = {name: list(resolved[name]) for name in SCOPE_LISTS}
    scope_state["confirmed"] = resolved["confirmed"]

    current = dict(record)
    current.update({
        "archive_root": str(Path(archive_root)),
        "path": str(root),
        "created_at": created_at,
        "round": 0,
    })

    (root / "research.yaml").write_text(
        yaml.safe_dump(record, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    _write_json(root / "state" / "current.json", current)
    _write_json(root / "state" / "scope.json", scope_state)

    record["scope"] = scope_state
    return record


def load_research(root):
    """Return the record written by ``init_research`` for a project root.

    The record is read back from ``research.yaml`` so that edits a user makes
    to it -- correcting a scope, moving a time window -- are what the rest of
    the Skill sees, not a stale copy. The confirmation state lives beside it
    in ``state/scope.json`` and is merged back under ``scope``, because a
    caller reading the scope is the caller that must know whether it may
    enforce it.

    A file that is missing raises FileNotFoundError naming the path; that is a
    project that was never initialized or whose archive was moved, and
    guessing a record from a partial directory would be worse than saying so.
    """
    root = Path(root)
    record = yaml.safe_load((root / "research.yaml").read_text(encoding="utf-8"))
    scope_state = json.loads(
        (root / "state" / "scope.json").read_text(encoding="utf-8")
    )
    record["scope"] = scope_state
    return record


def _normalize_time_window(time_window):
    """Return a schema-shaped time window from whatever the caller passed.

    The schema describes a window as ``{"start": ..., "end": ...}`` with either
    end open, but a caller naturally says "the last 30 days". Both spellings
    are accepted and the relative one is resolved against the current UTC time
    into a real start date: the schema's shape is a boundary the rest of the
    pipeline can compare, and no date is invented -- a missing or unreadable
    window leaves both ends null rather than guessing a period.
    """
    window = time_window or {}
    start = window.get("start")
    end = window.get("end")
    if start is None and end is None:
        recent_days = window.get("recent_days")
        if isinstance(recent_days, int) and recent_days > 0:
            now = datetime.now(timezone.utc)
            start = (now - timedelta(days=recent_days)).date().isoformat()
            end = now.date().isoformat()
    return {"start": start, "end": end}


def _write_json(path, payload):
    """Write JSON with a trailing newline, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
