"""The single-round executor: step 1 and step 6 of the research loop.

This module is deliberately **not** a research engine (spec 5.2). It executes
one round of the deterministic half of the loop and hands control back to the
host agent, which owns every semantic judgement:

* ``run_round`` is step 1 -- admission, compliance, collection, extraction,
  deduplication, persistence -- and returns a structured handoff
  (spec 5.6). It never decides what the next action should be.
* ``apply_results`` is step 6 -- validate the agent's backfill, merge it into
  research memory, and write the round's record. It never chooses a decision;
  the host supplies one, and it is only checked against the eight loop states
  of spec 7.9.

Four invariants meet here for the first time.

1. **Compliance before collection (invariant 1, V2).** For every verified
   source the round obtains a compliance decision first: it calls
   ``compliance_gate.check_compliance`` when the source carries no
   ``checked_at``, and honours the recorded decision when it does. Only
   ``ALLOWED`` reaches ``collector.collect``, whose
   ``assert_compliance_allows`` remains the structural backstop. Nothing in
   this module collects without a decision, trusts a permission status that no
   check recorded, or retries past a refusal.

2. **Raw records, analysis and conclusions stay apart (invariant 5).** The
   agent's backfilled findings are untrusted input at a boundary, so
   ``apply_results`` schema-validates every one of them before merging. A bad
   backfill is set aside and reported in the iteration record rather than
   aborting the round (one malformed record is not worth a whole round), but it
   never reaches ``findings.jsonl``.

3. **Hard limits are enforced by the program (invariant 8).** Documents per
   round, documents per research, requests per research, runtime and rounds
   are checked here, from ``config/iteration.yaml`` over ``DEFAULTS``. Hitting
   any of them stops collection before the next ``collect`` call; documents
   already collected are still persisted.

4. **No host, no data (V11).** ``check_env`` is a pure function and the CLI's
   readiness report writes nothing. A skill that produced half-analysed
   intermediate data because no model was there to judge it would be worse
   than an honest error.

Two deliberate details found by earlier tasks:

* The scope must come from ``planner.load_research``, never straight from
  ``research.yaml``: ``confirmed`` only exists in ``state/scope.json``, and
  enforcing an unconfirmed suggestion is exactly what spec 7.8 forbids. Scope
  is read lazily -- only when there are actions to admit -- so a round with no
  actions needs no research record.
* ``archive.load_state`` is strict on purpose, because a reader must not
  mistake an incomplete project for round zero. A writer needs a starting
  point, so ``_load_state_or_empty`` catches just ``FileNotFoundError``.
  Whether the project exists at all is still guarded by the archive writers'
  directory checks.

Nothing here opens a socket except through the fetcher the compliance gate and
the collector are given (the real transport when none is injected), and nothing
here reads a URL out of untrusted content: addresses come from the source
records and from the fixed set of action fields. Tests inject a fetcher and use
only reserved domains (V9).
"""
import argparse
import importlib
import json
import os
import platform
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

# ``python scripts/research_loop.py`` puts ``scripts/`` on sys.path, not the
# repository root, so the ``scripts`` package itself would not import. The
# documented invocation is the script-path form, so add the root only in that
# form; ``python -m scripts.research_loop`` and an ordinary import already have
# it, and an imported module must not touch sys.path.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jsonschema import ValidationError

from scripts.action_gate import evaluate_action
from scripts.archive import (DOCUMENTS_FILE, FINDINGS_FILE, ROUND_DIGITS,
                             SOURCES_FILE, load_state, rebuild_manifest,
                             save_documents, save_findings, save_iteration,
                             save_state, save_summary)
from scripts.collector import collect
from scripts.compliance_gate import STATUS_ALLOWED, check_compliance
from scripts.content_extractor import extract_content
from scripts.deduplicator import dedupe
from scripts.lib.config import DEFAULTS, load_config
from scripts.lib.hashing import content_hash
from scripts.lib.ids import new_id
from scripts.lib.jsonl import read_records, upsert_by_id
from scripts.lib.urlnorm import normalize_url
from scripts.lib.validate import validate_record
from scripts.planner import load_research
from scripts.reflection import (FINDING_SCORE_FIELDS, compute_weight,
                                merge_findings, summarize_state)

# The eight loop states of spec 7.9. They are also the values
# ``research.schema.json`` allows for ``status``, so the persisted status is the
# decision itself rather than a second vocabulary to keep in sync.
DECISIONS = (
    "CONTINUE",
    "DEEPEN",
    "BROADEN",
    "VERIFY",
    "STOP_COMPLETE",
    "STOP_BUDGET",
    "STOP_NO_PROGRESS",
    "PAUSED_REVIEW",
)

