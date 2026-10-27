"""Shared deterministic render primitives for Markdown research output.

Two products render research memory to Markdown: ``reflection.summarize_state``
(the current-state summary) and ``exporter.export_markdown_report`` (the
research report). They must not disagree about what a finding is called, how
findings are ordered, or how an action is rendered, so those decisions live
here once. Each function is pure, performs no I/O and reads no clock: identical
inputs render byte-identical text.

Every value handled here may have come from a web page or an agent backfill
(CLAUDE.md invariant 6). The functions that return display text apply
``neutralise_line`` at the render boundary, so a value cannot start a line and
forge a heading, bullet or fence. Neutralising is a display decision: callers
must never apply it before persisting or merging a record.
"""
import math

from scripts.lib.markdown import neutralise_line

# Rendered in a section that has nothing to report. An empty section with no
# marker is a malformed document; the marker keeps the shape readable.
EMPTY_MARKER = "（暂无）"

# The fallback for a next action that carries none of topic/type/reason.
_ACTION_WITHOUT_FIELDS = "（该行动未提供 topic/type/reason）"


def is_real_number(value):
    """True for an int/float that is neither bool nor NaN/infinity.

    ``bool`` is excluded because ``True`` is an ``int`` in Python but is never
    a score or a weight; silently reading it as ``1`` would hide a caller bug.
    NaN and infinity are excluded because they poison every comparison
    downstream.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def format_number(value):
    """Render a number compactly, or ``None`` when it is not a usable number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return "%g" % value


def evidence_list(record):
    """Return a record's evidence list, tolerating dirty data.

    A missing or non-list ``evidence`` becomes an empty list: a malformed
    state file must not abort a round or a report, and there is nothing to
    merge or render from a value that is not a list anyway.
    """
    value = record.get("evidence")
    return value if isinstance(value, list) else []


def statement_of(finding):
    """Return a finding's neutralised statement, or a marker when absent."""
    statement = finding.get("statement")
    if isinstance(statement, str) and statement.strip():
        return neutralise_line(statement)
    return "（无陈述）"


def finding_id_of(finding):
    """Return a finding's neutralised id, or a marker when absent."""
    finding_id = finding.get("finding_id")
    if isinstance(finding_id, str) and finding_id:
        return neutralise_line(finding_id)
    return "（无 ID）"


def sort_findings(findings):
    """Order findings by importance descending, then by ``finding_id``.

    Both the summary and the report use this function, so their relative
    order is the same by construction rather than by two copies agreeing.
    Non-mapping entries are skipped. A missing or non-numeric importance
    sorts last by being treated as 0.
    """
    def key(finding):
        importance = finding.get("importance")
        if not is_real_number(importance):
            importance = 0.0
        return (-float(importance), finding_id_of(finding))

    return sorted(
        (item for item in findings if isinstance(item, dict)), key=key)


def action_line(action):
    """Render one next action as a single neutralised Markdown list item.

    Only non-blank *strings* count as a supplied ``topic``, ``type`` or
    ``reason``: a number or a list is not prose and rendering it as if it were
    would invent wording the agent did not write. A mapping with none of the
    three gets the shared fallback line; a non-mapping action is rendered as
    one neutralised line.

    The returned line starts with ``- `` so both renderers can append it
    unchanged.
    """
    if not isinstance(action, dict):
        return "- %s" % neutralise_line(action)

    headline = ""
    topic = action.get("topic")
    if isinstance(topic, str) and topic.strip():
        headline = "**%s**" % neutralise_line(topic)
    action_type = action.get("type")
    if isinstance(action_type, str) and action_type.strip():
        if headline:
            headline += "（`%s`）" % neutralise_line(action_type)
        else:
            headline = "`%s`" % neutralise_line(action_type)

    pieces = [headline] if headline else []
    reason = action.get("reason")
    if isinstance(reason, str) and reason.strip():
        pieces.append(neutralise_line(reason))
    if not pieces:
        return "- %s" % _ACTION_WITHOUT_FIELDS
    return "- " + "：".join(pieces)
