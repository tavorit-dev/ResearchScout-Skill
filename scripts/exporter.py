"""Deterministic Chinese Markdown report over one research archive.

The archive keeps three different kinds of record and this module is where
they must stay visibly different (spec 9.8, spec 10.6):

1. **Raw records** -- ``documents.jsonl``: what a page said, its metadata and
   the analysis written for that one document (first and second layers).
2. **Research conclusions** -- ``findings.jsonl``: conclusions synthesised
   *across* documents. They are labelled as inferences (推断) with their
   evidence, never written as an article's stated fact.
3. **Hypotheses** -- the optional ``trends`` and ``unverified_hypotheses``
   keys an agent writes into the latest iteration's ``observations``. They are
   labelled as hypotheses and rendered as missing when the agent did not
   supply them; this module never manufactures a trend.

Two further invariants shape the code:

* **Absence is not zero.** ``archive.load_state`` and
  ``planner.load_research`` raise ``FileNotFoundError`` for a project without
  those files. The report says ``（暂无）`` (or reports the window as open)
  instead of reading an absent state as round 0 or inventing a boundary. Only
  ``FileNotFoundError`` is caught: a corrupt file's ``ValueError`` is
  corruption and must reach the caller.
* **Untrusted text is neutralised at render only.** Every value that came
  from a web page or an agent backfill -- evidence quote, locator, note,
  finding prose, document title/summary/author/URL, observation strings,
  action fields -- passes through ``neutralise_line`` as it is written, so it
  cannot start a line and forge a heading or fence. It is never rewritten
  before persistence or merging.

The report is a *view*, not an authoritative record: it reads the archive,
writes one file under ``reports/``, opens no socket, calls no model and
touches no other record. It carries no ``generated_at`` timestamp, because the
same archive must render byte-identical text; the file's mtime is when it was
generated. ``reports/`` must already exist -- the exporter refuses a mistyped
root rather than creating a directory tree that makes a typo look like a
project.
"""
import json
import math
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from scripts.archive import (
    DOCUMENTS_FILE,
    FINDINGS_FILE,
    ITERATIONS_DIR,
    SOURCES_FILE,
    export_csv,
    load_state,
)
from scripts.lib.jsonl import read_records
from scripts.lib.markdown import neutralise_line
from scripts.planner import load_research

# The report's location below the research root, and the directory it needs.
REPORTS_DIR = Path("reports")
REPORT_FILE = REPORTS_DIR / "research-report.md"

# Rendered in a section that has nothing to report. An empty section with no
# marker is a malformed document; the marker keeps the shape readable.
EMPTY_MARKER = "（暂无）"

# The nine sections the plan names, in this exact order.
SECTION_TITLES = (
    "本轮研究目标",
    "采集概况",
    "主要发现",
    "重要资料",
    "技术趋势",
    "争议与差异",
    "历史资料",
    "信息缺口",
    "下一步计划",
)

# ``iterations/round-NNN.json``; the number is parsed from the name so a
# round count past 999 still sorts numerically rather than lexically.
_ROUND_NAME = re.compile(r"round-(\d+)\.json\Z")


def export_markdown_report(root):
    """Render the research report and return the path written.

    Reads documents, findings, sources, the latest iteration, the research
    record and the state through the archive layer, then writes
    ``reports/research-report.md`` with ``Path.write_text``. A report is a
    regenerable view, so it is not written through the archive's atomic
    writer: a truncated report loses no authoritative record and the next
    export replaces it.

    Raises
    ------
    FileNotFoundError
        ``reports/`` does not exist below ``root``. The message names the
        root, so a mistyped path is reported as the mistyped path. No
        directory is created.
    ValueError
        ``research.yaml`` or ``state/current.json`` exists but is corrupt.
        Absence is reported as ``（暂无）``; corruption is not absence.
    """
    root = Path(root)
    if not (root / REPORTS_DIR).is_dir():
        raise FileNotFoundError(
            f"reports directory not found at {root}: {REPORTS_DIR}/ is "
            f"missing; initialize the project with planner.init_research "
            f"first"
        )

    rounds = _load_rounds(root)
    text = _render_report(
        research=_load_research_or_none(root),
        state=_load_state_or_none(root),
        documents=read_records(root / DOCUMENTS_FILE),
        findings=read_records(root / FINDINGS_FILE),
        sources=read_records(root / SOURCES_FILE),
        round_count=len(rounds),
        latest=rounds[-1][1] if rounds else None,
    )
    path = root / REPORT_FILE
    path.write_text(text, encoding="utf-8")
    return path


