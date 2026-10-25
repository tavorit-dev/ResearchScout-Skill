import unittest
from scripts.lib.config import load_config, DEFAULTS, ACTION_TIME_WINDOWS


class TestConfig(unittest.TestCase):
    def test_defaults_match_spec(self):
        self.assertEqual(DEFAULTS["max_rounds"], 8)
        self.assertEqual(DEFAULTS["max_documents_per_research"], 200)
        self.assertEqual(DEFAULTS["max_requests_per_research"], 1000)
        self.assertEqual(DEFAULTS["min_new_information_ratio"], 0.10)
        self.assertEqual(DEFAULTS["min_action_priority"], 60)
        self.assertEqual(DEFAULTS["stop_when_no_progress_rounds"], 2)

    def test_action_time_windows(self):
        self.assertEqual(ACTION_TIME_WINDOWS["deepen"], 180)
        self.assertEqual(ACTION_TIME_WINDOWS["broaden"], 180)
        self.assertIsNone(ACTION_TIME_WINDOWS["verify"])
        self.assertEqual(ACTION_TIME_WINDOWS["backtrack"], 1825)

    def test_load_config_merges_defaults(self):
        cfg = load_config()
        self.assertIn("iteration_policy", cfg)
        self.assertEqual(cfg["iteration_policy"]["max_rounds"], 8)


if __name__ == "__main__":
    unittest.main()
