"""Research memory merging and weight computation: the deterministic half of
Reflection.

Reflection is step 4 of the research loop (spec 7.1): after a round's evidence
has been extracted, the current research memory is updated with it. Spec 7.2
asks the update to "identify new / duplicate / conflicting / revised" and to
keep the *current state* separate from the round history and the raw document
library. This module implements only the mechanical part of that update. It
answers "how does new evidence merge into what we already believe", never "do
these two sentences mean the same thing". Two consequences are deliberate and
are the invariant this module protects:

1. **No semantic judgement.** Findings are matched by *exact* ``statement``
   equality. Two differently-worded phrasings of the same claim stay two
   findings. This is not a limitation to work around later by adding a
   similarity score: spec 7.2 lists "revised" as one of the four things to
   identify, and this module deliberately does **not** detect it. Recognising
   that new evidence *revises* an earlier conclusion is a semantic judgement,
   and the whole point of a deterministic half is that it does not pretend to
   make one. The host agent, which can call a model, owns that decision; it
   can express a revision by writing a new finding or editing the existing one
   through its own path.

2. **Never concatenate history.** Spec 7.2 is explicit that the summaries of
   all previous rounds must not be concatenated and handed to the model.
   ``summarize_state`` renders the current state (layer one of the three-layer
   memory) and nothing else: it takes the findings, actions and gaps it is
   given, and knows nothing about earlier rounds. Time is added by the archive
   layer, not here, so identical inputs render byte-identical output.

Merging rules (the contract ``tests/test_reflection.py`` pins):

* A finding is the same finding when its ``statement`` is exactly equal
  (``==``, no normalisation, no case folding, no whitespace stripping). The
  merge keeps the existing ``finding_id``: it is the identity that evidence
  and reports point at, and merging must not invalidate those references.
* Evidence is deduplicated by ``(document_id, evidence_type)``, not by
  ``document_id`` alone. One document may support the mechanism a finding
  claims while contradicting its effectiveness, so the two evidence types must
  coexist. When the same pair appears twice, the record already present wins
  and the incoming one is dropped: it is the same claim about the same
  document, and a different ``quote`` or ``locator`` where the evidence sits
  is not new evidence. Evidence with a missing key, or not a mapping at all,
  is kept without deduplication -- losing it silently would be worse than a
  duplicate.
* On a statement match, ``confidence`` and ``importance`` come from the
  *incoming* finding whenever it carries them: the agent has just seen more
  evidence, and incoming is its current assessment; keeping the old number
  would pretend the new evidence changed nothing. ``scope``, ``limitations``
  and ``follow_up`` are prose the agent writes; an incoming value that is
  present and non-empty replaces the old one, an empty or missing one leaves
  the old value alone. They are never concatenated or "union"ed -- that would
  fabricate a sentence nobody wrote. Any other field follows the same rule:
  incoming present overrides, incoming absent keeps the existing value.
* Both inputs are never modified. The merge works on private deep copies, and
  the returned findings do not alias the caller's dictionaries.
* A statement that appears more than once in ``current`` (dirty data) merges
  into the *first* matching record and does not raise: one malformed state
  file must not abort a whole research round. The later duplicates stay in the
  output untouched.
* The three result buckets are **independent sets**, not mutually exclusive
  categories. ``added`` holds findings first created by this call;
  ``reinforced`` holds matched findings the incoming evidence does not
  contradict; ``conflicting`` holds matched findings whose incoming evidence
  contains at least one ``contradicts`` item, and also new findings that
  arrive with one. A merge that carries a contradiction is therefore in
  ``conflicting`` and **not** in ``reinforced``: calling a merge that brings
  counter-evidence a "reinforcement" would read the evidence backwards, even
  though the supporting evidence still merges into the finding as usual. The
  buckets hold the *same objects* as ``findings`` (``is`` identity), deduped
  by identity and ordered by first classification, so a caller can see which
  result entries a bucket refers to without copying ids around. Classification
  is per incoming record against the working set as it stands, including
  findings added earlier in the same call; two incoming records that share one
  new statement therefore land the still-single finding in ``added`` and
  ``reinforced``. For the same reason, a batch carrying one record with
  ``supports`` and another with ``contradicts`` for the same existing
  statement lands the finding in **both** ``reinforced`` and ``conflicting``:
  each record is classified on its own, and the buckets describe the batch,
  not a single verdict about the finding.

``compute_weight`` implements spec 7.4's ``W = 0.35R + 0.25E + 0.20N + 0.20A``
with the per-dimension weights read from the caller's mapping (``config/
scoring.yaml``), not hardcoded, and returns the spec's 0-100 scale -- not the
0-1 scale the finding schema's ``weight`` field uses. The caller that persists
a finding divides by 100. ``confidence`` is never an input to this formula:
spec 7.4 and the schema both say the weight orders findings and is not a
factual accuracy rate, and folding confidence in would quietly make a ranking
number readable as one.

This is a pure function module. It reads no file, opens no socket, keeps no
state, and validates no record: persistence (schema validation, id generation,
dividing the weight into the record) belongs to the archive layer, not here.
"""
import copy
import math
from collections.abc import Mapping

