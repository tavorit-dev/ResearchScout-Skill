# tests/test_robots.py
import unittest
from scripts.lib.robots import parse_robots, evaluate_robots

ROBOTS = """
User-agent: *
Disallow: /private/
Allow: /private/public/
Crawl-delay: 2

User-agent: BadBot
Disallow: /
"""


class TestParseRobots(unittest.TestCase):
    def test_disallow_blocks_path(self):
        rules = parse_robots(ROBOTS)
        self.assertFalse(rules.is_allowed("/private/x"))

    def test_allow_overrides_disallow(self):
        rules = parse_robots(ROBOTS)
        self.assertTrue(rules.is_allowed("/private/public/x"))

    def test_unlisted_path_allowed(self):
        rules = parse_robots(ROBOTS)
        self.assertTrue(rules.is_allowed("/blog/post"))

    def test_crawl_delay_parsed(self):
        rules = parse_robots(ROBOTS)
        self.assertEqual(rules.crawl_delay(), 2.0)

    def test_specific_agent_overrides_wildcard(self):
        rules = parse_robots(ROBOTS)
        self.assertFalse(rules.is_allowed("/blog", user_agent="BadBot"))

    def test_empty_robots_allows_all(self):
        rules = parse_robots("")
        self.assertTrue(rules.is_allowed("/anything"))


class TestEvaluateRobots(unittest.TestCase):
    def test_server_error_is_error_not_allowed(self):
        status, reason = evaluate_robots({"outcome": "server_error", "status": 503})
        self.assertEqual(status, "error")
        self.assertIn("503", reason)

    def test_timeout_is_error(self):
        status, _ = evaluate_robots({"outcome": "timeout"})
        self.assertEqual(status, "error")

    def test_404_is_absent_and_allows(self):
        status, _ = evaluate_robots({"outcome": "not_found", "status": 404})
        self.assertEqual(status, "absent")

    def test_ok_parses_rules(self):
        status, _ = evaluate_robots({"outcome": "ok", "status": 200, "text": ROBOTS})
        self.assertEqual(status, "allowed")