# The paths this module owns inside a project. ``state/pending_actions.json``
# is not in the archive layer's contract (Task 15 is final), so this module
# writes it itself, atomically. ``sources/selected_sources.jsonl`` is the one
# file here that is written through ``scripts.lib.jsonl`` rather than the
# archive layer; it is the module's single writer, as Task 16 requires.
PENDING_ACTIONS_FILE = Path("state") / "pending_actions.json"

# Access methods whose target is a feed or an API. Everything else is a page,
# which is what the extraction path handles.
FEED_METHODS = ("rss", "api")

# Markers that identify a body as HTML rather than a feed or plain text. A web
# source can be pointed at a feed address too; without this check a feed with
# no entries would be "extracted" from its raw XML and archived as a page.
HTML_MARKERS = ("<html", "<!doctype", "<body", "<article", "<main",
                "<div", "<p", "<h1")

# The score fields ``compute_weight`` reads are in-memory inputs, not finding
# schema fields. They are removed at the agent boundary before validation.
SCORE_FIELDS = tuple(FINDING_SCORE_FIELDS.values())

# The retention mode this module may claim honestly. The extraction path keeps
# the metadata and the fingerprint, never the body, so ``reference`` is the
# only accurate value (spec 9.6).
RETENTION_REFERENCE = "reference"

# The document state after extraction. The collector hands over FETCHED; a
# page this module ran through the extractor has reached EXTRACTED (spec 10.3).
CRAWL_STATUS_EXTRACTED = "EXTRACTED"

# Two patterns that are, in practice, always worth telling the host about in a
# readiness report. The project imports both modules itself, so a missing one
# is a broken installation rather than a research condition.
REQUIRED_DEPENDENCIES = ("jsonschema", "yaml")

HOST_NOTE = (
    "本 Skill 需要 LLM 宿主驱动（Claude Code 等支持 Skill 的环境）；"
    "命令行只是宿主的手，语义判断由宿主完成。"
)


def check_env(host_available=True):
    """Report whether an LLM host is driving this skill (spec 5.5).

    Returns ``(ok, message)``. With no host the message says, in the user's
    language, that this skill cannot run on its own. The function is pure: it
    writes no file, so a hostless invocation produces no intermediate data
    (V11). ``host_available`` is an argument rather than a probe because a
    script genuinely cannot detect whether a model is driving it; the caller
    that knows (the host, or a test) supplies the answer.
    """
    if not host_available:
        return False, (
            "本 Skill 需要 LLM 宿主驱动，无法独立运行。"
            "请在 Claude Code 等支持 Skill 的宿主中使用。"
        )
    return True, ""