# Weight key -> finding field, in the order spec 7.4 writes the formula.
# Keeping the mapping here, rather than in four copies of arithmetic, is what
# makes "the weight keys come from configuration" true in one place.
FINDING_SCORE_FIELDS = {
    "relevance": "relevance_score",
    "evidence": "evidence_score",
    "novelty": "novelty_score",
    "guidance": "guidance_score",
}

# Numeric fields that an incoming finding takes over on a statement match.
CONFIDENCE_FIELDS = ("confidence", "importance")

# Prose fields that only override the existing value when the incoming value
# is present and non-empty.
PROSE_FIELDS = ("scope", "limitations", "follow_up")

# Rendered in a summary section that has nothing to report. An empty section
# with no marker is a malformed document; a marker keeps the shape readable.
EMPTY_MARKER = "（暂无）"

# The five sections ``summarize_state`` always renders, in order.
SECTION_TITLES = ("关键发现", "证据", "知识缺口", "矛盾", "下一步行动")


def _neutralise(value):
    """Return ``value`` as one Markdown-safe line for the rendered summary.

    Every string that reaches the document -- a statement, a finding or
    document id, an evidence type, a gap, an action's topic/type/reason -- is
    written by an LLM that reads untrusted web pages (CLAUDE.md invariant 6),
    so it is untrusted input. ``summaries/current.md`` is both a user-facing
    product and layer one of the memory fed back to the model; a forged
    ``## 下一步行动`` would read as planner output.

    Only characters that carry *document structure* are neutralised; content
    is never deleted:

    * CR, LF and CRLF collapse to a single space, so no value can start a new
      line and therefore cannot open a heading, bullet or code fence.
    * A backtick becomes an apostrophe, so a value rendered inside a code
      span cannot close it early and turn the rest into markup.
    * ``#`` is backslash-escaped, so the text ``## 知识缺口`` inside a value
      is never mistaken for a heading (Markdown renders ``\\#`` as ``#``, so
      no content is deleted).

    Other meaning-bearing characters (``*``, ``-``, ``>`` and the like) are
    kept as-is: once a value cannot start a line they cannot forge a section,
    and escaping them would corrupt the reader's text. Non-strings are
    rendered with ``str`` so a dirty value still cannot smuggle a newline in.
    """
    text = str(value)
    text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    text = text.replace("`", "'")
    return text.replace("#", "\\#")


def _is_real_number(value):
    """True for int/float values that are not bool and not NaN/infinity.

    ``bool`` is excluded because ``True`` is an ``int`` in Python but is never
    a score; silently reading it as ``1`` would hide a caller bug. NaN and
    infinity are excluded because they poison every comparison downstream.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def _check_weight(key, value):
    """Reject a weight that cannot be part of the formula, naming its key.

    A broken weight configuration must fail loudly: treating a bad weight as
    zero would produce a ranking that looks normal and is wrong. That is more
    dangerous than a crash, because nobody notices it.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            "weight %r must be a number, got %r" % (key, type(value).__name__))
    if not math.isfinite(value):
        raise ValueError("weight %r must be finite, got %r" % (key, value))
    if value < 0 or value > 1:
        raise ValueError(
            "weight %r must be between 0 and 1, got %r" % (key, value))


