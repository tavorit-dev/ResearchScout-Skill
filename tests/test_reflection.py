# tests/test_reflection.py
import unittest
from scripts.reflection import merge_findings, compute_weight, summarize_state


def finding(fid, statement, doc_ids, confidence=0.9, importance=0.9):
    return {"finding_id": fid, "statement": statement, "confidence": confidence,
            "importance": importance,
            "evidence": [{"document_id": d, "evidence_type": "supports"} for d in doc_ids]}


class TestMergeFindings(unittest.TestCase):
    def test_new_finding_added(self):
        result = merge_findings([], [finding("find_1", "A", ["doc_1"])])
        self.assertEqual(len(result["added"]), 1)
        self.assertEqual(len(result["findings"]), 1)

    def test_same_statement_reinforces_evidence(self):
        current = [finding("find_1", "A", ["doc_1"])]
        incoming = [finding("find_2", "A", ["doc_2"])]
        result = merge_findings(current, incoming)
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(len(result["reinforced"]), 1)
        ids = {e["document_id"] for e in result["findings"][0]["evidence"]}
        self.assertEqual(ids, {"doc_1", "doc_2"})

    def test_contradicting_evidence_recorded(self):
        current = [finding("find_1", "A", ["doc_1"])]
        incoming = [{"finding_id": "find_2", "statement": "A", "confidence": 0.5,
                     "importance": 0.8,
                     "evidence": [{"document_id": "doc_9", "evidence_type": "contradicts"}]}]
        result = merge_findings(current, incoming)
        self.assertEqual(len(result["conflicting"]), 1)


class TestComputeWeight(unittest.TestCase):
    def test_weight_formula(self):
        weights = {"relevance": 0.35, "evidence": 0.25, "novelty": 0.20, "guidance": 0.20}
        f = {"relevance_score": 100, "evidence_score": 100,
             "novelty_score": 100, "guidance_score": 100}
        self.assertAlmostEqual(compute_weight(f, weights), 100.0)

    def test_weight_is_not_confidence(self):
        weights = {"relevance": 0.35, "evidence": 0.25, "novelty": 0.20, "guidance": 0.20}
        f = {"relevance_score": 100, "evidence_score": 100,
             "novelty_score": 100, "guidance_score": 100, "confidence": 0.1}
        self.assertAlmostEqual(compute_weight(f, weights), 100.0)


class TestSummarizeState(unittest.TestCase):
    def test_renders_findings_and_gaps(self):
        text = summarize_state([finding("find_1", "结论一", ["doc_1"])],
                               [], ["还缺实现细节"])
        self.assertIn("结论一", text)
        self.assertIn("还缺实现细节", text)


# ---------------------------------------------------------------------------
# Tests added by the implementer. The six tests above are the plan's verbatim
# tests; everything below pins the controller's four rulings plus the edge
# cases the plan's tests leave uncovered.
# ---------------------------------------------------------------------------

CONFIG_WEIGHTS = {"relevance": 0.35, "evidence": 0.25,
                  "novelty": 0.20, "guidance": 0.20}

EMPTY_MARKER = "（暂无）"

SECTION_TITLES = ("关键发现", "证据", "知识缺口", "矛盾", "下一步行动")


def evidence(doc_id, evidence_type="supports", **extra):
    item = {"document_id": doc_id, "evidence_type": evidence_type}
    item.update(extra)
    return item


def record(fid, statement, items, confidence=0.9, importance=0.9, **extra):
    item = {"finding_id": fid, "statement": statement, "confidence": confidence,
            "importance": importance, "evidence": items}
    item.update(extra)
    return item


def section(text, title):
    """Return the body of one ``## <title>`` section of a rendered summary."""
    body = text.split("## " + title, 1)[1]
    return body.split("\n## ", 1)[0]


class TestMergeEvidenceDedup(unittest.TestCase):
    """Ruling 2: evidence is keyed by ``(document_id, evidence_type)``."""

    def test_same_document_supports_and_contradicts_both_kept(self):
        current = [record("find_1", "A", [evidence("doc_1", "supports")])]
        incoming = [record("find_2", "A", [evidence("doc_1", "contradicts")])]
        result = merge_findings(current, incoming)
        pairs = {(e["document_id"], e["evidence_type"])
                 for e in result["findings"][0]["evidence"]}
        self.assertEqual(len(result["findings"][0]["evidence"]), 2)
        self.assertEqual(pairs, {("doc_1", "supports"), ("doc_1", "contradicts")})

    def test_same_pair_keeps_the_first_record(self):
        current = [record("find_1", "A",
                          [evidence("doc_1", "supports", quote="first")])]
        incoming = [record("find_2", "A",
                           [evidence("doc_1", "supports", quote="second")])]
        result = merge_findings(current, incoming)
        self.assertEqual(len(result["findings"][0]["evidence"]), 1)
        self.assertEqual(result["findings"][0]["evidence"][0]["quote"], "first")

    def test_partially_supports_is_a_distinct_pair(self):
        current = [record("find_1", "A", [evidence("doc_1", "supports")])]
        incoming = [record("find_2", "A",
                           [evidence("doc_1", "partially_supports")])]
        result = merge_findings(current, incoming)
        pairs = {(e["document_id"], e["evidence_type"])
                 for e in result["findings"][0]["evidence"]}
        self.assertEqual(pairs, {("doc_1", "supports"),
                                 ("doc_1", "partially_supports")})