def run_round(root, *, round_no, actions, config, fetcher=None, sources=None):
    """Execute one round and return the structured handoff (spec 5.2, 5.6).

    The order is fixed:

    1. Read the confirmed scope from ``load_research`` and admit each proposed
       action. Scope is read **lazily**: only when ``actions`` is non-empty. An
       empty action list asks no scope question, and requiring a research
       record for a no-op round would make the round depend on a file it does
       not use. When actions *are* proposed, a missing ``research.yaml`` is a
       real error (``FileNotFoundError``) -- an action cannot be admitted
       without a scope, and silently collecting as if it had been is the wrong
       direction.
    2. Resolve sources: the ``sources`` argument, else
       ``sources/selected_sources.jsonl``. Sources whose
       ``discovery_verified`` is not exactly ``True`` go to
       ``candidates_for_review`` and are **never** collected (spec 8.3, V5);
       that split is program-enforced, not a note in the handoff.
    3. Obtain a compliance decision for every verified source (spec 6.1,
       invariant 1). A source that carries no ``checked_at`` is checked with
       ``compliance_gate.check_compliance`` and costs one unit of the request
       budget; a source that already carries ``checked_at`` keeps its recorded
       decision and is **not** re-checked. That is what lets a decision survive
       across rounds. ``config``'s ``compliance_recheck_days`` is not consulted
       yet -- a deferred refinement -- so a recorded decision is permanent for
       v1. The merged record is written back to ``sources/selected_sources.jsonl``
       **after** the check, so the file states the decision that was actually
       made. A source whose decision is not ``ALLOWED`` is recorded in
       ``actions_rejected`` and never reaches collection.
    4. Collect each permitted source with ``collector.collect``, whose
       ``assert_compliance_allows`` stays as the structural backstop. A
       ``PermissionError`` is reported and the round moves on to the next
       source; it is never retried or bypassed.
    5. Extract pages (a web source yields no feed entries, so the captured
       body is passed to ``extract_content``) and deduplicate the round's
       documents. ``dedupe`` returns ``(kept, report)``; only ``kept`` is
       archived, because semantic near-duplicates are a report, not a removal
       (spec 10.1).
    6. Persist documents and the cross-round request counter, then assemble the
       handoff.

    The round is single-pass: it returns after step 6 and never schedules the
    next one. ``decision`` is the host's to make in ``apply_results``.

    Collection is skipped when actions were proposed and *none* were admitted:
    a round whose every action was refused has nothing to collect. A round with
    no proposed actions may still collect from already-selected sources, which
    is what makes the first round (where the host has sources but no history)
    expressible.

    Budgets come from ``config["iteration_policy"]`` merged over
    ``DEFAULTS``; a missing key falls back to the default rather than raising.
    Request usage is cumulative across rounds in ``state/current.json``.
    """
    root = Path(root)
    _require_project(root)

    if isinstance(round_no, bool) or not isinstance(round_no, int) or round_no < 0:
        raise ValueError(f"round number must be a non-negative integer, got {round_no!r}")

    started = time.monotonic()
    policy = _iteration_policy(config)
    effective_config = _config_with_policy(config, policy)
    actions = list(actions or [])

    state = _load_state_or_empty(root)
    requests_used = _usable_count(state.get("requests_used"))
    existing_documents = len(read_records(root / DOCUMENTS_FILE))
    documents_room = policy["max_documents_per_research"] - existing_documents

    budget_exhausted = (
        documents_room <= 0
        or requests_used >= policy["max_requests_per_research"]
        or round_no > policy["max_rounds"]
    )

    accepted_actions = []
    rejected_actions = []
    seen_topics = []
    if actions:
        # Lazy by design: no actions, no scope, no research.yaml requirement.
        research = _load_research(root)
        scope = research["scope"]
        budget_view = {"remaining_documents": max(0, documents_room)}
        for action in actions:
            verdict = evaluate_action(
                action,
                scope=scope,
                budget=budget_view,
                seen_topics=seen_topics,
                min_priority=policy["min_action_priority"],
            )
            if verdict["accepted"]:
                accepted_actions.append(action)
                topic = action.get("topic")
                if isinstance(topic, str) and topic.strip():
                    seen_topics.append(topic.strip())
            else:
                rejected_actions.append({
                    "action": action,
                    "verdict": verdict,
                    "scope_list": verdict["scope_list"],
                })
        _write_pending_actions(root, round_no, accepted_actions, rejected_actions)

    source_list = _resolve_sources(root, sources)
    verified_sources, review_sources = _split_sources(source_list)

    actions_rejected = [_action_rejection(item) for item in rejected_actions]
    collected = []
    sources_path = root / SOURCES_FILE

    can_collect = (not actions) or bool(accepted_actions)
    if can_collect and not budget_exhausted:
        recorder = _RecordingFetcher(fetcher or _default_fetcher)
        for source in verified_sources:
            if budget_exhausted:
                break
            if len(collected) >= policy["max_documents_per_round"]:
                budget_exhausted = True
                break
            if existing_documents + len(collected) >= policy["max_documents_per_research"]:
                budget_exhausted = True
                break
            if requests_used >= policy["max_requests_per_research"]:
                budget_exhausted = True
                break
            if time.monotonic() - started >= policy["max_runtime_minutes"] * 60:
                budget_exhausted = True
                break

            # A source is permitted only after a check recorded a decision.
            # ``ALLOWED`` without ``checked_at`` is a claim the round must not
            # trust, so it is re-checked like any other unchecked source.
            if source.get("checked_at"):
                merged = dict(source)
            else:
                # The gate is one unit of request budget (it fetches
                # robots.txt). Counting it before the check is the conservative
                # direction: the hard limit can be reached early, never
                # exceeded.
                requests_used += 1
                merged = {
                    **source,
                    **check_compliance(
                        source, fetcher=recorder, config=effective_config
                    ),
                }

            if merged.get("compliance_status") == STATUS_ALLOWED:
                merged["selection_status"] = "selected"
            else:
                merged.setdefault("selection_status", "pending")

            # Persist only after the decision exists, so the file carries the
            # status the gate recorded rather than the caller's claim. This is
            # what makes the decision survive to the next round.
            upsert_by_id(sources_path, merged, "source_id")

            if merged.get("compliance_status") != STATUS_ALLOWED:
                actions_rejected.append({
                    "kind": "source",
                    "source_id": source.get("source_id"),
                    "compliance_status": merged.get("compliance_status"),
                    "reason": _compliance_rejection_reason(merged),
                })
                continue

            # The gate may have spent the round's last permitted request.
            if requests_used >= policy["max_requests_per_research"]:
                budget_exhausted = True
                break

            recorder.last = None
            # A collect call is also one unit of request budget. Counting it
            # even when the compliance block refuses (no transport request
            # happens) is the conservative direction: the hard limit can be
            # reached early, never exceeded.
            requests_used += 1
            try:
                documents = collect(merged, fetcher=recorder, config=effective_config)
            except PermissionError as exc:
                # Structural backstop: reaching here means the decision and
                # the collector disagree. The round reports it and moves on.
                actions_rejected.append({
                    "kind": "source",
                    "source_id": source.get("source_id"),
                    "reason": str(exc),
                })
                continue

            room = min(
                policy["max_documents_per_round"] - len(collected),
                policy["max_documents_per_research"] - existing_documents - len(collected),
            )
            if room <= 0:
                budget_exhausted = True
                break
            if len(documents) > room:
                documents = documents[:room]
                budget_exhausted = True
            collected.extend(documents)

            if not documents and not budget_exhausted:
                page = _page_document(merged, recorder.last, effective_config)
                if page is not None:
                    collected.append(page)

            if budget_exhausted:
                break

    # An unverified source is a candidate for review, not a collection target,
    # but its record is kept so the user can see what was proposed.
    for source in review_sources:
        upsert_by_id(sources_path, source, "source_id")

    kept, _report = dedupe(collected)
    if kept:
        save_documents(root, kept)

    state["requests_used"] = requests_used
    save_state(root, state)

    current_findings = read_records(root / FINDINGS_FILE)
    elapsed = time.monotonic() - started
    return {
        "round": round_no,
        "new_documents": kept,
        "candidates_for_review": review_sources,
        "state_summary": summarize_state(current_findings, [], []),
        "remaining_budget": {
            "documents_this_round": len(kept),
            "documents_remaining": max(
                0, policy["max_documents_per_research"] - existing_documents - len(kept)
            ),
            "requests_remaining": max(0, policy["max_requests_per_research"] - requests_used),
            "runtime_seconds_remaining": max(
                0.0, policy["max_runtime_minutes"] * 60 - elapsed
            ),
            "rounds_remaining": max(0, policy["max_rounds"] - round_no),
        },
        "actions_rejected": actions_rejected,
        "budget_exhausted": budget_exhausted,
    }