def _score(finding, field):
    """Return a score for the formula, or 0.0 when the field is unscored.

    Missing and ``None`` both mean "the agent has not scored this dimension
    yet", which is a normal state between extraction and analysis. A value
    that is present but not a usable number is a bug and raises.
    """
    value = finding.get(field)
    if value is None:
        return 0.0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            "score %r must be a number, got %r" % (field, type(value).__name__))
    if not math.isfinite(value):
        raise ValueError("score %r must be finite, got %r" % (field, value))
    if value < 0 or value > 100:
        raise ValueError(
            "score %r must be between 0 and 100, got %r" % (field, value))
    return float(value)


def compute_weight(finding, weights):
    """Return spec 7.4's ranking weight for ``finding``, on a 0-100 scale.

    ``weights`` is a mapping of the four spec 7.4 dimensions
    (``relevance``, ``evidence``, ``novelty``, ``guidance``) to their
    coefficients -- normally ``config/scoring.yaml``'s ``finding_weights``.
    The coefficients are read from that mapping, never hardcoded, so the
    formula and its tuning stay in one place.

    The result is on the **0-100 scale spec 7.4 uses**, not the 0-1 scale of
    ``weight`` in ``schemas/finding.schema.json``. This module does not
    normalise: the caller that persists a finding record is responsible for
    dividing by 100 to fit the schema's field. Doing it here would make the
    figure the summary shows disagree with the plan's formula for no gain.

    Each dimension's score is missing from a finding until the agent scores
    it; a missing or ``None`` score counts as 0. A score that is present is
    kept inside spec 7.4's 0-100 range and a weight inside 0-1: a value
    outside its bound is a bug in the caller or its configuration, and raises
    rather than quietly skewing the ranking (a single 101, or a weight of 5,
    would otherwise push the result past the documented scale unnoticed).
    A score that is present but a bool, non-numeric, or NaN/inf also raises
    rather than quietly counting as 0.

    ``confidence`` is never read. Spec 7.4 keeps the ranking weight and the
    confidence in the claim separate, and the schema's description of
    ``weight`` says the same.

    Raises
    ------
    ValueError
        When ``weights`` is not a mapping, when it is missing any of the four
        keys (the message names the missing key or keys), when a weight is
        not a finite number in [0, 1], or when a score that is present is not
        a finite number in [0, 100]. In every case the message names the
        offending key or field.
    """
    if not isinstance(weights, Mapping):
        raise ValueError(
            "weights must be a mapping, got %r" % type(weights).__name__)

    missing = [key for key in FINDING_SCORE_FIELDS if key not in weights]
    if missing:
        raise ValueError(
            "missing weight key(s): " + ", ".join(missing))

    total = 0.0
    for weight_key, score_field in FINDING_SCORE_FIELDS.items():
        weight = weights[weight_key]
        _check_weight(weight_key, weight)
        total += float(weight) * _score(finding, score_field)
    return total


def _evidence_list(record):
    """Return a record's evidence list, tolerating dirty data.

    A missing or non-list ``evidence`` becomes an empty list: a malformed
    state file must not abort the round, and there is nothing to merge from a
    value that is not a list anyway.
    """
    value = record.get("evidence")
    return value if isinstance(value, list) else []


def _usable_text(value):
    """True for a non-empty string, the only usable identity component."""
    return isinstance(value, str) and value != ""


def _evidence_key(item):
    """Return the deduplication key for an evidence item, or ``None``.

    The pair (not the document alone) is the key so that one document can both
    support and contradict the same finding -- spec 7.3 gives ``contradicts``
    and ``partially_supports`` exactly so this can be recorded, and spec 7.2
    lists contradictions among the memory that must survive a merge.

    ``None`` means the item has no usable key: it is not a mapping, or its
    ``document_id`` or ``evidence_type`` is missing, empty or not a string.
    Such items are kept without deduplication rather than silently dropped.
    Requiring *both* halves also keeps the key hashable: a hostile or dirty
    record whose ``document_id`` is a list cannot raise ``TypeError`` in the
    middle of a round.
    """
    if not isinstance(item, dict):
        return None
    document_id = item.get("document_id")
    evidence_type = item.get("evidence_type")
    if not _usable_text(document_id) or not _usable_text(evidence_type):
        return None
    return (document_id, evidence_type)


