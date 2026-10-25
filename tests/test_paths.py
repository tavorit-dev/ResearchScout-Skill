# tests/test_paths.py
import unittest
from pathlib import Path
from scripts.lib.ids import new_id
from scripts.lib.paths import slugify, research_dir, RESEARCH_SUBDIRS


class TestIds(unittest.TestCase):
    def test_new_id_format(self):
        value = new_id("src")
        self.assertTrue(value.startswith("src_"))
        self.assertEqual(len(value), 10)

    def test_new_id_unique(self):
        self.assertNotEqual(new_id("doc"), new_id("doc"))


class TestSlugify(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(slugify("AI Agent Security"), "ai-agent-security")

    def test_collapses_separators(self):
        self.assertEqual(slugify("Cloud  Native / Security"), "cloud-native-security")

    def test_rejects_non_ascii(self):
        with self.assertRaises(ValueError):
            slugify("云原生安全")


class TestResearchDir(unittest.TestCase):
    def test_layout(self):
        root = research_dir(Path("/tmp/archive"), "security", "ai-agent-security")
        self.assertEqual(root, Path("/tmp/archive/security/ai-agent-security"))

    def test_subdirs_match_spec(self):
        self.assertEqual(
            RESEARCH_SUBDIRS,
            ("state", "sources", "documents", "findings",
             "iterations", "summaries", "reports", "index"),
        )

    def test_research_dir_rejects_chinese(self):
        with self.assertRaises(ValueError):
            research_dir(Path("/tmp/archive"), "安全", "ai-agent-security")