def apply_results(root, *, handoff, observations, findings, next_actions, decision):
    """Persist one host-completed round (step 6) and return its summary.

    This is the agent/script boundary, so nothing the agent wrote is trusted
    until it has been checked:

    1. ``decision`` must be one of the eight loop states of spec 7.9
       (``ValueError`` otherwise). It is also the value written to the state's
       ``status``, whose schema enum is the same eight values.
    2. Each backfilled finding is validated against ``finding.schema.json``
       *before* it is merged. The four score fields ``compute_weight`` reads
       are removed first: they are in-memory inputs with no schema field (the
       Task 14 -> Task 15 contract), so validating the raw record would reject
       every scored finding. When any score is present, the weight is computed,
       normalised to the schema's 0-1 range and written to ``weight``; when
       none is present, no ``weight`` is written, because 0 would read as
       "scored worst" in an ordering.
    3. A finding that fails validation, or whose scores cannot be weighted, is
       reported in the iteration's ``rejected_findings`` and skipped. One bad
       backfill must not destroy the round.
    4. The survivors are merged into the existing findings with
       ``reflection.merge_findings`` (exact ``statement`` matching; the buckets
       are independent sets), then written in a fixed order:

       a. ``iterations/round-NNN.json`` -- what the round did and why;
       b. ``findings/findings.jsonl`` -- the complete merged set;
       c. ``state/current.json`` -- round, status and update time, preserving
          every existing field (``created_at``, ``archive_root``, ``path``);
       d. ``summaries/current.md`` -- the current research memory, never a
          concatenation of past rounds (spec 7.2);
       e. ``index/manifest.json`` last, because it counts what was just
          written.

    All validation happens before the first write, so a rejected backfill
    leaves the archive untouched; each individual write is atomic.

    When ``state/current.json`` does not exist this starts from an empty dict.
    That is **not** a relaxation of ``archive.load_state``: ``load_state`` is
    strict for readers, because an incomplete project must not read as round
    zero. ``apply_results`` is a writer whose job is to persist this round, and
    whether the project exists at all is still checked by the archive writers
    (a mistyped root fails on the first ``save_iteration``).
    """
    if decision not in DECISIONS:
        raise ValueError(
            f"decision {decision!r} is not one of the loop states: "
            f"{', '.join(DECISIONS)}"
        )
    if not isinstance(handoff, dict):
        raise ValueError(
            f"handoff must be a JSON object, got {type(handoff).__name__}"
        )
    round_no = handoff.get("round")
    if isinstance(round_no, bool) or not isinstance(round_no, int) or round_no < 0:
        raise ValueError(
            f"handoff must carry a non-negative integer 'round', got {round_no!r}"
        )

    # Observations are recorded as the agent sent them, so a malformed backfill
    # stays visible in the iteration. Only the gaps lookup needs a shape.
    gaps = observations.get("knowledge_gaps") if isinstance(observations, dict) else None
    if not isinstance(gaps, list):
        gaps = []
    next_actions = list(next_actions or [])
    weights = load_config()["finding_weights"]

    existing = read_records(Path(root) / FINDINGS_FILE)
    incoming = []
    rejected_findings = []
    for record in findings or []:
        prepared, error = _prepare_finding(record, weights)
        if error is not None:
            rejected_findings.append({"record": record, "error": error})
            continue
        incoming.append(prepared)

    merged = merge_findings(existing, incoming)
    skipped_findings = merged["skipped"]

    iteration = {
        "round": round_no,
        "decision": decision,
        "observations": observations,
        "findings_added": _finding_ids(merged["added"]),
        "findings_reinforced": _finding_ids(merged["reinforced"]),
        "findings_conflicting": _finding_ids(merged["conflicting"]),
        "rejected_findings": rejected_findings,
        "skipped_findings": skipped_findings,
        "next_actions": next_actions,
        "handoff": {
            "round": round_no,
            "new_documents": _list_length(handoff.get("new_documents")),
            "candidates_for_review": _list_length(handoff.get("candidates_for_review")),
            "budget_exhausted": handoff.get("budget_exhausted"),
        },
        "written_at": _utc_now(),
    }

    save_iteration(root, round_no, iteration)
    save_findings(root, merged["findings"])

    state = _load_state_or_empty(root)
    state["round"] = round_no
    state["status"] = decision
    state["updated_at"] = _utc_now()
    save_state(root, state)

    save_summary(root, summarize_state(merged["findings"], next_actions, gaps))
    rebuild_manifest(root)

    return {
        "decision": decision,
        "round": round_no,
        "status": decision,
        "findings_added": _finding_ids(merged["added"]),
        "findings_reinforced": _finding_ids(merged["reinforced"]),
        "findings_conflicting": _finding_ids(merged["conflicting"]),
        "rejected_findings": rejected_findings,
        "skipped_findings": skipped_findings,
        "findings_total": len(merged["findings"]),
        "iteration_path": str(Path(root) / "iterations" / _iteration_name(round_no)),
    }


