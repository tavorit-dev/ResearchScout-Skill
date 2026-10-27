# tests/test_archive.py
import csv
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from jsonschema import ValidationError

from scripts.archive import (save_state, load_state, save_documents, save_findings,
                             save_iteration, save_summary, rebuild_manifest, export_csv)
from scripts.lib.jsonl import append_record, read_records
from scripts.lib.paths import ensure_research_layout
from scripts.planner import init_research


DOCUMENT = {"document_id": "doc_1", "canonical_url": "https://e.org/a",
            "source_id": "src_1", "crawl_status": "PERSISTED",
            "retention_mode": "reference", "attempt_count": 0}

FINDING = {"finding_id": "find_1", "statement": "s",
           "confidence": 0.5, "importance": 0.5,
           "evidence": [{"document_id": "doc_1",
                         "evidence_type": "supports"}]}

CSV_FIELDS = ["document_id", "source_name", "title", "published_at",
              "crawled_at", "summary", "relevance_score", "canonical_url"]


class TestArchive(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = ensure_research_layout(self.tmp / "security" / "ai-agent-security")

    def test_state_roundtrips(self):
        save_state(self.root, {"round": 3, "status": "RUNNING"})
        self.assertEqual(load_state(self.root)["round"], 3)

    def test_documents_upsert(self):
        save_documents(self.root, [{"document_id": "doc_1", "canonical_url": "https://e.org/a",
                                    "source_id": "src_1", "crawl_status": "PERSISTED",
                                    "retention_mode": "reference", "attempt_count": 0}])
        save_documents(self.root, [{"document_id": "doc_1", "canonical_url": "https://e.org/a",
                                    "source_id": "src_1", "crawl_status": "PERSISTED",
                                    "retention_mode": "reference", "attempt_count": 1}])
        from scripts.lib.jsonl import read_records
        records = read_records(self.root / "documents" / "documents.jsonl")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["attempt_count"], 1)

    def test_summary_writes_snapshot(self):
        save_summary(self.root, "# 摘要\n内容")
        self.assertTrue((self.root / "summaries" / "current.md").exists())
        self.assertTrue(list((self.root / "summaries" / "snapshots").glob("*.md")))

    def test_iteration_written_per_round(self):
        save_iteration(self.root, 1, {"decision": "CONTINUE"})
        self.assertTrue((self.root / "iterations" / "round-001.json").exists())

    def test_manifest_counts(self):
        save_documents(self.root, [{"document_id": "doc_1", "canonical_url": "https://e.org/a",
                                    "source_id": "src_1", "crawl_status": "PERSISTED",
                                    "retention_mode": "reference", "attempt_count": 0}])
        save_findings(self.root, [{"finding_id": "find_1", "statement": "s",
                                   "confidence": 0.5, "importance": 0.5,
                                   "evidence": [{"document_id": "doc_1",
                                                 "evidence_type": "supports"}]}])
        manifest = rebuild_manifest(self.root)
        self.assertEqual(manifest["document_count"], 1)
        self.assertEqual(manifest["finding_count"], 1)

    def test_export_csv(self):
        save_documents(self.root, [{"document_id": "doc_1", "canonical_url": "https://e.org/a",
                                    "source_id": "src_1", "crawl_status": "PERSISTED",
                                    "retention_mode": "reference", "attempt_count": 0,
                                    "title": "标题"}])
        path = export_csv(self.root)
        self.assertTrue(path.exists())
        self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))


class TestValidationBeforeWrite(unittest.TestCase):
    """Ruling 1: every record is validated before the first one reaches disk."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = ensure_research_layout(self.tmp / "security" / "ai-agent-security")
        self.documents = self.root / "documents" / "documents.jsonl"
        self.findings = self.root / "findings" / "findings.jsonl"

    def test_invalid_document_leaves_existing_file_untouched(self):
        save_documents(self.root, [dict(DOCUMENT)])
        before = self.documents.read_bytes()
        changed = dict(DOCUMENT, attempt_count=7)
        broken = dict(DOCUMENT, document_id="doc_2", crawl_status="NOT_A_STATUS")
        with self.assertRaises(ValidationError):
            save_documents(self.root, [changed, broken])
        self.assertEqual(self.documents.read_bytes(), before)

    def test_invalid_document_creates_no_file(self):
        with self.assertRaises(ValidationError):
            save_documents(self.root, [dict(DOCUMENT, retention_mode="whatever")])
        self.assertFalse(self.documents.exists())

    def test_finding_with_scoring_fields_is_rejected(self):
        # Task 14's compute_weight reads four score fields that finding.schema.json
        # does not allow; Task 16 must strip them before calling save_findings.
        scored = dict(FINDING, relevance_score=0.8)
        with self.assertRaises(ValidationError):
            save_findings(self.root, [scored])
        self.assertFalse(self.findings.exists())


class TestLoadState(unittest.TestCase):
    """Ruling 2: a missing or corrupt state file is an error naming its path."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = ensure_research_layout(self.tmp / "security" / "ai-agent-security")
        self.path = self.root / "state" / "current.json"

    def test_missing_state_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError) as ctx:
            load_state(self.root)
        self.assertIn(str(self.path), str(ctx.exception))

    def test_corrupt_state_raises_value_error(self):
        for payload in ('{"round": 1', "[]", "null"):
            with self.subTest(payload=payload):
                self.path.write_text(payload, encoding="utf-8")
                with self.assertRaises(ValueError) as ctx:
                    load_state(self.root)
                self.assertIn(str(self.path), str(ctx.exception))


