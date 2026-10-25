import unittest
from scripts.lib.urlnorm import normalize_url, url_identity
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


class TestContentHash(unittest.TestCase):
    def test_whitespace_insensitive(self):
        self.assertEqual(content_hash("a  b\n\nc"), content_hash("a b c"))

    def test_different_content_differs(self):
        self.assertNotEqual(content_hash("hello"), content_hash("world"))