def export_all(root):
    """Export every v1 product and return their paths, keyed by product.

    ``csv`` is the path ``archive.export_csv`` returned, not a guessed
    ``documents/documents.csv``: the archive layer owns that path and a second
    copy of it here could drift from the writer.
    """
    root = Path(root)
    return {
        "csv": export_csv(root),
        "report": export_markdown_report(root),
    }


def _render_report(*, research, state, documents, findings, sources,
                   round_count, latest):
    """Return the complete report text; no clock is read."""
    bodies = {
        "本轮研究目标": _goal_lines(research),
        "采集概况":
            _collection_lines(state, documents, sources, round_count, latest),
        "主要发现": _finding_lines(findings),
        "重要资料": _document_lines(documents),
        "技术趋势": _trend_lines(latest),
        "争议与差异": _contradiction_lines(findings, latest),
        "历史资料": _history_lines(research, documents),
        "信息缺口": _gap_lines(latest),
        "下一步计划": _action_lines(latest),
    }
    lines = ["# 研究报告", ""]
    for title in SECTION_TITLES:
        body = bodies[title]
        lines.append("## %s" % title)
        lines.append("")
        lines.extend(body if body else [EMPTY_MARKER])
        lines.append("")
    return "\n".join(lines)


# -- archive reads ----------------------------------------------------------


def _load_research_or_none(root):
    """Return the validated research record, or ``None`` when absent.

    Only ``FileNotFoundError`` is swallowed: an absent record means the
    report has nothing to say about the objective, while a corrupt record
    raises ``ValueError`` and must not be rendered as a blank section.
    """
    try:
        return load_research(root)
    except FileNotFoundError:
        return None


def _load_state_or_none(root):
    """Return ``state/current.json``, or ``None`` when the file is absent.

    An absent state is reported as missing, never as round 0 or a fresh
    budget; a malformed state raises ``ValueError`` from the archive layer.
    """
    try:
        return load_state(root)
    except FileNotFoundError:
        return None


def _load_rounds(root):
    """Return ``(round_number, record)`` pairs, ordered by round number.

    Files that are not ``round-NNN.json`` are ignored. A round file that is
    not valid JSON raises ``ValueError`` naming the path: the latest iteration
    drives the trend, gap and action sections, and silently skipping a corrupt
    one would report an older round as the latest.
    """
    directory = Path(root) / ITERATIONS_DIR
    if not directory.is_dir():
        return []
    rounds = []
    for path in directory.iterdir():
        if not path.is_file():
            continue
        match = _ROUND_NAME.match(path.name)
        if match is None:
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"iteration record {path} is not valid JSON: {exc}"
            ) from exc
        rounds.append((int(match.group(1)), record))
    rounds.sort(key=lambda item: item[0])
    return rounds


# -- section renderers ------------------------------------------------------


def _goal_lines(research):
    """Render the objective and the research frame; empty when unrecorded."""
    if not isinstance(research, dict):
        return []
    lines = []
    objective = research.get("objective")
    if isinstance(objective, str) and objective.strip():
        lines.append("- 研究目标：%s" % neutralise_line(objective))

    topic = research.get("topic")
    domain = research.get("domain")
    if isinstance(topic, str) and topic.strip():
        if isinstance(domain, str) and domain.strip():
            lines.append("- 主题：%s（领域：%s）"
                         % (neutralise_line(topic), neutralise_line(domain)))
        else:
            lines.append("- 主题：%s" % neutralise_line(topic))

    keywords = research.get("keywords")
    if isinstance(keywords, list) and keywords:
        lines.append("- 关键词：%s"
                     % "、".join(neutralise_line(item) for item in keywords))

    window = research.get("time_window")
    if isinstance(window, dict):
        lines.append("- 时间范围：%s 至 %s"
                     % (_date_label(window.get("start")),
                        _date_label(window.get("end"))))
    return lines


