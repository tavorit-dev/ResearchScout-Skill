# tests/test_deduplicator.py
import unittest
from scripts.deduplicator import dedupe, find_semantic_candidates


def doc(doc_id, url, text):
    return {"document_id": doc_id, "canonical_url": url, "text": text}


class TestDedupe(unittest.TestCase):
    def test_url_variants_collapse(self):
        docs = [
            doc("doc_1", "https://e.org/article?id=123", "same body"),
            doc("doc_2", "https://e.org/article?id=123&utm_source=rss", "same body"),
            doc("doc_3", "https://e.org/article/123", "same body"),
        ]
        kept, report = dedupe(docs)
        self.assertEqual(len(kept), 1)
        self.assertEqual(report["url_dupes"], 2)

    def test_identical_content_different_url_collapses(self):
        docs = [
            doc("doc_1", "https://a.org/x", "identical body"),
            doc("doc_2", "https://b.org/y", "identical body"),
        ]
        kept, report = dedupe(docs)
        self.assertEqual(len(kept), 1)
        self.assertEqual(report["content_dupes"], 1)

    def test_distinct_documents_survive(self):
        docs = [
            doc("doc_1", "https://a.org/x", "first body"),
            doc("doc_2", "https://b.org/y", "second body"),
        ]
        kept, _ = dedupe(docs)
        self.assertEqual(len(kept), 2)

    def test_semantic_candidates_reported_not_merged(self):
        docs = [
            doc("doc_1", "https://a.org/x", "agent sandbox isolation mechanism design"),
            doc("doc_2", "https://b.org/y", "agent sandbox isolation mechanism design overview"),
        ]
        candidates = find_semantic_candidates(docs, threshold=0.5)
        self.assertEqual(len(candidates), 1)
        kept, _ = dedupe(docs)
        self.assertEqual(len(kept), 2)
