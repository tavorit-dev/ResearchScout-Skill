# tests/test_compliance_gate.py
import unittest
from scripts.compliance_gate import check_reachability, check_compliance


def fake_fetcher(responses):
    def _fetch(url, **kwargs):
        return responses.get(url, {"outcome": "not_found", "status": 404})
    return _fetch


class TestReachability(unittest.TestCase):
    def test_unreachable_marks_source(self):
        src = {"source_id": "src_1", "base_url": "https://gone.example"}
        result = check_reachability(src, fetcher=fake_fetcher({}))
        self.assertEqual(result, "UNREACHABLE")

    def test_reachable(self):
        src = {"source_id": "src_1", "base_url": "https://e.org"}
        fetcher = fake_fetcher({"https://e.org": {"outcome": "ok", "status": 200}})
        self.assertEqual(check_reachability(src, fetcher=fetcher), "REACHABLE")

    def test_timeout_is_unreachable(self):
        # A network failure answers "the address did not answer", which is all
        # the reachability check is allowed to decide (spec 8.4).
        src = {"source_id": "src_1", "base_url": "https://e.org"}
        fetcher = fake_fetcher({"https://e.org": {"outcome": "timeout"}})
        self.assertEqual(check_reachability(src, fetcher=fetcher), "UNREACHABLE")

    def test_refused_request_is_unreachable(self):
        src = {"source_id": "src_1", "base_url": "https://e.org"}
        fetcher = fake_fetcher({"https://e.org": {"outcome": "blocked", "status": 403}})
        self.assertEqual(check_reachability(src, fetcher=fetcher), "UNREACHABLE")

    def test_oversized_body_is_unreachable(self):
        src = {"source_id": "src_1", "base_url": "https://e.org"}
        fetcher = fake_fetcher({"https://e.org": {"outcome": "too_large"}})
        self.assertEqual(check_reachability(src, fetcher=fetcher), "UNREACHABLE")

    def test_server_error_is_unreachable(self):
        src = {"source_id": "src_1", "base_url": "https://e.org"}
        fetcher = fake_fetcher({"https://e.org": {"outcome": "server_error", "status": 503}})
        self.assertEqual(check_reachability(src, fetcher=fetcher), "UNREACHABLE")


