import unittest
from pathlib import Path
from scripts.content_extractor import extract_content, parse_date

FIXTURE = Path(__file__).parent / "fixtures" / "sample_page.html"


class TestParseDate(unittest.TestCase):
    def test_iso_date_high_confidence(self):
        value, confidence = parse_date("2026-10-20")
        self.assertEqual(value, "2026-10-20")
        self.assertEqual(confidence, "explicit")

    def test_unparseable_returns_none(self):
        value, confidence = parse_date("最近更新")
        self.assertIsNone(value)
        self.assertEqual(confidence, "unknown")

    def test_empty_returns_none(self):
        value, _ = parse_date("")
        self.assertIsNone(value)


class TestExtractContent(unittest.TestCase):
    def test_strips_navigation(self):
        result = extract_content(FIXTURE.read_text(encoding="utf-8"), "https://e.org/a")
        self.assertNotIn("Home | About", result["text"])
        self.assertIn("runtime isolation", result["text"])

    def test_extracts_title(self):
        result = extract_content(FIXTURE.read_text(encoding="utf-8"), "https://e.org/a")
        self.assertEqual(result["title"], "Agent Runtime Isolation")

    def test_missing_date_stays_none(self):
        html = "<html><body><article><p>Body text here.</p></article></body></html>"
        result = extract_content(html, "https://e.org/a")
        self.assertIsNone(result["published_at"])
        self.assertEqual(result["date_confidence"], "unknown")
