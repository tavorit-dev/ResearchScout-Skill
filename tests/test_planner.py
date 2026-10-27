# tests/test_planner.py
import json
import tempfile
import unittest
from pathlib import Path
from scripts.planner import init_research, suggest_scope, load_research


class TestInitResearch(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_creates_layout_and_record(self):
        rec = init_research(
            self.tmp, domain="security", topic="ai-agent-security",
            objective="识别近期技术趋势", keywords=["AI Agent Security"],
            time_window={"recent_days": 30},
        )
        root = self.tmp / "security" / "ai-agent-security"
        self.assertTrue((root / "state").is_dir())
        self.assertTrue((root / "findings").is_dir())
        self.assertEqual(rec["status"], "RUNNING")

    def test_empty_scope_is_unconfirmed(self):
        rec = init_research(
            self.tmp, domain="security", topic="x-topic",
            objective="o", keywords=["k"], time_window={"recent_days": 30},
        )
        self.assertFalse(rec["scope"]["confirmed"])

    def test_load_roundtrips(self):
        init_research(
            self.tmp, domain="security", topic="y-topic",
            objective="o", keywords=["k"], time_window={"recent_days": 30},
        )
        loaded = load_research(self.tmp / "security" / "y-topic")
        self.assertEqual(loaded["topic"], "y-topic")

    def test_supplied_lists_without_flag_are_unconfirmed(self):
        # The path the host agent takes when it backfills its own suggestion:
        # supplying lists is not the same act as a user confirming them.
        rec = init_research(
            self.tmp, domain="security", topic="agent-suggested",
            objective="o", keywords=["k"], time_window={"recent_days": 30},
            scope={"core": ["agent security"], "related": [], "excluded": []},
        )
        self.assertFalse(rec["scope"]["confirmed"])
        self.assertEqual(rec["scope"]["core"], ["agent security"])

    def test_explicit_confirmation_survives(self):
        rec = init_research(
            self.tmp, domain="security", topic="user-confirmed",
            objective="o", keywords=["k"], time_window={"recent_days": 30},
            scope={"core": ["agent security"], "confirmed": True},
        )
        self.assertTrue(rec["scope"]["confirmed"])
        loaded = load_research(self.tmp / "security" / "user-confirmed")
        self.assertTrue(loaded["scope"]["confirmed"])

    def test_second_init_refuses_and_leaves_project_untouched(self):
        init_research(
            self.tmp, domain="security", topic="twice-topic",
            objective="original", keywords=["k"], time_window={"recent_days": 30},
            scope={"core": ["a"], "confirmed": True},
        )
        root = self.tmp / "security" / "twice-topic"
        record_before = (root / "research.yaml").read_bytes()
        scope_before = (root / "state" / "scope.json").read_bytes()

        with self.assertRaises(FileExistsError):
            init_research(
                self.tmp, domain="security", topic="twice-topic",
                objective="overwritten", keywords=["other"],
                time_window={"recent_days": 7},
            )

        self.assertEqual((root / "research.yaml").read_bytes(), record_before)
        self.assertEqual((root / "state" / "scope.json").read_bytes(), scope_before)
        loaded = load_research(root)
        self.assertEqual(loaded["objective"], "original")
        self.assertTrue(loaded["scope"]["confirmed"])


class TestSuggestScope(unittest.TestCase):
    def test_suggestion_is_unconfirmed(self):
        scope = suggest_scope("研究 Agent 安全", ["AI Agent Security"])
        self.assertFalse(scope["confirmed"])
        self.assertIn("core", scope)
        self.assertIn("related", scope)
        self.assertIn("excluded", scope)


class TestLoadResearch(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        init_research(
            self.tmp, domain="security", topic="read-topic",
            objective="o", keywords=["k"], time_window={"recent_days": 30},
        )
        self.root = self.tmp / "security" / "read-topic"

    def test_research_yaml_is_authoritative_for_scope_lists(self):
        record = self.root / "research.yaml"
        record.write_text(
            record.read_text(encoding="utf-8").replace("  core: []", "  core:\n  - edited"),
            encoding="utf-8",
        )
        loaded = load_research(self.root)
        self.assertEqual(loaded["scope"]["core"], ["edited"])
        self.assertFalse(loaded["scope"]["confirmed"])

    def test_sidecar_without_confirmed_reads_as_unconfirmed(self):
        (self.root / "state" / "scope.json").write_text(
            json.dumps({"core": ["a"], "related": [], "excluded": []}),
            encoding="utf-8",
        )
        loaded = load_research(self.root)
        self.assertIs(loaded["scope"]["confirmed"], False)

    def test_malformed_sidecar_is_rejected_with_path(self):
        (self.root / "state" / "scope.json").write_text("[]", encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            load_research(self.root)
        self.assertIn("scope.json", str(caught.exception))

    def test_truncated_sidecar_is_rejected_with_path(self):
        (self.root / "state" / "scope.json").write_text("{", encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            load_research(self.root)
        self.assertIn("scope.json", str(caught.exception))

    def test_record_failing_its_schema_is_rejected(self):
        record = self.root / "research.yaml"
        record.write_text(
            record.read_text(encoding="utf-8").replace("status: RUNNING", "status: NONSENSE"),
            encoding="utf-8",
        )
        with self.assertRaises(ValueError) as caught:
            load_research(self.root)
        self.assertIn("research.yaml", str(caught.exception))

    def test_missing_record_raises_file_not_found(self):
        # "There is no record" is a different answer from "the record is
        # corrupt": a caller deciding whether to initialise the project must be
        # able to tell them apart (archive.load_state draws the same line).
        (self.root / "research.yaml").unlink()
        with self.assertRaises(FileNotFoundError) as caught:
            load_research(self.root)
        self.assertIn("research.yaml", str(caught.exception))

    def test_malformed_record_is_rejected_with_path(self):
        (self.root / "research.yaml").write_text("status: [", encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            load_research(self.root)
        self.assertIn("research.yaml", str(caught.exception))

    def test_boolean_recent_days_is_not_a_window(self):
        rec = init_research(
            self.tmp, domain="security", topic="bool-topic",
            objective="o", keywords=["k"], time_window={"recent_days": True},
        )
        self.assertEqual(rec["time_window"], {"start": None, "end": None})
