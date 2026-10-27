# tests/test_research_loop.py
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from scripts.archive import (load_state, rebuild_manifest, save_documents,
                             save_findings, save_state)
from scripts.lib.jsonl import read_records
from scripts.lib.paths import ensure_research_layout
from scripts.lib.validate import validate_record
from scripts.planner import init_research
from scripts.research_loop import apply_results, check_env, main, run_round
from scripts.source_discovery import discover

# -- fixtures ---------------------------------------------------------------
#
# Everything below is constructed. No test here touches the network: every
# collection goes through an injected fetcher and every address is under the
# reserved ``e.org`` domain (spec 3.1 C3, acceptance V9).

FEED_ONE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Example agent-security feed</title>
  <item>
    <title>Agent Sandbox Design</title>
    <link>https://e.org/a1</link>
    <pubDate>Tue, 20 Oct 2026 00:00:00 GMT</pubDate>
    <description>Runtime isolation for agent tools.</description>
  </item>
</channel></rss>"""

FEED_TWO = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Example agent-security feed</title>
  <item>
    <title>Agent Sandbox Design</title>
    <link>https://e.org/a1</link>
    <pubDate>Tue, 20 Oct 2026 00:00:00 GMT</pubDate>
    <description>Runtime isolation for agent tools.</description>
  </item>
  <item>
    <title>Agent Permission Model</title>
    <link>https://e.org/a2</link>
    <pubDate>Wed, 21 Oct 2026 00:00:00 GMT</pubDate>
    <description>Least privilege for tool calls.</description>
  </item>
</channel></rss>"""

PAGE = """<html><head><title>Agent Runtime Isolation</title></head>
<body><article><p>Runtime isolation keeps agent tools in a sandbox.</p>
</article></body></html>"""

# robots.txt bodies the gate can be pointed at. ``Allow: /`` is the ordinary
# permitted site; ``Disallow: /`` is the forged-status probe's site.
ROBOTS_ALLOW = "User-agent: *\nAllow: /\n"
ROBOTS_DISALLOW_ALL = "User-agent: *\nDisallow: /\n"

CONFIRMED_SCOPE = {
    "core": ["Agent Security"],
    "related": ["沙箱隔离"],
    "excluded": ["GPU 性能优化"],
    "confirmed": True,
}


def source(**overrides):
    """Return one verified source whose compliance decision is already recorded.

    ``checked_at`` is part of the fixture on purpose: a source that carries a
    recorded decision is not re-checked, so the collection tests below exercise
    collection rather than the gate. The gate tests build their own source with
    ``checked_at`` removed, which is exactly the forged-status case.
    """
    record = {
        "source_id": "src_feed",
        "source_name": "e.org",
        "base_url": "https://e.org",
        "access_method": "rss",
        "feed_url": "https://e.org/feed",
        "compliance_status": "ALLOWED",
        "checked_at": "2026-10-01T00:00:00+00:00",
        # ``domain`` is what the gate writes, and it is what binds a recorded
        # decision to the host it was made for. A fixture that omits it would
        # model a decision that never was one.
        "domain": "e.org",
        "discovery_verified": True,
        "selection_status": "selected",
    }
    record.update(overrides)
    return record


def action(**overrides):
    record = {
        "action_id": "act_1",
        "type": "deepen",
        "topic": "Agent Security",
        "reason": "补充实现细节",
        "priority": 90,
        "time_window_days": 180,
    }
    record.update(overrides)
    return record


def text_fetcher(text, final_url=None):
    """Return a fetcher serving ``text`` and recording the URLs it was asked."""
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


def missing_fetcher():
    """Return a fetcher that answers 404 and records every call."""
    calls = []

    def fetcher(url, **kwargs):
        calls.append(url)
        return {"outcome": "not_found", "status": 404, "text": None,
                "final_url": url}

    fetcher.calls = calls
    return fetcher


def routed_fetcher(routes):
    """Return a fetcher answering each URL from ``routes``, recording calls.

    An unlisted URL is a 404. The gate requests the base URL and robots.txt
    before collection, so a test has to say what each address returns instead
    of letting one body answer every question.
    """
    calls = []

    def fetcher(url, **kwargs):
        calls.append(url)
        result = dict(routes.get(url, {
            "outcome": "not_found", "status": 404, "text": None,
        }))
        result.setdefault("final_url", url)
        return result

    fetcher.calls = calls
    return fetcher


def site_fetcher(*, body=FEED_ONE, robots=ROBOTS_ALLOW):
    """Return a fetcher for one permitted site, with ``robots`` at its rules URL."""
    return routed_fetcher({
        "https://e.org": {"outcome": "ok", "status": 200, "text": body},
        "https://e.org/robots.txt": {"outcome": "ok", "status": 200, "text": robots},
    })


def layout_root(tmp):
    return ensure_research_layout(Path(tmp) / "security" / "ai-agent-security")


def project(tmp, *, scope=None, topic="ai-agent-security"):
    init_research(
        Path(tmp), domain="security", topic=topic,
        objective="识别 AI Agent 安全的技术趋势",
        keywords=["Agent Security"], time_window={"recent_days": 30},
        scope=scope,
    )
    return Path(tmp) / "security" / topic


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


# -- the plan's six tests, verbatim -----------------------------------------


class TestCheckEnv(unittest.TestCase):
    def test_reports_missing_host(self):
        ok, message = check_env(host_available=False)
        self.assertFalse(ok)
        self.assertIn("LLM", message)

    def test_ok_when_host_present(self):
        ok, _ = check_env(host_available=True)
        self.assertTrue(ok)


