# tests/test_source_discovery.py
import unittest
from scripts.source_discovery import discover, manual_provider, llm_provider


class TestManualProvider(unittest.TestCase):
    def test_parses_domain_and_name(self):
        result = manual_provider(["https://blog.example.org/feed"])
        self.assertEqual(len(result), 1)
        src = result[0]
        self.assertEqual(src["base_url"], "https://blog.example.org")
        self.assertEqual(src["source_name"], "blog.example.org")
        self.assertEqual(src["discovery_method"], "manual")
        self.assertTrue(src["discovery_verified"])
        self.assertEqual(src["compliance_status"], "DISCOVERED")

    def test_deduplicates_urls(self):
        result = manual_provider(["https://e.org/a", "https://e.org/b"])
        self.assertEqual(len(result), 1)


class TestLlmProvider(unittest.TestCase):
    def test_marks_unverified(self):
        result = llm_provider([{"source_name": "arXiv", "base_url": "https://arxiv.org"}])
        self.assertFalse(result[0]["discovery_verified"])
        self.assertEqual(result[0]["discovery_method"], "llm")


class TestDiscover(unittest.TestCase):
    def test_unknown_provider_raises(self):
        with self.assertRaises(ValueError):
            discover("q", provider="nope")

    def test_discover_routes_to_manual(self):
        result = discover("q", provider="manual", payload=["https://e.org"])
        self.assertEqual(len(result), 1)
