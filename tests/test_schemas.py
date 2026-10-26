# tests/test_schemas.py
import unittest
from jsonschema import ValidationError
from scripts.lib.validate import validate_record


class TestSchemas(unittest.TestCase):
    def test_document_requires_core_fields(self):
        with self.assertRaises(ValidationError):
            validate_record("document", {"title": "missing ids"})

    def test_document_accepts_minimal_valid(self):
        validate_record("document", {
            "document_id": "doc_1", "source_id": "src_1", "canonical_url": "https://e.org/a",
            "crawl_status": "PERSISTED", "retention_mode": "reference", "attempt_count": 0,
        })

    def test_document_rejects_unknown_field(self):
        with self.assertRaises(ValidationError):
            validate_record("document", {
                "document_id": "doc_1", "source_id": "src_1", "canonical_url": "https://e.org/a",
                "crawl_status": "PERSISTED", "retention_mode": "reference",
                "attempt_count": 0, "surprise": 1,
            })

    def test_source_rejects_bad_compliance_status(self):
        with self.assertRaises(ValidationError):
            validate_record("source", {
                "source_id": "src_1", "base_url": "https://e.org",
                "compliance_status": "MAYBE", "selection_status": "pending",
            })

    def test_action_requires_time_window(self):
        with self.assertRaises(ValidationError):
            validate_record("action", {
                "action_id": "act_1", "type": "deepen", "topic": "x",
                "reason": "y", "priority": 90,
            })

    def test_action_accepts_null_window_for_verify(self):
        validate_record("action", {
            "action_id": "act_1", "type": "verify", "topic": "x",
            "reason": "y", "priority": 90, "time_window_days": None,
        })

    def test_finding_requires_evidence(self):
        with self.assertRaises(ValidationError):
            validate_record("finding", {
                "finding_id": "find_1", "statement": "s", "confidence": 0.5,
                "importance": 0.5, "evidence": [],
            })
