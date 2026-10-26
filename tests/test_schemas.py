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

    def test_source_accepts_candidate_shape(self):
        validate_record("source", {
            "source_id": "src_1", "source_name": "blog.example.org",
            "base_url": "https://blog.example.org", "discovery_method": "manual",
            "discovery_verified": True, "compliance_status": "DISCOVERED",
            "selection_status": "pending",
        })

    def test_source_accepts_compliance_record_shape(self):
        validate_record("source", {
            "source_id": "src_1", "domain": "e.org", "base_url": "https://e.org",
            "checked_at": "2026-01-01T00:00:00Z", "robots_status": "ok", "terms_status": "absent", "access_method": "web",
            "compliance_status": "ALLOWED", "allowed_paths": ["/feed"],
            "request_rate_limit": 1.0,
            "evidence": [{"type": "robots_txt", "url": "https://e.org/robots.txt", "detail": "no rules"}],
            "notes": "", "selection_status": "selected",
        })

    def test_source_rejects_unknown_field(self):
        with self.assertRaises(ValidationError):
            validate_record("source", {
                "source_id": "src_1", "base_url": "https://e.org",
                "compliance_status": "DISCOVERED", "selection_status": "pending",
                "surprise": 1,
            })
