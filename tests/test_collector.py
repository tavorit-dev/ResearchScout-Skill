# tests/test_collector.py
import unittest
from pathlib import Path
from scripts.collector import (assert_compliance_allows, collect, parse_feed,
                               path_is_permitted)

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


# A constructed Atom feed, namespaced as real Atom is. Kept alongside the RSS
# fixture so both feed dialects are covered by committed tests rather than only
# by ad hoc checks: the Atom path is where a local-name matching bug hides.
ATOM_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example Atom Notes</title>
  <entry>
    <title>Atom Entry One</title>
    <link rel="alternate" href="https://e.org/atom1"/>
    <published>2026-10-19T12:30:00Z</published>
    <summary>An atom summary.</summary>
  </entry>
  <entry>
    <title>Atom Entry Two</title>
    <link href="https://e.org/atom2"/>
    <summary>An atom summary without a date.</summary>
  </entry>
</feed>
"""

# A title with mixed content, and the same entry with a plain title. XML allows
# child markup inside a title, so the two must extract to different values and
# therefore fingerprint differently.
MIXED_TITLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Nested <b>Title</b> Here</title>
      <link>https://e.org/a1</link>
    </item>
  </channel>
</rss>
"""

PLAIN_TITLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Nested</title>
      <link>https://e.org/a1</link>
    </item>
  </channel>
</rss>
"""


def source(**overrides):
    """Return an ALLOWED rss source, with overrides applied."""
    base = {
        "source_id": "src_1",
        "base_url": "https://e.org",
        "access_method": "rss",
        "feed_url": "https://e.org/feed",
        "compliance_status": "ALLOWED",
    }
    base.update(overrides)
    return base


def text_fetcher(text, final_url=None):
    """Return a fetcher that serves text, recording the URLs it was asked for.

    The recorded URL list is what lets a test prove the collector made exactly
    one request, or made none at all.
    """
    calls = []

    def fetcher(url, **kwargs):
        calls.append(url)
        return {
            "outcome": "ok",
            "status": 200,
            "text": text,
            "final_url": final_url or url,
        }

    fetcher.calls = calls
    return fetcher


class TestParseFeedAtom(unittest.TestCase):
    def test_parses_atom_entries(self):
        items = parse_feed(ATOM_FEED)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "Atom Entry One")
        self.assertEqual(items[0]["link"], "https://e.org/atom1")
        self.assertEqual(items[0]["published_at"], "2026-10-19T12:30:00Z")
        self.assertEqual(items[0]["summary"], "An atom summary.")

    def test_atom_link_without_rel_is_used(self):
        # An Atom link may omit rel, which means alternate. Reading only
        # rel="alternate" would lose the address of every such entry.
        items = parse_feed(ATOM_FEED)
        self.assertEqual(items[1]["link"], "https://e.org/atom2")

    def test_atom_entry_without_date_is_none(self):
        items = parse_feed(ATOM_FEED)
        self.assertIsNone(items[1]["published_at"])


class TestMixedContentText(unittest.TestCase):
    def test_mixed_content_title_is_not_truncated(self):
        items = parse_feed(MIXED_TITLE_FEED)
        self.assertEqual(items[0]["title"], "Nested Title Here")

    def test_mixed_content_title_changes_the_content_hash(self):
        # The reason truncation matters rather than being cosmetic: the title
        # feeds content_hash, so a truncated title would also corrupt the
        # fingerprint the second deduplication layer compares (spec 10.1).
        mixed = collect(source(), fetcher=text_fetcher(MIXED_TITLE_FEED))
        plain = collect(source(), fetcher=text_fetcher(PLAIN_TITLE_FEED))
        self.assertNotEqual(
            mixed[0]["content_hash"], plain[0]["content_hash"]
        )

    def test_mixed_content_summary_is_not_truncated(self):
        feed = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel><item>
          <title>t</title>
          <link>https://e.org/a1</link>
          <description>Desc <b>ription</b> tail</description>
        </item></channel></rss>"""
        self.assertEqual(parse_feed(feed)[0]["summary"], "Desc ription tail")