# -- the round pipeline -----------------------------------------------------


def _require_project(root):
    """Return ``root`` if this round's own directories exist.

    The archive layer already refuses to invent a project one writer at a time.
    Checking the three directories this module uses before it writes anything
    gives the caller the same answer up front, without a round that collects
    and then fails on its first write. It also stops ``upsert_by_id`` -- which
    does create its parent -- from leaving a ``sources/`` tree under a mistyped
    path.
    """
    for name in ("state", "sources", "documents"):
        if not (Path(root) / name).is_dir():
            raise FileNotFoundError(
                f"research project not found at {root}: {name}/ is missing; "
                f"initialize the project with planner.init_research first"
            )
    return Path(root)


def _load_research(root):
    """Load the research record, letting ``planner`` name the failure.

    ``planner.load_research`` already distinguishes the two answers this caller
    needs: a missing ``research.yaml`` raises ``FileNotFoundError`` naming the
    path, while a malformed or schema-invalid record raises ``ValueError``.
    Re-checking existence here would only produce a second message for the same
    condition. Spec 7.8 still holds either way: neither answer is silently read
    as an empty scope.
    """
    return load_research(root)


def _iteration_policy(config):
    """Return the iteration policy with ``DEFAULTS`` underneath.

    The caller may pass the full loaded config (``iteration_policy`` filled
    from ``config/iteration.yaml``) or a bare mapping such as
    ``{"iteration_policy": {}}``. Merging over ``DEFAULTS`` means a missing key
    is the documented default rather than a ``KeyError``; the merge order also
    makes the caller's value win over the default.
    """
    supplied = config.get("iteration_policy") if isinstance(config, dict) else None
    if not isinstance(supplied, dict):
        supplied = {}
    return {**DEFAULTS, **supplied}


def _config_with_policy(config, policy):
    """Return a copy of ``config`` whose ``iteration_policy`` is resolved.

    ``collector.collect`` reads transport limits from the top level and from
    ``iteration_policy``; giving it the resolved policy keeps the two views of
    the same configuration from disagreeing.
    """
    resolved = dict(config) if isinstance(config, dict) else {}
    resolved["iteration_policy"] = policy
    return resolved


def _load_state_or_empty(root):
    """Return the current state, or ``{}`` when no state file exists.

    This is not a relaxation of ``archive.load_state``: that reader is strict
    on purpose, so an incomplete project cannot be mistaken for round zero.
    This helper serves a *writer*, which needs a starting point; the project's
    existence is still checked by the archive writers and by
    ``_require_project``. Only ``FileNotFoundError`` is caught -- a malformed
    state is corruption and must stay loud.
    """
    try:
        return load_state(Path(root))
    except FileNotFoundError:
        return {}


