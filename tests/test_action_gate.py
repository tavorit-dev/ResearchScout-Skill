# tests/test_action_gate.py
import unittest
from scripts.action_gate import evaluate_action, check_scope, scope_list_of

SCOPE = {
    "core": ["Agent 工具调用安全", "Agent 权限控制"],
    "related": ["Prompt Injection", "沙箱隔离"],
    "excluded": ["GPU 性能优化", "通用云计算成本分析"],
    "confirmed": True,
}


def action(**kw):
    base = {"action_id": "act_1", "type": "deepen", "topic": "Agent 权限控制",
            "reason": "缺少实现细节", "priority": 90, "time_window_days": 180}
    base.update(kw)
    return base


class TestCheckScope(unittest.TestCase):
    def test_core_accepted(self):
        ok, _ = check_scope(action(topic="Agent 权限控制"), SCOPE)
        self.assertTrue(ok)

    def test_excluded_rejected(self):
        ok, reason = check_scope(action(topic="GPU 性能优化"), SCOPE)
        self.assertFalse(ok)
        self.assertIn("excluded", reason)

    def test_related_without_justification_rejected(self):
        ok, _ = check_scope(action(topic="沙箱隔离"), SCOPE)
        self.assertFalse(ok)

    def test_related_with_justification_accepted(self):
        ok, _ = check_scope(
            action(topic="沙箱隔离", scope_justification="运行时防护的基础机制"), SCOPE
        )
        self.assertTrue(ok)

    def test_out_of_scope_topic_rejected(self):
        ok, reason = check_scope(action(topic="完全无关的主题"), SCOPE)
        self.assertFalse(ok)
        self.assertIn("out of scope", reason)


class TestEvaluateAction(unittest.TestCase):
    def test_requires_time_window(self):
        result = evaluate_action(action(time_window_days=None, type="backtrack"),
                                 scope=SCOPE, budget={"remaining_documents": 100},
                                 seen_topics=[])
        self.assertFalse(result["accepted"])

    def test_verify_allows_null_window(self):
        result = evaluate_action(action(time_window_days=None, type="verify", topic="Agent 权限控制"),
                                 scope=SCOPE, budget={"remaining_documents": 100},
                                 seen_topics=[])
        self.assertTrue(result["accepted"])

    def test_below_priority_threshold_rejected(self):
        result = evaluate_action(action(priority=10), scope=SCOPE,
                                 budget={"remaining_documents": 100}, seen_topics=[])
        self.assertFalse(result["accepted"])

    def test_duplicate_topic_rejected(self):
        result = evaluate_action(action(), scope=SCOPE,
                                 budget={"remaining_documents": 100},
                                 seen_topics=["Agent 权限控制"])
        self.assertFalse(result["accepted"])

    def test_excluded_recorded_as_candidate_topic(self):
        result = evaluate_action(action(topic="GPU 性能优化"), scope=SCOPE,
                                 budget={"remaining_documents": 100}, seen_topics=[])
        self.assertEqual(result["rejected_as"], "candidate_topic")

    def test_budget_exhausted_rejected(self):
        result = evaluate_action(action(), scope=SCOPE,
                                 budget={"remaining_documents": 0}, seen_topics=[])
        self.assertFalse(result["accepted"])


def unconfirmed(**kw):
    """Return SCOPE without the confirmation flag (spec 7.8, V6)."""
    scope = {name: list(values) for name, values in SCOPE.items() if name != "confirmed"}
    scope.update(kw)
    return scope


