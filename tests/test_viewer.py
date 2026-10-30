"""The static viewer renders the archive; these tests pin what it may show.

The viewer is a *view*: it reads the archive, writes only below
``<project>/viewer/``, opens no socket and never rewrites a record. Three
properties matter beyond "the files exist":

* **Untrusted text cannot become markup.** Document titles, summaries,
  finding prose and source names all originate from web pages or an agent
  backfill. They are escaped at the render boundary, and a URL that is not
  ``http``/``https`` is not emitted as a link (CLAUDE.md invariants 4 and 6).
* **A document id cannot choose a path.** Ids come from the same untrusted
  side; a generated detail page must stay inside ``viewer/documents/``.
* **The output is deterministic.** ``build`` reads no clock, so building the
  same archive twice produces byte-identical files.
"""
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from scripts.archive import (SOURCES_FILE, save_documents, save_findings,
                             save_iteration)
from scripts.lib.jsonl import append_record
from scripts.lib.paths import research_dir
from scripts.planner import init_research
from scripts.research_loop import main
from scripts.viewer import build


def make_document(document_id, **overrides):
    """Return a schema-valid document record for the tests."""
    record = {
        "document_id": document_id,
        "source_id": "src_1",
        "source_name": "示例来源",
        "canonical_url": "https://e.org/%s" % document_id,
        "title": "文档 %s" % document_id,
        "crawl_status": "PERSISTED",
        "retention_mode": "reference",
        "attempt_count": 0,
    }
    record.update(overrides)
    return record


def make_finding(finding_id, statement, evidence, **overrides):
    """Return a schema-valid finding record for the tests."""
    record = {
        "finding_id": finding_id,
        "statement": statement,
        "confidence": 0.8,
        "importance": 0.6,
        "evidence": evidence,
    }
    record.update(overrides)
    return record


def make_iteration(round_no, **overrides):
    """Return an iteration record shaped like research_loop.apply_results."""
    record = {
        "round": round_no,
        "decision": "CONTINUE",
        "observations": {},
        "findings_added": [],
        "findings_reinforced": [],
        "findings_conflicting": [],
        "rejected_findings": [],
        "skipped_findings": [],
        "next_actions": [],
        "handoff": {
            "round": round_no,
            "new_documents": 0,
            "candidates_for_review": 0,
            "budget_exhausted": False,
        },
        "written_at": "2025-01-01T00:00:00+00:00",
    }
    record.update(overrides)
    return record


class ViewerTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.archive_root = Path(self.tmp.name) / "archive"
        init_research(
            self.archive_root,
            domain="industry",
            topic="agent-infra",
            objective="评估 Agent 基础设施需求",
            keywords=["agent", "infra"],
            time_window={"start": "2025-01-01", "end": None},
        )
        self.root = research_dir(self.archive_root, "industry", "agent-infra")

    @property
    def viewer(self):
        return self.root / "viewer"

    def populate(self):
        save_documents(self.root, [
            make_document(
                "doc_1", title="资料标题一", summary="摘要内容一",
                canonical_url="https://e.org/one",
                published_at="2025-03-01"),
            make_document(
                "doc_2", title="资料标题二", summary="摘要内容二",
                canonical_url="https://e.org/two", published_at=None),
        ])
        save_findings(self.root, [
            make_finding(
                "find_1", "跨文档结论一",
                [{"document_id": "doc_1", "evidence_type": "supports",
                  "locator": "第2节"}],
                scope="适用于内部系统", limitations="样本有限",
                follow_up="还需要验证"),
        ])
        append_record(self.root / SOURCES_FILE, {
            "source_id": "src_1",
            "source_name": "示例来源",
            "base_url": "https://e.org",
            "compliance_status": "ALLOWED",
            "selection_status": "selected",
        })
        save_iteration(self.root, 1, make_iteration(
            1,
            decision="CONTINUE",
            observations={
                "knowledge_gaps": ["缺口说明"],
                "trends": ["趋势说明"],
                "unverified_hypotheses": ["假设说明"],
                "contradictions": [],
            },
            next_actions=[
                {"topic": "深入方向", "type": "deepen",
                 "reason": "理由说明"},
            ],
        ))

    def write_report(self, text):
        path = self.root / "reports" / "research-report.md"
        path.write_text(text, encoding="utf-8")
        return path