class TestRunRound(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = ensure_research_layout(self.tmp / "security" / "ai-agent-security")

    def test_handoff_shape(self):
        handoff = run_round(self.root, round_no=1, actions=[], config={"iteration_policy": {}},
                            fetcher=lambda url, **kw: {"outcome": "not_found", "status": 404})
        for key in ("round", "new_documents", "candidates_for_review",
                    "state_summary", "remaining_budget"):
            self.assertIn(key, handoff)

    def test_round_is_single_pass(self):
        handoff = run_round(self.root, round_no=1, actions=[], config={"iteration_policy": {}},
                            fetcher=lambda url, **kw: {"outcome": "not_found", "status": 404})
        self.assertEqual(handoff["round"], 1)


class TestApplyResults(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = ensure_research_layout(self.tmp / "security" / "ai-agent-security")

    def test_rejects_invalid_decision(self):
        with self.assertRaises(ValueError):
            apply_results(self.root, handoff={"round": 1}, observations={},
                          findings=[], next_actions=[], decision="NOT_A_STATE")

    def test_writes_iteration_and_state(self):
        result = apply_results(
            self.root, handoff={"round": 1, "new_documents": []}, observations={},
            findings=[], next_actions=[], decision="CONTINUE",
        )
        self.assertEqual(result["decision"], "CONTINUE")
        self.assertTrue((self.root / "iterations" / "round-001.json").exists())


# -- collection -------------------------------------------------------------


class TestRoundCollection(TempCase):
    def test_collects_a_document_end_to_end(self):
        root = layout_root(self.tmp)
        fetcher = text_fetcher(FEED_ONE)

        handoff = run_round(root, round_no=1, actions=[],
                            config={"iteration_policy": {}},
                            fetcher=fetcher, sources=[source()])

        self.assertEqual(len(handoff["new_documents"]), 1)
        document = handoff["new_documents"][0]
        self.assertEqual(document["canonical_url"], "https://e.org/a1")
        self.assertEqual(document["crawl_status"], "FETCHED")
        self.assertEqual(fetcher.calls, ["https://e.org/feed"])

        stored = read_records(root / "documents" / "documents.jsonl")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["document_id"], document["document_id"])

    def test_collected_document_is_schema_valid(self):
        root = layout_root(self.tmp)
        handoff = run_round(root, round_no=1, actions=[],
                            config={"iteration_policy": {}},
                            fetcher=text_fetcher(FEED_ONE), sources=[source()])
        validate_record("document", handoff["new_documents"][0])

    def test_manifest_counts_the_collected_document(self):
        # run_round persists documents; the manifest is rebuilt by
        # apply_results at the end of the host's round, so the test rebuilds it
        # to read the count the next round would see.
        root = layout_root(self.tmp)
        run_round(root, round_no=1, actions=[], config={"iteration_policy": {}},
                  fetcher=text_fetcher(FEED_ONE), sources=[source()])
        manifest = rebuild_manifest(root)
        self.assertEqual(manifest["document_count"], 1)

    def test_blocked_source_is_refused_without_a_fetch(self):
        root = layout_root(self.tmp)
        fetcher = text_fetcher(FEED_ONE)

        handoff = run_round(
            root, round_no=1, actions=[], config={"iteration_policy": {}},
            fetcher=fetcher,
            sources=[source(source_id="src_blocked", compliance_status="BLOCKED")],
        )

        self.assertEqual(handoff["new_documents"], [])
        # The program-level block runs before the transport: no request at all.
        self.assertEqual(fetcher.calls, [])
        rejected = [item for item in handoff["actions_rejected"]
                    if item.get("kind") == "source"]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["source_id"], "src_blocked")

    def test_unverified_source_goes_to_review_without_a_fetch(self):
        root = layout_root(self.tmp)
        fetcher = text_fetcher(FEED_ONE)

        handoff = run_round(
            root, round_no=1, actions=[], config={"iteration_policy": {}},
            fetcher=fetcher,
            sources=[source(source_id="src_memory", discovery_verified=False)],
        )

        self.assertEqual(handoff["new_documents"], [])
        self.assertEqual(fetcher.calls, [])
        self.assertEqual([item["source_id"] for item in handoff["candidates_for_review"]],
                         ["src_memory"])

    def test_mixed_sources_only_verified_ones_are_collected(self):
        root = layout_root(self.tmp)
        fetcher = text_fetcher(FEED_ONE)

        handoff = run_round(
            root, round_no=1, actions=[], config={"iteration_policy": {}},
            fetcher=fetcher,
            sources=[
                source(source_id="src_verified"),
                source(source_id="src_memory", discovery_verified=False),
            ],
        )

        self.assertEqual(len(handoff["new_documents"]), 1)
        self.assertEqual(fetcher.calls, ["https://e.org/feed"])
        self.assertEqual([item["source_id"] for item in handoff["candidates_for_review"]],
                         ["src_memory"])

    def test_sources_are_written_to_the_selected_sources_file(self):
        root = layout_root(self.tmp)
        run_round(root, round_no=1, actions=[], config={"iteration_policy": {}},
                  fetcher=text_fetcher(FEED_ONE),
                  sources=[source(source_id="src_feed")])

        stored = read_records(root / "sources" / "selected_sources.jsonl")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["source_id"], "src_feed")

    def test_the_same_source_is_updated_not_duplicated(self):
        root = layout_root(self.tmp)
        run_round(root, round_no=1, actions=[], config={"iteration_policy": {}},
                  fetcher=missing_fetcher(),
                  sources=[source(source_id="src_feed", source_name="old")])
        run_round(root, round_no=2, actions=[], config={"iteration_policy": {}},
                  fetcher=missing_fetcher(),
                  sources=[source(source_id="src_feed", source_name="new")])

        stored = read_records(root / "sources" / "selected_sources.jsonl")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["source_name"], "new")

    def test_html_source_is_extracted_into_one_document(self):
        # A web source is not a feed: collect parses no entries, so the round
        # runs extract_content on the captured body instead. This is the only
        # consumer of the extractor in the v1 pipeline.
        root = layout_root(self.tmp)
        handoff = run_round(
            root, round_no=1, actions=[], config={"iteration_policy": {}},
            fetcher=text_fetcher(PAGE),
            sources=[source(source_id="src_page", access_method="web",
                            feed_url=None, base_url="https://e.org")],
        )

        self.assertEqual(len(handoff["new_documents"]), 1)
        document = handoff["new_documents"][0]
        self.assertEqual(document["title"], "Agent Runtime Isolation")
        self.assertEqual(document["crawl_status"], "EXTRACTED")
        validate_record("document", document)

    def test_a_non_html_response_is_not_extracted_as_a_page(self):
        root = layout_root(self.tmp)
        handoff = run_round(
            root, round_no=1, actions=[], config={"iteration_policy": {}},
            fetcher=text_fetcher("just some plain text, no markup"),
            sources=[source(source_id="src_page", access_method="web",
                            feed_url=None, base_url="https://e.org")],
        )
        self.assertEqual(handoff["new_documents"], [])

    def test_redirected_html_source_is_not_archived(self):
        # The collector refuses an off-host redirect; the extraction path must
        # not become a way around that refusal (invariant 1).
        root = layout_root(self.tmp)
        handoff = run_round(
            root, round_no=1, actions=[], config={"iteration_policy": {}},
            fetcher=text_fetcher(PAGE, final_url="https://evil.example/page"),
            sources=[source(source_id="src_page", access_method="web",
                            feed_url=None, base_url="https://e.org")],
        )
        self.assertEqual(handoff["new_documents"], [])