class TestUnconfirmedScope(unittest.TestCase):
    """The path spec 7.8 names explicitly: a suggestion may not be enforced.

    ``research.yaml`` has no ``confirmed`` field by design (the schema forbids
    unknown properties), so a caller that builds a scope dict from that file
    passes a scope whose ``confirmed`` is absent. That is the common mistake
    this suite pins down.
    """

    def test_check_scope_rejects_scope_without_confirmed_key(self):
        ok, reason = check_scope(action(), unconfirmed())
        self.assertFalse(ok)
        self.assertIn("unconfirmed scope", reason)

    def test_evaluate_action_rejects_scope_without_confirmed_key(self):
        result = evaluate_action(action(), scope=unconfirmed(),
                                 budget={"remaining_documents": 100}, seen_topics=[])
        self.assertFalse(result["accepted"])
        self.assertEqual(result["rejected_as"], "unconfirmed_scope")

    def test_check_scope_rejects_confirmed_false(self):
        ok, reason = check_scope(action(), unconfirmed(confirmed=False))
        self.assertFalse(ok)
        self.assertIn("unconfirmed scope", reason)

    def test_evaluate_action_rejects_confirmed_false(self):
        result = evaluate_action(action(), scope=unconfirmed(confirmed=False),
                                 budget={"remaining_documents": 100}, seen_topics=[])
        self.assertEqual(result["rejected_as"], "unconfirmed_scope")

    def test_truthy_non_bool_confirmed_is_not_confirmation(self):
        # Strict identity: 1 and "yes" are not True. A truthiness check would
        # read a caller's stray value as a user confirmation.
        for value in (1, "yes", [1]):
            with self.subTest(confirmed=value):
                ok, reason = check_scope(action(), unconfirmed(confirmed=value))
                self.assertFalse(ok)
                self.assertIn("unconfirmed scope", reason)
                result = evaluate_action(action(), scope=unconfirmed(confirmed=value),
                                         budget={"remaining_documents": 100},
                                         seen_topics=[])
                self.assertEqual(result["rejected_as"], "unconfirmed_scope")

    def test_unconfirmed_scope_outranks_every_other_rejection(self):
        # An action that would also fail budget, priority, duplication and the
        # time window still reports the unconfirmed scope first: the scope is
        # not usable as a constraint at all, so its verdict is the only one
        # that can be trusted.
        result = evaluate_action(
            action(topic="GPU 性能优化", priority=0, time_window_days=None, type="backtrack"),
            scope=unconfirmed(), budget={"remaining_documents": 0},
            seen_topics=["GPU 性能优化"],
        )
        self.assertEqual(result["rejected_as"], "unconfirmed_scope")


class TestWhitespaceJustification(unittest.TestCase):
    """spec 7.8 requires a *non-empty* justification for related topics.

    A blank string is not an explanation, and ``if not value`` would accept
    it, turning a missing justification into a passing form check.
    """

    def test_whitespace_justification_rejected(self):
        ok, reason = check_scope(action(topic="沙箱隔离", scope_justification="   "), SCOPE)
        self.assertFalse(ok)
        self.assertIn("scope_justification", reason)

    def test_empty_justification_rejected(self):
        ok, _ = check_scope(action(topic="沙箱隔离", scope_justification=""), SCOPE)
        self.assertFalse(ok)

    def test_newline_only_justification_rejected(self):
        ok, _ = check_scope(action(topic="沙箱隔离", scope_justification="\n\t "), SCOPE)
        self.assertFalse(ok)

    def test_non_string_justification_rejected(self):
        ok, _ = check_scope(action(topic="沙箱隔离", scope_justification=123), SCOPE)
        self.assertFalse(ok)

    def test_related_without_justification_is_candidate_topic(self):
        result = evaluate_action(action(topic="沙箱隔离"), scope=SCOPE,
                                 budget={"remaining_documents": 100}, seen_topics=[])
        self.assertFalse(result["accepted"])
        self.assertEqual(result["rejected_as"], "candidate_topic")


