# tests/test_http.py
import unittest

from scripts.lib import http


class RecordingClock:
    """A monotonic clock and sleep recorder driven by a test."""

    def __init__(self, start=0.0):
        self.time = start
        self.slept = []

    def now(self):
        return self.time

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.time += seconds


class TestEffectiveDelay(unittest.TestCase):
    def test_negative_delay_is_floored_at_zero(self):
        self.assertEqual(http.effective_delay(-5), 0.0)

    def test_zero_delay_stays_zero(self):
        self.assertEqual(http.effective_delay(0), 0.0)

    def test_positive_delay_is_kept(self):
        self.assertEqual(http.effective_delay(2.5), 2.5)

    def test_non_numeric_delay_means_no_delay(self):
        self.assertEqual(http.effective_delay(None), 0.0)
        self.assertEqual(http.effective_delay("soon"), 0.0)


class TestWaitForSlot(unittest.TestCase):
    def setUp(self):
        http._LAST_REQUEST.clear()

    def test_negative_delay_never_sleeps(self):
        # The floor's reason for existing: a hostile Crawl-delay of -5 must not
        # become a negative sleep, which would corrupt the per-domain
        # timestamp and turn the next request into a busy-loop.
        clock = RecordingClock()
        http.wait_for_slot("e.org", -5, now=clock.now, sleep=clock.sleep)
        http.wait_for_slot("e.org", -5, now=clock.now, sleep=clock.sleep)
        self.assertEqual(clock.slept, [])

    def test_spacing_waits_the_remaining_interval(self):
        clock = RecordingClock()
        http.wait_for_slot("e.org", 2, now=clock.now, sleep=clock.sleep)
        clock.time += 0.5
        http.wait_for_slot("e.org", 2, now=clock.now, sleep=clock.sleep)
        self.assertEqual(clock.slept, [1.5])

    def test_elapsed_time_counts_towards_the_interval(self):
        clock = RecordingClock()
        http.wait_for_slot("e.org", 2, now=clock.now, sleep=clock.sleep)
        clock.time += 5
        http.wait_for_slot("e.org", 2, now=clock.now, sleep=clock.sleep)
        self.assertEqual(clock.slept, [])

    def test_domains_are_paced_independently(self):
        clock = RecordingClock()
        http.wait_for_slot("a.org", 2, now=clock.now, sleep=clock.sleep)
        http.wait_for_slot("b.org", 2, now=clock.now, sleep=clock.sleep)
        self.assertEqual(clock.slept, [])


if __name__ == "__main__":
    unittest.main()