class TestBuildOutput(ViewerTestBase):
    def test_build_writes_navigable_pages(self):
        self.populate()
        index = build(self.root)

        self.assertEqual(index, self.viewer / "index.html")
        for relative in (
            "index.html",
            "report.html",
            "findings.html",
            "documents.html",
            "sources.html",
            "rounds.html",
            "assets/styles.css",
            "assets/app.js",
            "documents/doc_1.html",
            "documents/doc_2.html",
        ):
            self.assertTrue((self.viewer / relative).exists(), relative)

    def test_index_shows_objective_and_counts(self):
        self.populate()
        build(self.root)
        html = (self.viewer / "index.html").read_text(encoding="utf-8")

        self.assertIn("评估 Agent 基础设施需求", html)
        self.assertIn("文档 2 · 来源 1 · 发现 1 · 轮次 1", html)

    def test_every_page_carries_the_navigation(self):
        self.populate()
        build(self.root)

        for relative in ("index.html", "report.html", "findings.html",
                         "documents.html", "sources.html", "rounds.html"):
            html = (self.viewer / relative).read_text(encoding="utf-8")
            for target in ("index.html", "report.html", "findings.html",
                           "documents.html", "sources.html", "rounds.html"):
                self.assertIn('href="%s"' % target, html, relative)

    def test_document_page_links_back_to_the_root(self):
        self.populate()
        build(self.root)
        html = (self.viewer / "documents" / "doc_1.html").read_text(
            encoding="utf-8")

        for target in ("../index.html", "../documents.html",
                       "../findings.html"):
            self.assertIn('href="%s"' % target, html)

    def test_empty_project_still_builds(self):
        build(self.root)

        self.assertTrue((self.viewer / "index.html").exists())
        index_html = (self.viewer / "index.html").read_text(encoding="utf-8")
        self.assertIn("文档 0 · 来源 0 · 发现 0 · 轮次 0", index_html)
        findings_html = (self.viewer / "findings.html").read_text(
            encoding="utf-8")
        self.assertIn("（暂无）", findings_html)

    def test_missing_project_is_an_error(self):
        with self.assertRaises(FileNotFoundError):
            build(Path(self.tmp.name) / "does-not-exist")


class TestRenderedContent(ViewerTestBase):
    def test_document_detail_shows_metadata_and_external_link(self):
        self.populate()
        build(self.root)
        html = (self.viewer / "documents" / "doc_1.html").read_text(
            encoding="utf-8")

        self.assertIn("资料标题一", html)
        self.assertIn("摘要内容一", html)
        self.assertIn("2025-03-01", html)
        self.assertIn('href="https://e.org/one"', html)
        self.assertIn('rel="noopener noreferrer"', html)
        self.assertIn('target="_blank"', html)

    def test_document_detail_lists_related_findings(self):
        self.populate()
        build(self.root)
        html = (self.viewer / "documents" / "doc_1.html").read_text(
            encoding="utf-8")

        self.assertIn("跨文档结论一", html)
        self.assertIn('href="../findings.html#find_1"', html)

    def test_finding_evidence_links_to_the_document(self):
        self.populate()
        build(self.root)
        html = (self.viewer / "findings.html").read_text(encoding="utf-8")

        self.assertIn("跨文档结论一", html)
        self.assertIn('href="documents/doc_1.html"', html)
        self.assertIn("第2节", html)

    def test_sources_page_shows_compliance_status(self):
        self.populate()
        build(self.root)
        html = (self.viewer / "sources.html").read_text(encoding="utf-8")

        self.assertIn("示例来源", html)
        self.assertIn("ALLOWED", html)

    def test_rounds_page_shows_decisions_and_actions(self):
        self.populate()
        build(self.root)
        html = (self.viewer / "rounds.html").read_text(encoding="utf-8")

        self.assertIn("CONTINUE", html)
        self.assertIn("缺口说明", html)
        self.assertIn("趋势说明", html)
        self.assertIn("假设说明", html)
        self.assertIn("深入方向", html)
        self.assertIn("理由说明", html)


