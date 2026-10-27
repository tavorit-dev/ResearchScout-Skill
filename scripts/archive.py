"""Archive read/write layer: the system record of one research project.

Everything the earlier stages of the Skill produce lives in memory as a dict;
this module is where a research project's state, documents, findings,
iterations and summaries become files on disk. It is therefore the module that
keeps spec 9.3's promise that a run can be resumed after the process dies: a
project that writes nothing durable has no history, only a transcript. The
stakes are the reason for the three principles this module enforces:

1. **Validation before write (acceptance V12).** No record reaches disk
   without passing its JSON Schema. ``save_documents`` and ``save_findings``
   validate *every* record first and only then write anything, so one bad
   record cannot leave a half-written archive behind. The check is not
   decoration: ``finding.schema.json`` forbids unknown properties, and the
   scoring fields Task 14's ``compute_weight`` reads (``relevance_score``,
   ``evidence_score``, ``novelty_score``, ``guidance_score``) are not part of
   it, so a caller that forgets to strip them learns it here, at the boundary,
   rather than by means of an illegal record quietly entering the JSONL.

2. **Writes are atomic.** ``state/current.json``, ``findings.jsonl``,
   ``index/manifest.json`` and ``summaries/current.md`` are written to a
   temporary file in the same directory and moved into place with
   ``os.replace``. A process killed mid-write -- a budget timeout, a user
   interrupt -- leaves the previous file intact instead of a truncated JSON
   that no later run can parse.

3. **A read that fails says so, with its path.** ``load_state`` raises
   ``FileNotFoundError`` or ``ValueError`` naming the file rather than
   returning ``{}``. An empty dict would read as "round zero, budget untouched"
   and silently restart a project that was merely incomplete (spec 9.3). The
   first state is written by ``planner.init_research``; this module never
   invents one.

The record formats are the ones ``schemas/`` defines; document records keep
metadata and a reference to the body, never the body itself (spec 9.6, spec
9.8). The CSV export reuses ``scripts.lib.jsonl.write_csv`` so the
Excel-safe encoding stays in one place (acceptance V8).

Deliberately absent: spec 9.2 also sketches ``findings/evidence.jsonl`` and
``iterations/history.jsonl``. Nothing produces them in the v1 plan, evidence
already lives inside each finding's required ``evidence`` array, and one file
per round already records the iteration history. A second copy of either would
be a copy to keep in sync, so this module does not create them.

Nothing here opens a socket or calls a model, so the module cannot be a path
around an access restriction, and its tests need no network (acceptance V9).
"""
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

from scripts.lib.jsonl import read_records, upsert_by_id, write_csv
from scripts.lib.paths import ensure_research_layout
from scripts.lib.validate import validate_record

# Project-relative paths, all below the research root (spec 9.2).
STATE_FILE = Path("state") / "current.json"
DOCUMENTS_FILE = Path("documents") / "documents.jsonl"
DOCUMENTS_CSV = Path("documents") / "documents.csv"
FINDINGS_FILE = Path("findings") / "findings.jsonl"
SOURCES_FILE = Path("sources") / "selected_sources.jsonl"
SUMMARY_FILE = Path("summaries") / "current.md"
SNAPSHOTS_DIR = Path("summaries") / "snapshots"
ITERATIONS_DIR = Path("iterations")
MANIFEST_FILE = Path("index") / "manifest.json"
RESEARCH_FILE = Path("research.yaml")

# The CSV columns spec 9.7 calls useful for a table view, in this exact order.
CSV_FIELDS = (
    "document_id", "source_name", "title", "published_at",
    "crawled_at", "summary", "relevance_score", "canonical_url",
)

# ``iterations/round-NNN.json``. Three digits fit a thousand rounds, far past
# any budget the Skill enforces; longer numbers simply take more digits.
ROUND_DIGITS = 3


def save_state(root, state):
    """Atomically write the current research state to ``state/current.json``.

    ``state`` is a JSON object the caller owns; this module does not define
    its fields. The write goes through a same-directory temporary file and
    ``os.replace``, so a run interrupted mid-write leaves the previous state
    readable rather than a truncated file (spec 9.3).
    """
    root = ensure_research_layout(root)
    _write_json(root / STATE_FILE, state)


