"""Build-time static HTML viewer for one research project (Phase 5).

The viewer is a *view*, not a second store: it reads the archive, writes only
below ``<project>/viewer/`` and never rewrites a record. It opens no socket,
runs no model and reads no clock, so building the same archive twice produces
byte-identical files. ``viewer/`` is generated output and is rebuilt as a
whole; the ``documents/`` subdirectory is removed first so a deleted document
does not leave a stale detail page behind.

Two invariants meet at this boundary.

1. **Untrusted text stays text (invariant 6).** Document titles, summaries,
   finding statements, evidence locators and source names all originate from
   a web page or an agent backfill. Every value is HTML-escaped as it is
   written, and a URL is emitted only when it parses as ``http``/``https``
   with a host -- so a ``javascript:`` or ``data:`` value can never become an
   ``href``. Markdown is rendered by the small renderer in this module, never
   by passing raw HTML through.
2. **An id cannot choose a path.** Document ids come from the same untrusted
   side. A detail page is written only for an id matching
   ``^[A-Za-z0-9][A-Za-z0-9._-]*$``; anything else is listed as plain text and
   gets no page, so ``../../evil`` stays a string.

The pages mirror ``docs/prd-refactor.md`` section 7: a project overview, the
report, findings, a filterable document list, one page per document, sources
and the iteration history. A document's saved ``summary`` is shown because it
is part of the permitted excerpt; a body that was never retained
(``retention_mode`` other than a full-text mode) is not invented -- the page
links to the original address instead.
"""
import html
import json
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from scripts.archive import (DOCUMENTS_FILE, FINDINGS_FILE, ITERATIONS_DIR,
                             SOURCES_FILE, load_state)
from scripts.lib.jsonl import read_records
from scripts.lib.render import evidence_list, sort_findings
from scripts.planner import load_research

# Output layout, relative to the project root.
VIEWER_DIR = Path("viewer")
DOCUMENTS_OUT = VIEWER_DIR / "documents"
ASSETS_OUT = VIEWER_DIR / "assets"

# Archive inputs the viewer reads.
REPORT_FILE = Path("reports") / "research-report.md"
PENDING_ACTIONS_FILE = Path("state") / "pending_actions.json"

# Templates live with the Skill, not with a project's archive, so one copy of
# the stylesheet and script serves every project.
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"

EMPTY_MARKER = "（暂无）"

# ``iterations/round-NNN.json``; the number is parsed from the name so a round
# count past 999 still sorts numerically rather than lexically.
_ROUND_NAME = re.compile(r"round-(\d+)\.json\Z")

# A document id is also a file name. The pattern keeps it a single path
# segment: no separators, no leading dot and no ``..`` component.
_SAFE_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*\Z")

# Navigation in page order. ``key`` is the page identifier used for the
# active state; ``label`` is what a reader sees; ``href`` is relative to the
# page being rendered.
NAV_ITEMS = (
    ("index", "研究总览", "index.html"),
    ("report", "研究报告", "report.html"),
    ("findings", "研究发现", "findings.html"),
    ("documents", "文档", "documents.html"),
    ("sources", "信息来源", "sources.html"),
    ("rounds", "迭代历史", "rounds.html"),
)


def build(root):
    """Generate the static viewer and return the path to ``index.html``.

    ``root`` is a research project root (``archive/<domain>/<topic>``). The
    whole ``viewer/`` tree is regenerated; no archive record is modified.

    Raises
    ------
    FileNotFoundError
        ``root`` is not a directory. A mistyped path fails here instead of
        producing a viewer for an archive that does not exist.
    ValueError
        ``research.yaml``, ``state/current.json`` or an iteration file exists
        but is corrupt. Absence is rendered as ``（暂无）``; corruption is
        not absence.
    """
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(
            f"research project not found at {root}: pass the project root "
            f"archive/<domain>/<topic>"
        )

    data = _load(root)
    out = root / VIEWER_DIR
    documents_out = out / DOCUMENTS_OUT.name

    # ``viewer/documents/`` is generated output. Clear it so a document that
    # disappeared from the archive cannot leave an orphan detail page.
    if documents_out.exists():
        shutil.rmtree(documents_out)
    documents_out.mkdir(parents=True, exist_ok=True)

    assets = out / ASSETS_OUT.name
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "styles.css").write_text(
        _read_template("styles.css"), encoding="utf-8")
    (assets / "app.js").write_text(
        _read_template("app.js"), encoding="utf-8")

    pages = {
        "index.html": _render_index(data),
        "report.html": _render_report(data),
        "findings.html": _render_findings(data),
        "documents.html": _render_documents(data),
        "sources.html": _render_sources(data),
        "rounds.html": _render_rounds(data),
    }
    for name, text in pages.items():
        (out / name).write_text(text, encoding="utf-8")

    for document in data["documents"]:
        document_id = _safe_id(document.get("document_id"))
        if document_id is None:
            continue
        (documents_out / f"{document_id}.html").write_text(
            _render_document(data, document), encoding="utf-8")

    return out / "index.html"