class TestBudget(unittest.TestCase):
    def test_budget_available_accepted(self):
        result = evaluate_action(action(), scope=SCOPE,
                                 budget={"remaining_documents": 100}, seen_topics=[])
        self.assertTrue(result["accepted"])
        self.assertIsNone(result["rejected_as"])

    def test_budget_exhausted_rejected_as_budget(self):
        result = evaluate_action(action(), scope=SCOPE,
                                 budget={"remaining_documents": 0}, seen_topics=[])
        self.assertFalse(result["accepted"])
        self.assertEqual(result["rejected_as"], "budget")
        self.assertTrue(result["reason"])

    def test_missing_budget_key_is_treated_as_exhausted(self):
        # A caller that never worked out the remaining budget cannot claim
        # there is room for another action; unknown means no.
        result = evaluate_action(action(), scope=SCOPE, budget={}, seen_topics=[])
        self.assertEqual(result["rejected_as"], "budget")

    def test_negative_budget_rejected(self):
        result = evaluate_action(action(), scope=SCOPE,
                                 budget={"remaining_documents": -1}, seen_topics=[])
        self.assertEqual(result["rejected_as"], "budget")


class TestTimeWindow(unittest.TestCase):
    def test_zero_window_rejected(self):
        # 0 is "no window", not "a window of zero days"; ACTION_TIME_WINDOWS
        # only lets verify use None, and the schema's minimum is 1.
        result = evaluate_action(action(time_window_days=0), scope=SCOPE,
                                 budget={"remaining_documents": 100}, seen_topics=[])
        self.assertFalse(result["accepted"])
        self.assertEqual(result["rejected_as"], "missing_time_window")

    def test_negative_window_rejected(self):
        result = evaluate_action(action(time_window_days=-30), scope=SCOPE,
                                 budget={"remaining_documents": 100}, seen_topics=[])
        self.assertEqual(result["rejected_as"], "missing_time_window")

    def test_boolean_window_is_not_a_day_count(self):
        result = evaluate_action(action(time_window_days=True), scope=SCOPE,
                                 budget={"remaining_documents": 100}, seen_topics=[])
        self.assertEqual(result["rejected_as"], "missing_time_window")

    def test_missing_window_key_falls_back_to_the_type_default(self):
        raw = action()
        del raw["time_window_days"]
        result = evaluate_action(raw, scope=SCOPE,
                                 budget={"remaining_documents": 100}, seen_topics=[])
        self.assertTrue(result["accepted"])

    def test_deepen_default_window_is_180_days(self):
        self.assertEqual(
            evaluate_action(action(time_window_days=180), scope=SCOPE,
                            budget={"remaining_documents": 100}, seen_topics=[])["accepted"],
            True,
        )

    def test_null_window_rejected_for_deepen(self):
        result = evaluate_action(action(time_window_days=None, type="deepen"),
                                 scope=SCOPE, budget={"remaining_documents": 100},
                                 seen_topics=[])
        self.assertEqual(result["rejected_as"], "missing_time_window")

    def test_null_window_rejected_for_broaden(self):
        result = evaluate_action(action(time_window_days=None, type="broaden"),
                                 scope=SCOPE, budget={"remaining_documents": 100},
                                 seen_topics=[])
        self.assertEqual(result["rejected_as"], "missing_time_window")

    def test_verify_without_window_key_is_accepted(self):
        # verify's default window is None, so a missing key is not a refusal.
        raw = action(type="verify")
        del raw["time_window_days"]
        result = evaluate_action(raw, scope=SCOPE,
                                 budget={"remaining_documents": 100}, seen_topics=[])
        self.assertTrue(result["accepted"])


