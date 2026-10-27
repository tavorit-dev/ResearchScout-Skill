# tests/test_exporter.py
"""The report exporter renders the archive; these tests pin what it may claim.

Two properties matter beyond "the file exists". First, untrusted text --
web excerpts, agent-written observations -- must not be able to forge report
structure, so a newline plus a heading in a quote stays one line. Second, the
report must never invent what the archive does not record: an absent state is
not round 0, an absent window start is open rather than a guessed boundary,
and an unknown publication date is reported as unknown rather than classified
as historical.
"""
import tempfile
import unittest
from pathlib import Path

from scripts.archive import export_csv, save_documents, save_findings, save_iteration
from scripts.exporter import export_all, export_markdown_report
from scripts.lib.paths import ensure_research_layout, research_dir
from scripts.planner import init_research
from scripts.viewer import build

REPORT_REL = Path("reports") / "research-report.md"

SECTION_TITLES = (
    "本轮研究目标", "采集概况", "主要发现", "重要资料", "技术趋势",
    "争议与差异", "历史资料", "信息缺口", "下一步计划",
)


def make_document(document_id, **overrides):
    """Return a schema-valid document record for the tests."""
    record = {
        "document_id": document_id,
        "source_id": "src_1",
        "canonical_url": "https://e.org/%s" % document_id,
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


def new_project(tmp, *, time_window=None):
    """Create a research project through the planner and return its root."""
    archive_root = Path(tmp) / "archive"
    init_research(
        archive_root,
        domain="security",
        topic="ai-agent-security",
        objective="评估 AI Agent 安全风险",
        keywords=["agent", "prompt injection"],
        time_window=time_window if time_window is not None
        else {"start": "2025-01-01", "end": None},
    )
    return research_dir(archive_root, "security", "ai-agent-security")


def section(text, title):
    """Return the body under ``## title``, up to the next level-2 heading."""
    marker = "## %s" % title
    start = text.index(marker) + len(marker)
    rest = text[start:]
    end = rest.find("\n## ")
    if end == -1:
        end = len(rest)
    return rest[:end]


class TestExporterBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())


class TestBriefContract(TestExporterBase):
    def test_report_written(self):
        root = ensure_research_layout(
            self.tmp / "security" / "ai-agent-security")
        path = export_markdown_report(root)
        self.assertTrue(path.exists())
        self.assertIn("研究", path.read_text(encoding="utf-8"))

    def test_export_all_returns_paths(self):
        root = ensure_research_layout(
            self.tmp / "security" / "ai-agent-security")
        paths = export_all(root)
        self.assertIn("report", paths)
        self.assertIn("csv", paths)

    def test_export_all_uses_archive_csv_path_and_report_path(self):
        root = ensure_research_layout(
            self.tmp / "security" / "ai-agent-security")
        paths = export_all(root)
        self.assertEqual(paths["csv"], export_csv(root))
        self.assertTrue(paths["csv"].exists())
        self.assertEqual(paths["report"], root / REPORT_REL)
        self.assertTrue(paths["report"].exists())


class TestViewerPlaceholder(unittest.TestCase):
    def test_raises_with_phase_note(self):
        with self.assertRaises(NotImplementedError) as ctx:
            build(Path("/tmp/archive"))
        self.assertIn("Phase 5", str(ctx.exception))


class TestPopulatedReport(TestExporterBase):
    def setUp(self):
        super().setUp()
        self.root = new_project(self.tmp)
        save_documents(self.root, [
            make_document(
                "doc_1", title="资料标题一", summary="摘要内容一",
                canonical_url="https://e.org/one",
                published_at="2025-03-01",
                crawl_status="PERSISTED"),
            make_document(
                "doc_2", title="资料标题二",
                canonical_url="https://e.org/two",
                published_at=None,
                crawl_status="ANALYZED"),
        ])
        save_findings(self.root, [
            make_finding(
                "find_1", "跨文档结论一",
                [{"document_id": "doc_1", "evidence_type": "supports",
                  "quote": "引用原文", "locator": "第2节",
                  "note": "支持说明"}],
                scope="适用于内部系统", limitations="样本有限",
                follow_up="还需要验证"),
        ])
        save_iteration(self.root, 1, make_iteration(
            1,
            observations={
                "trends": ["趋势说明"],
                "unverified_hypotheses": ["假设说明"],
                "contradictions": ["差异说明"],
                "knowledge_gaps": ["缺口说明"],
            },
            next_actions=[
                {"topic": "深入方向", "type": "deepen", "reason": "理由说明"},
                "普通行动",
            ],
            handoff={"round": 1, "new_documents": 1,
                     "candidates_for_review": 0, "budget_exhausted": False},
        ))

    def test_renders_records(self):
        text = export_markdown_report(self.root).read_text(encoding="utf-8")
        for title in SECTION_TITLES:
            self.assertIn("## %s" % title, text)
        for expected in (
            "跨文档结论一", "资料标题一", "https://e.org/one", "引用原文",
            "第2节", "支持说明", "适用于内部系统", "样本有限", "还需要验证",
            "缺口说明", "趋势说明", "假设说明", "差异说明",
            "深入方向", "deepen", "理由说明", "普通行动",
        ):
            self.assertIn(expected, text)

    def test_labels_inference_and_document_layers(self):
        text = export_markdown_report(self.root).read_text(encoding="utf-8")
        finding_body = section(text, "主要发现")
        self.assertIn("推断", finding_body)
        self.assertIn("`find_1`", finding_body)
        self.assertIn("`doc_1`", finding_body)
        document_body = section(text, "重要资料")
        self.assertIn("文档分析", document_body)
        self.assertIn("`doc_1`", document_body)

    def test_sections_order(self):
        text = export_markdown_report(self.root).read_text(encoding="utf-8")
        positions = [text.index("## %s" % title) for title in SECTION_TITLES]
        self.assertEqual(positions, sorted(positions))

    def test_collection_counts(self):
        text = export_markdown_report(self.root).read_text(encoding="utf-8")
        body = section(text, "采集概况")
        self.assertIn("文档总数：2", body)
        self.assertIn("发布日期未知或无法解析的文档：1 篇", body)
        self.assertIn("已记录轮次：1", body)
        self.assertIn("最近一轮新增文档：1", body)