# -- archive reads ----------------------------------------------------------


def _load(root):
    """Read every input once; missing optional files become ``None``/empty."""
    return {
        "root": root,
        "research": _load_research_or_none(root),
        "state": _load_state_or_none(root),
        "documents": _sorted_documents(read_records(root / DOCUMENTS_FILE)),
        "findings": sort_findings(read_records(root / FINDINGS_FILE)),
        "sources": read_records(root / SOURCES_FILE),
        "rounds": _load_rounds(root),
        "report": _read_text_or_none(root / REPORT_FILE),
        "pending_actions": _read_json_or_none(root / PENDING_ACTIONS_FILE),
    }


def _load_research_or_none(root):
    try:
        return load_research(root)
    except FileNotFoundError:
        return None


def _load_state_or_none(root):
    try:
        return load_state(root)
    except FileNotFoundError:
        return None


def _read_text_or_none(path):
    return path.read_text(encoding="utf-8") if path.exists() else None


def _read_json_or_none(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc


def _load_rounds(root):
    """Return ``(round_number, record)`` pairs ordered by round number."""
    directory = Path(root) / ITERATIONS_DIR
    if not directory.is_dir():
        return []
    rounds = []
    for path in directory.iterdir():
        match = _ROUND_NAME.match(path.name)
        if not match or not path.is_file():
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} is not valid JSON: {exc}") from exc
        rounds.append((int(match.group(1)), record))
    rounds.sort(key=lambda item: item[0])
    return rounds


def _sorted_documents(documents):
    """Order dated documents newest first, then undated by id."""
    dated = [doc for doc in documents if _document_date(doc) is not None]
    undated = [doc for doc in documents if _document_date(doc) is None]
    dated.sort(key=lambda doc: (_document_date(doc), _id_text(doc)),
               reverse=True)
    undated.sort(key=_id_text)
    return dated + undated


def _document_date(document):
    value = document.get("published_at")
    return value if isinstance(value, str) and value.strip() else None


def _id_text(document):
    value = document.get("document_id")
    return value if isinstance(value, str) else ""


# -- page shell -------------------------------------------------------------


def _page(title, body, *, prefix="", active=None):
    """Return a complete page around ``body``; ``prefix`` reaches the root."""
    nav = []
    for key, label, href in NAV_ITEMS:
        css = (' class="nav-link active"' if key == active
               else ' class="nav-link"')
        nav.append(f'<a{css} href="{prefix}{href}">{_text(label)}</a>')

    return (
        "<!doctype html>\n"
        '<html lang="zh-CN">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" '
        'content="width=device-width, initial-scale=1">\n'
        f"<title>{_text(title)} · Research Scout</title>\n"
        f'<link rel="stylesheet" href="{prefix}assets/styles.css">\n'
        "</head>\n"
        "<body>\n"
        f'<a class="skip-link" href="#main">跳到正文</a>\n'
        '<div class="layout">\n'
        '<aside class="sidebar">\n'
        f'<a class="brand" href="{prefix}index.html">Research Scout</a>\n'
        '<p class="brand-note">本地研究档案</p>\n'
        '<nav aria-label="页面导航">\n'
        + "\n".join(nav)
        + "\n</nav>\n"
        "</aside>\n"
        "<main id=\"main\" class=\"content\">\n"
        f"{body}\n"
        "</main>\n"
        "</div>\n"
        f'<script src="{prefix}assets/app.js" defer></script>\n'
        "</body>\n"
        "</html>\n"
    )


# -- pages ------------------------------------------------------------------