def _merge_evidence(existing_items, incoming_items):
    """Return the union of two evidence lists, deduplicated by pair.

    Entries already present win over incoming duplicates: the same pair is the
    same claim about the same document, and a different quote or locator is a
    location detail, not new evidence. Non-mapping entries are appended
    without deduplication because they carry no key.
    """
    merged = copy.deepcopy(list(existing_items))
    seen = set()
    for item in merged:
        key = _evidence_key(item)
        if key is not None:
            seen.add(key)

    for item in incoming_items:
        key = _evidence_key(item)
        if key is not None and key in seen:
            continue
        merged.append(copy.deepcopy(item))
        if key is not None:
            seen.add(key)
    return merged


def _merge_into(target, incoming):
    """Merge ``incoming`` fields into ``target`` in place and return it.

    ``target`` is a private deep copy owned by ``merge_findings``, so mutating
    it keeps its identity stable -- the objects in the result buckets stay the
    same objects as in ``findings`` -- without touching the caller's dict.
    """
    if "evidence" in target and not isinstance(target["evidence"], list):
        # Dirty data: a malformed state file may carry a single dict instead
        # of a list. Preserve it so schema validation reports the real
        # problem; replacing it with a merged list would destroy the evidence
        # and hide the corruption.
        pass
    else:
        target["evidence"] = _merge_evidence(_evidence_list(target),
                                             _evidence_list(incoming))

    for field in CONFIDENCE_FIELDS:
        if field in incoming:
            target[field] = copy.deepcopy(incoming[field])

    for field in PROSE_FIELDS:
        if field in incoming and incoming[field]:
            target[field] = copy.deepcopy(incoming[field])

    for field, value in incoming.items():
        if field in ("statement", "finding_id", "evidence"):
            continue
        if field in CONFIDENCE_FIELDS or field in PROSE_FIELDS:
            continue
        target[field] = copy.deepcopy(value)

    return target


def _has_contradiction(record):
    """True when an incoming finding carries any ``contradicts`` evidence."""
    return any(
        isinstance(item, dict) and item.get("evidence_type") == "contradicts"
        for item in _evidence_list(record)
    )


def _append_once(bucket, seen_ids, record):
    if id(record) in seen_ids:
        return
    seen_ids.add(id(record))
    bucket.append(record)


