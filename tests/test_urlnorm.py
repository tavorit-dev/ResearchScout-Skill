import unittest
from scripts.lib.urlnorm import normalize_url, same_origin, url_identity
from scripts.lib.hashing import content_hash


class TestNormalizeUrl(unittest.TestCase):
    def test_strips_tracking_params(self):
        self.assertEqual(
            normalize_url("https://example.org/article?id=123&utm_source=rss"),
            "https://example.org/article?id=123",
        )

    def test_strips_fragment_and_trailing_slash(self):
        self.assertEqual(
            normalize_url("https://Example.org/blog/#section"),
            "https://example.org/blog",
        )

    def test_sorts_query_params(self):
        self.assertEqual(
            normalize_url("https://example.org/a?b=2&a=1"),
            "https://example.org/a?a=1&b=2",
        )

    def test_drops_default_port(self):
        self.assertEqual(
            normalize_url("https://example.org:443/a"),
            "https://example.org/a",
        )


class TestUrlIdentity(unittest.TestCase):
    def test_three_forms_of_same_article_collapse(self):
        forms = [
            "https://example.org/article?id=123",
            "https://example.org/article?id=123&utm_source=rss",
            "https://example.org/article/123",
        ]
        identities = {url_identity(u) for u in forms}
        self.assertEqual(len(identities), 1)


class TestSameOrigin(unittest.TestCase):
    def test_default_ports_are_normalised(self):
        self.assertTrue(same_origin("https://e.org", "https://e.org:443/a"))
        self.assertTrue(same_origin("http://e.org:80/a", "http://e.org"))

    def test_a_different_scheme_is_a_different_origin(self):
        self.assertFalse(same_origin("https://e.org/a", "http://e.org/a"))

    def test_a_different_port_is_a_different_origin(self):
        self.assertFalse(same_origin("https://e.org/a", "https://e.org:8443/a"))

    def test_a_different_host_is_a_different_origin(self):
        self.assertFalse(same_origin("https://e.org/a", "https://evil.example/a"))

    def test_an_unreadable_url_is_never_the_same_origin(self):
        self.assertFalse(same_origin("", "https://e.org"))
        self.assertFalse(same_origin("https://e.org", None))


class TestContentHash(unittest.TestCase):
    def test_whitespace_insensitive(self):
        self.assertEqual(content_hash("a  b\n\nc"), content_hash("a b c"))

    def test_different_content_differs(self):
        self.assertNotEqual(content_hash("hello"), content_hash("world"))