# -- compliance gate inside the round ---------------------------------------


class TestComplianceGateInRound(TempCase):
    """The round must obtain a decision; a source cannot arrive pre-permitted."""

    def test_discovered_source_is_gated_and_then_collected(self):
        # The natural pipeline: init_research -> discover() -> run_round. The
        # candidate leaves discovery as DISCOVERED, so the round itself has to
        # run the gate before collect can accept it.
        root = layout_root(self.tmp)
        candidates = discover("agent security", provider="manual",
                              payload=["https://e.org"])
        self.assertEqual(candidates[0]["compliance_status"], "DISCOVERED")

        fetcher = site_fetcher()
        handoff = run_round(root, round_no=1, actions=[],
                            config={"iteration_policy": {}},
                            fetcher=fetcher, sources=candidates)

        self.assertEqual(len(handoff["new_documents"]), 1)
        self.assertEqual(len(read_records(root / "documents" / "documents.jsonl")), 1)

        stored = read_records(root / "sources" / "selected_sources.jsonl")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["compliance_status"], "ALLOWED")
        self.assertTrue(stored[0]["checked_at"])
        self.assertEqual(stored[0]["selection_status"], "selected")
        self.assertIs(stored[0]["discovery_verified"], True)
        validate_record("source", stored[0])

        # The gate asked for the base URL and robots.txt, then collection read
        # the base URL the source names (access_method "web").
        self.assertIn("https://e.org/robots.txt", fetcher.calls)

    def test_forged_allowed_without_checked_at_is_rechecked(self):
        # An ALLOWED status with no recorded check is a claim, not a decision.
        # The round must re-run the gate rather than trust it.
        root = layout_root(self.tmp)
        forged = source()
        forged.pop("checked_at")
        self.assertEqual(forged["compliance_status"], "ALLOWED")

        fetcher = site_fetcher(robots=ROBOTS_DISALLOW_ALL)
        handoff = run_round(root, round_no=1, actions=[],
                            config={"iteration_policy": {}},
                            fetcher=fetcher, sources=[forged])

        self.assertEqual(handoff["new_documents"], [])
        self.assertIn("https://e.org/robots.txt", fetcher.calls)
        # collect never ran: the feed address was never requested.
        self.assertNotIn("https://e.org/feed", fetcher.calls)

        stored = read_records(root / "sources" / "selected_sources.jsonl")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["compliance_status"], "BLOCKED")
        self.assertTrue(stored[0]["checked_at"])

        rejected = [item for item in handoff["actions_rejected"]
                    if item.get("kind") == "source"]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["source_id"], "src_feed")
        self.assertEqual(rejected[0]["compliance_status"], "BLOCKED")
        self.assertIn("BLOCKED", rejected[0]["reason"])

    def test_recorded_decision_is_honoured_without_a_fetch(self):
        # A decision that carries checked_at survives across rounds: it is
        # neither re-checked nor overridden.
        root = layout_root(self.tmp)
        recorded = source(compliance_status="BLOCKED",
                          checked_at="2026-10-01T00:00:00+00:00")
        fetcher = site_fetcher()

        handoff = run_round(root, round_no=1, actions=[],
                            config={"iteration_policy": {}},
                            fetcher=fetcher, sources=[recorded])

        self.assertEqual(handoff["new_documents"], [])
        self.assertEqual(fetcher.calls, [])

        stored = read_records(root / "sources" / "selected_sources.jsonl")
        self.assertEqual(stored[0]["compliance_status"], "BLOCKED")
        self.assertEqual(stored[0]["checked_at"], "2026-10-01T00:00:00+00:00")

        rejected = [item for item in handoff["actions_rejected"]
                    if item.get("kind") == "source"]
        self.assertEqual(rejected[0]["source_id"], "src_feed")
        self.assertEqual(rejected[0]["compliance_status"], "BLOCKED")
        self.assertIn("BLOCKED", rejected[0]["reason"])

    def test_review_required_and_error_are_skipped_and_recorded(self):
        root = layout_root(self.tmp)
        review = source(source_id="src_review",
                        base_url="https://review.example",
                        access_method="web", feed_url=None,
                        terms_url="https://review.example/terms")
        review.pop("checked_at")
        error = source(source_id="src_error",
                       base_url="https://error.example",
                       access_method="web", feed_url=None)
        error.pop("checked_at")

        fetcher = routed_fetcher({
            "https://review.example": {
                "outcome": "ok", "status": 200, "text": FEED_ONE},
            "https://review.example/robots.txt": {
                "outcome": "ok", "status": 200, "text": ROBOTS_ALLOW},
            "https://error.example": {
                "outcome": "ok", "status": 200, "text": FEED_ONE},
            "https://error.example/robots.txt": {
                "outcome": "server_error", "status": 503, "text": None},
        })

        handoff = run_round(root, round_no=1, actions=[],
                            config={"iteration_policy": {}},
                            fetcher=fetcher, sources=[review, error])

        self.assertEqual(handoff["new_documents"], [])
        # Each base URL was requested once, by reachability. A second request
        # would mean collect ran for a source that was not permitted.
        self.assertEqual(fetcher.calls.count("https://review.example"), 1)
        self.assertEqual(fetcher.calls.count("https://error.example"), 1)

        stored = {record["source_id"]: record
                  for record in read_records(root / "sources" / "selected_sources.jsonl")}
        self.assertEqual(stored["src_review"]["compliance_status"], "REVIEW_REQUIRED")
        self.assertEqual(stored["src_error"]["compliance_status"], "ERROR")

        rejected = {item["source_id"]: item
                    for item in handoff["actions_rejected"]
                    if item.get("kind") == "source"}
        self.assertEqual(rejected["src_review"]["compliance_status"], "REVIEW_REQUIRED")
        self.assertEqual(rejected["src_error"]["compliance_status"], "ERROR")
        self.assertIn("REVIEW_REQUIRED", rejected["src_review"]["reason"])
        self.assertIn("ERROR", rejected["src_error"]["reason"])