class TestDecisionOrdering(unittest.TestCase):
    """Pin the documented order in which simultaneous failures are reported."""

    def test_scope_outranks_budget(self):
        result = evaluate_action(action(topic="GPU 性能优化"), scope=SCOPE,
                                 budget={"remaining_documents": 0}, seen_topics=[])
        self.assertEqual(result["rejected_as"], "candidate_topic")

    def test_budget_outranks_duplicate_and_priority(self):
        result = evaluate_action(action(priority=0), scope=SCOPE,
                                 budget={"remaining_documents": 0},
                                 seen_topics=["Agent 权限控制"])
        self.assertEqual(result["rejected_as"], "budget")

    def test_duplicate_outranks_priority(self):
        result = evaluate_action(action(priority=0), scope=SCOPE,
                                 budget={"remaining_documents": 100},
                                 seen_topics=["Agent 权限控制"])
        self.assertEqual(result["rejected_as"], "duplicate")

    def test_duplicate_outranks_time_window(self):
        result = evaluate_action(action(time_window_days=None, type="backtrack"),
                                 scope=SCOPE, budget={"remaining_documents": 100},
                                 seen_topics=["Agent 权限控制"])
        self.assertEqual(result["rejected_as"], "duplicate")

    def test_priority_outranks_time_window(self):
        result = evaluate_action(action(priority=0, time_window_days=0), scope=SCOPE,
                                 budget={"remaining_documents": 100}, seen_topics=[])
        self.assertEqual(result["rejected_as"], "low_priority")

    def test_core_out_of_scope_is_candidate_topic(self):
        result = evaluate_action(action(topic="完全无关的主题"), scope=SCOPE,
                                 budget={"remaining_documents": 100}, seen_topics=[])
        self.assertEqual(result["rejected_as"], "candidate_topic")


class TestExactTopicMatching(unittest.TestCase):
    """Matching is exact equality, not substring.

    Substring matching would let "GPU 性能优化与调度" inherit the excluded
    verdict of "GPU 性能优化" -- an inference the spec does not make, since it
    only ever lists topics as list members.
    """

    def test_superset_of_excluded_topic_is_not_excluded(self):
        ok, reason = check_scope(action(topic="GPU 性能优化与调度"), SCOPE)
        self.assertFalse(ok)
        self.assertIn("out of scope", reason)
        self.assertNotIn("excluded", reason)

    def test_superset_of_core_topic_is_not_core(self):
        ok, _ = check_scope(action(topic="Agent 权限控制的实现细节"), SCOPE)
        self.assertFalse(ok)

    def test_substring_of_core_topic_is_not_core(self):
        ok, _ = check_scope(action(topic="权限控制"), SCOPE)
        self.assertFalse(ok)

    def test_candidate_topic_is_not_an_excluded_topic(self):
        # V6: an out-of-scope topic is recorded as its own candidate research
        # topic, which is a different outcome from a deliberately excluded one.
        ok, reason = check_scope(action(topic="完全无关的主题"), SCOPE)
        self.assertFalse(ok)
        self.assertNotIn("excluded", reason)


