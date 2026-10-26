# tests/test_collector.py
import unittest
from pathlib import Path
from scripts.collector import parse_feed, collect, assert_compliance_allows

FIXTURE = Path(__file__).parent / "fixtures" / "sample_feed.xml"


class TestParseFeed(unittest.TestCase):
    def test_parses_rss_items(self):
        items = parse_feed(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "Agent Sandbox Design")
        self.assertEqual(items[0]["link"], "https://e.org/a1")
        self.assertEqual(items[0]["published_at"], "2026-10-20T00:00:00Z")

    def test_missing_date_is_none_not_guessed(self):
        items = parse_feed(FIXTURE.read_text(encoding="utf-8"))
        self.assertIsNone(items[1]["published_at"])


class TestComplianceEnforcement(unittest.TestCase):
    def test_blocked_source_cannot_be_collected(self):
        with self.assertRaises(PermissionError):
            assert_compliance_allows(
                {"source_id": "src_1", "compliance_status": "BLOCKED"}
            )

    def test_review_required_cannot_be_collected(self):
        with self.assertRaises(PermissionError):
            assert_compliance_allows(
                {"source_id": "src_1", "compliance_status": "REVIEW_REQUIRED"}
            )

    def test_error_cannot_be_collected(self):
        with self.assertRaises(PermissionError):
            assert_compliance_allows(
                {"source_id": "src_1", "compliance_status": "ERROR"}
            )

    def test_allowed_passes(self):
        assert_compliance_allows({"source_id": "src_1", "compliance_status": "ALLOWED"})


class TestCollect(unittest.TestCase):
    def test_collect_records_compliance_ref(self):
        def fetcher(url, **kwargs):
            return {"outcome": "ok", "status": 200, "text": FIXTURE.read_text(encoding="utf-8")}

        docs = collect(
            {"source_id": "src_1", "base_url": "https://e.org", "access_method": "rss",
             "feed_url": "https://e.org/feed", "compliance_status": "ALLOWED"},
            fetcher=fetcher,
        )
        self.assertEqual(len(docs), 2)
        self.assertEqual(docs[0]["compliance_record_id"], "src_1")
        self.assertEqual(docs[0]["crawl_status"], "FETCHED")


if __name__ == "__main__":
    unittest.main()