def _render_index(data):
    research = data["research"] or {}
    state = data["state"] or {}
    documents = data["documents"]
    findings = data["findings"]
    sources = data["sources"]
    rounds = data["rounds"]

    objective = _first_text(
        research.get("objective"), "（暂无研究目标）")
    domain, topic = _project_names(data)
    status = _first_text(state.get("status"), research.get("status"))
    latest = rounds[-1][1] if rounds else {}

    parts = [
        '<header class="page-head">',
        f'<p class="eyebrow">{_text(domain)} / {_text(topic)}</p>',
        "<h1>研究总览</h1>",
        f'<p class="lede">{_text(objective)}</p>',
        f'<p class="counts">文档 {len(documents)} · 来源 {len(sources)} '
        f'· 发现 {len(findings)} · 轮次 {len(rounds)}</p>',
        "</header>",
    ]

    facts = []
    if status:
        facts.append(("状态", status))
    if state.get("round") is not None:
        facts.append(("当前轮次", state.get("round")))
    if state.get("requests_used") is not None:
        facts.append(("已用请求", state.get("requests_used")))
    if state.get("updated_at"):
        facts.append(
            ("最后更新", _datetime_label(state.get("updated_at"))))
    window = research.get("time_window")
    if isinstance(window, dict):
        if window.get("start"):
            facts.append(("开始日期", _date_label(window.get("start"))))
        facts.append(("结束日期", _date_label(window.get("end"))
                      if window.get("end") else "开放"))
    parts.append(_fact_grid(facts))

    parts.append('<details class="panel scope-panel">')
    parts.append("<summary>研究范围</summary>")
    parts.append(_scope_block(research.get("scope")))
    parts.append("</details>")

    parts.append('<section class="panel">')
    parts.append(
        '<div class="panel-head"><h2>主要发现</h2>'
        '<a class="more" href="findings.html">查看全部 →</a></div>')
    if findings:
        parts.append('<div class="cards">')
        for finding in findings[:8]:
            parts.append(_finding_card(finding))
        parts.append("</div>")
    else:
        parts.append(_empty())
    parts.append("</section>")

    parts.append('<section class="panel">')
    parts.append("<h2>知识缺口与下一步</h2>")
    gaps = _items(latest, "observations", "knowledge_gaps")
    parts.append(_bullet_section("尚待回答", gaps))
    actions = _next_actions(data)
    parts.append(_action_section("候选行动", actions))
    parts.append("</section>")

    return _page("研究总览", "\n".join(parts), active="index")


def _render_report(data):
    report = data["report"]
    title = "研究报告"
    if report is None or not report.strip():
        body = f'<p class="empty">{_text(EMPTY_MARKER)}</p>'
    else:
        title, markdown_body = _split_report_title(report)
        body = (
            f'<article class="markdown">{_markdown(markdown_body)}</article>')
    return _page(
        title,
        '<header class="page-head"><p class="eyebrow">研究产物</p>'
        f"<h1>{_text(title)}</h1></header>\n" + body,
        active="report",
    )


def _split_report_title(report):
    """Use the report's own ``#`` heading as the page title.

    The exporter always starts with ``# 研究报告``. Rendering it twice
    (once in the page header, once inside the Markdown body) would give the
    page two ``h1`` elements, so the heading is lifted out and the rest is
    rendered.
    A report without a leading level-1 heading keeps a generic title.
    """
    lines = report.splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        match = re.match(r"^#\s+(.*)$", line.strip())
        if match:
            title = match.group(1).strip() or "研究报告"
            return title, "\n".join(lines[index + 1:])
        break
    return "研究报告", report


def _render_findings(data):
    findings = data["findings"]
    parts = [
        '<header class="page-head">'
        '<p class="eyebrow">跨文档综合结论</p>'
        f"<h1>研究发现</h1><p class=\"lede\">共 {len(findings)} 条，"
        "按重要性排序；每一条都标注为推断，"
        "并附证据与局限。</p></header>",
    ]
    if not findings:
        parts.append(_empty())
    for finding in findings:
        parts.append(_finding_article(finding))
    return _page("研究发现", "\n".join(parts), active="findings")