# -- action admission and scope ---------------------------------------------


class TestActionAdmission(TempCase):
    def test_unconfirmed_scope_rejects_every_action_and_collects_nothing(self):
        root = project(self.tmp)  # init_research without a scope -> unconfirmed
        fetcher = text_fetcher(FEED_ONE)

        handoff = run_round(
            root, round_no=1, actions=[action()],
            config={"iteration_policy": {}}, fetcher=fetcher,
            sources=[source()],
        )

        self.assertEqual(len(handoff["actions_rejected"]), 1)
        rejection = handoff["actions_rejected"][0]
        self.assertEqual(rejection["rejected_as"], "unconfirmed_scope")
        self.assertEqual(rejection["scope_list"], "unconfirmed")
        self.assertEqual(handoff["new_documents"], [])
        self.assertEqual(fetcher.calls, [])

        pending = json.loads((root / "state" / "pending_actions.json").read_text("utf-8"))
        self.assertEqual(pending["accepted"], [])
        self.assertEqual(len(pending["rejected"]), 1)
        self.assertEqual(pending["rejected"][0]["verdict"]["rejected_as"],
                         "unconfirmed_scope")

    def test_excluded_action_is_recorded_as_a_candidate_topic(self):
        root = project(self.tmp, scope=CONFIRMED_SCOPE)
        handoff = run_round(
            root, round_no=1,
            actions=[action(action_id="act_excluded", topic="GPU 性能优化")],
            config={"iteration_policy": {}}, fetcher=missing_fetcher(),
            sources=[],
        )

        rejection = handoff["actions_rejected"][0]
        self.assertEqual(rejection["rejected_as"], "candidate_topic")
        self.assertEqual(rejection["scope_list"], "excluded")
        # A candidate research TOPIC does not contaminate this round's queue.
        self.assertEqual(handoff["candidates_for_review"], [])

        pending = json.loads((root / "state" / "pending_actions.json").read_text("utf-8"))
        self.assertEqual(pending["rejected"][0]["scope_list"], "excluded")

    def test_related_without_justification_is_a_candidate_lead(self):
        root = project(self.tmp, scope=CONFIRMED_SCOPE)
        handoff = run_round(
            root, round_no=1,
            actions=[action(action_id="act_lead", topic="沙箱隔离")],
            config={"iteration_policy": {}}, fetcher=missing_fetcher(),
            sources=[],
        )

        rejection = handoff["actions_rejected"][0]
        self.assertEqual(rejection["rejected_as"], "candidate_topic")
        # related + candidate_topic is a candidate LEAD, not a new topic.
        self.assertEqual(rejection["scope_list"], "related")

    def test_pending_actions_file_records_accepted_and_rejected(self):
        root = project(self.tmp, scope=CONFIRMED_SCOPE)
        run_round(
            root, round_no=1,
            actions=[
                action(action_id="act_core"),
                action(action_id="act_excluded", topic="GPU 性能优化"),
                action(action_id="act_related", topic="沙箱隔离"),
            ],
            config={"iteration_policy": {}}, fetcher=missing_fetcher(),
            sources=[],
        )

        pending = json.loads((root / "state" / "pending_actions.json").read_text("utf-8"))
        self.assertEqual(pending["round"], 1)
        self.assertEqual([item["action_id"] for item in pending["accepted"]], ["act_core"])
        self.assertEqual(
            [(item["action"]["action_id"], item["scope_list"])
             for item in pending["rejected"]],
            [("act_excluded", "excluded"), ("act_related", "related")],
        )

    def test_an_accepted_action_allows_collection(self):
        root = project(self.tmp, scope=CONFIRMED_SCOPE)
        handoff = run_round(
            root, round_no=1, actions=[action()],
            config={"iteration_policy": {}}, fetcher=text_fetcher(FEED_ONE),
            sources=[source()],
        )
        self.assertEqual(len(handoff["new_documents"]), 1)
        self.assertEqual(handoff["actions_rejected"], [])

    def test_a_round_with_actions_needs_a_research_record(self):
        # No research.yaml: the scope cannot be read, so actions cannot be
        # admitted. Silently collecting anyway would be the wrong direction.
        root = layout_root(self.tmp)
        with self.assertRaises(FileNotFoundError):
            run_round(root, round_no=1, actions=[action()],
                      config={"iteration_policy": {}}, fetcher=missing_fetcher(),
                      sources=[])


# -- budgets ----------------------------------------------------------------


class TestBudget(TempCase):
    def test_documents_per_round_caps_the_round(self):
        root = layout_root(self.tmp)
        handoff = run_round(
            root, round_no=1, actions=[],
            config={"iteration_policy": {"max_documents_per_round": 1}},
            fetcher=text_fetcher(FEED_TWO), sources=[source()],
        )
        self.assertEqual(len(handoff["new_documents"]), 1)
        self.assertEqual(len(read_records(root / "documents" / "documents.jsonl")), 1)
        self.assertTrue(handoff["budget_exhausted"])

    def test_per_round_cap_stops_before_the_next_source(self):
        root = layout_root(self.tmp)
        fetcher = text_fetcher(FEED_TWO)
        handoff = run_round(
            root, round_no=1, actions=[],
            config={"iteration_policy": {"max_documents_per_round": 1}},
            fetcher=fetcher,
            sources=[source(source_id="src_1"), source(source_id="src_2")],
        )
        self.assertEqual(len(handoff["new_documents"]), 1)
        self.assertEqual(len(fetcher.calls), 1)

    def test_per_research_budget_full_skips_collection_without_a_fetch(self):
        root = layout_root(self.tmp)
        save_documents(root, [{
            "document_id": "doc_existing", "source_id": "src_old",
            "canonical_url": "https://e.org/old", "crawl_status": "PERSISTED",
            "retention_mode": "reference", "attempt_count": 0,
        }])
        fetcher = text_fetcher(FEED_ONE)

        handoff = run_round(
            root, round_no=1, actions=[],
            config={"iteration_policy": {"max_documents_per_research": 1}},
            fetcher=fetcher, sources=[source()],
        )

        self.assertTrue(handoff["budget_exhausted"])
        self.assertEqual(handoff["new_documents"], [])
        self.assertEqual(fetcher.calls, [])

    def test_requests_used_accumulates_across_rounds(self):
        root = layout_root(self.tmp)
        config = {"iteration_policy": {}}
        first = run_round(root, round_no=1, actions=[], config=config,
                          fetcher=text_fetcher(FEED_ONE), sources=[source()])
        second = run_round(root, round_no=2, actions=[], config=config,
                           fetcher=missing_fetcher(), sources=[source()])

        first_remaining = first["remaining_budget"]["requests_remaining"]
        second_remaining = second["remaining_budget"]["requests_remaining"]
        self.assertLess(second_remaining, first_remaining)
        self.assertEqual(load_state(root)["requests_used"], 2)

    def test_remaining_budget_has_the_documented_keys(self):
        root = layout_root(self.tmp)
        handoff = run_round(root, round_no=1, actions=[],
                            config={"iteration_policy": {}},
                            fetcher=missing_fetcher(), sources=[])
        budget = handoff["remaining_budget"]
        self.assertEqual(set(budget), {
            "documents_this_round", "documents_remaining", "requests_remaining",
            "runtime_seconds_remaining", "rounds_remaining",
        })
        self.assertEqual(budget["documents_this_round"], 0)
        self.assertEqual(budget["documents_remaining"], 200)
        self.assertEqual(budget["requests_remaining"], 1000)
        self.assertEqual(budget["rounds_remaining"], 7)