class TestMergeFieldRules(unittest.TestCase):
    """Ruling 3: which value wins when a statement matches."""

    def test_finding_id_stays_with_the_existing_record(self):
        result = merge_findings([finding("find_1", "A", ["doc_1"])],
                                [finding("find_2", "A", ["doc_2"])])
        self.assertEqual(result["findings"][0]["finding_id"], "find_1")

    def test_confidence_and_importance_take_incoming_values(self):
        current = [finding("find_1", "A", ["doc_1"], confidence=0.9, importance=0.9)]
        incoming = [finding("find_2", "A", ["doc_2"], confidence=0.4, importance=0.7)]
        result = merge_findings(current, incoming)
        self.assertEqual(result["findings"][0]["confidence"], 0.4)
        self.assertEqual(result["findings"][0]["importance"], 0.7)

    def test_non_empty_prose_overrides_and_empty_keeps_existing(self):
        current = [record("find_1", "A", [evidence("doc_1")],
                          scope="旧范围", limitations="旧限制", follow_up="旧问题")]
        incoming = [record("find_2", "A", [evidence("doc_2")],
                           scope="新范围", limitations="")]
        result = merge_findings(current, incoming)
        merged = result["findings"][0]
        self.assertEqual(merged["scope"], "新范围")
        self.assertEqual(merged["limitations"], "旧限制")
        self.assertEqual(merged["follow_up"], "旧问题")

    def test_unknown_fields_follow_incoming_when_present(self):
        current = [record("find_1", "A", [evidence("doc_1")], weight=0.2, tag="old")]
        incoming = [record("find_2", "A", [evidence("doc_2")], weight=0.8)]
        merged = merge_findings(current, incoming)["findings"][0]
        self.assertEqual(merged["weight"], 0.8)
        self.assertEqual(merged["tag"], "old")

    def test_new_finding_is_copied_not_referenced(self):
        incoming = [record("find_1", "A", [evidence("doc_1")], scope="某范围")]
        result = merge_findings([], incoming)
        added = result["added"][0]
        self.assertEqual(added["scope"], "某范围")
        self.assertIsNot(added, incoming[0])


class TestMergeBuckets(unittest.TestCase):
    """Ruling 4: the three buckets are independent sets of merged findings."""

    def test_new_finding_with_contradiction_is_added_and_conflicting(self):
        incoming = [record("find_1", "A", [evidence("doc_9", "contradicts")])]
        result = merge_findings([], incoming)
        self.assertEqual(len(result["added"]), 1)
        self.assertEqual(len(result["conflicting"]), 1)
        self.assertEqual(len(result["reinforced"]), 0)
        self.assertIs(result["added"][0], result["conflicting"][0])
        self.assertIs(result["added"][0], result["findings"][0])

    def test_reinforcement_without_contradiction_is_not_conflicting(self):
        result = merge_findings([finding("find_1", "A", ["doc_1"])],
                                [finding("find_2", "A", ["doc_2"])])
        self.assertEqual(len(result["reinforced"]), 1)
        self.assertEqual(len(result["conflicting"]), 0)
        self.assertIs(result["reinforced"][0], result["findings"][0])

    def test_contradiction_does_not_count_as_reinforcement(self):
        current = [finding("find_1", "A", ["doc_1"])]
        incoming = [record("find_2", "A", [evidence("doc_9", "contradicts")])]
        result = merge_findings(current, incoming)
        self.assertEqual(len(result["conflicting"]), 1)
        self.assertEqual(len(result["reinforced"]), 0)

    def test_contradicting_evidence_is_still_merged_into_the_finding(self):
        current = [finding("find_1", "A", ["doc_1"])]
        incoming = [record("find_2", "A", [evidence("doc_9", "contradicts")])]
        result = merge_findings(current, incoming)
        pairs = {(e["document_id"], e["evidence_type"])
                 for e in result["findings"][0]["evidence"]}
        self.assertEqual(pairs, {("doc_1", "supports"), ("doc_9", "contradicts")})

    def test_same_statement_without_evidence_counts_as_reinforced(self):
        # The literal reading of ruling 4: no `contradicts` in the incoming
        # evidence means the merge is not a conflict, so it is reinforcement.
        current = [finding("find_1", "A", ["doc_1"])]
        incoming = [record("find_2", "A", [])]
        result = merge_findings(current, incoming)
        self.assertEqual(len(result["reinforced"]), 1)

    def test_repeated_incoming_statement_fills_bucket_once(self):
        current = [finding("find_1", "A", ["doc_1"])]
        incoming = [finding("find_2", "A", ["doc_2"]),
                    finding("find_3", "A", ["doc_3"])]
        result = merge_findings(current, incoming)
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(len(result["reinforced"]), 1)
        docs = {e["document_id"] for e in result["findings"][0]["evidence"]}
        self.assertEqual(docs, {"doc_1", "doc_2", "doc_3"})

    def test_two_incoming_copies_of_a_new_statement_merge(self):
        incoming = [finding("find_1", "A", ["doc_1"]),
                    finding("find_2", "A", ["doc_2"])]
        result = merge_findings([], incoming)
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(len(result["added"]), 1)
        self.assertEqual(len(result["reinforced"]), 1)
        self.assertIs(result["added"][0], result["reinforced"][0])