class TestRedirectIsRechecked(unittest.TestCase):
    def test_redirect_to_another_host_is_refused(self):
        docs = collect(
            source(),
            fetcher=text_fetcher(ATOM_FEED, final_url="https://evil.example/feed"),
        )
        self.assertEqual(docs, [])

    def test_redirect_to_a_subdomain_is_refused(self):
        docs = collect(
            source(),
            fetcher=text_fetcher(ATOM_FEED, final_url="https://cdn.e.org/feed"),
        )
        self.assertEqual(docs, [])

    def test_redirect_within_the_same_host_is_accepted(self):
        docs = collect(
            source(),
            fetcher=text_fetcher(ATOM_FEED, final_url="https://e.org/feed/"),
        )
        self.assertEqual(len(docs), 2)

    def test_redirect_to_a_non_http_scheme_is_refused(self):
        docs = collect(
            source(),
            fetcher=text_fetcher(ATOM_FEED, final_url="ftp://e.org/feed"),
        )
        self.assertEqual(docs, [])


class TestSchemeAllowlist(unittest.TestCase):
    def test_non_http_scheme_is_skipped_without_a_fetch(self):
        fetcher = text_fetcher(ATOM_FEED)
        docs = collect(source(feed_url="ftp://e.org/feed"), fetcher=fetcher)
        self.assertEqual(docs, [])
        # The address is skipped, not handed to the transport: asking urllib to
        # speak a protocol this project has no compliance story for is exactly
        # what the allowlist exists to prevent.
        self.assertEqual(fetcher.calls, [])


class TestTargetResolution(unittest.TestCase):
    def test_web_source_reads_its_base_url(self):
        fetcher = text_fetcher(ATOM_FEED)
        collect(source(access_method="web", feed_url=None), fetcher=fetcher)
        self.assertEqual(fetcher.calls, ["https://e.org"])

    def test_api_source_reads_its_api_url(self):
        fetcher = text_fetcher(ATOM_FEED)
        collect(
            source(
                access_method="api",
                api_url="https://e.org/api/v1",
                feed_url=None,
            ),
            fetcher=fetcher,
        )
        self.assertEqual(fetcher.calls, ["https://e.org/api/v1"])

    def test_one_source_is_one_request(self):
        # Nothing is followed out of the fetched document (spec 8.6, invariant
        # 2): the source's own address is read once, never crawled outward.
        fetcher = text_fetcher(ATOM_FEED)
        collect(source(), fetcher=fetcher)
        self.assertEqual(len(fetcher.calls), 1)


class TestTargetHostBoundToTheSource(unittest.TestCase):
    """A consent for one host cannot become a fetch of another (invariant 1)."""

    def test_rss_feed_on_another_host_is_a_misconfiguration(self):
        fetcher = text_fetcher(ATOM_FEED)
        with self.assertRaises(ValueError) as caught:
            collect(source(feed_url="https://evil.example/feed"), fetcher=fetcher)
        self.assertEqual(fetcher.calls, [])
        self.assertIn("evil.example", str(caught.exception))
        self.assertIn("e.org", str(caught.exception))

    def test_api_url_on_another_host_is_a_misconfiguration(self):
        fetcher = text_fetcher(ATOM_FEED)
        with self.assertRaises(ValueError) as caught:
            collect(
                source(access_method="api", feed_url=None,
                       api_url="https://evil.example/api"),
                fetcher=fetcher,
            )
        self.assertEqual(fetcher.calls, [])
        self.assertIn("evil.example", str(caught.exception))

    def test_a_feed_on_the_base_host_still_collects(self):
        docs = collect(source(feed_url="https://e.org/other-feed"),
                       fetcher=text_fetcher(ATOM_FEED))
        self.assertEqual(len(docs), 2)


