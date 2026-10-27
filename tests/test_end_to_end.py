import tempfile
import unittest
from pathlib import Path
from scripts.planner import init_research
from scripts.source_discovery import discover
from scripts.compliance_gate import check_compliance
from scripts.archive import save_documents, rebuild_manifest, export_csv
from scripts.lib.validate import validate_record

FEED = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Agent Tool Security</title><link>https://e.org/a1</link>
<pubDate>Mon, 20 Oct 2026 00:00:00 GMT</pubDate>
<description>调用前授权</description></item>
</channel></rss>"""


class TestEndToEnd(unittest.TestCase):
    def test_discovery_to_archive(self):
        tmp = Path(tempfile.mkdtemp())
        init_research(tmp, domain="security", topic="ai-agent-security",
                      objective="识别趋势", keywords=["Agent Security"],
                      time_window={"recent_days": 30})

        sources = discover("Agent Security", provider="manual",
                           payload=["https://e.org"])
        self.assertTrue(sources[0]["discovery_verified"])

        def fetcher(url, **kw):
            if url.endswith("/robots.txt"):
                return {"outcome": "ok", "status": 200, "text": ""}
            return {"outcome": "ok", "status": 200, "text": FEED}

        record = check_compliance(sources[0], fetcher=fetcher)
        self.assertEqual(record["compliance_status"], "ALLOWED")

        root = tmp / "security" / "ai-agent-security"
        save_documents(root, [{
            "document_id": "doc_1", "source_id": sources[0]["source_id"],
            "canonical_url": "https://e.org/a1", "title": "Agent Tool Security",
            "crawl_status": "PERSISTED", "retention_mode": "reference", "attempt_count": 0,
        }])
        validate_record("document", {
            "document_id": "doc_1", "source_id": sources[0]["source_id"],
            "canonical_url": "https://e.org/a1", "crawl_status": "PERSISTED",
            "retention_mode": "reference", "attempt_count": 0,
        })
        manifest = rebuild_manifest(root)
        self.assertEqual(manifest["document_count"], 1)
        self.assertTrue(export_csv(root).exists())

    def test_blocked_source_never_reaches_archive(self):
        tmp = Path(tempfile.mkdtemp())
        init_research(tmp, domain="security", topic="blocked-topic",
                      objective="o", keywords=["k"], time_window={"recent_days": 30})

        def fetcher(url, **kw):
            if url.endswith("/robots.txt"):
                return {"outcome": "ok", "status": 200, "text": "User-agent: *\nDisallow: /\n"}
            return {"outcome": "ok", "status": 200}

        record = check_compliance(
            {"source_id": "src_1", "base_url": "https://e.org", "access_method": "web"},
            fetcher=fetcher,
        )
        self.assertEqual(record["compliance_status"], "BLOCKED")

        from scripts.collector import assert_compliance_allows
        with self.assertRaises(PermissionError):
            assert_compliance_allows(record)