def _render_documents(data):
    documents = data["documents"]
    sources = sorted({
        doc.get("source_name") for doc in documents
        if isinstance(doc.get("source_name"), str)
        and doc.get("source_name")
    })
    statuses = sorted({
        doc.get("crawl_status") for doc in documents
        if isinstance(doc.get("crawl_status"), str) and doc.get("crawl_status")
    })

    source_options = "".join(
        f'<option value="{_text(name)}">{_text(name)}</option>'
        for name in sources
    )
    status_options = "".join(
        f'<option value="{_text(name)}">{_text(name)}</option>'
        for name in statuses
    )

    rows = []
    for document in documents:
        rows.append(_document_row(document))
    body_rows = "\n".join(rows) if rows else ""

    parts = [
        '<header class="page-head"><p class="eyebrow">采集到的资料</p>'
        f"<h1>文档</h1><p class=\"lede\">共 {len(documents)} 篇。"
        "本页只展示元数据与已保存摘要；"
        "原文请打开原始地址。</p></header>",
        '<div class="filters">',
        '<label class="field"><span>搜索标题</span>'
        '<input type="search" data-doc-search '
        'placeholder="输入标题关键词"></label>',
        '<label class="field"><span>来源</span>'
        f'<select data-doc-source><option value="">全部</option>'
        f"{source_options}</select></label>",
        '<label class="field"><span>状态</span>'
        f'<select data-doc-status><option value="">全部</option>'
        f"{status_options}</select></label>",
        "</div>",
        '<div class="table-wrap">',
        '<table class="documents-table">',
        "<thead><tr><th>标题</th><th>来源</th><th>发布日期</th>"
        "<th>状态</th><th>摘要</th></tr></thead>",
        "<tbody>",
        body_rows,
        "</tbody>",
        "</table>",
        "</div>",
        f'<p class="empty" data-doc-empty hidden>{_text(EMPTY_MARKER)}</p>',
    ]
    return _page("文档", "\n".join(parts), active="documents")


def _render_document(data, document):
    document_id = _safe_id(document.get("document_id"))
    title = _first_text(document.get("title"), "（无标题）")
    findings = _findings_for_document(data["findings"], document_id)

    facts = [
        ("文档 ID",
         document_id or document.get("document_id") or "未记录"),
        ("来源", document.get("source_name")),
        ("发布日期", _datetime_label(
            _first_text(document.get("published_at"), "未记录"))),
        ("采集时间", _datetime_label(
            _first_text(document.get("crawled_at"), "未记录"))),
        ("首次发现", _datetime_label(
            _first_text(document.get("first_seen_at"), "未记录"))),
        ("采集状态", document.get("crawl_status")),
        ("保留方式", document.get("retention_mode")),
    ]

    parts = [
        '<p class="crumbs"><a href="../documents.html">文档</a> / '
        f"{_text(document_id or '未命名')}</p>",
        '<header class="page-head">',
        f"<h1>{_text(title)}</h1>",
        _source_link(document),
        "</header>",
        _fact_grid(facts),
    ]

    summary = document.get("summary")
    parts.append('<section class="panel">')
    parts.append("<h2>已保存摘要</h2>")
    parts.append(
        f'<p class="prose">{_text(summary)}</p>'
        if isinstance(summary, str) and summary.strip() else _empty()
    )
    parts.append("</section>")

    parts.append('<section class="panel">')
    parts.append("<h2>关联研究发现</h2>")
    if findings:
        items = []
        for finding in findings:
            finding_id = _anchor_id(finding)
            statement = _first_text(
                finding.get("statement"), "（无陈述）")
            items.append(
                f'<li><a href="../findings.html#{_text(finding_id)}">'
                f"{_text(statement)}</a></li>"
            )
        parts.append("<ul class=\"link-list\">" + "".join(items) + "</ul>")
    else:
        parts.append(_empty())
    parts.append("</section>")

    return _page(title, "\n".join(parts), prefix="../", active="documents")