def _usable_count(value):
    """Return a non-negative int from a state field, else 0."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _resolve_sources(root, sources):
    """Return the round's source records without writing them.

    ``sources=None`` reads ``sources/selected_sources.jsonl``; ``read_records``
    returns ``[]`` when the file is absent, which is an empty selection rather
    than an error. A supplied list is returned as given. Writing it back is
    deliberately **not** done here: ``run_round`` persists each record only
    after the compliance gate has decided it, so the file never holds an
    unchecked claim in place of a decision. This module remains the file's only
    writer, as Task 16 requires; the archive layer's interface is not extended
    for it.
    """
    if sources is None:
        return read_records(Path(root) / SOURCES_FILE)
    return list(sources)


def _split_sources(sources):
    """Split sources into (verified, candidates-for-review).

    ``discovery_verified is not True`` -- strict identity -- means the source
    is a model recollection or otherwise unconfirmed. It is shown to the user
    and never collected: spec 8.3/V5 require confirmation before a remembered
    source even reaches the compliance check, and this split is what enforces
    it in code rather than in a comment.
    """
    verified = []
    review = []
    for source in sources:
        if isinstance(source, dict) and source.get("discovery_verified") is True:
            verified.append(source)
        else:
            review.append(source)
    return verified, review


def _compliance_rejection_reason(source):
    """Return the handoff reason for a source the gate did not permit.

    It names both the decision and the evidence note, because "BLOCKED" alone
    does not tell a reviewer whether robots.txt refused the path, the rules
    could not be read, or a terms page parked the source for a human.
    """
    status = source.get("compliance_status")
    notes = source.get("notes")
    if isinstance(notes, str) and notes.strip():
        return f"compliance gate returned {status}: {notes}"
    return (
        f"compliance gate returned {status}: the source is not permitted for "
        f"collection"
    )


class _RecordingFetcher:
    """A fetcher wrapper that remembers the last response.

    ``collector.collect`` returns document records and never the body, so a
    page source has nothing for the extractor to read unless the response is
    kept somewhere. This wrapper keeps the last one; the collector still makes
    exactly one request per source, and the compliance block still runs before
    the wrapped fetcher is called.
    """

    def __init__(self, fetcher):
        self._fetcher = fetcher
        self.last = None

    def __call__(self, url, **kwargs):
        result = self._fetcher(url, **kwargs)
        self.last = result if isinstance(result, dict) else None
        return result


def _default_fetcher(url, *, timeout, max_bytes, delay):
    """Fetch through the real transport, mirroring ``collector``'s default."""
    from scripts.lib.http import fetch

    return fetch(url, timeout=timeout, max_bytes=max_bytes, delay=delay)


def _page_document(source, response, config):
    """Return one document extracted from an HTML page source, or ``None``.

    A web source is not a feed, so ``collect`` parses no entries from it. This
    path runs the same rules the collector does before archiving what the page
    said:

    * only a successful response is read;
    * the response must still be on the permitted source's host and scheme,
      so a redirect cannot turn this path into a way around the collector's
      re-check (invariant 1);
    * the extractor must find some body text -- an empty extract is not a
      document.

    The body itself is not persisted (``retention_mode`` is ``reference``);
    only the metadata the page stated, a reference and a fingerprint are
    kept. The fingerprint comes from the extracted text, so the second
    deduplication layer sees what the reader would (spec 9.6, 10.1).
    """
    if not isinstance(response, dict) or response.get("outcome") != "ok":
        return None
    if (source.get("access_method") or "").strip().lower() in FEED_METHODS:
        return None

    target = source.get("base_url") or ""
    final_url = response.get("final_url") or target
    if not _same_host(target, final_url) or not _is_http(final_url):
        return None

    text = response.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    if not _looks_like_html(text):
        return None
    extracted = extract_content(text, final_url)
    body = extracted.get("text") or ""
    if not isinstance(body, str) or not body.strip():
        return None

    try:
        canonical_url = normalize_url(final_url)
    except ValueError:
        # A malformed address that survived the host/scheme check (a bad port,
        # for example) cannot be a document's identity.
        return None

    retrieved_at = _utc_now()
    record = {
        "document_id": new_id("doc"),
        "source_id": source.get("source_id"),
        "source_name": source.get("source_name") or _host_of(target),
        "source_url": source.get("base_url"),
        "title": extracted.get("title") or "",
        "published_at": extracted.get("published_at"),
        "crawled_at": retrieved_at,
        "first_seen_at": retrieved_at,
        "canonical_url": canonical_url,
        "content_hash": content_hash(body),
        "collection_method": (source.get("access_method") or "").strip() or "http_get",
        "compliance_record_id": source.get("source_id"),
        "crawl_status": CRAWL_STATUS_EXTRACTED,
        "retention_mode": RETENTION_REFERENCE,
        "date_confidence": extracted.get("date_confidence") or "unknown",
        "attempt_count": 0,
    }
    author = extracted.get("author")
    if isinstance(author, str) and author.strip():
        record["author"] = author
    return record