def load_state(root):
    """Read ``state/current.json``, naming the path when it is unusable.

    Raises
    ------
    FileNotFoundError
        The file does not exist, with its path in the message. This is *not*
        "the project has not started": ``planner.init_research`` writes the
        initial state, so a missing file means the project is incomplete, and
        returning ``{}`` would restart it at round zero with its budget reset.
    ValueError
        The file cannot be read, is not valid JSON (truncated, ``"[]"``,
        ``"null"``), or is valid JSON but not an object, with its path in the
        message.
    """
    path = Path(root) / STATE_FILE
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"research state file not found: {path}; the project is "
            f"incomplete, not unstarted -- initialize it with "
            f"planner.init_research or restore the file"
        ) from exc
    except OSError as exc:
        raise ValueError(f"cannot read research state {path}: {exc}") from exc

    try:
        state = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"research state {path} is not valid JSON: {exc}") from exc
    if not isinstance(state, dict):
        raise ValueError(
            f"research state {path} must be a JSON object, "
            f"got {type(state).__name__}"
        )
    return state


def save_documents(root, docs):
    """Upsert document records by ``document_id`` and return how many were given.

    Every record is validated against ``document.schema.json`` before the
    first write, so an invalid record leaves the existing file untouched
    instead of leaving a partial upsert behind. Upsert (rather than replace)
    is the right shape because the same document is written several times as
    it moves through ``FETCHED`` to ``PERSISTED``.

    An empty list returns 0 and creates no file: a project with no documents
    yet should not carry an empty archive file.

    ``upsert_by_id`` rewrites the whole file rather than replacing it through
    a temporary file. That non-atomicity is a known deferred minor from the
    JSONL library (Task 4) and is deliberately not reimplemented here.
    """
    records = list(docs)
    if not records:
        return 0
    for record in records:
        validate_record("document", record)

    root = ensure_research_layout(root)
    path = root / DOCUMENTS_FILE
    for record in records:
        upsert_by_id(path, record, "document_id")
    return len(records)


def save_findings(root, findings):
    """Replace ``findings/findings.jsonl`` with the complete finding set.

    The caller passes the **complete merged set**, not the round's new
    findings: Task 16 merges existing and new findings with
    ``reflection.merge_findings`` and hands over the result. This function
    replaces the file wholesale, so a partial list would erase earlier
    findings rather than accumulate them.

    All records are validated against ``finding.schema.json`` before anything
    is written. A scored finding -- one still carrying the ``*_score`` fields
    ``compute_weight`` reads -- fails this check, which is the intended
    contract: those fields are in-memory inputs, not persistable record
    fields. The file is replaced atomically.
    """
    records = list(findings)
    for record in records:
        validate_record("finding", record)

    root = ensure_research_layout(root)
    text = "".join(
        json.dumps(record, ensure_ascii=False) + "\n" for record in records
    )
    _atomic_write_text(root / FINDINGS_FILE, text)


def save_iteration(root, round_no, record):
    """Write one round's history record as ``iterations/round-NNN.json``.

    ``round_no`` is zero-padded so the files sort chronologically as text.
    The record is serialised as JSON; no schema is defined for an iteration
    record, whose shape is the caller's (Task 16).

    Raises
    ------
    ValueError
        ``round_no`` is not a non-negative integer. A bool is not a round
        number even though Python calls it an int.
    """
    if isinstance(round_no, bool) or not isinstance(round_no, int) or round_no < 0:
        raise ValueError(
            f"round number must be a non-negative integer, got {round_no!r}"
        )
    root = ensure_research_layout(root)
    path = root / ITERATIONS_DIR / f"round-{round_no:0{ROUND_DIGITS}d}.json"
    _write_json(path, record)