def _render_sources(data):
    sources = data["sources"]
    counts = {}
    for document in data["documents"]:
        source_id = document.get("source_id")
        if isinstance(source_id, str):
            counts[source_id] = counts.get(source_id, 0) + 1

    parts = [
        '<header class="page-head"><p class="eyebrow">来源与合规</p>'
        f"<h1>信息来源</h1>"
        f"<p class=\"lede\">共 {len(sources)} 个来源；"
        "合规状态由合规闸门记录。</p></header>",
    ]
    if not sources:
        parts.append(_empty())
    else:
        parts.append('<div class="table-wrap"><table>')
        parts.append(
            "<thead><tr><th>来源</th><th>地址</th><th>类型</th>"
            "<th>合规状态</th><th>权威度</th>"
            "<th>文档</th></tr></thead>"
        )
        parts.append("<tbody>")
        for source in sources:
            name = _first_text(
                source.get("source_name"), "（未命名来源）")
            href = _safe_href(source.get("base_url"))
            url_text = _text(source.get("base_url"))
            address = (
                f'<a href="{href}" target="_blank" '
                f'rel="noopener noreferrer">{url_text}</a>'
                if href else "（无可用地址）")
            status = _first_text(source.get("compliance_status"), "未知")
            parts.append(
                "<tr>"
                f"<td>{_text(name)}</td>"
                f"<td>{address}</td>"
                f"<td>{_text(source.get('source_type') or '未记录')}</td>"
                f"<td>{_badge(status)}</td>"
                f"<td>{_text(source.get('authority_level') or '未记录')}"
                "</td>"
                f"<td>{counts.get(source.get('source_id'), 0)}</td>"
                "</tr>"
            )
        parts.append("</tbody></table></div>")
    return _page("信息来源", "\n".join(parts), active="sources")


def _render_rounds(data):
    rounds = data["rounds"]
    parts = [
        '<header class="page-head"><p class="eyebrow">研究过程</p>'
        f"<h1>迭代历史</h1><p class=\"lede\">共 {len(rounds)} 轮。"
        "每轮记录观察、发现变化与下一步行动。</p></header>",
    ]
    if not rounds:
        parts.append(_empty())
    for number, record in rounds:
        parts.append(_round_article(number, record, data["findings"]))
    return _page("迭代历史", "\n".join(parts), active="rounds")


# -- findings ---------------------------------------------------------------


def _finding_card(finding):
    finding_id = _anchor_id(finding)
    statement = _first_text(finding.get("statement"), "（无陈述）")
    return (
        '<article class="card">'
        f'<a class="card-link" href="findings.html#{_text(finding_id)}">'
        f"<h3>{_text(statement)}</h3></a>"
        f'<p class="scores">{_score_text(finding)}</p>'
        "</article>"
    )


def _finding_article(finding):
    finding_id = _anchor_id(finding)
    statement = _first_text(finding.get("statement"), "（无陈述）")
    parts = [
        f'<article class="finding" id="{_text(finding_id)}">',
        f"<h2>{_text(statement)}</h2>",
        f'<p class="scores">{_score_text(finding)}</p>',
    ]
    for label, key in (("适用范围", "scope"), ("局限", "limitations"),
                       ("后续问题", "follow_up")):
        value = finding.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(
                f'<p><span class="label">{_text(label)}</span>'
                f"{_text(value)}</p>"
            )
    parts.append(_evidence_block(finding))
    parts.append("</article>")
    return "\n".join(parts)


def _evidence_block(finding):
    evidence = [item for item in evidence_list(finding)
                if isinstance(item, dict)]
    if not evidence:
        return f'<p class="empty">{_text(EMPTY_MARKER)}</p>'
    items = []
    for item in evidence:
        document_id = item.get("document_id")
        target = _document_link(document_id, "")
        evidence_type = _first_text(item.get("evidence_type"), "未知")
        locator = item.get("locator")
        quote = item.get("quote")
        tail = []
        if isinstance(locator, str) and locator.strip():
            tail.append(f"定位：{_text(locator)}")
        if isinstance(quote, str) and quote.strip():
            tail.append(f"引文：{_text(quote)}")
        detail = "；".join(tail)
        items.append(
            f"<li>{target}（{_text(evidence_type)}）"
            + (f"<br><span class=\"muted\">{detail}</span>" if detail else "")
            + "</li>"
        )
    return '<ul class="evidence">' + "".join(items) + "</ul>"


def _findings_for_document(findings, document_id):
    if not document_id:
        return []
    return [
        finding for finding in findings
        if any(isinstance(item, dict)
               and item.get("document_id") == document_id
               for item in evidence_list(finding))
    ]


# -- rounds -----------------------------------------------------------------