class TestResultShape(unittest.TestCase):
    def test_accepted_result_has_empty_rejection(self):
        result = evaluate_action(action(), scope=SCOPE,
                                 budget={"remaining_documents": 100}, seen_topics=[])
        # ``scope_list`` was added by Task 16 ruling 4 so downstream code can
        # tell a candidate LEAD (related) from a candidate research TOPIC
        # (excluded/none) without parsing ``reason``. This is the one existing
        # assertion the new key necessarily changes; the plan's 11 verbatim
        # tests do not inspect the key set.
        self.assertEqual(set(result), {"accepted", "reason", "rejected_as", "scope_list"})
        self.assertIs(result["accepted"], True)
        self.assertIsNone(result["rejected_as"])
        self.assertTrue(result["reason"])

    def test_every_rejection_has_a_reason_and_a_label(self):
        cases = [
            evaluate_action(action(), scope=unconfirmed(),
                            budget={"remaining_documents": 100}, seen_topics=[]),
            evaluate_action(action(topic="GPU 性能优化"), scope=SCOPE,
                            budget={"remaining_documents": 100}, seen_topics=[]),
            evaluate_action(action(), scope=SCOPE,
                            budget={"remaining_documents": 100},
                            seen_topics=["Agent 权限控制"]),
            evaluate_action(action(), scope=SCOPE,
                            budget={"remaining_documents": 0}, seen_topics=[]),
            evaluate_action(action(priority=0), scope=SCOPE,
                            budget={"remaining_documents": 100}, seen_topics=[]),
            evaluate_action(action(time_window_days=0), scope=SCOPE,
                            budget={"remaining_documents": 100}, seen_topics=[]),
        ]
        for result in cases:
            with self.subTest(rejected_as=result["rejected_as"]):
                self.assertFalse(result["accepted"])
                self.assertTrue(result["reason"].strip())
                self.assertIn(result["rejected_as"], {
                    "unconfirmed_scope", "candidate_topic", "duplicate",
                    "budget", "low_priority", "missing_time_window",
                })

    def test_min_priority_override_is_honoured(self):
        result = evaluate_action(action(priority=10), scope=SCOPE,
                                 budget={"remaining_documents": 100}, seen_topics=[],
                                 min_priority=5)
        self.assertTrue(result["accepted"])

    def test_unusable_min_priority_falls_back_to_the_configured_floor(self):
        # A string floor would otherwise crash the comparison and abort the
        # whole evaluation instead of admitting or refusing one action.
        result = evaluate_action(action(priority=90), scope=SCOPE,
                                 budget={"remaining_documents": 100}, seen_topics=[],
                                 min_priority="60")
        self.assertTrue(result["accepted"])
        self.assertFalse(evaluate_action(action(priority=10), scope=SCOPE,
                                         budget={"remaining_documents": 100},
                                         seen_topics=[], min_priority="60")["accepted"])

    def test_missing_priority_is_rejected_not_raised(self):
        raw = action()
        del raw["priority"]
        result = evaluate_action(raw, scope=SCOPE,
                                 budget={"remaining_documents": 100}, seen_topics=[])
        self.assertEqual(result["rejected_as"], "low_priority")

    def test_non_string_topic_is_out_of_scope_not_a_crash(self):
        result = evaluate_action(action(topic=None), scope=SCOPE,
                                 budget={"remaining_documents": 100}, seen_topics=[])
        self.assertEqual(result["rejected_as"], "candidate_topic")

    def test_seen_topics_ignores_entries_that_are_not_topics(self):
        result = evaluate_action(action(), scope=SCOPE,
                                 budget={"remaining_documents": 100},
                                 seen_topics=[None, 5])
        self.assertTrue(result["accepted"])


class TestTimeWindowDefaults(unittest.TestCase):
    def test_unknown_action_type_without_window_is_refused(self):
        # The four known types are the only ones with a default window, and
        # "no default" must not be read as "no window needed" -- that would
        # hand an unrecognised type the unlimited reach reserved for verify.
        raw = action(type="weird")
        del raw["time_window_days"]
        result = evaluate_action(raw, scope=SCOPE,
                                 budget={"remaining_documents": 100}, seen_topics=[])
        self.assertEqual(result["rejected_as"], "missing_time_window")

    def test_unknown_action_type_with_a_window_is_accepted(self):
        result = evaluate_action(action(type="weird", time_window_days=30), scope=SCOPE,
                                 budget={"remaining_documents": 100}, seen_topics=[])
        self.assertTrue(result["accepted"])


class TestListPrecedence(unittest.TestCase):
    def test_excluded_wins_when_a_topic_is_in_two_lists(self):
        # A topic can only be excluded deliberately; core membership cannot
        # override that, or editing a list would silently reopen a topic the
        # user ruled out.
        scope = {"core": ["dup"], "related": ["dup"], "excluded": ["dup"],
                 "confirmed": True}
        ok, reason = check_scope(action(topic="dup"), scope)
        self.assertFalse(ok)
        self.assertIn("excluded", reason)

    def test_core_wins_over_related(self):
        scope = {"core": ["dup"], "related": ["dup"], "excluded": [],
                 "confirmed": True}
        ok, reason = check_scope(action(topic="dup"), scope)
        self.assertTrue(ok)
        self.assertIn("core", reason)

    def test_surrounding_whitespace_in_the_topic_is_ignored(self):
        ok, _ = check_scope(action(topic="  Agent 权限控制  "), SCOPE)
        self.assertTrue(ok)