# -- fix round 2: the hard limits and the record boundary -------------------


def discovered_source(url="https://e.org"):
    """Return one DISCOVERED, verified source as discovery produces it."""
    return discover("agent security", provider="manual", payload=[url])[0]


def selected_records(root):
    return read_records(root / "sources" / "selected_sources.jsonl")


class TestRequestBudgetCountsFetches(TempCase):
    """The counter must count requests actually made, not phases entered."""

    def test_request_budget_counts_every_fetch(self):
        # Measured before the fix: max=2, one unchecked source with robots
        # Allow -> three fetches (reachability, robots, collection) while the
        # state said 2. The third fetch is now refused and the round stops.
        root = layout_root(self.tmp)
        fetcher = site_fetcher()

        handoff = run_round(
            root, round_no=1, actions=[],
            config={"iteration_policy": {"max_requests_per_research": 2}},
            fetcher=fetcher, sources=[discovered_source()],
        )

        self.assertEqual(fetcher.calls,
                         ["https://e.org", "https://e.org/robots.txt"])
        self.assertEqual(load_state(root)["requests_used"], 2)
        self.assertTrue(handoff["budget_exhausted"])
        self.assertEqual(handoff["remaining_budget"]["requests_remaining"], 0)

    def test_fetch_over_the_request_budget_is_refused(self):
        # max=1: reachability is the one permitted request; the robots request
        # would exceed the budget, so the fetcher is not called for it.
        root = layout_root(self.tmp)
        fetcher = site_fetcher()

        handoff = run_round(
            root, round_no=1, actions=[],
            config={"iteration_policy": {"max_requests_per_research": 1}},
            fetcher=fetcher, sources=[discovered_source()],
        )

        self.assertEqual(fetcher.calls, ["https://e.org"])
        self.assertTrue(handoff["budget_exhausted"])
        self.assertEqual(load_state(root)["requests_used"], 1)
        # Running out of budget is not a compliance decision: the source must
        # not be written back with a verdict the gate never reached.
        self.assertEqual(selected_records(root), [])

    def test_requests_at_the_limit_mark_the_budget_exhausted(self):
        # Exactly reaching the limit is exhaustion, not "one more is fine".
        root = layout_root(self.tmp)
        handoff = run_round(
            root, round_no=1, actions=[],
            config={"iteration_policy": {"max_requests_per_research": 1}},
            fetcher=text_fetcher(FEED_ONE), sources=[source()],
        )
        self.assertEqual(handoff["remaining_budget"]["requests_remaining"], 0)
        self.assertTrue(handoff["budget_exhausted"])

    def test_documents_at_the_round_limit_mark_the_budget_exhausted(self):
        root = layout_root(self.tmp)
        handoff = run_round(
            root, round_no=1, actions=[],
            config={"iteration_policy": {"max_documents_per_round": 1}},
            fetcher=text_fetcher(FEED_ONE), sources=[source()],
        )
        self.assertEqual(handoff["remaining_budget"]["documents_this_round"], 1)
        self.assertTrue(handoff["budget_exhausted"])


class TestGateOrdering(TempCase):
    def test_calls_are_reachability_then_robots_then_collection(self):
        # Asserted exactly, because the ordering *is* invariant 1: no
        # collection request may precede the gate's decision.
        root = layout_root(self.tmp)
        fetcher = site_fetcher()

        handoff = run_round(
            root, round_no=1, actions=[],
            config={"iteration_policy": {}},
            fetcher=fetcher, sources=[discovered_source()],
        )

        self.assertEqual(fetcher.calls, [
            "https://e.org",
            "https://e.org/robots.txt",
            "https://e.org",
        ])
        self.assertEqual(len(handoff["new_documents"]), 1)


class TestInvalidSourceRecords(TempCase):
    def test_non_mapping_source_is_rejected_without_crashing(self):
        root = layout_root(self.tmp)
        fetcher = text_fetcher(FEED_ONE)

        handoff = run_round(
            root, round_no=1, actions=[], config={"iteration_policy": {}},
            fetcher=fetcher, sources=[123],
        )

        self.assertEqual(handoff["new_documents"], [])
        self.assertEqual(fetcher.calls, [])
        rejected = [item for item in handoff["actions_rejected"]
                    if item.get("kind") == "source"]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(selected_records(root), [])

    def test_source_failing_the_schema_is_rejected_and_not_persisted(self):
        root = layout_root(self.tmp)
        fetcher = text_fetcher(FEED_ONE)

        handoff = run_round(
            root, round_no=1, actions=[], config={"iteration_policy": {}},
            fetcher=fetcher, sources=[source(source_id=123)],
        )

        self.assertEqual(handoff["new_documents"], [])
        self.assertEqual(fetcher.calls, [])
        rejected = [item for item in handoff["actions_rejected"]
                    if item.get("kind") == "source"]
        self.assertEqual(len(rejected), 1)
        self.assertIn("source_id", rejected[0]["reason"])
        self.assertEqual(selected_records(root), [])

    def test_one_bad_source_does_not_discard_the_good_one(self):
        root = layout_root(self.tmp)
        handoff = run_round(
            root, round_no=1, actions=[], config={"iteration_policy": {}},
            fetcher=text_fetcher(FEED_ONE),
            sources=[source(source_id=123), source(source_id="src_good")],
        )

        self.assertEqual(len(handoff["new_documents"]), 1)
        self.assertEqual([record["source_id"] for record in selected_records(root)],
                         ["src_good"])