def _round_article(number, record, findings):
    decision = _first_text(record.get("decision"), "未记录")
    parts = [
        '<article class="round">',
        f'<div class="panel-head"><h2>第 {_text(number)} 轮</h2>'
        f"{_badge(decision)}</div>",
    ]
    written_at = record.get("written_at")
    if isinstance(written_at, str) and written_at:
        parts.append(f'<p class="muted">{_text(written_at)}</p>')

    observations = record.get("observations")
    observations = observations if isinstance(observations, dict) else {}
    parts.append(_bullet_section(
        "知识缺口", _string_items(observations.get("knowledge_gaps"))))
    parts.append(_bullet_section(
        "趋势", _string_items(observations.get("trends"))))
    parts.append(_bullet_section(
        "未验证假设",
        _string_items(observations.get("unverified_hypotheses"))))
    parts.append(_bullet_section(
        "矛盾", _string_items(observations.get("contradictions"))))

    added = _string_items(record.get("findings_added"))
    if added:
        items = []
        for finding_id in added:
            items.append(
                f'<li>{_finding_link(finding_id, findings)}</li>'
            )
        parts.append('<h3>新增发现</h3><ul>' + "".join(items) + "</ul>")

    actions = _string_items(record.get("next_actions"))
    parts.append(_action_section("下一步行动", actions))
    parts.append(_action_section(
        "已接受行动", _string_items(record.get("actions_accepted"))))
    parts.append(_handoff_block(record.get("handoff")))
    parts.append("</article>")
    return "\n".join(parts)


def _handoff_block(handoff):
    if not isinstance(handoff, dict):
        return ""
    labels = (
        ("new_documents", "新增文档"),
        ("candidates_for_review", "待审候选"),
        ("budget_exhausted", "预算耗尽"),
    )
    facts = []
    for key, label in labels:
        value = handoff.get(key)
        if value is None:
            continue
        if isinstance(value, bool):
            value = "是" if value else "否"
        facts.append((label, value))
    if not facts:
        return ""
    return '<h3>本轮交接</h3>' + _fact_grid(facts)


# -- shared fragments -------------------------------------------------------


def _fact_grid(facts):
    cells = []
    for label, value in facts:
        if value is None or value == "":
            continue
        cells.append(
            '<div class="fact">'
            f'<dt>{_text(label)}</dt><dd>{_text(value)}</dd>'
            "</div>"
        )
    if not cells:
        return ""
    return '<dl class="fact-grid">' + "".join(cells) + "</dl>"


def _scope_block(scope):
    if not isinstance(scope, dict):
        return _empty()
    parts = []
    for key, label in (("core", "核心范围"), ("related", "相关范围"),
                       ("excluded", "排除范围")):
        items = _string_items(scope.get(key))
        if items:
            parts.append(f"<h3>{_text(label)}</h3>")
            parts.append(_bullet_list(items))
    return "".join(parts) if parts else _empty()


def _bullet_section(title, items):
    items = _string_items(items)
    if not items:
        return ""
    return f"<h3>{_text(title)}</h3>" + _bullet_list(items)


def _bullet_list(items):
    return "<ul>" + "".join(f"<li>{_text(item)}</li>"
                            for item in items) + "</ul>"


def _action_section(title, actions):
    actions = _string_items(actions)
    if not actions:
        return ""
    items = "".join(f"<li>{_action_text(action)}</li>" for action in actions)
    return f"<h3>{_text(title)}</h3><ul>{items}</ul>"


def _action_text(action):
    if not isinstance(action, dict):
        return _text(action)
    headline = ""
    topic = action.get("topic")
    if isinstance(topic, str) and topic.strip():
        headline = f"<strong>{_text(topic)}</strong>"
    action_type = action.get("type")
    if isinstance(action_type, str) and action_type.strip():
        code = f"<code>{_text(action_type)}</code>"
        headline = f"{headline}（{code}）" if headline else code
    reason = action.get("reason")
    if isinstance(reason, str) and reason.strip():
        return f"{headline}：{_text(reason)}" if headline else _text(reason)
    return headline or _text(EMPTY_MARKER)


def _score_text(finding):
    confidence = finding.get("confidence")
    importance = finding.get("importance")
    pieces = []
    if _is_number(confidence):
        pieces.append(f"置信度 {confidence:g}")
    if _is_number(importance):
        pieces.append(f"重要性 {importance:g}")
    return " · ".join(pieces) if pieces else "未评分"