def _collection_lines(state, documents, sources, round_count, latest):
    """Render factual counts only, never an interpretation of them."""
    lines = ["- 文档总数：%d" % len(documents)]

    statuses = Counter(
        document["crawl_status"] for document in documents
        if isinstance(document, dict)
        and isinstance(document.get("crawl_status"), str)
    )
    if statuses:
        lines.append("- 文档状态：%s" % _counts_text(statuses))

    lines.append("- 发布日期未知或无法解析的文档：%d 篇（日期未记录，未作推断）"
                 % _unknown_date_count(documents))
    lines.append("- 信息源总数：%d" % len(sources))

    compliance = Counter(
        source["compliance_status"] for source in sources
        if isinstance(source, dict)
        and isinstance(source.get("compliance_status"), str)
    )
    if compliance:
        lines.append("- 信息源合规状态：%s" % _counts_text(compliance))

    lines.append("- 已记录轮次：%d" % round_count)

    requests_used = state.get("requests_used") if isinstance(state, dict) else None
    if _is_count(requests_used):
        lines.append("- 已用请求数：%d" % requests_used)

    handoff = latest.get("handoff") if isinstance(latest, dict) else None
    new_documents = handoff.get("new_documents") if isinstance(handoff, dict) else None
    if _is_count(new_documents):
        lines.append("- 最近一轮新增文档：%d" % new_documents)
    return lines


def _finding_lines(findings):
    """Render cross-document conclusions as labelled inferences."""
    ordered = _sort_findings(findings)
    if not ordered:
        return []
    lines = [
        "以下为跨文档综合结论（推断），不是任何单篇文章的原文事实。",
        "",
    ]
    for finding in ordered:
        lines.append("- **%s**（推断）" % _statement_of(finding))
        meta = ["`%s`" % _finding_id_of(finding)]
        confidence = _format_number(finding.get("confidence"))
        if confidence is not None:
            meta.append("置信度 %s" % confidence)
        importance = _format_number(finding.get("importance"))
        if importance is not None:
            meta.append("重要性 %s" % importance)
        lines.append("  - %s" % "；".join(meta))
        for piece in _evidence_list(finding):
            lines.append(_evidence_line(piece))
        for label, field in (
            ("适用范围", "scope"),
            ("局限", "limitations"),
            ("后续问题", "follow_up"),
        ):
            value = finding.get(field)
            if isinstance(value, str) and value.strip():
                lines.append("  - %s：%s" % (label, neutralise_line(value)))
    return lines


def _evidence_line(piece):
    """Render one evidence item; a dirty non-mapping still shows its absence."""
    if isinstance(piece, dict):
        document_id = piece.get("document_id")
        evidence_type = piece.get("evidence_type")
        quote = piece.get("quote")
        locator = piece.get("locator")
        note = piece.get("note")
    else:
        document_id = evidence_type = quote = locator = note = None

    if not isinstance(document_id, str) or not document_id:
        document_id = "（未知文档）"
    if not isinstance(evidence_type, str) or not evidence_type:
        evidence_type = "（未知类型）"

    line = "  - 证据 `%s`（%s）" % (
        neutralise_line(document_id), neutralise_line(evidence_type))
    parts = []
    if isinstance(quote, str) and quote.strip():
        parts.append(neutralise_line(quote))
    if isinstance(locator, str) and locator.strip():
        parts.append("定位：%s" % neutralise_line(locator))
    if isinstance(note, str) and note.strip():
        parts.append("说明：%s" % neutralise_line(note))
    if parts:
        line += "：" + "；".join(parts)
    return line


def _document_lines(documents):
    """Render first-layer facts plus second-layer analysis, per document."""
    ordered = _sort_documents(documents)
    if not ordered:
        return []
    lines = [
        "以下为单篇文档的事实与文档分析（原始记录与文档分析两层），"
        "不是跨文档结论。",
        "",
    ]
    for document in ordered:
        title = document.get("title")
        if not (isinstance(title, str) and title.strip()):
            title = "（无标题）"
        lines.append("- **%s**（`%s`）"
                     % (neutralise_line(title), _document_id_of(document)))

        url = document.get("canonical_url")
        if isinstance(url, str) and url.strip():
            lines.append("  - 链接：%s" % neutralise_line(url))

        source_id = document.get("source_id")
        source_name = document.get("source_name")
        if isinstance(source_id, str) and source_id:
            if isinstance(source_name, str) and source_name.strip():
                lines.append("  - 来源：%s（`%s`）"
                             % (neutralise_line(source_name),
                                neutralise_line(source_id)))
            else:
                lines.append("  - 来源：`%s`" % neutralise_line(source_id))

        author = document.get("author")
        if isinstance(author, str) and author.strip():
            lines.append("  - 作者：%s" % neutralise_line(author))

        published = document.get("published_at")
        published_text = (
            neutralise_line(published)
            if isinstance(published, str) and published.strip()
            else "未知（未作推断）"
        )
        crawled = document.get("crawled_at")
        crawled_text = (
            neutralise_line(crawled)
            if isinstance(crawled, str) and crawled.strip()
            else "未记录"
        )
        lines.append("  - 发布：%s；抓取：%s" % (published_text, crawled_text))

        summary = document.get("summary")
        if isinstance(summary, str) and summary.strip():
            lines.append("  - 摘要：%s" % neutralise_line(summary))
    return lines