class TestFindingsReplace(unittest.TestCase):
    """Ruling 3: save_findings replaces the file; it never accumulates."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = ensure_research_layout(self.tmp / "security" / "ai-agent-security")

    def test_findings_replaced_not_appended(self):
        save_findings(self.root, [dict(FINDING), dict(FINDING, finding_id="find_2")])
        save_findings(self.root, [dict(FINDING)])
        records = read_records(self.root / "findings" / "findings.jsonl")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["finding_id"], "find_1")


class TestDocumentsAndCsv(unittest.TestCase):
    """Rulings 1 and 5: document upsert counts and the Excel-safe CSV export."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = ensure_research_layout(self.tmp / "security" / "ai-agent-security")

    def test_empty_document_list_returns_zero_and_creates_no_file(self):
        self.assertEqual(save_documents(self.root, []), 0)
        self.assertFalse((self.root / "documents" / "documents.jsonl").exists())

    def test_save_documents_returns_record_count(self):
        self.assertEqual(save_documents(self.root, [dict(DOCUMENT)]), 1)
        self.assertEqual(
            save_documents(self.root, [dict(DOCUMENT, attempt_count=1),
                                       dict(DOCUMENT, document_id="doc_2")]),
            2,
        )

    def test_empty_export_is_header_only(self):
        path = export_csv(self.root)
        self.assertEqual(path, self.root / "documents" / "documents.csv")
        with path.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(rows, [CSV_FIELDS])
        self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_export_header_order_and_values(self):
        save_documents(self.root, [dict(DOCUMENT, title="标题")])
        path = export_csv(self.root)
        with path.open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            self.assertEqual(reader.fieldnames, CSV_FIELDS)
            rows = list(reader)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["document_id"], "doc_1")
        self.assertEqual(rows[0]["title"], "标题")
        self.assertEqual(rows[0]["summary"], "")


class TestManifestCounts(unittest.TestCase):
    """Ruling 6: the manifest reports what is on disk, inventing nothing."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = ensure_research_layout(self.tmp / "security" / "ai-agent-security")

    def test_missing_files_count_zero_and_null_research_id(self):
        manifest = rebuild_manifest(self.root)
        self.assertEqual(manifest["document_count"], 0)
        self.assertEqual(manifest["finding_count"], 0)
        self.assertEqual(manifest["source_count"], 0)
        self.assertEqual(manifest["round_count"], 0)
        self.assertIsNone(manifest["research_id"])
        datetime.fromisoformat(manifest["generated_at"])
        written = json.loads(
            (self.root / "index" / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(written, manifest)

    def test_source_and_round_counts(self):
        sources = self.root / "sources" / "selected_sources.jsonl"
        append_record(sources, {"source_id": "src_1"})
        append_record(sources, {"source_id": "src_2"})
        save_iteration(self.root, 1, {"decision": "CONTINUE"})
        save_iteration(self.root, 2, {"decision": "DEEPEN"})
        manifest = rebuild_manifest(self.root)
        self.assertEqual(manifest["source_count"], 2)
        self.assertEqual(manifest["round_count"], 2)

    def test_research_id_read_from_research_yaml(self):
        record = init_research(
            self.tmp, domain="security", topic="ai-agent-security",
            objective="o", keywords=["k"], time_window={"recent_days": 30},
        )
        self.assertEqual(rebuild_manifest(self.root)["research_id"], record["research_id"])

    def test_corrupt_research_yaml_raises_with_path(self):
        # Present-but-broken is not absent: reporting null would hide it.
        path = self.root / "research.yaml"
        path.write_text("research_id: [unclosed", encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            rebuild_manifest(self.root)
        self.assertIn(str(path), str(ctx.exception))


class TestSummaryAndIteration(unittest.TestCase):
    """Ruling 4 helpers: snapshot names stay unique and round names zero-pad."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = ensure_research_layout(self.tmp / "security" / "ai-agent-security")

    def test_two_summaries_produce_two_snapshots(self):
        save_summary(self.root, "one")
        save_summary(self.root, "two")
        snapshots = sorted((self.root / "summaries" / "snapshots").glob("*.md"))
        self.assertEqual(len(snapshots), 2)
        current = (self.root / "summaries" / "current.md").read_text(encoding="utf-8")
        self.assertEqual(current, "two")

    def test_iteration_names_are_zero_padded(self):
        save_iteration(self.root, 1, {"decision": "CONTINUE"})
        save_iteration(self.root, 12, {"decision": "STOP_COMPLETE"})
        self.assertTrue((self.root / "iterations" / "round-001.json").exists())
        payload = json.loads(
            (self.root / "iterations" / "round-012.json").read_text(encoding="utf-8")
        )
        self.assertEqual(payload["decision"], "STOP_COMPLETE")

    def test_save_state_leaves_no_temporary_files(self):
        save_state(self.root, {"round": 1})
        save_state(self.root, {"round": 2})
        self.assertEqual(
            sorted(path.name for path in (self.root / "state").iterdir()),
            ["current.json"],
        )
        self.assertEqual(load_state(self.root), {"round": 2})

    def test_iteration_requires_non_negative_integer_round(self):
        for bad in (-1, True, "1"):
            with self.subTest(round_no=bad):
                with self.assertRaises(ValueError):
                    save_iteration(self.root, bad, {"decision": "CONTINUE"})
        self.assertEqual(list((self.root / "iterations").iterdir()), [])