class TestPathIsPermitted(unittest.TestCase):
    """``allowed_paths`` is a segment boundary, not a string prefix (fix 4, NEW-1)."""

    def permitted(self, url):
        return path_is_permitted({"allowed_paths": ["/public"]}, url)

    def test_the_prefix_itself_is_permitted(self):
        self.assertTrue(self.permitted("https://e.org/public"))

    def test_a_descendant_segment_is_permitted(self):
        self.assertTrue(self.permitted("https://e.org/public/notes"))

    def test_a_longer_segment_name_is_not_a_descendant(self):
        # "/publicity" starts with "/public" as a string but not as a path.
        self.assertFalse(self.permitted("https://e.org/publicity"))

    def test_a_dash_suffix_is_not_a_descendant(self):
        self.assertFalse(self.permitted("https://e.org/public-secrets"))

    def test_dot_segments_are_removed_before_the_comparison(self):
        self.assertFalse(self.permitted("https://e.org/public/../secret"))

    def test_an_encoded_separator_is_refused(self):
        # %2F would become a separator after decoding, so the raw segment
        # boundaries no longer describe the path being compared. Refuse.
        self.assertFalse(self.permitted("https://e.org/public%2Fsecret"))

    def test_an_encoded_letter_is_decoded(self):
        # Percent-decoding is not only a defence: "/p%75blic" is "/public".
        self.assertTrue(self.permitted("https://e.org/p%75blic/notes"))

    def test_a_trailing_slash_on_the_permission_does_not_widen_it(self):
        self.assertFalse(path_is_permitted(
            {"allowed_paths": ["/public/"]}, "https://e.org/publicity"))
        self.assertTrue(path_is_permitted(
            {"allowed_paths": ["/public/"]}, "https://e.org/public/notes"))

    def test_an_encoded_permission_and_a_plain_target_agree(self):
        # The gate and the collector must read the same permission the same
        # way. Both directions are asserted so a fix cannot simply reject
        # everything: encoded permission/plain target and plain permission/
        # encoded target both name the same path segment.
        self.assertTrue(path_is_permitted(
            {"allowed_paths": ["/%66eed"]}, "https://e.org/feed"))
        self.assertTrue(path_is_permitted(
            {"allowed_paths": ["/feed"]}, "https://e.org/%66eed"))

    def test_a_root_permission_permits_every_path(self):
        # A web source whose base path is "/" still permits everything
        # beneath it, as it did before this check existed.
        for url in ("https://e.org/", "https://e.org/anywhere/at/all"):
            with self.subTest(url=url):
                self.assertTrue(path_is_permitted(
                    {"allowed_paths": ["/"]}, url))

    def test_an_absent_or_empty_list_means_no_restriction(self):
        for source in ({}, {"allowed_paths": None}, {"allowed_paths": []}):
            with self.subTest(source=source):
                self.assertTrue(path_is_permitted(source, "https://e.org/x"))

    def test_a_malformed_permission_fails_closed_and_says_why(self):
        # A permission the tool cannot read is not "no restriction".
        for allowed, url in (
            ("public", "https://e.org/public"),
            ([""], "https://e.org/anywhere"),
            ([123], "https://e.org/anywhere"),
            (["/public", 123], "https://e.org/public"),
            (123, "https://e.org/anywhere"),
        ):
            with self.subTest(allowed=allowed):
                with self.assertWarns(RuntimeWarning) as caught:
                    self.assertFalse(path_is_permitted(
                        {"allowed_paths": allowed}, url))
                self.assertIn("allowed_paths", str(caught.warning))


class TestAllowedPaths(unittest.TestCase):
    def test_the_initial_target_outside_the_allowed_path_is_not_requested(self):
        # The request itself is the violation: the target path is checked
        # before the transport is touched (fix 4, NEW-5).
        fetcher = text_fetcher(ATOM_FEED)
        docs = collect(
            source(allowed_paths=["/public"],
                   feed_url="https://e.org/secret/feed"),
            fetcher=fetcher,
        )
        self.assertEqual(docs, [])
        self.assertEqual(fetcher.calls, [])

    def test_redirect_into_a_disallowed_path_is_refused(self):
        docs = collect(
            source(allowed_paths=["/public"],
                   feed_url="https://e.org/public/feed"),
            fetcher=text_fetcher(ATOM_FEED,
                                 final_url="https://e.org/secret/feed"),
        )
        self.assertEqual(docs, [])

    def test_redirect_inside_an_allowed_path_is_accepted(self):
        # Initial and final addresses must differ, or this exercises no
        # redirect at all: the point is that a redirect which stays inside the
        # permitted path is followed and archived.
        fetcher = text_fetcher(ATOM_FEED,
                               final_url="https://e.org/public/archive/feed")
        docs = collect(
            source(allowed_paths=["/public"],
                   feed_url="https://e.org/public/feed"),
            fetcher=fetcher,
        )
        self.assertEqual(fetcher.calls, ["https://e.org/public/feed"])
        self.assertEqual(len(docs), 2)

    def test_no_allowed_paths_means_no_path_restriction(self):
        docs = collect(
            source(),
            fetcher=text_fetcher(ATOM_FEED,
                                 final_url="https://e.org/anywhere/feed"),
        )
        self.assertEqual(len(docs), 2)