def _trend_lines(latest):
    """Render agent-supplied trends and hypotheses as hypotheses, not facts."""
    observations = _observations(latest)
    trends = _items(observations, "trends")
    lines = []
    if trends:
        lines.append(
            "以下为趋势判断（假设，非事实），由研究代理基于当前证据归纳。")
        lines.append("")
        lines.extend("- %s" % neutralise_line(item) for item in trends)
    else:
        lines.append(EMPTY_MARKER)

    lines.append("")
    lines.append("### 待验证假设")
    lines.append("")
    hypotheses = _items(observations, "unverified_hypotheses")
    if hypotheses:
        lines.extend("- %s" % neutralise_line(item) for item in hypotheses)
    else:
        lines.append(EMPTY_MARKER)
    return lines


def _contradiction_lines(findings, latest):
    """Render recorded contradictions plus the deterministic evidence rule.

    The rule matches ``reflection.summarize_state``: a finding whose evidence
    contains ``contradicts`` is listed with the document that raised it. The
    section reports the evidence type; it does not judge which side wins.
    """
    observations = _observations(latest)
    lines = ["- %s" % neutralise_line(item)
             for item in _items(observations, "contradictions")]

    for finding in _sort_findings(findings):
        documents = []
        for piece in _evidence_list(finding):
            if not isinstance(piece, dict):
                continue
            if piece.get("evidence_type") != "contradicts":
                continue
            document_id = piece.get("document_id")
            if not isinstance(document_id, str) or not document_id:
                document_id = "（未知文档）"
            documents.append("`%s`" % neutralise_line(document_id))
        if documents:
            lines.append(
                "- **%s**（`%s`）：%s 提出相反证据（contradicts）"
                % (_statement_of(finding), _finding_id_of(finding),
                   "、".join(documents))
            )
    return lines


def _history_lines(research, documents):
    """List documents older than the window start; recency only orders.

    Spec 7.6: the time window orders results and never disqualifies historical
    material. A null or absent start is reported as an open window rather than
    guessed, and a document with no parseable publication date is reported as
    date-unknown instead of being classified by a date nobody recorded
    (invariant 11, spec 10.2).
    """
    start_text = None
    if isinstance(research, dict):
        window = research.get("time_window")
        if isinstance(window, dict):
            start_text = window.get("start")

    has_start = isinstance(start_text, str) and bool(start_text.strip())
    start_date = _as_date(start_text) if has_start else None
    lines = []
    if not has_start:
        lines.append("- 时间窗口起始未设置（窗口开放），不单列历史资料。")
    elif start_date is None:
        lines.append("- 时间窗口起始无法解析（按开放处理），不单列历史资料。")
    else:
        historical = [
            document for document in _sort_documents(documents)
            if _is_before(document.get("published_at"), start_date)
        ]
        if not historical:
            lines.append(EMPTY_MARKER)
        for document in historical:
            title = document.get("title")
            if not (isinstance(title, str) and title.strip()):
                title = "（无标题）"
            line = "- **%s**（`%s`，%s）" % (
                neutralise_line(title),
                _document_id_of(document),
                neutralise_line(document.get("published_at")),
            )
            url = document.get("canonical_url")
            if isinstance(url, str) and url.strip():
                line += "：%s" % neutralise_line(url)
            lines.append(line)

    lines.append("")
    lines.append(
        "- 发布日期未知或无法解析的文档：%d 篇（日期未记录，未作推断）"
        % _unknown_date_count(documents))
    return lines


def _gap_lines(latest):
    """Render the agent's knowledge gaps; never derive one from silence."""
    return ["- %s" % neutralise_line(item)
            for item in _items(_observations(latest), "knowledge_gaps")]