def save_summary(root, text):
    """Write the research summary to ``current.md`` and add a snapshot.

    ``current.md`` is always the latest summary (spec 9.2, acceptance V4); the
    snapshot keeps every earlier version so a resumed run can show how the
    findings evolved. Both are written atomically, and the snapshot name is
    unique so two consecutive calls never overwrite the first.
    """
    root = ensure_research_layout(root)
    _atomic_write_text(root / SUMMARY_FILE, text)
    snapshot = _unique_snapshot_path(root / SNAPSHOTS_DIR)
    _atomic_write_text(snapshot, text)


def rebuild_manifest(root):
    """Count what is on disk into ``index/manifest.json`` and return it.

    Missing files count as 0 and a missing ``research.yaml`` yields
    ``research_id`` of ``None``: the manifest reports the disk as it is and
    does not invent a project identity. ``research_id`` is read, never
    generated, so a wrong manifest cannot mint a second id for the project.

    Raises
    ------
    ValueError
        ``research.yaml`` exists but cannot be read or parsed as YAML. A file
        that is present and broken is not the same as an absent one, and
        reporting ``null`` would hide the corruption the manifest exists to
        surface.

    The returned dict is the one written to the file.
    """
    root = Path(root)
    manifest = {
        "document_count": len(read_records(root / DOCUMENTS_FILE)),
        "finding_count": len(read_records(root / FINDINGS_FILE)),
        "source_count": len(read_records(root / SOURCES_FILE)),
        "round_count": _count_rounds(root / ITERATIONS_DIR),
        "generated_at": _utc_now(),
        "research_id": _read_research_id(root / RESEARCH_FILE),
    }
    _write_json(root / MANIFEST_FILE, manifest)
    return manifest


def export_csv(root):
    """Export the document table to ``documents/documents.csv`` and return it.

    Rows come from the JSONL archive and columns are exactly ``CSV_FIELDS``,
    in that order. The file is UTF-8 with a BOM so Excel opens it correctly
    (acceptance V8), which ``write_csv`` already guarantees. A missing or empty
    archive still produces a valid CSV with its header row, because an empty
    research project is still exportable.
    """
    root = Path(root)
    path = root / DOCUMENTS_CSV
    write_csv(path, read_records(root / DOCUMENTS_FILE), list(CSV_FIELDS))
    return path


def _atomic_write_text(path, text):
    """Write ``text`` via a same-directory temporary file and ``os.replace``.

    ``tempfile.mkstemp`` creates the temporary file in ``path``'s directory,
    which is what makes ``os.replace`` atomic: a rename across filesystems is
    not. The content is flushed and fsynced before the rename so a rename
    never publishes bytes that are still buffered. On any failure the
    temporary file is removed and the original path is left as it was.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        # Both paths are best effort: the original exception is what the
        # caller needs, and a leftover temp file must not mask it.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _write_json(path, payload):
    """Atomically write a JSON object with a trailing newline."""
    _atomic_write_text(
        path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    )


def _unique_snapshot_path(directory):
    """Return an unused ``<UTC timestamp>.md`` path inside ``directory``.

    The timestamp carries microseconds, and a numeric suffix is appended if
    that is somehow already taken, so two summaries saved in quick succession
    always get two snapshots.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    candidate = directory / f"{stamp}.md"
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stamp}-{counter}.md"
        counter += 1
    return candidate


def _read_research_id(path):
    """Return ``research_id`` from ``research.yaml``; ``None`` when absent.

    A missing file, a YAML document that is not a mapping, and a mapping
    without a ``research_id`` all read as ``None`` -- there is no id on disk
    to report. A file that exists but cannot be parsed raises ValueError with
    its path instead, because that is corruption, not absence.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError(f"cannot read research record {path}: {exc}") from exc

    try:
        record = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ValueError(f"research record {path} is not valid YAML: {exc}") from exc
    if not isinstance(record, dict):
        return None
    research_id = record.get("research_id")
    return research_id if isinstance(research_id, str) else None


def _count_rounds(directory):
    """Count ``round-*.json`` files; a missing directory has none."""
    directory = Path(directory)
    if not directory.is_dir():
        return 0
    return sum(1 for _ in directory.glob("round-*.json"))


def _utc_now():
    """Return the current UTC time in ISO 8601, seconds precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