def merge_findings(current, incoming):
    """Merge newly extracted findings into the current research memory.

    Returns ``{"findings": [...], "added": [...], "reinforced": [...],
    "conflicting": [...], "skipped": [...]}``.

    Matching is exact ``statement`` equality only. Revision of a statement's
    wording is not detected here; see the module docstring for why that is a
    semantic judgement reserved for the host agent.

    Field rules on a match (all documented on the module as well):

    * ``finding_id`` stays the existing record's, so references to it remain
      valid.
    * ``evidence`` is the union deduplicated by ``(document_id,
      evidence_type)``; on a repeated pair the existing entry is kept.
    * ``confidence`` and ``importance`` take the incoming value when the
      incoming record carries the field: merging means the agent reassessed
      after seeing more evidence.
    * ``scope``, ``limitations`` and ``follow_up`` are replaced only when the
      incoming value is present and non-empty; otherwise the existing value
      stays. They are prose and are never concatenated.
    * Any other field follows "incoming present overrides, absent keeps".
    * A new statement is copied into the result verbatim (id and all).

    Ordering and identity:

    * ``findings`` keeps the current order and appends new statements in the
      order they first appear in ``incoming``.
    * The three buckets hold the *same objects* (``is``) as ``findings``, so
      a caller can tell which result entries they name. Each bucket is a set
      by identity: a finding appears at most once per bucket. Buckets are
      independent, not mutually exclusive -- a new finding that arrives with a
      contradiction is in both ``added`` and ``conflicting``.
    * A new statement is classified as ``added`` when first seen. A record
      that matches a statement already in the working set -- including a
      finding added earlier in the same call -- is ``reinforced`` when it
      carries no ``contradicts`` evidence and ``conflicting`` when it does
      (and then it is not in ``reinforced``). Its evidence merges into the
      finding either way; a contradiction stays in the data instead of being
      discarded. Classification is per record: if one batch carries both a
      supporting record and a contradicting record for the same statement,
      the finding is in both ``reinforced`` and ``conflicting``. That is not
      a contradiction -- the buckets describe the batch's records, not one
      verdict.

    Dirty data: a statement repeated in ``current`` merges into the *first*
    matching record and never raises; the later duplicates are returned
    untouched. Non-mapping entries in ``current`` are copied through
    unchanged.

    An incoming record whose ``statement`` is missing, not a string, or blank
    (whitespace only), and any non-mapping entry in ``incoming``, cannot be
    matched. Such records are **not** appended as findings -- one statement-
    less finding would make a later validate-everything-then-write step reject
    the whole round -- and they do **not** disappear silently either: a deep
    copy of each is returned in ``skipped`` so the caller can report or repair
    it. ``skipped`` is always present and is an empty list when nothing was
    skipped.

    Neither input is modified, and the result does not alias either one.
    """
    findings = [copy.deepcopy(item) for item in current]

    # statement -> index of the first record in ``current`` that carries it.
    # Built once; appending later never invalidates these positions.
    by_statement = {}
    for position, record in enumerate(findings):
        if not isinstance(record, dict):
            continue
        statement = record.get("statement")
        if isinstance(statement, str) and statement not in by_statement:
            by_statement[statement] = position

    added = []
    reinforced = []
    conflicting = []
    skipped = []
    added_ids = set()
    reinforced_ids = set()
    conflicting_ids = set()

    for record in incoming:
        if not isinstance(record, dict):
            skipped.append(copy.deepcopy(record))
            continue

        statement = record.get("statement")
        if not (isinstance(statement, str) and statement.strip()):
            skipped.append(copy.deepcopy(record))
            continue

        position = by_statement.get(statement)
        contradicts = _has_contradiction(record)

        if position is None:
            merged = copy.deepcopy(record)
            findings.append(merged)
            # ``setdefault`` mirrors "first one wins" for a statement
            # repeated inside one batch, so a second copy merges into this
            # record instead of creating a duplicate.
            by_statement.setdefault(statement, len(findings) - 1)
            _append_once(added, added_ids, merged)
            if contradicts:
                _append_once(conflicting, conflicting_ids, merged)
            continue

        merged = _merge_into(findings[position], record)
        if contradicts:
            _append_once(conflicting, conflicting_ids, merged)
        else:
            _append_once(reinforced, reinforced_ids, merged)

    return {
        "findings": findings,
        "added": added,
        "reinforced": reinforced,
        "conflicting": conflicting,
        "skipped": skipped,
    }


