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

    def test_default_port_is_dropped_so_one_site_is_one_source(self):
        # The base URL is the identity unit, so a default port spelled out must
        # not mint a second source: two sources here would mean two ids, two
        # robots fetches and two pacing keys for one site.
        result = manual_provider(["https://e.org:443/a", "https://e.org/b"])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["base_url"], "https://e.org")

    def test_non_default_port_is_preserved(self):
        # The other half of the rule: a service on a non-default port is a
        # different service and must stay a separate source.
        result = manual_provider(["https://e.org:8080/a", "https://e.org/b"])
        self.assertEqual(len(result), 2)
        self.assertEqual(
            sorted(src["base_url"] for src in result),
            ["https://e.org", "https://e.org:8080"],
        )

    def test_malformed_port_is_skipped_not_raised(self):
        # A port that cannot be read is malformed input, the same bucket as a
        # missing host: the candidate is skipped, not raised on.
        self.assertEqual(manual_provider(["https://e.org:notaport/x"]), [])
        result = manual_provider(["https://e.org:notaport/x", "https://e.org/ok"])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["base_url"], "https://e.org")


class TestLlmProvider(unittest.TestCase):
    def test_marks_unverified(self):
        result = llm_provider([{"source_name": "arXiv", "base_url": "https://arxiv.org"}])
        self.assertFalse(result[0]["discovery_verified"])
        self.assertEqual(result[0]["discovery_method"], "llm")

    def test_caller_cannot_force_verification(self):
        # The A4 guarantee: a candidate that claims to be verified is still
        # marked unverified and attributed to the model, because how the source
        # was obtained is not the caller's to assert.
        result = llm_provider(
            [{"base_url": "https://x.org", "discovery_verified": True, "discovery_method": "manual"}]
        )
        self.assertFalse(result[0]["discovery_verified"])
        self.assertEqual(result[0]["discovery_method"], "llm")

    def test_malformed_port_is_skipped_not_raised(self):
        # This is the path whose input is unverified model output, so a bad
        # port must not become an unhandled exception.
        self.assertEqual(llm_provider([{"base_url": "https://e.org:notaport/x"}]), [])
        result = llm_provider([{"base_url": "https://e.org:notaport/x"}, {"base_url": "https://e.org/ok"}])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["base_url"], "https://e.org")


class TestLimit(unittest.TestCase):
    def test_limit_is_honoured_by_the_providers(self):
        urls = [f"https://e{i}.org" for i in range(5)]
        self.assertEqual(len(manual_provider(urls, limit=2)), 2)
        self.assertEqual(len(llm_provider([{"base_url": url} for url in urls], limit=2)), 2)

    def test_limit_applies_after_deduplication(self):
        # A caller asking for 2 distinct sources must get 2, not 2 raw entries
        # that collapse to fewer.
        urls = ["https://e.org/a", "https://e.org/b", "https://f.org/a"]
        self.assertEqual(len(manual_provider(urls, limit=2)), 2)

    def test_none_limit_uses_the_default_cap(self):
        # None is the plausible accidental value in this codebase, and quietly
        # removing the cap is the wrong default here.
        urls = [f"https://e{i}.org" for i in range(30)]
        self.assertEqual(len(manual_provider(urls, limit=None)), 20)

    def test_non_positive_limit_yields_nothing(self):
        self.assertEqual(manual_provider(["https://e.org"], limit=0), [])
        self.assertEqual(manual_provider(["https://e.org"], limit=-1), [])


class TestDiscover(unittest.TestCase):
    def test_unknown_provider_raises(self):
        with self.assertRaises(ValueError):
            discover("q", provider="nope")

    def test_discover_routes_to_manual(self):
        result = discover("q", provider="manual", payload=["https://e.org"])
        self.assertEqual(len(result), 1)