class TestCompliance(unittest.TestCase):
    def test_robots_server_error_yields_error_status(self):
        fetcher = fake_fetcher({
            "https://e.org": {"outcome": "ok", "status": 200},
            "https://e.org/robots.txt": {"outcome": "server_error", "status": 503},
        })
        record = check_compliance(
            {"source_id": "src_1", "base_url": "https://e.org", "access_method": "web"},
            fetcher=fetcher,
        )
        self.assertEqual(record["compliance_status"], "ERROR")

    def test_robots_blocks_path(self):
        fetcher = fake_fetcher({
            "https://e.org": {"outcome": "ok", "status": 200},
            "https://e.org/robots.txt": {
                "outcome": "ok", "status": 200, "text": "User-agent: *\nDisallow: /\n",
            },
        })
        record = check_compliance(
            {"source_id": "src_1", "base_url": "https://e.org", "access_method": "web"},
            fetcher=fetcher,
        )
        self.assertEqual(record["compliance_status"], "BLOCKED")

    def test_record_carries_evidence(self):
        fetcher = fake_fetcher({
            "https://e.org": {"outcome": "ok", "status": 200},
            "https://e.org/robots.txt": {"outcome": "ok", "status": 200, "text": ""},
        })
        record = check_compliance(
            {"source_id": "src_1", "base_url": "https://e.org", "access_method": "web"},
            fetcher=fetcher,
        )
        self.assertTrue(record["evidence"])
        self.assertEqual(record["evidence"][0]["type"], "robots_txt")

    def test_robots_403_is_blocked_not_allowed(self):
        # Pins the mapping the robots module cannot cover on its own: only the
        # fetch layer produces the "blocked" outcome, and a refused robots.txt
        # request must not be read as permission to collect (spec 6.4).
        fetcher = fake_fetcher({
            "https://e.org": {"outcome": "ok", "status": 200},
            "https://e.org/robots.txt": {"outcome": "blocked", "status": 403},
        })
        record = check_compliance(
            {"source_id": "src_1", "base_url": "https://e.org", "access_method": "web"},
            fetcher=fetcher,
        )
        self.assertEqual(record["compliance_status"], "BLOCKED")

    def test_oversized_robots_is_error_not_allowed(self):
        # Same reason as above: rules that could not be read are unknown, and
        # unknown rules suspend the source (spec 6.4).
        fetcher = fake_fetcher({
            "https://e.org": {"outcome": "ok", "status": 200},
            "https://e.org/robots.txt": {"outcome": "too_large"},
        })
        record = check_compliance(
            {"source_id": "src_1", "base_url": "https://e.org", "access_method": "web"},
            fetcher=fetcher,
        )
        self.assertEqual(record["compliance_status"], "ERROR")

    def test_unknown_robots_status_fails_closed(self):
        # evaluate_robots fails closed on an outcome it does not recognise; the
        # gate must preserve that and never turn it into ALLOWED.
        def fetcher(url, **kwargs):
            if url.endswith("/robots.txt"):
                return {"outcome": "some_future_outcome", "status": 200}
            return {"outcome": "ok", "status": 200}

        record = check_compliance(
            {"source_id": "src_1", "base_url": "https://e.org", "access_method": "web"},
            fetcher=fetcher,
        )
        self.assertEqual(record["compliance_status"], "ERROR")

    def test_unreachable_source_does_not_reach_compliance(self):
        # Review Focus 4: an unreachable candidate is marked, not collected and
        # not reported as an error crash (spec 8.4).
        record = check_compliance(
            {"source_id": "src_1", "base_url": "https://gone.example", "access_method": "web"},
            fetcher=fake_fetcher({}),
        )
        self.assertEqual(record["compliance_status"], "UNREACHABLE")
        self.assertEqual(record["robots_status"], "not_checked")

    def test_absent_robots_is_allowed_and_recorded(self):
        fetcher = fake_fetcher({
            "https://e.org": {"outcome": "ok", "status": 200},
            "https://e.org/robots.txt": {"outcome": "not_found", "status": 404},
        })
        record = check_compliance(
            {"source_id": "src_1", "base_url": "https://e.org", "access_method": "web"},
            fetcher=fetcher,
        )
        self.assertEqual(record["compliance_status"], "ALLOWED")
        self.assertEqual(record["robots_status"], "absent")
        self.assertIn("https://e.org/robots.txt", record["evidence"][0]["url"])

    def test_terms_url_requires_review(self):
        fetcher = fake_fetcher({
            "https://e.org": {"outcome": "ok", "status": 200},
            "https://e.org/robots.txt": {"outcome": "ok", "status": 200, "text": ""},
        })
        record = check_compliance(
            {
                "source_id": "src_1",
                "base_url": "https://e.org",
                "access_method": "web",
                "terms_url": "https://e.org/terms",
            },
            fetcher=fetcher,
        )
        self.assertEqual(record["compliance_status"], "REVIEW_REQUIRED")
        self.assertEqual(record["terms_status"], "present")

    def test_crawl_delay_sets_request_rate_limit(self):
        fetcher = fake_fetcher({
            "https://e.org": {"outcome": "ok", "status": 200},
            "https://e.org/robots.txt": {
                "outcome": "ok", "status": 200, "text": "User-agent: *\nCrawl-delay: 5\n",
            },
        })
        record = check_compliance(
            {"source_id": "src_1", "base_url": "https://e.org", "access_method": "web"},
            fetcher=fetcher,
        )
        self.assertEqual(record["request_rate_limit"], 5.0)

    def test_negative_crawl_delay_never_reduces_the_rate_limit(self):
        # A hostile or broken Crawl-delay must not become a faster crawl. The
        # expected value is asserted exactly: a regression that let -5 through
        # the ">" guard would put -5 in the record and fail here.
        fetcher = fake_fetcher({
            "https://e.org": {"outcome": "ok", "status": 200},
            "https://e.org/robots.txt": {
                "outcome": "ok", "status": 200, "text": "User-agent: *\nCrawl-delay: -5\n",
            },
        })
        record = check_compliance(
            {"source_id": "src_1", "base_url": "https://e.org", "access_method": "web"},
            fetcher=fetcher,
        )
        self.assertEqual(record["request_rate_limit"], 2.0)

    def test_record_carries_spec_6_7_fields(self):
        fetcher = fake_fetcher({
            "https://e.org": {"outcome": "ok", "status": 200},
            "https://e.org/robots.txt": {"outcome": "ok", "status": 200, "text": ""},
        })
        record = check_compliance(
            {"source_id": "src_1", "base_url": "https://e.org", "access_method": "web"},
            fetcher=fetcher,
        )
        self.assertEqual(record["source_id"], "src_1")
        self.assertEqual(record["domain"], "e.org")
        self.assertTrue(record["checked_at"])
        self.assertEqual(record["access_method"], "web")
        self.assertEqual(record["robots_status"], "allowed")
        self.assertEqual(record["terms_status"], "absent")
        self.assertIsInstance(record["allowed_paths"], list)
        self.assertIsInstance(record["notes"], str)