GOOD_DOCUMENT = {
    "document_id": "doc_good",
    "source_id": "src_feed",
    "canonical_url": "https://e.org/a1",
    "crawl_status": "FETCHED",
    "retention_mode": "reference",
    "attempt_count": 0,
}


class TestInvalidDocumentRecords(TempCase):
    def test_a_bad_document_is_skipped_while_the_good_one_persists(self):
        root = layout_root(self.tmp)
        bad = {"document_id": "doc_bad"}  # missing required fields

        with mock.patch("scripts.research_loop.collect",
                        return_value=[bad, dict(GOOD_DOCUMENT)]):
            handoff = run_round(
                root, round_no=2, actions=[], config={"iteration_policy": {}},
                fetcher=text_fetcher(FEED_ONE), sources=[source()],
            )

        stored = read_records(root / "documents" / "documents.jsonl")
        self.assertEqual([record["document_id"] for record in stored], ["doc_good"])
        self.assertEqual([record["document_id"] for record in handoff["new_documents"]],
                         ["doc_good"])
        rejected = [item for item in handoff["actions_rejected"]
                    if item.get("kind") == "document"]
        self.assertEqual(len(rejected), 1)
        self.assertIn("doc_bad", json.dumps(rejected[0]))


class TestCollectorMisconfiguration(TempCase):
    def test_offhost_feed_is_rejected_and_the_round_continues(self):
        # The gate decided about e.org; the feed address points elsewhere.
        # collect refuses before any fetch, and the round still collects from
        # the next source.
        root = layout_root(self.tmp)
        fetcher = text_fetcher(FEED_ONE)

        handoff = run_round(
            root, round_no=1, actions=[], config={"iteration_policy": {}},
            fetcher=fetcher,
            sources=[
                source(source_id="src_bad", feed_url="https://evil.example/feed"),
                source(source_id="src_good"),
            ],
        )

        self.assertEqual(fetcher.calls, ["https://e.org/feed"])
        rejected = [item for item in handoff["actions_rejected"]
                    if item.get("source_id") == "src_bad"]
        self.assertEqual(len(rejected), 1)
        self.assertIn("evil.example", rejected[0]["reason"])
        self.assertEqual(len(handoff["new_documents"]), 1)