class TestMergeInputSafety(unittest.TestCase):
    """Ruling 3: neither input list may be modified in place."""

    def test_inputs_are_not_modified(self):
        import copy
        current = [record("find_1", "A", [evidence("doc_1")], scope="旧范围")]
        incoming = [record("find_2", "A", [evidence("doc_1", "contradicts")],
                           scope="新范围")]
        current_before = copy.deepcopy(current)
        incoming_before = copy.deepcopy(incoming)
        merge_findings(current, incoming)
        self.assertEqual(current, current_before)
        self.assertEqual(incoming, incoming_before)

    def test_result_does_not_alias_incoming(self):
        incoming = [finding("find_1", "A", ["doc_1"])]
        result = merge_findings([], incoming)
        result["findings"][0]["evidence"].append(evidence("doc_99"))
        result["findings"][0]["confidence"] = 0.0
        self.assertEqual(incoming[0]["evidence"], [evidence("doc_1")])
        self.assertEqual(incoming[0]["confidence"], 0.9)


class TestMergeEdgeCases(unittest.TestCase):
    def test_empty_inputs(self):
        result = merge_findings([], [])
        self.assertEqual(result, {"findings": [], "added": [],
                                  "reinforced": [], "conflicting": []})

    def test_findings_keep_current_order_then_append_new(self):
        current = [finding("find_1", "A", ["doc_1"]),
                   finding("find_2", "B", ["doc_1"])]
        incoming = [finding("find_3", "B", ["doc_2"]),
                    finding("find_4", "C", ["doc_1"])]
        result = merge_findings(current, incoming)
        self.assertEqual([f["statement"] for f in result["findings"]],
                         ["A", "B", "C"])
        self.assertEqual([f["finding_id"] for f in result["findings"]],
                         ["find_1", "find_2", "find_4"])

    def test_duplicate_statement_in_current_merges_into_first(self):
        # Dirty data: two current findings already share a statement. The merge
        # must not crash and must land on the first one.
        current = [finding("find_1", "A", ["doc_1"]),
                   finding("find_2", "A", ["doc_2"])]
        incoming = [finding("find_3", "A", ["doc_3"])]
        result = merge_findings(current, incoming)
        self.assertEqual(len(result["findings"]), 2)
        first, second = result["findings"]
        self.assertEqual(first["finding_id"], "find_1")
        self.assertEqual({e["document_id"] for e in first["evidence"]},
                         {"doc_1", "doc_3"})
        self.assertEqual(second["finding_id"], "find_2")
        self.assertEqual({e["document_id"] for e in second["evidence"]},
                         {"doc_2"})