class TestUntrustedInput(ViewerTestBase):
    def test_document_text_is_escaped(self):
        save_documents(self.root, [
            make_document(
                "doc_x", title='<script>alert("x")</script>',
                summary="<img src=x onerror=alert(1)>",
                canonical_url="https://e.org/x"),
        ])
        build(self.root)

        html = (self.viewer / "documents.html").read_text(encoding="utf-8")
        self.assertNotIn('<script>alert("x")</script>', html)
        self.assertNotIn("<img src=x onerror", html)
        self.assertIn("&lt;script&gt;alert", html)

    def test_finding_text_is_escaped(self):
        save_findings(self.root, [
            make_finding(
                "find_x", "<b>结论</b>",
                [{"document_id": "doc_1", "evidence_type": "supports",
                  "locator": "<i>位置</i>"}],
                scope="<u>范围</u>", limitations="<s>局限</s>",
                follow_up="<em>后续</em>"),
        ])
        build(self.root)

        html = (self.viewer / "findings.html").read_text(encoding="utf-8")
        for raw in ("<b>结论</b>", "<i>位置</i>", "<u>范围</u>",
                    "<s>局限</s>", "<em>后续</em>"):
            self.assertNotIn(raw, html)
        self.assertIn("&lt;b&gt;结论&lt;/b&gt;", html)

    def test_non_http_url_is_not_a_link(self):
        save_documents(self.root, [
            make_document(
                "doc_evil", title="危险链接",
                canonical_url="javascript:alert(1)"),
        ])
        append_record(self.root / SOURCES_FILE, {
            "source_id": "src_evil",
            "source_name": "危险来源",
            "base_url": "data:text/html,<script>alert(1)</script>",
            "compliance_status": "ALLOWED",
            "selection_status": "selected",
        })
        build(self.root)

        for relative in ("documents.html", "documents/doc_evil.html",
                         "sources.html"):
            html = (self.viewer / relative).read_text(encoding="utf-8")
            self.assertNotIn("javascript:alert", html, relative)
            self.assertNotIn("data:text/html", html, relative)

    def test_document_id_cannot_escape_the_viewer_directory(self):
        save_documents(self.root, [
            make_document("../../evil", title="路径穿越"),
        ])
        build(self.root)

        outside = list((self.root / "viewer").parent.parent.rglob("evil.html"))
        self.assertEqual(outside, [])
        self.assertFalse((self.root / "viewer" / "evil.html").exists())


class TestReportRendering(ViewerTestBase):
    def test_report_markdown_becomes_html(self):
        self.write_report(
            "# 报告标题\n\n"
            "**重点结论** 与 `find_1`。\n\n"
            "- 第一项\n"
            "  - 嵌套项\n\n"
            "## 小节\n\n"
            "<script>alert(1)</script>\n"
        )
        build(self.root)
        html = (self.viewer / "report.html").read_text(encoding="utf-8")

        self.assertIn("<h1>报告标题</h1>", html)
        self.assertIn("<strong>重点结论</strong>", html)
        self.assertIn("<code>find_1</code>", html)
        self.assertIn("<li>第一项</li>", html)
        self.assertIn('class="nested"', html)
        self.assertIn("嵌套项", html)
        self.assertIn("<h2>小节</h2>", html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_missing_report_is_reported_as_absent(self):
        build(self.root)
        html = (self.viewer / "report.html").read_text(encoding="utf-8")

        self.assertIn("（暂无）", html)


class TestDeterminism(ViewerTestBase):
    def test_building_twice_produces_identical_bytes(self):
        self.populate()
        build(self.root)
        first = {
            path.relative_to(self.viewer): path.read_bytes()
            for path in self.viewer.rglob("*") if path.is_file()
        }

        build(self.root)
        second = {
            path.relative_to(self.viewer): path.read_bytes()
            for path in self.viewer.rglob("*") if path.is_file()
        }

        self.assertEqual(first, second)


class TestCommandLine(ViewerTestBase):
    def test_build_viewer_flag_writes_the_pages(self):
        self.populate()
        out = StringIO()
        with redirect_stdout(out):
            code = main(["--build-viewer", "--root", str(self.root)])

        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(Path(payload["index"]), self.viewer / "index.html")
        self.assertTrue((self.viewer / "index.html").exists())