class TestRequestCounterSurvivesFailure(TempCase):
    def test_requests_used_is_persisted_when_the_round_fails_later(self):
        root = layout_root(self.tmp)
        with mock.patch("scripts.research_loop.save_documents",
                        side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                run_round(root, round_no=1, actions=[],
                          config={"iteration_policy": {}},
                          fetcher=text_fetcher(FEED_ONE), sources=[source()])

        # The fetch happened, so the budget spent it even though the round
        # failed at the write. A crash cannot return the requests.
        self.assertEqual(load_state(root)["requests_used"], 1)


class TestCrossRoundDedup(TempCase):
    def test_the_same_article_is_not_archived_twice(self):
        root = layout_root(self.tmp)
        config = {"iteration_policy": {}}

        first = run_round(root, round_no=1, actions=[], config=config,
                          fetcher=text_fetcher(FEED_ONE), sources=[source()])
        second = run_round(root, round_no=2, actions=[], config=config,
                           fetcher=text_fetcher(FEED_ONE), sources=[source()])

        stored = read_records(root / "documents" / "documents.jsonl")
        self.assertEqual(len(stored), 1)
        self.assertEqual(len(first["new_documents"]), 1)
        self.assertEqual(second["new_documents"], [])


class TestRoundLimit(TempCase):
    def test_a_round_beyond_max_rounds_does_not_collect(self):
        root = layout_root(self.tmp)
        fetcher = text_fetcher(FEED_ONE)

        handoff = run_round(
            root, round_no=2, actions=[],
            config={"iteration_policy": {"max_rounds": 1}},
            fetcher=fetcher, sources=[source()],
        )

        self.assertTrue(handoff["budget_exhausted"])
        self.assertEqual(fetcher.calls, [])
        self.assertEqual(handoff["remaining_budget"]["rounds_remaining"], 0)
        self.assertTrue(any(item.get("kind") == "round"
                            for item in handoff["actions_rejected"]))

    def test_a_replayed_round_is_rejected(self):
        root = layout_root(self.tmp)
        handoff = run_round(root, round_no=1, actions=[],
                            config={"iteration_policy": {}},
                            fetcher=text_fetcher(FEED_ONE), sources=[source()])
        apply(root, findings=[], handoff=handoff)

        with self.assertRaises(ValueError):
            run_round(root, round_no=1, actions=[],
                      config={"iteration_policy": {}},
                      fetcher=text_fetcher(FEED_ONE), sources=[source()])

    def test_the_round_after_the_recorded_one_is_allowed(self):
        root = layout_root(self.tmp)
        handoff = run_round(root, round_no=1, actions=[],
                            config={"iteration_policy": {}},
                            fetcher=text_fetcher(FEED_ONE), sources=[source()])
        apply(root, findings=[], handoff=handoff)

        next_handoff = run_round(root, round_no=2, actions=[],
                                 config={"iteration_policy": {}},
                                 fetcher=text_fetcher(FEED_ONE), sources=[source()])
        self.assertEqual(next_handoff["round"], 2)


class TestAllowedPathsInTheRound(TempCase):
    def test_redirected_page_outside_allowed_paths_is_not_archived(self):
        root = layout_root(self.tmp)
        handoff = run_round(
            root, round_no=1, actions=[], config={"iteration_policy": {}},
            fetcher=text_fetcher(PAGE, final_url="https://e.org/secret/page"),
            sources=[source(source_id="src_page", access_method="web",
                            feed_url=None, allowed_paths=["/public"])],
        )
        self.assertEqual(handoff["new_documents"], [])

    def test_redirected_page_inside_allowed_paths_is_archived(self):
        root = layout_root(self.tmp)
        handoff = run_round(
            root, round_no=1, actions=[], config={"iteration_policy": {}},
            fetcher=text_fetcher(PAGE, final_url="https://e.org/public/page"),
            sources=[source(source_id="src_page", access_method="web",
                            feed_url=None, allowed_paths=["/public"])],
        )
        self.assertEqual(len(handoff["new_documents"]), 1)


class TestRecordedDecisionBoundToItsHost(TempCase):
    def test_a_decision_recorded_for_another_base_url_is_rechecked(self):
        root = layout_root(self.tmp)
        first = run_round(root, round_no=1, actions=[],
                          config={"iteration_policy": {}},
                          fetcher=site_fetcher(), sources=[discovered_source()])
        recorded = selected_records(root)[0]
        self.assertEqual(recorded["domain"], "e.org")
        self.assertTrue(recorded["checked_at"])

        tampered = dict(recorded, base_url="https://evil.example")
        fetcher = routed_fetcher({
            "https://evil.example": {
                "outcome": "ok", "status": 200, "text": FEED_ONE},
            "https://evil.example/robots.txt": {
                "outcome": "ok", "status": 200, "text": ROBOTS_ALLOW},
        })
        run_round(root, round_no=2, actions=[],
                  config={"iteration_policy": {}},
                  fetcher=fetcher, sources=[tampered])

        # The carried checked_at was made for e.org; it does not authorise a
        # fetch of evil.example without a new decision.
        self.assertIn("https://evil.example/robots.txt", fetcher.calls)


class TestSelectionStatus(TempCase):
    def test_a_non_allowed_outcome_clears_a_stale_selected_status(self):
        root = layout_root(self.tmp)
        run_round(root, round_no=1, actions=[], config={"iteration_policy": {}},
                  fetcher=missing_fetcher(),
                  sources=[source(compliance_status="BLOCKED")])
        self.assertEqual(selected_records(root)[0]["selection_status"], "pending")

    def test_an_excluded_source_is_not_promoted_to_selected(self):
        root = layout_root(self.tmp)
        run_round(root, round_no=1, actions=[], config={"iteration_policy": {}},
                  fetcher=missing_fetcher(),
                  sources=[source(selection_status="excluded")])
        self.assertEqual(selected_records(root)[0]["selection_status"], "excluded")


class TestNonMappingAction(TempCase):
    def test_a_non_mapping_action_is_rejected_instead_of_crashing(self):
        root = project(self.tmp, scope=CONFIRMED_SCOPE)
        handoff = run_round(
            root, round_no=1, actions=["not-an-action"],
            config={"iteration_policy": {}}, fetcher=missing_fetcher(),
            sources=[],
        )
        self.assertEqual(len(handoff["actions_rejected"]), 1)
        self.assertEqual(handoff["actions_rejected"][0]["rejected_as"],
                         "invalid_action")

        pending = json.loads(
            (root / "state" / "pending_actions.json").read_text("utf-8"))
        self.assertEqual(len(pending["rejected"]), 1)


# -- apply_results: the agent boundary --------------------------------------


FINDING = {
    "finding_id": "find_1",
    "statement": "Agent tools need runtime isolation.",
    "confidence": 0.8,
    "importance": 0.7,
    "evidence": [{"document_id": "doc_1", "evidence_type": "supports"}],
}


def apply(root, *, findings, decision="CONTINUE", observations=None,
          next_actions=None, handoff=None):
    return apply_results(
        root,
        handoff=handoff if handoff is not None else {"round": 1, "new_documents": []},
        observations=observations or {},
        findings=findings,
        next_actions=next_actions or [],
        decision=decision,
    )


class TestApplyResultsBoundary(TempCase):
    def test_invalid_finding_is_skipped_without_aborting_the_round(self):
        root = layout_root(self.tmp)
        bad = dict(FINDING, finding_id="find_bad", statement="bad", confidence=2)

        result = apply(root, findings=[dict(FINDING), bad])

        stored = read_records(root / "findings" / "findings.jsonl")
        self.assertEqual([item["finding_id"] for item in stored], ["find_1"])
        self.assertEqual(len(result["rejected_findings"]), 1)

        iteration = json.loads((root / "iterations" / "round-001.json").read_text("utf-8"))
        self.assertEqual(len(iteration["rejected_findings"]), 1)
        self.assertIn("confidence", iteration["rejected_findings"][0]["error"])

    def test_score_fields_are_stripped_and_the_weight_is_normalised(self):
        root = layout_root(self.tmp)
        scored = dict(FINDING, relevance_score=100, evidence_score=100,
                      novelty_score=100, guidance_score=100)

        apply(root, findings=[scored])

        stored = read_records(root / "findings" / "findings.jsonl")[0]
        for field in ("relevance_score", "evidence_score",
                      "novelty_score", "guidance_score"):
            self.assertNotIn(field, stored)
        self.assertIn("weight", stored)
        self.assertGreaterEqual(stored["weight"], 0.0)
        self.assertLessEqual(stored["weight"], 1.0)
        self.assertAlmostEqual(stored["weight"], 1.0)
        validate_record("finding", stored)

    def test_an_unscored_finding_is_not_given_a_weight(self):
        # 0 would read as "scored worst" in any ordering; an absent weight
        # honestly says the agent has not scored this finding yet.
        root = layout_root(self.tmp)
        apply(root, findings=[dict(FINDING)])
        stored = read_records(root / "findings" / "findings.jsonl")[0]
        self.assertNotIn("weight", stored)

    def test_all_none_scores_are_still_unscored(self):
        # None is "not scored yet", not a score of zero: four explicit Nones
        # must behave like four absent keys.
        root = layout_root(self.tmp)
        apply(root, findings=[dict(
            FINDING, relevance_score=None, evidence_score=None,
            novelty_score=None, guidance_score=None,
        )])
        stored = read_records(root / "findings" / "findings.jsonl")[0]
        self.assertNotIn("weight", stored)

    def test_an_out_of_range_score_rejects_only_that_finding(self):
        # compute_weight is the only place the 0-100 bound can be enforced,
        # and its ValueError is a backfill problem, not a round failure.
        root = layout_root(self.tmp)
        result = apply(root, findings=[
            dict(FINDING, relevance_score=101),
            dict(FINDING, finding_id="find_2", statement="second"),
        ])
        stored = read_records(root / "findings" / "findings.jsonl")
        self.assertEqual([item["finding_id"] for item in stored], ["find_2"])
        self.assertEqual(len(result["rejected_findings"]), 1)
        self.assertIn("relevance_score", result["rejected_findings"][0]["error"])

    def test_state_keeps_existing_fields(self):
        root = layout_root(self.tmp)
        save_state(root, {
            "round": 0, "status": "RUNNING",
            "created_at": "2026-01-01T00:00:00+00:00",
            "archive_root": "archive", "path": str(root),
        })

        apply(root, findings=[])

        state = load_state(root)
        self.assertEqual(state["created_at"], "2026-01-01T00:00:00+00:00")
        self.assertEqual(state["archive_root"], "archive")
        self.assertEqual(state["path"], str(root))
        self.assertEqual(state["round"], 1)

    def test_decision_becomes_the_state_status(self):
        root = layout_root(self.tmp)
        apply(root, findings=[], decision="STOP_COMPLETE")
        self.assertEqual(load_state(root)["status"], "STOP_COMPLETE")

    def test_the_round_state_starts_from_empty_when_the_file_is_absent(self):
        # The plan's test builds only the directory layout. load_state stays
        # strict for readers; apply_results is a writer with a starting point.
        root = layout_root(self.tmp)
        apply(root, findings=[], decision="CONTINUE")
        self.assertEqual(load_state(root)["round"], 1)

    def test_new_findings_are_merged_with_existing_ones(self):
        root = layout_root(self.tmp)
        apply(root, findings=[dict(FINDING)])

        incoming = dict(FINDING, evidence=[
            {"document_id": "doc_2", "evidence_type": "supports"},
        ])
        result = apply(root, findings=[incoming], handoff={"round": 2})

        stored = read_records(root / "findings" / "findings.jsonl")
        self.assertEqual(len(stored), 1)
        self.assertEqual(len(stored[0]["evidence"]), 2)
        self.assertEqual(result["findings_reinforced"], ["find_1"])

    def test_iteration_records_the_rounds_work(self):
        root = layout_root(self.tmp)
        apply(root, findings=[dict(FINDING)],
              observations={"knowledge_gaps": ["缺少运行时证据"]},
              next_actions=[action()])

        iteration = json.loads((root / "iterations" / "round-001.json").read_text("utf-8"))
        self.assertEqual(iteration["round"], 1)
        self.assertEqual(iteration["decision"], "CONTINUE")
        self.assertEqual(iteration["findings_added"], ["find_1"])
        self.assertEqual(iteration["findings_reinforced"], [])
        self.assertEqual(iteration["findings_conflicting"], [])
        self.assertEqual(iteration["rejected_findings"], [])
        self.assertEqual(iteration["skipped_findings"], [])
        self.assertEqual(iteration["next_actions"], [action()])
        self.assertIn("handoff", iteration)

    def test_summary_uses_the_observation_gaps(self):
        root = layout_root(self.tmp)
        apply(root, findings=[dict(FINDING)],
              observations={"knowledge_gaps": ["缺少运行时证据"]})
        summary = (root / "summaries" / "current.md").read_text("utf-8")
        self.assertIn("缺少运行时证据", summary)

    def test_apply_requires_a_round_number(self):
        root = layout_root(self.tmp)
        with self.assertRaises(ValueError):
            apply_results(root, handoff={}, observations={}, findings=[],
                          next_actions=[], decision="CONTINUE")

    def test_round_then_apply_persists_the_whole_round(self):
        root = layout_root(self.tmp)
        handoff = run_round(root, round_no=1, actions=[],
                            config={"iteration_policy": {}},
                            fetcher=text_fetcher(FEED_ONE), sources=[source()])
        document_id = handoff["new_documents"][0]["document_id"]
        finding = dict(FINDING, evidence=[
            {"document_id": document_id, "evidence_type": "supports"},
        ])

        result = apply(root, findings=[finding], handoff=handoff)

        manifest = rebuild_manifest(root)
        self.assertEqual(result["decision"], "CONTINUE")
        self.assertEqual(manifest["document_count"], 1)
        self.assertEqual(manifest["finding_count"], 1)
        self.assertEqual(manifest["round_count"], 1)


# -- CLI --------------------------------------------------------------------


class TestCli(TempCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_check_env_exits_zero_and_writes_nothing(self):
        old = os.getcwd()
        os.chdir(self.tmp)
        try:
            code, out, _ = self.run_main(["--check-env"])
        finally:
            os.chdir(old)
        self.assertEqual(code, 0)
        self.assertIn("Python", out)
        self.assertIn("LLM", out)
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_round_prints_a_json_handoff(self):
        root = layout_root(self.tmp)
        actions_path = self.tmp / "actions.json"
        actions_path.write_text("[]", encoding="utf-8")

        code, out, _ = self.run_main([
            "--root", str(root), "--round", "1", "--actions", str(actions_path),
        ])

        self.assertEqual(code, 0)
        handoff = json.loads(out)
        self.assertEqual(handoff["round"], 1)
        self.assertIn("new_documents", handoff)

    def test_apply_prints_a_json_result(self):
        root = layout_root(self.tmp)
        results_path = self.tmp / "results.json"
        results_path.write_text(json.dumps({
            "handoff": {"round": 1, "new_documents": []},
            "observations": {},
            "findings": [],
            "next_actions": [],
            "decision": "CONTINUE",
        }), encoding="utf-8")

        code, out, _ = self.run_main(["--root", str(root), "--apply", str(results_path)])

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["decision"], "CONTINUE")
        self.assertTrue((root / "iterations" / "round-001.json").exists())

    def test_invalid_arguments_exit_non_zero(self):
        with self.assertRaises(SystemExit) as caught:
            with redirect_stderr(io.StringIO()):
                main(["--root", str(self.tmp)])
        self.assertNotEqual(caught.exception.code, 0)

    def test_apply_errors_go_to_stderr_and_the_exit_code_is_non_zero(self):
        root = layout_root(self.tmp)
        results_path = self.tmp / "results.json"
        results_path.write_text(json.dumps({
            "handoff": {"round": 1},
            "observations": {},
            "findings": [],
            "next_actions": [],
            "decision": "NOT_A_STATE",
        }), encoding="utf-8")

        code, out, err = self.run_main(["--root", str(root), "--apply", str(results_path)])

        self.assertEqual(code, 1)
        self.assertIn("error", err)
        self.assertEqual(out, "")
        self.assertFalse((root / "iterations" / "round-001.json").exists())


if __name__ == "__main__":
    unittest.main()
