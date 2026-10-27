# tests/test_jsonl.py
import csv
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from scripts.lib.jsonl import append_record, read_records, upsert_by_id, write_csv


class TestJsonl(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "docs.jsonl"

    def test_read_missing_file_returns_empty(self):
        self.assertEqual(read_records(self.path), [])

    def test_append_and_read(self):
        append_record(self.path, {"document_id": "doc_1", "title": "A"})
        append_record(self.path, {"document_id": "doc_2", "title": "B"})
        self.assertEqual(len(read_records(self.path)), 2)

    def test_upsert_inserts_then_updates(self):
        self.assertEqual(
            upsert_by_id(self.path, {"document_id": "doc_1", "title": "A"}, "document_id"),
            "inserted",
        )
        self.assertEqual(
            upsert_by_id(self.path, {"document_id": "doc_1", "title": "A2"}, "document_id"),
            "updated",
        )
        records = read_records(self.path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["title"], "A2")

    def test_upsert_keeps_the_old_file_when_the_replace_fails(self):
        upsert_by_id(self.path, {"document_id": "doc_1", "title": "A"}, "document_id")
        before = self.path.read_bytes()

        with mock.patch("scripts.lib.jsonl.os.replace",
                        side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                upsert_by_id(
                    self.path, {"document_id": "doc_1", "title": "B"},
                    "document_id",
                )

        self.assertEqual(self.path.read_bytes(), before)
        # The temp file is same-directory and dot-prefixed; a failed replace
        # must not leave it behind.
        self.assertEqual([entry.name for entry in self.tmp.iterdir()],
                         [self.path.name])

    def test_write_csv_is_excel_safe(self):
        out = self.tmp / "docs.csv"
        write_csv(out, [{"document_id": "doc_1", "title": "中文标题"}], ["document_id", "title"])
        raw = out.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        with out.open(encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(rows[0]["title"], "中文标题")