def _format_number(value):
    """Render a number compactly, or ``None`` when it is not a usable number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return "%g" % value


def _statement_of(finding):
    statement = finding.get("statement")
    if isinstance(statement, str) and statement.strip():
        return _neutralise(statement)
    return "（无陈述）"


def _finding_id_of(finding):
    finding_id = finding.get("finding_id")
    if isinstance(finding_id, str) and finding_id:
        return _neutralise(finding_id)
    return "（无 ID）"


def _sort_findings(findings):
    """Order findings by importance descending, then by ``finding_id``.

    The order is deliberately not derived from ``compute_weight``: this
    function has no weights, and quietly assuming the defaults would make the
    ordering unexplainable from the call. A missing or non-numeric importance
    sorts last by being treated as 0.
    """
    def key(finding):
        importance = finding.get("importance")
        if isinstance(importance, bool) or not isinstance(importance, (int, float)):
            importance = 0.0
        elif not math.isfinite(importance):
            importance = 0.0
        return (-float(importance), _finding_id_of(finding))

    return sorted((item for item in findings if isinstance(item, dict)), key=key)


def summarize_state(findings, actions, gaps):
    """Render ``summaries/current.md`` from the current research state.

    This is layer one of spec 7.2's three-layer memory: the current state
    only. It never sees earlier rounds and never accumulates them, because
    spec 7.2 forbids concatenating past summaries into the model's context.

    The output is Markdown with the five sections this module always renders:
    关键发现, 证据, 知识缺口, 矛盾, 下一步行动. An empty section gets an
    ``EMPTY_MARKER`` line so the document always has a body under its heading.

    * Findings are ordered by ``importance`` descending, ties by
      ``finding_id``. Non-numeric importance sorts last.
    * 矛盾 is derived by a deterministic rule only: a finding whose evidence
      contains an item with ``evidence_type == "contradicts"`` is listed with
      the ``document_id`` that raised the objection. The module states the
      evidence type; it does not judge whether the contradiction is decisive.
    * ``gaps`` is a list of strings, one per knowledge gap.
    * ``actions`` is a list of dicts; ``topic``, ``type`` and ``reason`` are
      rendered when present, and an action that carries none of them is
      rendered as such rather than assumed to have them.

    No timestamp is written: time is the archive layer's job, and the function
    must be deterministic so identical state renders byte-identical text.
    """
    ordered = _sort_findings(findings)

    finding_lines = []
    for item in ordered:
        meta = ["`%s`" % _finding_id_of(item)]
        confidence = _format_number(item.get("confidence"))
        if confidence is not None:
            meta.append("置信度 %s" % confidence)
        importance = _format_number(item.get("importance"))
        if importance is not None:
            meta.append("重要性 %s" % importance)
        finding_lines.append(
            "- **%s**（%s）" % (_statement_of(item), "，".join(meta)))

    evidence_lines = []
    for item in ordered:
        finding_id = _finding_id_of(item)
        for piece in _evidence_list(item):
            if isinstance(piece, dict):
                document_id = piece.get("document_id")
                evidence_type = piece.get("evidence_type")
            else:
                document_id = None
                evidence_type = None
            if not isinstance(document_id, str) or not document_id:
                document_id = "（未知文档）"
            if not isinstance(evidence_type, str) or not evidence_type:
                evidence_type = "（未知类型）"
            evidence_lines.append(
                "- `%s` ← `%s`（%s）" % (finding_id, _neutralise(document_id),
                                         _neutralise(evidence_type)))

    gap_lines = []
    for gap in gaps:
        if gap is None:
            continue
        if isinstance(gap, str) and not gap.strip():
            # A blank gap would render a bare "- " line, which reads as a
            # malformed document. There is no content to show.
            continue
        gap_lines.append("- %s" % _neutralise(gap))

    contradiction_lines = []
    for item in ordered:
        documents = []
        for piece in _evidence_list(item):
            if not isinstance(piece, dict):
                continue
            if piece.get("evidence_type") != "contradicts":
                continue
            document_id = piece.get("document_id")
            if not isinstance(document_id, str) or not document_id:
                # Match the 证据 section's fallback: a contradiction is still
                # a contradiction even when its document is unknown, so do
                # not drop it and render （暂无）.
                document_id = "（未知文档）"
            documents.append("`%s`" % _neutralise(document_id))
        if documents:
            contradiction_lines.append(
                "- **%s**（`%s`）：%s 提出相反证据（contradicts）"
                % (_statement_of(item), _finding_id_of(item),
                   "、".join(documents)))

    action_lines = []
    for action in actions:
        if not isinstance(action, dict):
            action_lines.append("- %s" % _neutralise(action))
            continue
        headline = ""
        if action.get("topic"):
            headline = "**%s**" % _neutralise(action["topic"])
        if action.get("type"):
            if headline:
                headline += "（`%s`）" % _neutralise(action["type"])
            else:
                headline = "`%s`" % _neutralise(action["type"])
        pieces = [headline] if headline else []
        if action.get("reason"):
            pieces.append("%s" % _neutralise(action["reason"]))
        if pieces:
            action_lines.append("- " + "：".join(pieces))
        else:
            action_lines.append("- （该行动未提供 topic/type/reason）")

    bodies = {
        "关键发现": finding_lines,
        "证据": evidence_lines,
        "知识缺口": gap_lines,
        "矛盾": contradiction_lines,
        "下一步行动": action_lines,
    }

    lines = ["# 研究状态", ""]
    for title in SECTION_TITLES:
        lines.append("## %s" % title)
        lines.append("")
        body = bodies[title]
        lines.extend(body if body else [EMPTY_MARKER])
        lines.append("")

    return "\n".join(lines)