def _document_row(document):
    document_id = _safe_id(document.get("document_id"))
    title = _first_text(document.get("title"), "（无标题）")
    if document_id:
        title_html = (f'<a href="documents/{document_id}.html">'
                      f"{_text(title)}</a>")
    else:
        title_html = _text(title)
    source = _first_text(document.get("source_name"), "未记录")
    published = _date_label(
        _first_text(document.get("published_at"), "未记录"))
    status = _first_text(document.get("crawl_status"), "未记录")
    summary = document.get("summary")
    summary_html = (_text(_truncate(summary, 80))
                    if isinstance(summary, str) and summary.strip() else "")
    return (
        f'<tr data-doc-row data-title="{_text(title)}" '
        f'data-source="{_text(source)}" data-status="{_text(status)}">'
        f"<td>{title_html}</td>"
        f"<td>{_text(source)}</td>"
        f"<td>{_text(published)}</td>"
        f"<td>{_badge(status)}</td>"
        f"<td>{summary_html}</td>"
        "</tr>"
    )


def _source_link(document):
    href = _safe_href(document.get("canonical_url")) or \
        _safe_href(document.get("source_url"))
    if href is None:
        return '<p class="muted">（未记录可打开的原文地址）</p>'
    return (f'<p><a class="external" href="{href}" target="_blank" '
            f'rel="noopener noreferrer">打开原文 ↗</a></p>')


def _badge(value):
    css = _badge_class(value)
    return f'<span class="badge badge-{css}">{_text(value)}</span>'


def _badge_class(value):
    text = str(value).lower()
    if text in ("allowed", "persisted", "stop_complete", "complete"):
        return "good"
    if text in ("blocked", "error", "unreachable", "stop_budget"):
        return "bad"
    if text in ("review_required", "paused_review"):
        return "warn"
    return "neutral"


def _empty():
    return f'<p class="empty">{_text(EMPTY_MARKER)}</p>'


def _finding_link(finding_id, findings):
    if not isinstance(finding_id, str) or not finding_id:
        return _text(EMPTY_MARKER)
    for finding in findings:
        if finding.get("finding_id") == finding_id:
            return (f'<a href="findings.html#{_text(_anchor_id(finding))}">'
                    f"{_text(finding_id)}</a>")
    return _text(finding_id)


def _next_actions(data):
    """Return the actions a reader should see as next steps.

    ``state/pending_actions.json`` is the accepted queue after the latest
    round; the iteration record's ``next_actions`` is the agent's proposal.
    The queue wins when both exist because it is what the next round would
    actually run.
    """
    pending = data["pending_actions"]
    if isinstance(pending, dict):
        accepted = pending.get("accepted")
        if isinstance(accepted, list) and accepted:
            return accepted
    if data["rounds"]:
        latest = data["rounds"][-1][1]
        return latest.get("next_actions") or []
    return []


# -- safe rendering ---------------------------------------------------------


def _text(value):
    """Escape a value for HTML text and attribute contexts."""
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def _first_text(*values):
    """Return the first non-blank string value, or an empty string."""
    for value in values:
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _safe_id(value):
    """Return ``value`` when it is one safe path segment, else ``None``."""
    if not isinstance(value, str):
        return None
    if value in (".", "..") or not _SAFE_ID.match(value):
        return None
    return value


def _anchor_id(finding):
    value = finding.get("finding_id")
    return value if isinstance(value, str) and value else "finding"


def _safe_href(value):
    """Return an escaped ``http``/``https`` URL, or ``None``.

    Only an absolute URL with a host is accepted. ``javascript:``,
    ``data:``, ``file:`` and relative values are refused, so untrusted text
    can never become a link target (invariant 6).
    """
    if not isinstance(value, str) or not value.strip():
        return None
    if any(ord(character) < 32 for character in value):
        return None
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https"):
        return None
    if not parts.netloc:
        return None
    return html.escape(value.strip(), quote=True)


def _document_link(document_id, prefix):
    safe = _safe_id(document_id)
    if safe is None:
        return _text(document_id if isinstance(document_id, str) else "")
    return (f'<a href="{prefix}documents/{safe}.html">'
            f"<code>{_text(document_id)}</code></a>")


