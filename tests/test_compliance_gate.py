# tests/test_compliance_gate.py
import unittest
from datetime import datetime, timezone

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

    def test_refused_request_is_blocked(self):
        # A 401/403/4xx is an access refusal, not a missing answer: it is
        # BLOCKED, never UNREACHABLE and never permission.
        src = {"source_id": "src_1", "base_url": "https://e.org"}
        fetcher = fake_fetcher({"https://e.org": {"outcome": "blocked", "status": 403}})
        self.assertEqual(check_reachability(src, fetcher=fetcher), "BLOCKED")

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

    def test_a_refused_reachability_request_is_blocked_without_robots(self):
        calls = []

        def fetcher(url, **kwargs):
            calls.append(url)
            return {"outcome": "blocked", "status": 403}

        record = check_compliance(
            {"source_id": "src_1", "base_url": "https://e.org", "access_method": "web"},
            fetcher=fetcher,
        )

        self.assertEqual(record["compliance_status"], "BLOCKED")
        self.assertEqual(record["robots_status"], "not_checked")
        # robots.txt is not consulted for a source that refused to answer.
        self.assertEqual(calls, ["https://e.org"])
        self.assertTrue(any(item["type"] == "reachability"
                            for item in record["evidence"]))

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


class TestTargetPathIsNormalised(unittest.TestCase):
    """The gate must evaluate the same path the collector enforces (fix round 5).

    robots.txt is asked about ``/secret``; the collector compares normalised
    paths. A gate that queries robots with the raw path while the collector
    compares the normalised one lets ``/%73ecret`` and ``/public/../secret``
    through: the gate sees a path robots never refused, records it, and the
    collector's pre-fetch check then matches its own recorded value by
    construction.
    """

    ROBOTS = "User-agent: *\nDisallow: /secret\n"

    def _checked(self, target_url, robots=None):
        fetcher = fake_fetcher({
            "https://e.org": {"outcome": "ok", "status": 200},
            "https://e.org/robots.txt": {
                "outcome": "ok", "status": 200, "text": robots or self.ROBOTS,
            },
        })
        return check_compliance(
            {
                "source_id": "src_1",
                "base_url": "https://e.org",
                "access_method": "rss",
                "feed_url": target_url,
            },
            fetcher=fetcher,
            target_url=target_url,
        )

    def test_the_plain_disallowed_target_is_blocked(self):
        record = self._checked("https://e.org/secret/feed")
        self.assertEqual(record["compliance_status"], "BLOCKED")
        self.assertEqual(record["allowed_paths"], [])

    def test_an_encoded_disallowed_target_is_blocked(self):
        record = self._checked("https://e.org/%73ecret/feed")
        self.assertEqual(record["compliance_status"], "BLOCKED")
        self.assertEqual(record["allowed_paths"], [])

    def test_a_dot_segment_target_that_resolves_into_the_disallowed_path_is_blocked(self):
        record = self._checked("https://e.org/public/../secret/feed")
        self.assertEqual(record["compliance_status"], "BLOCKED")
        self.assertEqual(record["allowed_paths"], [])

    def test_an_encoded_permitted_target_records_its_plain_path(self):
        # The inverse direction: normalisation must not make everything fail.
        # A permitted address stated in encoded form is still permitted, and
        # the path recorded is the one the collector will compare against.
        record = self._checked(
            "https://e.org/%66eed", robots="User-agent: *\nAllow: /\n",
        )
        self.assertEqual(record["compliance_status"], "ALLOWED")
        self.assertEqual(record["allowed_paths"], ["/feed"])