class TestComputeWeightBoundaries(unittest.TestCase):
    def test_weights_are_read_from_the_given_mapping(self):
        weights = {"relevance": 0.5, "evidence": 0.5,
                   "novelty": 0.0, "guidance": 0.0}
        target = {"relevance_score": 80, "evidence_score": 40}
        self.assertAlmostEqual(compute_weight(target, weights), 60.0)

    def test_missing_and_none_scores_count_as_zero(self):
        self.assertAlmostEqual(compute_weight({}, CONFIG_WEIGHTS), 0.0)
        self.assertAlmostEqual(
            compute_weight({"relevance_score": None}, CONFIG_WEIGHTS), 0.0)

    def test_partial_scores_use_only_the_present_fields(self):
        self.assertAlmostEqual(
            compute_weight({"relevance_score": 100}, CONFIG_WEIGHTS), 35.0)

    def test_bool_score_rejected(self):
        with self.assertRaises(ValueError) as raised:
            compute_weight({"relevance_score": True}, CONFIG_WEIGHTS)
        self.assertIn("relevance_score", str(raised.exception))

    def test_non_numeric_score_rejected(self):
        with self.assertRaises(ValueError) as raised:
            compute_weight({"evidence_score": "80"}, CONFIG_WEIGHTS)
        self.assertIn("evidence_score", str(raised.exception))

    def test_nan_and_infinite_scores_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(ValueError):
                compute_weight({"guidance_score": bad}, CONFIG_WEIGHTS)

    def test_missing_weight_key_is_named(self):
        with self.assertRaises(ValueError) as raised:
            compute_weight({}, {"relevance": 0.5, "evidence": 0.5, "novelty": 0.0})
        self.assertIn("guidance", str(raised.exception))

    def test_negative_weight_rejected(self):
        weights = {"relevance": -0.35, "evidence": 0.25,
                   "novelty": 0.20, "guidance": 0.20}
        with self.assertRaises(ValueError) as raised:
            compute_weight({}, weights)
        self.assertIn("relevance", str(raised.exception))

    def test_bool_and_non_numeric_weights_rejected(self):
        for bad in (True, "0.35"):
            weights = dict(CONFIG_WEIGHTS, relevance=bad)
            with self.assertRaises(ValueError):
                compute_weight({}, weights)

    def test_output_is_on_the_0_100_scale(self):
        target = {"relevance_score": 100, "evidence_score": 100,
                  "novelty_score": 100, "guidance_score": 100}
        self.assertAlmostEqual(compute_weight(target, CONFIG_WEIGHTS), 100.0)


class TestSummarizeStateSections(unittest.TestCase):
    def test_all_five_sections_are_present(self):
        text = summarize_state([], [], [])
        for title in SECTION_TITLES:
            self.assertIn("## " + title, text)

    def test_empty_input_produces_a_marker_in_every_section(self):
        text = summarize_state([], [], [])
        for title in SECTION_TITLES:
            self.assertIn(EMPTY_MARKER, section(text, title))

    def test_findings_sorted_by_importance_then_id(self):
        findings = [finding("find_b", "低", ["doc_1"], importance=0.2),
                    finding("find_c", "高后", ["doc_1"], importance=0.8),
                    finding("find_a", "高前", ["doc_1"], importance=0.8)]
        body = section(summarize_state(findings, [], []), "关键发现")
        self.assertLess(body.index("高前"), body.index("高后"))
        self.assertLess(body.index("高后"), body.index("低"))

    def test_non_numeric_importance_sorts_last(self):
        findings = [finding("find_b", "无分", ["doc_1"], importance=None),
                    finding("find_a", "有分", ["doc_1"], importance=0.1)]
        body = section(summarize_state(findings, [], []), "关键发现")
        self.assertLess(body.index("有分"), body.index("无分"))

    def test_contradictions_section_names_the_document(self):
        findings = [record("find_1", "机制有效",
                           [evidence("doc_1", "supports"),
                            evidence("doc_9", "contradicts")])]
        body = section(summarize_state(findings, [], []), "矛盾")
        self.assertIn("doc_9", body)
        self.assertNotIn("doc_1", body)
        self.assertIn("机制有效", body)

    def test_clean_finding_does_not_appear_in_contradictions(self):
        text = summarize_state([finding("find_1", "A", ["doc_1"])], [], [])
        self.assertIn(EMPTY_MARKER, section(text, "矛盾"))

    def test_evidence_section_lists_document_and_type(self):
        body = section(summarize_state([finding("find_1", "A", ["doc_1"])],
                                       [], []), "证据")
        self.assertIn("doc_1", body)
        self.assertIn("supports", body)
        self.assertIn("find_1", body)

    def test_actions_render_only_the_fields_they_carry(self):
        actions = [{"topic": "验证机制", "type": "verify", "reason": "来源冲突"},
                   {"type": "deepen"}]
        body = section(summarize_state([], actions, []), "下一步行动")
        self.assertIn("验证机制", body)
        self.assertIn("verify", body)
        self.assertIn("来源冲突", body)
        self.assertIn("deepen", body)

    def test_output_is_deterministic_and_carries_no_timestamp(self):
        findings = [finding("find_1", "结论一", ["doc_1"])]
        actions = [{"topic": "验证", "type": "verify"}]
        first = summarize_state(findings, actions, ["缺口"])
        second = summarize_state(findings, actions, ["缺口"])
        self.assertEqual(first, second)
        self.assertNotRegex(first, r"\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b")