def _date_label(value):
    """Render an ISO timestamp as a date in list views; keep anything else."""
    match = re.match(r"^(\d{4}-\d{2}-\d{2})(?:[T\s].*)?$", str(value))
    return match.group(1) if match else str(value)


def _datetime_label(value):
    """Render an ISO timestamp as ``YYYY-MM-DD HH:MM`` when the shape fits."""
    match = re.match(
        r"^(\d{4}-\d{2}-\d{2})[T\s](\d{2}:\d{2})(?::\d{2})?", str(value))
    return f"{match.group(1)} {match.group(2)}" if match else str(value)


def _truncate(value, limit):
    text = str(value)
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _string_items(value):
    """Return list items as-is, dropping non-list values and non-mappings."""
    return list(value) if isinstance(value, list) else []


def _items(container, outer, inner):
    value = container.get(outer) if isinstance(container, dict) else None
    if not isinstance(value, dict):
        return []
    return _string_items(value.get(inner))


def _project_names(data):
    research = data["research"] or {}
    domain = _first_text(research.get("domain"), data["root"].parent.name)
    topic = _first_text(research.get("topic"), data["root"].name)
    return domain, topic


# -- Markdown ---------------------------------------------------------------


def _markdown(text):
    """Render the small Markdown subset the report exporter emits.

    Supported: ATX headings, unordered and ordered lists (one visual nesting
    level via indentation), blockquotes, horizontal rules, paragraphs, bold
    and inline code. Everything is HTML-escaped before formatting, so raw
    HTML in the source stays text. Links and tables are not part of the
    exporter's output and are deliberately not supported here.
    """
    lines = text.splitlines()
    out = []
    paragraph = []
    list_kind = None
    in_quote = False

    def close_paragraph():
        if paragraph:
            out.append("<p>" + _inline(" ".join(paragraph)) + "</p>")
            paragraph.clear()

    def close_list():
        nonlocal list_kind
        if list_kind is not None:
            out.append(f"</{list_kind}>")
            list_kind = None

    def close_quote():
        nonlocal in_quote
        if in_quote:
            out.append("</blockquote>")
            in_quote = False

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            close_paragraph()
            close_list()
            close_quote()
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            close_paragraph()
            close_list()
            close_quote()
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            continue

        if re.match(r"^([-*_])\1{2,}$", stripped):
            close_paragraph()
            close_list()
            close_quote()
            out.append("<hr>")
            continue

        quote = re.match(r"^>\s?(.*)$", stripped)
        if quote:
            close_paragraph()
            close_list()
            if not in_quote:
                out.append("<blockquote>")
                in_quote = True
            out.append("<p>" + _inline(quote.group(1)) + "</p>")
            continue
        close_quote()

        bullet = re.match(r"^(\s*)[-*]\s+(.*)$", line)
        if bullet:
            close_paragraph()
            indent = len(bullet.group(1))
            if list_kind != "ul":
                close_list()
                out.append("<ul>")
                list_kind = "ul"
            css = ' class="nested"' if indent >= 2 else ""
            out.append(f"<li{css}>{_inline(bullet.group(2))}</li>")
            continue

        ordered = re.match(r"^(\s*)\d+\.\s+(.*)$", line)
        if ordered:
            close_paragraph()
            indent = len(ordered.group(1))
            if list_kind != "ol":
                close_list()
                out.append("<ol>")
                list_kind = "ol"
            css = ' class="nested"' if indent >= 2 else ""
            out.append(f"<li{css}>{_inline(ordered.group(2))}</li>")
            continue

        close_list()
        paragraph.append(stripped)

    close_paragraph()
    close_list()
    close_quote()
    return "\n".join(out)


def _inline(text):
    """Escape ``text`` then apply bold and inline-code formatting.

    Code spans are protected from bold processing by splitting on them
    first; there is no other markup, so no raw HTML can pass through.
    """
    escaped = html.escape(text, quote=False)
    pieces = re.split(r"(`[^`]*`)", escaped)
    rendered = []
    for index, piece in enumerate(pieces):
        if index % 2 == 1 and piece.startswith("`") and piece.endswith("`"):
            rendered.append(f"<code>{piece[1:-1]}</code>")
        else:
            rendered.append(
                re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", piece))
    return "".join(rendered)


# -- templates --------------------------------------------------------------


def _read_template(name):
    return (TEMPLATES_DIR / name).read_text(encoding="utf-8")
