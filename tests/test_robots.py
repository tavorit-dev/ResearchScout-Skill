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

    def test_leading_bom_is_stripped(self):
        # chr(0xFEFF) is built here from an integer so that this test does not
        # share a source of truth with the module's own BOM constant.
        rules = parse_robots(chr(0xFEFF) + "User-agent: *\nDisallow: /secret\n")
        self.assertFalse(rules.is_allowed("/secret"))
        plain = parse_robots("User-agent: *\nDisallow: /secret\n")
        self.assertFalse(plain.is_allowed("/secret"))

    def test_longest_match_wins(self):
        rules = parse_robots("User-agent: *\nDisallow: /a/b/\nAllow: /a/\n")
        self.assertFalse(rules.is_allowed("/a/b/c"))

    def test_longer_allow_rule_overrides_shorter_disallow(self):
        # Only the longer Allow rule can permit this path, so the test fails
        # under plain "Disallow wins" logic.
        rules = parse_robots("User-agent: *\nDisallow: /a/\nAllow: /a/b/\n")
        self.assertTrue(rules.is_allowed("/a/b/c"))
        self.assertFalse(rules.is_allowed("/a/other"))

    def test_equal_length_tie_goes_to_allow_either_order(self):
        allow_first = parse_robots("User-agent: *\nAllow: /x/\nDisallow: /x/\n")
        self.assertTrue(allow_first.is_allowed("/x/y"))
        disallow_first = parse_robots("User-agent: *\nDisallow: /x/\nAllow: /x/\n")
        self.assertTrue(disallow_first.is_allowed("/x/y"))

    def test_specific_agent_group_overrides_wildcard(self):
        rules = parse_robots(
            "User-agent: *\nDisallow: /\n\nUser-agent: GoodBot\nAllow: /\n"
        )
        self.assertTrue(rules.is_allowed("/blog", user_agent="GoodBot"))
        self.assertFalse(rules.is_allowed("/blog", user_agent="OtherBot"))


class TestEvaluateRobots(unittest.TestCase):
    def test_unknown_outcome_fails_closed(self):
        status, _ = evaluate_robots({"outcome": "something_new"})
        self.assertEqual(status, "error")

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