class TestTargetOriginBoundToTheSource(unittest.TestCase):
    """The gate must not grant a path for a target it never checked (fix round 5).

    ``check_compliance`` consults robots.txt for the source's origin. A target
    on another origin cannot be covered by that answer, so it is marked as a
    misconfiguration and no path is recorded for it. The collector's own
    off-host refusal remains the second line.
    """

    def test_an_off_origin_target_is_marked_misconfigured_without_a_path(self):
        calls = []

        def fetcher(url, **kwargs):
            calls.append(url)
            if url.endswith("/robots.txt"):
                return {"outcome": "ok", "status": 200,
                        "text": "User-agent: *\nAllow: /\n"}
            return {"outcome": "ok", "status": 200}

        record = check_compliance(
            {
                "source_id": "src_1",
                "base_url": "https://e.org",
                "access_method": "rss",
                "feed_url": "https://evil.example/feed",
            },
            fetcher=fetcher,
            target_url="https://evil.example/feed",
        )

        self.assertEqual(record["compliance_status"], "ERROR")
        self.assertEqual(record["allowed_paths"], [])
        # No decision marker: the next round must check this record again.
        self.assertFalse(record.get("checked_at"))
        # The off-origin target was never requested; only the source's own
        # origin was consulted.
        self.assertNotIn("https://evil.example/feed", calls)
        self.assertIn("https://e.org/robots.txt", calls)


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


class TestRobotsEvaluatedAsThisClient(unittest.TestCase):
    """robots.txt is queried as ResearchScout, not as the wildcard (C1)."""

    def _record(self, robots):
        fetcher = fake_fetcher({
            "https://e.org": {"outcome": "ok", "status": 200},
            "https://e.org/robots.txt": {
                "outcome": "ok", "status": 200, "text": robots,
            },
        })
        return check_compliance(
            {"source_id": "src_1", "base_url": "https://e.org",
             "access_method": "web"},
            fetcher=fetcher,
        )

    def test_a_specific_disallow_blocks_without_a_wildcard_group(self):
        record = self._record("User-agent: ResearchScout\nDisallow: /\n")
        self.assertEqual(record["compliance_status"], "BLOCKED")
        self.assertEqual(record["allowed_paths"], [])

    def test_a_wildcard_only_site_still_allows(self):
        record = self._record("User-agent: *\nAllow: /\n")
        self.assertEqual(record["compliance_status"], "ALLOWED")

    def test_a_specific_group_sets_the_crawl_delay(self):
        record = self._record(
            "User-agent: ResearchScout\nCrawl-delay: 7\n\n"
            "User-agent: *\nCrawl-delay: 1\n"
        )
        self.assertEqual(record["request_rate_limit"], 7.0)


class TestDeclaredRobotsUrlBoundToOrigin(unittest.TestCase):
    """An explicit robots URL on another origin is never fetched (Minor 11)."""

    def test_an_off_origin_robots_url_is_refused_before_any_request(self):
        calls = []

        def fetcher(url, **kwargs):
            calls.append(url)
            return {"outcome": "ok", "status": 200,
                    "text": "User-agent: *\nAllow: /\n"}

        record = check_compliance(
            {
                "source_id": "src_1",
                "base_url": "https://e.org",
                "access_method": "web",
                "robots_url": "https://evil.example/robots.txt",
            },
            fetcher=fetcher,
        )

        self.assertEqual(record["compliance_status"], "ERROR")
        self.assertIsNone(record["checked_at"])
        self.assertEqual(calls, [])
        self.assertTrue(any(item["type"] == "robots_url"
                            for item in record["evidence"]))

    def test_a_same_origin_robots_url_is_consulted(self):
        calls = []

        def fetcher(url, **kwargs):
            calls.append(url)
            if url.endswith("/custom-robots.txt"):
                return {"outcome": "ok", "status": 200,
                        "text": "User-agent: *\nAllow: /\n"}
            return {"outcome": "ok", "status": 200}

        record = check_compliance(
            {
                "source_id": "src_1",
                "base_url": "https://e.org",
                "access_method": "web",
                "robots_url": "https://e.org/custom-robots.txt",
            },
            fetcher=fetcher,
        )

        self.assertEqual(record["compliance_status"], "ALLOWED")
        self.assertIn("https://e.org/custom-robots.txt", calls)