class TestMissingTransportAddress(unittest.TestCase):
    """rss/api without their address is a misconfiguration, not an empty feed."""

    def test_rss_with_an_empty_feed_url_raises(self):
        fetcher = text_fetcher(ATOM_FEED)
        with self.assertRaises(ValueError) as caught:
            collect(source(feed_url=""), fetcher=fetcher)
        self.assertEqual(fetcher.calls, [])
        self.assertIn("feed_url", str(caught.exception))
        self.assertIn("src_1", str(caught.exception))

    def test_rss_with_an_absent_feed_url_raises(self):
        absent = source()
        absent.pop("feed_url")
        fetcher = text_fetcher(ATOM_FEED)
        with self.assertRaises(ValueError) as caught:
            collect(absent, fetcher=fetcher)
        self.assertEqual(fetcher.calls, [])
        self.assertIn("feed_url", str(caught.exception))

    def test_api_without_an_api_url_raises(self):
        fetcher = text_fetcher(ATOM_FEED)
        with self.assertRaises(ValueError) as caught:
            collect(source(access_method="api", feed_url="", api_url=""),
                    fetcher=fetcher)
        self.assertEqual(fetcher.calls, [])
        self.assertIn("api_url", str(caught.exception))

    def test_a_web_source_reads_its_base_url_without_a_transport_address(self):
        fetcher = text_fetcher(ATOM_FEED)
        collect(source(access_method="web", feed_url=""), fetcher=fetcher)
        self.assertEqual(fetcher.calls, ["https://e.org"])


class TestFailedFetchYieldsNothing(unittest.TestCase):
    def _collect_with_outcome(self, outcome):
        def fetcher(url, **kwargs):
            return {
                "outcome": outcome,
                "status": 403,
                "text": None,
                "final_url": url,
            }

        return collect(source(), fetcher=fetcher)

    def test_every_non_ok_outcome_yields_no_documents(self):
        # BLOCKED and ERROR stay distinct upstream (spec 6.2, invariant 3), but
        # none of them is retried here: asking again is how a client tries to
        # get past a refusal (spec 10.4).
        for outcome in ("not_found", "server_error", "timeout", "blocked", "too_large"):
            with self.subTest(outcome=outcome):
                self.assertEqual(self._collect_with_outcome(outcome), [])


class TestRateLimitPacing(unittest.TestCase):
    def _delay_used(self, src, config=None):
        recorded = {}

        def fetcher(url, **kwargs):
            recorded["delay"] = kwargs.get("delay")
            return {
                "outcome": "ok",
                "status": 200,
                "text": ATOM_FEED,
                "final_url": url,
            }

        collect(src, fetcher=fetcher, config=config)
        return recorded["delay"]

    def test_recorded_rate_limit_paces_the_request(self):
        # The interval the gate resolved -- including any Crawl-delay the site
        # published -- must reach the transport, not stay a number in a record.
        self.assertEqual(self._delay_used(source(request_rate_limit=7.5)), 7.5)

    def test_absent_rate_limit_falls_back_to_the_config(self):
        delay = self._delay_used(source(), config={"request_delay_seconds": 4})
        self.assertEqual(delay, 4)

    def test_unreadable_rate_limit_is_ignored(self):
        delay = self._delay_used(
            source(request_rate_limit="garbage"),
            config={"request_delay_seconds": 4},
        )
        self.assertEqual(delay, 4)


class TestCollectFailsClosed(unittest.TestCase):
    def test_collect_blocks_before_it_fetches(self):
        fetcher = text_fetcher(ATOM_FEED)
        with self.assertRaises(PermissionError):
            collect(source(compliance_status="DISCOVERED"), fetcher=fetcher)
        self.assertEqual(fetcher.calls, [])

    def test_every_non_allowed_status_is_refused(self):
        for status in (
            "BLOCKED",
            "REVIEW_REQUIRED",
            "ERROR",
            "DISCOVERED",
            "CHECKING",
            "UNREACHABLE",
            None,
        ):
            with self.subTest(status=status):
                with self.assertRaises(PermissionError):
                    assert_compliance_allows(
                        {"source_id": "src_1", "compliance_status": status}
                    )


if __name__ == "__main__":
    unittest.main()