def _looks_like_html(text):
    """Return True when the body carries HTML markup.

    The extraction path is for pages (spec 8.6's third stage). A response with
    no markup -- plain text, JSON, a feed with no entries -- is not a page the
    extractor can honestly turn into a document, and archiving its raw text as
    one would put noise in the document layer.
    """
    lowered = text.lower()
    return any(marker in lowered for marker in HTML_MARKERS)


def _host_of(url):
    """Return the lowercased host of ``url``, or ``""`` when unreadable."""
    try:
        return (urlsplit(url or "").hostname or "").lower()
    except ValueError:
        return ""


def _same_host(left, right):
    """Return True when both URLs name the same non-empty host."""
    left_host = _host_of(left)
    return bool(left_host) and left_host == _host_of(right)


def _is_http(url):
    """Return True when ``url`` uses a scheme this project may fetch."""
    try:
        return urlsplit(url or "").scheme.lower() in ("http", "https")
    except ValueError:
        return False


def _action_rejection(entry):
    """Return the handoff's machine-readable summary of one refused action."""
    action = entry["action"] if isinstance(entry.get("action"), dict) else {}
    verdict = entry["verdict"]
    return {
        "kind": "action",
        "action_id": action.get("action_id"),
        "topic": action.get("topic"),
        "rejected_as": verdict.get("rejected_as"),
        "scope_list": entry["scope_list"],
        "reason": verdict.get("reason"),
    }


def _write_pending_actions(root, round_no, accepted, rejected):
    """Atomically write ``state/pending_actions.json`` for this round.

    The archive layer owns ``state/current.json``; this file is this module's
    own record of what the gate admitted and refused, including the
    ``scope_list`` that tells a candidate lead from a candidate topic. It is
    written only when actions were proposed, so a round that proposes nothing
    does not create an empty queue.
    """
    _write_json_atomic(
        Path(root) / PENDING_ACTIONS_FILE,
        {"round": round_no, "accepted": accepted, "rejected": rejected},
    )


def _write_json_atomic(path, payload):
    """Write a JSON object via a same-directory temp file and ``os.replace``.

    Same contract as the archive layer's private writer: the temporary file
    lives in the target directory so the rename is atomic, the bytes are
    fsynced before the rename, and the parent directory is required to exist
    (this function never invents a project). Kept local rather than exported
    from ``archive.py`` because the archive contract was finalised in Task 15.
    """
    path = Path(path)
    parent = path.parent
    if not parent.is_dir():
        raise FileNotFoundError(
            f"cannot write {path}: parent directory {parent} does not exist"
        )
    descriptor, tmp_name = tempfile.mkstemp(
        dir=parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# -- apply_results helpers --------------------------------------------------


def _prepare_finding(record, weights):
    """Return ``(persistable_record, error_message_or_None)``.

    Score fields are pulled out first because they are not finding fields; the
    rest of the record must then pass the schema. A record that passes and
    carries any score gets a normalised ``weight``. A weighting failure (for
    example a score outside 0-100) is returned as an error, not raised, so the
    boundary reports one bad backfill instead of losing the round.
    """
    if not isinstance(record, dict):
        return None, "finding record is not a JSON object"

    persistable = dict(record)
    scores = {}
    for field in SCORE_FIELDS:
        if field in persistable:
            scores[field] = persistable.pop(field)

    try:
        validate_record("finding", persistable)
    except ValidationError as exc:
        return None, _validation_error_message(exc)

    # ``None`` is the extractor's "not scored yet", not a score of zero, so a
    # record whose four score fields are all explicitly None is still unscored.
    # Writing weight 0 for it would make the ordering treat an unassessed
    # finding as the worst one.
    if any(value is not None for value in scores.values()):
        try:
            computed = compute_weight(scores, weights)
        except ValueError as exc:
            return None, str(exc)
        persistable["weight"] = _normalise_weight(computed)
    return persistable, None


def _validation_error_message(exc):
    """Return a schema failure message that names the offending field.

    ``jsonschema``'s ``.message`` for a bound violation is just
    "2 is greater than the maximum of 1"; without the path the agent cannot
    tell which field to repair. ``absolute_path`` is the address of the failure
    inside the record.
    """
    path = ".".join(str(part) for part in getattr(exc, "absolute_path", ()) or ())
    message = getattr(exc, "message", str(exc))
    return f"{path}: {message}" if path else message


def _normalise_weight(value):
    """Return a 0-100 weight as the schema's 0-1 value, clamped and rounded.

    Clamping is not cosmetic: floating-point addition of the four coefficients
    can produce 1.0000000000000002 for four perfect scores, and the schema's
    maximum is 1.
    """
    normalised = round(float(value) / 100.0, 6)
    return max(0.0, min(1.0, normalised))


def _finding_ids(records):
    """Return the ``finding_id`` of each record that carries one."""
    identifiers = []
    for record in records:
        if isinstance(record, dict):
            identifier = record.get("finding_id")
            if isinstance(identifier, str) and identifier:
                identifiers.append(identifier)
    return identifiers


def _list_length(value):
    """Return the length of a list, or 0 when the value is not a list."""
    return len(value) if isinstance(value, list) else 0


def _iteration_name(round_no):
    """Return the iteration file name for a round.

    Uses the archive layer's width so the reported path and the written file
    cannot drift apart.
    """
    return f"round-{round_no:0{ROUND_DIGITS}d}.json"


def _utc_now():
    """Return the current UTC time in ISO 8601, seconds precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# -- command line -----------------------------------------------------------


def main(argv=None):
    """Run the CLI and return an exit code (spec 5.5, V11).

    Thin by design: every judgement lives in ``run_round`` / ``apply_results``.
    Successful output is a JSON document on stdout with no decoration, so the
    host can parse it. Any error is a message on stderr and a non-zero exit;
    nothing is printed to stdout in that case.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(parser, args)
    except SystemExit:
        raise
    except Exception as exc:
        # The CLI's job is to report a failure, not to spill a traceback over
        # the host's stderr. The message is the contract; the exit code is the
        # signal.
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="research_loop.py",
        description="Research Scout 单轮执行器：采集一轮，或回填一轮结果。",
    )
    parser.add_argument(
        "--check-env", action="store_true",
        help="检查运行环境并退出（不写任何文件）",
    )
    parser.add_argument(
        "--root", metavar="<项目根>", help="研究项目根目录（archive/<领域>/<主题>）",
    )
    parser.add_argument(
        "--round", dest="round_no", type=int, metavar="<N>",
        help="要执行的轮次编号",
    )
    parser.add_argument(
        "--actions", metavar="<actions.json>",
        help="候选行动的 JSON 数组文件",
    )
    parser.add_argument(
        "--apply", dest="apply_path", metavar="<results.json>",
        help="宿主回填结果的 JSON 对象文件",
    )
    return parser