class TestEmptyScopeLists(unittest.TestCase):
    def test_confirmed_scope_with_empty_lists_rejects_everything(self):
        # No scope is declared, so nothing is inside it. The gate does not
        # infer a scope to fill the gap.
        scope = {"core": [], "related": [], "excluded": [], "confirmed": True}
        ok, reason = check_scope(action(), scope)
        self.assertFalse(ok)
        self.assertIn("out of scope", reason)
        self.assertFalse(evaluate_action(action(), scope=scope,
                                         budget={"remaining_documents": 100},
                                         seen_topics=[])["accepted"])


class TestNonMappingArguments(unittest.TestCase):
    """A non-mapping argument is a caller bug, and it is raised as one.

    ``scope`` and ``action`` are required arguments, so a caller that passes
    ``None`` (or a list, or a string) has an integration error, not a research
    situation. Reporting it as ``unconfirmed_scope`` would disguise the bug as
    a normal rejection and blame the user for not confirming a scope that was
    never passed. These tests pin the documented Raises contract; they pin
    existing behaviour rather than driving new behaviour.
    """

    def test_check_scope_raises_on_non_mapping_scope(self):
        for bad in (None, [], "core", 0):
            with self.subTest(scope=bad):
                with self.assertRaises(AttributeError):
                    check_scope(action(), bad)

    def test_evaluate_action_raises_on_non_mapping_scope(self):
        for bad in (None, [], "core", 0):
            with self.subTest(scope=bad):
                with self.assertRaises(AttributeError):
                    evaluate_action(action(), scope=bad,
                                    budget={"remaining_documents": 100},
                                    seen_topics=[])

    def test_check_scope_raises_on_non_mapping_action(self):
        for bad in (None, [], "deepen", 0):
            with self.subTest(action=bad):
                with self.assertRaises(AttributeError):
                    check_scope(bad, SCOPE)

    def test_evaluate_action_raises_on_non_mapping_action(self):
        for bad in (None, [], "deepen", 0):
            with self.subTest(action=bad):
                with self.assertRaises(AttributeError):
                    evaluate_action(bad, scope=SCOPE,
                                    budget={"remaining_documents": 100},
                                    seen_topics=[])

    def test_non_mapping_scope_is_never_reported_as_unconfirmed(self):
        # The distinction the contract turns on: a mapping without
        # ``confirmed=True`` is a business rejection with a reason; a
        # non-mapping is a raised AttributeError. Collapsing the second into
        # the first would hide the integration error.
        self.assertFalse(check_scope(action(), unconfirmed())[0])
        with self.assertRaises(AttributeError):
            check_scope(action(), None)

    def test_unconfirmed_scope_refuses_before_a_non_mapping_action_is_read(self):
        # The scope guard runs first, so an unconfirmed scope reports its own
        # rejection even when the action is also unusable.
        result = evaluate_action(None, scope=unconfirmed(),
                                 budget={"remaining_documents": 100},
                                 seen_topics=[])
        self.assertFalse(result["accepted"])
        self.assertEqual(result["rejected_as"], "unconfirmed_scope")