class TestUserAuthorisationPolicies(unittest.TestCase):
    """V13: a live, scoped, expiring policy releases only REVIEW_REQUIRED."""

    NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)

    def _policy(self, **overrides):
        record = {
            "source_id": "src_1",
            "resolved_status": "ALLOWED",
            "resolved_by": "user",
            "resolved_at": "2026-10-01T00:00:00+00:00",
            "reason": "The user reviewed the terms.",
            "scope": {
                "paths": ["/"],
                "access_method": "web",
                "expires_at": "2026-11-01T00:00:00+00:00",
            },
            "check_record_ref": "src_1",
        }
        record.update(overrides)
        return record

    def _source(self, **overrides):
        record = {
            "source_id": "src_1",
            "base_url": "https://e.org",
            "access_method": "web",
            "terms_url": "https://e.org/terms",
        }
        record.update(overrides)
        return record

    def _fetcher(self, robots="User-agent: *\nAllow: /\n", base=None):
        return fake_fetcher({
            "https://e.org": base or {"outcome": "ok", "status": 200},
            "https://e.org/robots.txt": {
                "outcome": "ok", "status": 200, "text": robots,
            },
        })

    def _check(self, policies, robots="User-agent: *\nAllow: /\n", **overrides):
        return check_compliance(
            self._source(**overrides),
            fetcher=self._fetcher(robots),
            policies=policies,
            now=self.NOW,
        )

    def test_a_valid_policy_releases_review_required(self):
        record = self._check([self._policy()])
        self.assertEqual(record["compliance_status"], "ALLOWED")
        self.assertTrue(any(item["type"] == "source_policy"
                            for item in record["evidence"]))

    def test_an_expired_policy_leaves_review_required(self):
        record = self._check([self._policy(scope={
            "paths": ["/"],
            "access_method": "web",
            "expires_at": "2026-10-01T00:00:00+00:00",
        })])
        self.assertEqual(record["compliance_status"], "REVIEW_REQUIRED")

    def test_an_out_of_scope_path_leaves_review_required(self):
        record = self._check([self._policy(scope={
            "paths": ["/private"],
            "access_method": "web",
            "expires_at": "2026-11-01T00:00:00+00:00",
        })])
        self.assertEqual(record["compliance_status"], "REVIEW_REQUIRED")

    def test_an_access_method_mismatch_leaves_review_required(self):
        record = self._check([self._policy(scope={
            "paths": ["/"],
            "access_method": "rss",
            "expires_at": "2026-11-01T00:00:00+00:00",
        })])
        self.assertEqual(record["compliance_status"], "REVIEW_REQUIRED")

    def test_a_blocked_policy_never_allows(self):
        record = self._check([self._policy(resolved_status="BLOCKED")])
        self.assertEqual(record["compliance_status"], "REVIEW_REQUIRED")

    def test_a_policy_cannot_relax_a_robots_refusal(self):
        record = self._check(
            [self._policy()], robots="User-agent: *\nDisallow: /\n"
        )
        self.assertEqual(record["compliance_status"], "BLOCKED")

    def test_a_policy_cannot_relax_a_robots_error(self):
        record = check_compliance(
            self._source(),
            fetcher=fake_fetcher({
                "https://e.org": {"outcome": "ok", "status": 200},
                "https://e.org/robots.txt": {"outcome": "server_error",
                                             "status": 503},
            }),
            policies=[self._policy()],
            now=self.NOW,
        )
        self.assertEqual(record["compliance_status"], "ERROR")

    def test_a_policy_cannot_relax_an_unreachable_source(self):
        record = check_compliance(
            self._source(),
            fetcher=fake_fetcher({"https://e.org": {"outcome": "timeout"}}),
            policies=[self._policy()],
            now=self.NOW,
        )
        self.assertEqual(record["compliance_status"], "UNREACHABLE")