class TestWritersEnforceRecordShape(unittest.TestCase):
    """I1: a writer refuses a payload its own reader cannot read back."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = ensure_research_layout(self.tmp / "security" / "ai-agent-security")

    def test_save_state_rejects_non_mapping_and_keeps_previous_state(self):
        save_state(self.root, {"round": 3})
        state_path = self.root / "state" / "current.json"
        before = state_path.read_bytes()
        with self.assertRaises(TypeError) as ctx:
            save_state(self.root, [1, 2])
        self.assertIn("list", str(ctx.exception))
        self.assertEqual(state_path.read_bytes(), before)
        self.assertEqual(load_state(self.root), {"round": 3})

    def test_save_iteration_rejects_non_mapping_record(self):
        with self.assertRaises(TypeError) as ctx:
            save_iteration(self.root, 0, "scalar")
        self.assertIn("str", str(ctx.exception))
        self.assertEqual(list((self.root / "iterations").iterdir()), [])


class TestAtomicWriters(unittest.TestCase):
    """I2: a failed os.replace keeps the old bytes and leaves no temp file."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = ensure_research_layout(self.tmp / "security" / "ai-agent-security")

    def test_save_state_keeps_old_state_when_replace_fails(self):
        save_state(self.root, {"round": 1})
        state_path = self.root / "state" / "current.json"
        before = state_path.read_bytes()
        with mock.patch("scripts.archive.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                save_state(self.root, {"round": 2})
        self.assertEqual(state_path.read_bytes(), before)
        self.assertEqual(list((self.root / "state").glob(".current.json.*")), [])
        self.assertEqual(load_state(self.root), {"round": 1})

    def test_save_findings_keeps_old_file_when_replace_fails(self):
        save_findings(self.root, [dict(FINDING)])
        findings_path = self.root / "findings" / "findings.jsonl"
        before = findings_path.read_bytes()
        with mock.patch("scripts.archive.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                save_findings(
                    self.root,
                    [dict(FINDING), dict(FINDING, finding_id="find_2")],
                )
        self.assertEqual(findings_path.read_bytes(), before)
        self.assertEqual(
            list((self.root / "findings").glob(".findings.jsonl.*")), []
        )


class TestWritersRefuseToInventProject(unittest.TestCase):
    """I3: a writer never creates the project it was asked to write into."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_writers_require_an_initialised_project(self):
        cases = {
            "save_state": lambda root: save_state(root, {"round": 0}),
            "save_documents": lambda root: save_documents(root, [dict(DOCUMENT)]),
            "save_documents_empty": lambda root: save_documents(root, []),
            "save_findings": lambda root: save_findings(root, [dict(FINDING)]),
            "save_iteration": lambda root: save_iteration(
                root, 1, {"decision": "CONTINUE"}
            ),
            "save_summary": lambda root: save_summary(root, "text"),
            "export_csv": lambda root: export_csv(root),
        }
        for name, call in cases.items():
            with self.subTest(writer=name):
                root = self.tmp / name
                with self.assertRaises(FileNotFoundError) as ctx:
                    call(root)
                self.assertIn(str(root), str(ctx.exception))
                self.assertFalse(root.exists())

    def test_rebuild_manifest_writes_nothing_at_a_missing_root(self):
        root = self.tmp / "manifest"
        with self.assertRaises(FileNotFoundError) as ctx:
            rebuild_manifest(root)
        message = str(ctx.exception)
        self.assertIn(str(root), message)
        self.assertIn("init_research", message)
        self.assertFalse(root.exists())

    def test_atomic_writer_never_creates_directories(self):
        from scripts.archive import _atomic_write_text
        target = self.tmp / "missing" / "deeper" / "file.txt"
        with self.assertRaises(FileNotFoundError):
            _atomic_write_text(target, "x")
        self.assertFalse((self.tmp / "missing").exists())


class TestTemporaryFileHygiene(unittest.TestCase):
    """M1/M2: a crashed write leaves nothing behind and leaks no descriptor."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = ensure_research_layout(self.tmp / "security" / "ai-agent-security")

    def test_stale_temporary_file_is_swept_before_the_next_write(self):
        stale = self.root / "state" / ".current.json.CRASHED.tmp"
        stale.write_text("garbage", encoding="utf-8")
        save_state(self.root, {"round": 1})
        self.assertFalse(stale.exists())
        self.assertEqual(load_state(self.root), {"round": 1})

    def test_stale_sweep_removes_only_its_own_temporary_files(self):
        # Pin the radius, not just the cleanup: a file that merely shares the
        # prefix is somebody else's (a manual backup, say) and must survive.
        state_dir = self.root / "state"
        stale = state_dir / ".current.json.CRASHED.tmp"
        backup = state_dir / ".current.json.backup"
        stale.write_text("garbage", encoding="utf-8")
        backup.write_text("keep me", encoding="utf-8")
        save_state(self.root, {"round": 1})
        self.assertFalse(stale.exists())
        self.assertTrue(backup.exists())
        self.assertEqual(backup.read_text(encoding="utf-8"), "keep me")

    def test_fdopen_failure_closes_descriptor_and_removes_temp_file(self):
        captured = []

        def failing_fdopen(descriptor, *args, **kwargs):
            captured.append(descriptor)
            raise RuntimeError("fdopen failed")

        with mock.patch("scripts.archive.os.fdopen", failing_fdopen):
            with self.assertRaises(RuntimeError):
                save_state(self.root, {"round": 1})
        self.assertEqual(len(captured), 1)
        with self.assertRaises(OSError):
            os.fstat(captured[0])
        self.assertEqual(list((self.root / "state").glob(".current.json.*")), [])


class TestErrorMessagesNameTheRecord(unittest.TestCase):
    """M3/M4: a failure says which record and which file, not just "invalid"."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = ensure_research_layout(self.tmp / "security" / "ai-agent-security")

    def test_invalid_document_in_a_batch_names_index_and_id(self):
        batch = [
            dict(DOCUMENT, document_id="doc_first"),
            dict(DOCUMENT, document_id="doc_second"),
            dict(DOCUMENT, document_id="doc_third", crawl_status="NOT_A_STATUS"),
        ]
        with self.assertRaises(ValidationError) as ctx:
            save_documents(self.root, batch)
        message = str(ctx.exception)
        self.assertIn("record 2", message)
        self.assertIn("doc_third", message)
        self.assertIsInstance(ctx.exception.__cause__, ValidationError)

    def test_invalid_finding_in_a_batch_names_index_and_id(self):
        batch = [
            dict(FINDING, finding_id="find_first"),
            dict(FINDING, finding_id="find_second", relevance_score=0.9),
        ]
        with self.assertRaises(ValidationError) as ctx:
            save_findings(self.root, batch)
        message = str(ctx.exception)
        self.assertIn("record 1", message)
        self.assertIn("find_second", message)
        self.assertIsInstance(ctx.exception.__cause__, ValidationError)

    def test_non_utf8_state_file_names_its_path(self):
        path = self.root / "state" / "current.json"
        path.write_bytes(b"\xff\xfe\x00not utf-8")
        with self.assertRaises(ValueError) as ctx:
            load_state(self.root)
        self.assertIn(str(path), str(ctx.exception))


class TestRoundCounting(unittest.TestCase):
    """M5: only files are rounds; a directory with the right name is not."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = ensure_research_layout(self.tmp / "security" / "ai-agent-security")

    def test_directory_named_like_a_round_is_not_counted(self):
        (self.root / "iterations" / "round-999.json").mkdir()
        self.assertEqual(rebuild_manifest(self.root)["round_count"], 0)


if __name__ == "__main__":
    unittest.main()
