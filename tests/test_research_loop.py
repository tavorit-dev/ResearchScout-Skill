# tests/test_research_loop.py
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from scripts.archive import (load_state, rebuild_manifest, save_documents,
                             save_findings, save_state)
from scripts.lib.jsonl import read_records
from scripts.lib.paths import ensure_research_layout
from scripts.lib.validate import validate_record
from scripts.planner import init_research
from scripts.research_loop import apply_results, check_env, main, run_round

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

CONFIRMED_SCOPE = {
    "core": ["Agent Security"],
    "related": ["沙箱隔离"],
    "excluded": ["GPU 性能优化"],
    "confirmed": True,
}


def source(**overrides):
    """Return one verified, already-permitted feed source."""
    record = {
        "source_id": "src_feed",
        "source_name": "e.org",
        "base_url": "https://e.org",
        "access_method": "rss",
        "feed_url": "https://e.org/feed",
        "compliance_status": "ALLOWED",
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