class TestUntrustedTextCannotForgeStructure(TestExporterBase):
    def setUp(self):
        super().setUp()
        self.root = new_project(self.tmp)
        forged = "正常`片段\n## 伪造标题"
        save_documents(self.root, [
            make_document("doc_1", title="标题", summary=forged,
                          canonical_url="https://e.org/one"),
        ])
        save_findings(self.root, [
            make_finding(
                "find_1", forged,
                [{"document_id": "doc_1", "evidence_type": "supports",
                  "quote": forged, "locator": forged, "note": forged}]),
        ])
        save_iteration(self.root, 1, make_iteration(
            1,
            observations={
                "trends": [forged],
                "unverified_hypotheses": [forged],
                "contradictions": [forged],
                "knowledge_gaps": [forged],
            },
            next_actions=[
                {"topic": "方向", "type": "deepen", "reason": forged},
            ],
        ))

    def test_no_line_becomes_the_forged_heading(self):
        text = export_markdown_report(self.root).read_text(encoding="utf-8")
        for line in text.splitlines():
            self.assertNotEqual(line.strip(), "## 伪造标题")
        self.assertNotIn("\n## 伪造标题", text)
        # Neutralising replaces the backtick too, so no code span is opened
        # by the injected value.
        self.assertIn("正常'片段 ## 伪造标题", text)


class TestEmptyProject(TestExporterBase):
    def test_all_sections_render_without_invented_records(self):
        root = ensure_research_layout(
            self.tmp / "security" / "ai-agent-security")
        text = export_markdown_report(root).read_text(encoding="utf-8")
        for title in SECTION_TITLES:
            self.assertIn("## %s" % title, text)
        for title in (
            "本轮研究目标", "主要发现", "重要资料", "技术趋势",
            "争议与差异", "信息缺口", "下一步计划",
        ):
            self.assertIn("（暂无）", section(text, title))
        self.assertIn("### 待验证假设", section(text, "技术趋势"))
        collection = section(text, "采集概况")
        self.assertIn("文档总数：0", collection)
        self.assertIn("信息源总数：0", collection)
        self.assertIn("已记录轮次：0", collection)
        # No state file exists; the report must not read that as round 0.
        self.assertNotIn("第 0 轮", text)
        history = section(text, "历史资料")
        self.assertIn("窗口开放", history)


class TestHistoricalDocuments(TestExporterBase):
    def test_classifies_by_window_start_without_inventing_dates(self):
        root = new_project(
            self.tmp, time_window={"start": "2025-01-01", "end": None})
        save_documents(root, [
            make_document("doc_old", title="旧资料标题",
                          published_at="2024-06-01"),
            make_document("doc_new", title="新资料标题",
                          published_at="2025-06-01"),
            make_document("doc_unknown", title="无日期标题"),
            make_document("doc_bad", title="坏日期标题",
                          published_at="not-a-date"),
        ])
        text = export_markdown_report(root).read_text(encoding="utf-8")
        history = section(text, "历史资料")
        self.assertIn("旧资料标题", history)
        self.assertNotIn("新资料标题", history)
        self.assertNotIn("无日期标题", history)
        self.assertNotIn("坏日期标题", history)
        self.assertIn("2 篇", history)
        self.assertIn("未作推断", history)

    def test_open_window_lists_nothing(self):
        root = new_project(
            self.tmp, time_window={"start": None, "end": None})
        save_documents(root, [
            make_document("doc_old", title="旧资料标题",
                          published_at="2020-01-01"),
        ])
        text = export_markdown_report(root).read_text(encoding="utf-8")
        history = section(text, "历史资料")
        self.assertIn("窗口开放", history)
        self.assertNotIn("旧资料标题", history)


class TestDeterminism(TestExporterBase):
    def test_two_exports_are_identical(self):
        root = new_project(self.tmp)
        save_documents(root, [make_document("doc_1", title="标题")])
        save_findings(root, [
            make_finding("find_1", "结论",
                         [{"document_id": "doc_1",
                           "evidence_type": "supports"}]),
        ])
        first = export_markdown_report(root).read_text(encoding="utf-8")
        second = export_markdown_report(root).read_text(encoding="utf-8")
        self.assertEqual(first, second)


class TestMissingReportsDirectory(TestExporterBase):
    def test_raises_naming_the_root(self):
        root = ensure_research_layout(
            self.tmp / "security" / "ai-agent-security")
        (root / "reports").rmdir()
        with self.assertRaises(FileNotFoundError) as ctx:
            export_markdown_report(root)
        self.assertIn(str(root), str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