class TestScopeListOf(unittest.TestCase):
    """``scope_list_of`` names which list decided, as data.

    ``rejected_as == "candidate_topic"`` collapses three outcomes that spec 7.8
    keeps apart: an ``excluded`` topic and a topic in no list are candidate
    research TOPICS, while a ``related`` topic missing its justification is a
    candidate LEAD. Downstream code must not recover that distinction by
    parsing the English ``reason``.
    """

    def test_core_member(self):
        self.assertEqual(scope_list_of(action(), SCOPE), "core")

    def test_related_member(self):
        self.assertEqual(scope_list_of(action(topic="沙箱隔离"), SCOPE), "related")

    def test_excluded_member(self):
        self.assertEqual(scope_list_of(action(topic="GPU 性能优化"), SCOPE), "excluded")

    def test_topic_in_no_list(self):
        self.assertEqual(scope_list_of(action(topic="完全无关的主题"), SCOPE), "none")

    def test_unconfirmed_scope_is_named_unconfirmed(self):
        self.assertEqual(scope_list_of(action(), unconfirmed()), "unconfirmed")

    def test_excluded_outranks_core(self):
        # Same precedence as check_scope: a deliberate exclusion cannot be
        # reopened by the topic also appearing in core.
        scope = {"core": ["dup"], "related": [], "excluded": ["dup"],
                 "confirmed": True}
        self.assertEqual(scope_list_of(action(topic="dup"), scope), "excluded")

    def test_core_outranks_related(self):
        scope = {"core": ["dup"], "related": ["dup"], "excluded": [],
                 "confirmed": True}
        self.assertEqual(scope_list_of(action(topic="dup"), scope), "core")

    def test_surrounding_whitespace_is_ignored(self):
        self.assertEqual(scope_list_of(action(topic="  Agent 权限控制  "), SCOPE), "core")

    def test_empty_topic_is_in_no_list(self):
        self.assertEqual(scope_list_of(action(topic=""), SCOPE), "none")

    def test_non_string_topic_is_in_no_list(self):
        self.assertEqual(scope_list_of(action(topic=None), SCOPE), "none")


class TestVerdictCarriesScopeList(unittest.TestCase):
    """Task 16 ruling 4: every verdict carries the machine-readable list."""

    def verdict(self, raw, scope=SCOPE):
        return evaluate_action(raw, scope=scope,
                               budget={"remaining_documents": 100},
                               seen_topics=[])

    def test_accepted_verdict_names_core(self):
        result = self.verdict(action())
        self.assertTrue(result["accepted"])
        self.assertEqual(result["scope_list"], "core")

    def test_excluded_is_a_candidate_topic(self):
        result = self.verdict(action(topic="GPU 性能优化"))
        self.assertEqual(result["rejected_as"], "candidate_topic")
        self.assertEqual(result["scope_list"], "excluded")

    def test_related_without_justification_is_a_candidate_lead(self):
        result = self.verdict(action(topic="沙箱隔离"))
        self.assertEqual(result["rejected_as"], "candidate_topic")
        # candidate_topic + related == a candidate LEAD, not a new topic.
        self.assertEqual(result["scope_list"], "related")

    def test_out_of_scope_is_a_candidate_topic(self):
        result = self.verdict(action(topic="完全无关的主题"))
        self.assertEqual(result["rejected_as"], "candidate_topic")
        self.assertEqual(result["scope_list"], "none")

    def test_unconfirmed_scope_keeps_its_label_and_scope_list(self):
        result = self.verdict(action(), scope=unconfirmed())
        self.assertEqual(result["rejected_as"], "unconfirmed_scope")
        self.assertEqual(result["scope_list"], "unconfirmed")

    def test_every_verdict_has_a_scope_list(self):
        cases = [
            self.verdict(action()),
            self.verdict(action(topic="GPU 性能优化")),
            self.verdict(action(topic="沙箱隔离")),
            self.verdict(action(topic="完全无关的主题")),
            self.verdict(action(), scope=unconfirmed()),
            evaluate_action(action(), scope=SCOPE,
                            budget={"remaining_documents": 100},
                            seen_topics=["Agent 权限控制"]),
            evaluate_action(action(), scope=SCOPE, budget={"remaining_documents": 0},
                            seen_topics=[]),
            evaluate_action(action(priority=0), scope=SCOPE,
                            budget={"remaining_documents": 100}, seen_topics=[]),
        ]
        for result in cases:
            with self.subTest(rejected_as=result["rejected_as"]):
                self.assertIn("scope_list", result)


if __name__ == "__main__":
    unittest.main()