def _dispatch(parser, args):
    if args.check_env:
        if args.root or args.round_no is not None or args.actions or args.apply_path:
            parser.error("--check-env 不能与 --root/--round/--actions/--apply 同时使用")
        return _run_check_env()

    if args.apply_path:
        if args.round_no is not None or args.actions:
            parser.error("--apply 不能与 --round/--actions 同时使用")
        if not args.root:
            parser.error("--apply 需要 --root")
        return _run_apply(Path(args.root), Path(args.apply_path))

    if args.round_no is not None or args.actions:
        if args.round_no is None:
            parser.error("--round 需要一个轮次编号")
        if not args.actions:
            parser.error("--round 需要 --actions")
        if not args.root:
            parser.error("--round 需要 --root")
        return _run_round_cli(Path(args.root), args.round_no, Path(args.actions))

    parser.error("必须指定 --check-env、--round 或 --apply 之一")


def _run_check_env():
    """Print the readiness report. Writes no file (V11)."""
    lines = ["Research Scout 就绪检查", f"Python: {platform.python_version()}"]
    missing = []
    for name in REQUIRED_DEPENDENCIES:
        try:
            importlib.import_module(name)
        except ImportError as exc:
            missing.append(f"{name}: {exc}")
            lines.append(f"依赖 {name}: 缺失")
        else:
            lines.append(f"依赖 {name}: OK")

    ok, message = check_env(host_available=True)
    lines.append(f"宿主: {HOST_NOTE}")
    lines.append("状态: 就绪" if ok and not missing else f"状态: {message or '依赖缺失'}")
    print("\n".join(lines))
    return 0 if ok and not missing else 1


def _run_round_cli(root, round_no, actions_path):
    actions = _read_json(actions_path)
    if not isinstance(actions, list):
        raise ValueError(f"{actions_path} 必须是 JSON 数组（候选行动列表）")
    handoff = run_round(root, round_no=round_no, actions=actions,
                        config=load_config())
    _print_json(handoff)
    return 0


def _run_apply(root, results_path):
    payload = _read_json(results_path)
    if not isinstance(payload, dict):
        raise ValueError(f"{results_path} 必须是 JSON 对象（回填结果）")
    result = apply_results(
        root,
        handoff=payload.get("handoff") or {},
        observations=payload.get("observations") or {},
        findings=payload.get("findings") or [],
        next_actions=payload.get("next_actions") or [],
        decision=payload.get("decision"),
    )
    _print_json(result)
    return 0


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _print_json(payload):
    """Print one JSON document, with no other text (machine-readable stdout)."""
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