class TestTargetUrlIsEvaluated(unittest.TestCase):
    """The gate evaluates the path that will actually be fetched (fix 4, NEW-7)."""

    def _fetcher(self, robots):
        return fake_fetcher({
            "https://e.org": {"outcome": "ok", "status": 200},
            "https://e.org/robots.txt": {
                "outcome": "ok", "status": 200, "text": robots,
            },
        })

    def _source(self, **overrides):
        record = {
            "source_id": "src_1",
            "base_url": "https://e.org",
            "access_method": "rss",
            "feed_url": "https://e.org/feed",
        }
        record.update(overrides)
        return record

    def test_robots_disallowing_the_feed_path_blocks_the_source(self):
        record = check_compliance(
            self._source(),
            fetcher=self._fetcher("User-agent: *\nDisallow: /feed\n"),
            target_url="https://e.org/feed",
        )
        self.assertEqual(record["compliance_status"], "BLOCKED")
        self.assertEqual(record["allowed_paths"], [])

    def test_without_target_url_the_base_path_is_still_evaluated(self):
        # Task 7's behaviour is unchanged when no target is supplied.
        record = check_compliance(
            self._source(),
            fetcher=self._fetcher("User-agent: *\nDisallow: /feed\n"),
        )
        self.assertEqual(record["compliance_status"], "ALLOWED")
        self.assertEqual(record["allowed_paths"], ["/"])

    def test_allowed_paths_records_the_evaluated_target_path(self):
        record = check_compliance(
            self._source(),
            fetcher=self._fetcher("User-agent: *\nAllow: /\n"),
            target_url="https://e.org/feed",
        )
        self.assertEqual(record["compliance_status"], "ALLOWED")
        self.assertEqual(record["allowed_paths"], ["/feed"])

    def test_an_absent_robots_record_is_for_the_target_path(self):
        fetcher = fake_fetcher({
            "https://e.org": {"outcome": "ok", "status": 200},
            "https://e.org/robots.txt": {"outcome": "not_found", "status": 404},
        })
        record = check_compliance(
            self._source(), fetcher=fetcher,
            target_url="https://e.org/feed",
        )
        self.assertEqual(record["compliance_status"], "ALLOWED")
        self.assertEqual(record["allowed_paths"], ["/feed"])


class RecordingFetcher:
    """A fetcher that records the delay each request was issued with."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append({"url": url, "delay": kwargs.get("delay")})
        return self.responses.get(url, {"outcome": "not_found", "status": 404})

    def delays_for(self, url):
        return [call["delay"] for call in self.calls if call["url"] == url]


class TestRequestPacing(unittest.TestCase):
    def test_crawl_delay_reaches_the_transport(self):
        # The point of the test is the delay handed to the fetcher, not the
        # recorded field: a Crawl-delay that is recorded but never applied
        # leaves the source crawled at the default rate. The reachability
        # request precedes robots.txt and so necessarily uses the configured
        # delay; what matters is that the interval for the source is then
        # re-established at 5s.
        fetcher = RecordingFetcher({
            "https://e.org": {"outcome": "ok", "status": 200},
            "https://e.org/robots.txt": {
                "outcome": "ok", "status": 200, "text": "User-agent: *\nCrawl-delay: 5\n",
            },
        })
        record = check_compliance(
            {"source_id": "src_1", "base_url": "https://e.org", "access_method": "web"},
            fetcher=fetcher,
        )
        self.assertEqual(record["request_rate_limit"], 5.0)
        self.assertEqual(fetcher.delays_for("https://e.org")[-1], 5.0)

    def test_no_crawl_delay_uses_the_configured_delay(self):
        fetcher = RecordingFetcher({
            "https://e.org": {"outcome": "ok", "status": 200},
            "https://e.org/robots.txt": {"outcome": "ok", "status": 200, "text": ""},
        })
        check_compliance(
            {"source_id": "src_1", "base_url": "https://e.org", "access_method": "web"},
            fetcher=fetcher,
        )
        self.assertEqual(fetcher.delays_for("https://e.org"), [2.0])
        self.assertEqual(fetcher.delays_for("https://e.org/robots.txt"), [2.0])

    def test_crawl_delay_never_lowers_the_configured_delay(self):
        # A site may not talk us into crawling faster than we chose to.
        fetcher = RecordingFetcher({
            "https://e.org": {"outcome": "ok", "status": 200},
            "https://e.org/robots.txt": {
                "outcome": "ok", "status": 200, "text": "User-agent: *\nCrawl-delay: 0.5\n",
            },
        })
        record = check_compliance(
            {"source_id": "src_1", "base_url": "https://e.org", "access_method": "web"},
            fetcher=fetcher,
        )
        self.assertEqual(record["request_rate_limit"], 2.0)
        self.assertNotIn(0.5, fetcher.delays_for("https://e.org"))
