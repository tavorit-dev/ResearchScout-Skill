# tests/test_planner.py
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


class TestSuggestScope(unittest.TestCase):
    def test_suggestion_is_unconfirmed(self):
        scope = suggest_scope("研究 Agent 安全", ["AI Agent Security"])
        self.assertFalse(scope["confirmed"])
        self.assertIn("core", scope)
        self.assertIn("related", scope)
        self.assertIn("excluded", scope)