def _action_lines(latest):
    """Render the latest iteration's decision and proposed next actions."""
    if not isinstance(latest, dict):
        return []
    lines = []
    decision = latest.get("decision")
    if isinstance(decision, str) and decision.strip():
        lines.append("- 本轮决策：`%s`" % neutralise_line(decision))
    for action in _items(latest, "next_actions"):
        lines.append(_action_line(action))
    return lines


def _action_line(action):
    """Render one next action, mirroring reflection's action shape."""
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
        return "- （该行动未提供 topic/type/reason）"
    return "- " + "：".join(pieces)


# -- small shared helpers ---------------------------------------------------


def _observations(latest):
    """Return the latest iteration's observation dict, or an empty one."""
    if not isinstance(latest, dict):
        return {}
    observations = latest.get("observations")
    return observations if isinstance(observations, dict) else {}


def _items(container, key):
    """Return a non-blank list under ``key``; a non-list key yields nothing.

    Only a list is accepted, so a scalar or mapping an agent happened to write
    cannot be iterated as if it were a list of statements. Blank and ``None``
    items are dropped because a blank bullet is a malformed document.
    """
    if not isinstance(container, dict):
        return []
    value = container.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value
            if item is not None
            and not (isinstance(item, str) and not item.strip())]


def _evidence_list(record):
    """Return a finding's evidence list, tolerating dirty data."""
    value = record.get("evidence")
    return value if isinstance(value, list) else []


def _statement_of(finding):
    statement = finding.get("statement")
    if isinstance(statement, str) and statement.strip():
        return neutralise_line(statement)
    return "（无陈述）"


def _finding_id_of(finding):
    finding_id = finding.get("finding_id")
    if isinstance(finding_id, str) and finding_id:
        return neutralise_line(finding_id)
    return "（无 ID）"


def _document_id_of(document):
    document_id = document.get("document_id")
    if isinstance(document_id, str) and document_id:
        return neutralise_line(document_id)
    return "（无 ID）"


def _sort_findings(findings):
    """Order findings by importance descending, then by ``finding_id``.

    The order matches ``reflection.summarize_state`` so the report and the
    current summary do not disagree about what matters most.
    """
    def key(finding):
        importance = finding.get("importance")
        if isinstance(importance, bool) or not isinstance(
                importance, (int, float)):
            importance = 0.0
        elif not math.isfinite(importance):
            importance = 0.0
        return (-float(importance), _finding_id_of(finding))

    return sorted(
        (item for item in findings if isinstance(item, dict)), key=key)


def _sort_documents(documents):
    """Order documents by publication date descending, unknown dates last.

    Publication date is stated by the page or null; nothing is inferred. A
    document with no parseable date sorts after dated ones and is never
    placed on the timeline.
    """
    def key(document):
        published = _as_date(document.get("published_at"))
        if published is None:
            return (1, 0, _document_id_of(document))
        return (0, -published.toordinal(), _document_id_of(document))

    return sorted(
        (item for item in documents if isinstance(item, dict)), key=key)


def _as_date(value):
    """Return an ISO 8601 date parsed from ``value``, or ``None``.

    Accepts a date or a timestamp (including a trailing ``Z``). Anything else
    -- missing, null, non-string, a non-date string -- is unknown, not
    approximated.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _is_before(value, boundary):
    """True when ``value`` parses as a date strictly before ``boundary``."""
    published = _as_date(value)
    return published is not None and published < boundary


def _unknown_date_count(documents):
    """Count documents whose publication date is missing or unparseable."""
    return sum(
        1 for document in documents
        if isinstance(document, dict)
        and _as_date(document.get("published_at")) is None
    )


def _is_count(value):
    """True for a non-negative int that is not a bool."""
    return (isinstance(value, int) and not isinstance(value, bool)
            and value >= 0)


def _counts_text(counts):
    """Render a Counter as a stable ``key N，key N`` list."""
    return "，".join(
        "%s %d" % (neutralise_line(key), counts[key]) for key in sorted(counts))


def _date_label(value):
    """Render a window boundary, saying 未设置 when it is absent."""
    if isinstance(value, str) and value.strip():
        return neutralise_line(value)
    return "未设置"


def _format_number(value):
    """Render a number compactly, or ``None`` when it is not usable."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return "%g" % value
