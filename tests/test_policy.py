# tests/test_policy.py
"""Tests for the REVIEW_REQUIRED user authorisation registry (spec 6.5, V13)."""
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.lib.policy import (POLICIES_FILE, load_policies, resolve_policy,
                                save_policy, validate_policy)

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)


def policy(**overrides):
    record = {
        "source_id": "src_1",
        "resolved_status": "ALLOWED",
        "resolved_by": "user",
        "resolved_at": "2026-10-01T00:00:00+00:00",
        "reason": "The user reviewed the terms.",
        "scope": {
            "paths": ["/public"],
            "access_method": "web",
            "expires_at": "2026-11-01T00:00:00+00:00",
        },
        "check_record_ref": "src_1",
    }
    record.update(overrides)
    return record


class TempCase(unittest.TestCase):
    def setUp(self):
        self.archive_root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.archive_root, ignore_errors=True)


class TestValidatePolicy(unittest.TestCase):
    def test_a_complete_record_is_valid(self):
        validate_policy(policy())

    def test_reason_and_check_record_ref_are_optional(self):
        record = policy()
        record.pop("reason")
        record.pop("check_record_ref")
        validate_policy(record)

    def test_invalid_records_are_rejected_with_a_specific_message(self):
        cases = {
            "source_id": policy(source_id=""),
            "resolved_status": policy(resolved_status="MAYBE"),
            "resolved_by": policy(resolved_by="agent"),
            "resolved_at": policy(resolved_at="yesterday"),
            "scope": policy(scope=[]),
            "scope.paths": policy(scope={"paths": [], "access_method": "web",
                                         "expires_at": "2026-11-01T00:00:00+00:00"}),
            "scope.access_method": policy(scope={"paths": ["/"],
                                                 "access_method": "",
                                                 "expires_at": "2026-11-01T00:00:00+00:00"}),
            "scope.expires_at": policy(scope={"paths": ["/"],
                                              "access_method": "web",
                                              "expires_at": "soon"}),
            "reason": policy(reason=3),
            "check_record_ref": policy(check_record_ref=3),
        }
        for field, record in cases.items():
            with self.subTest(field=field):
                with self.assertRaises(ValueError) as caught:
                    validate_policy(record)
                self.assertIn(field, str(caught.exception))

    def test_a_relative_scope_path_is_rejected(self):
        record = policy(scope={"paths": ["private"], "access_method": "web",
                               "expires_at": "2026-11-01T00:00:00+00:00"})
        with self.assertRaises(ValueError) as caught:
            validate_policy(record)
        self.assertIn("scope.paths", str(caught.exception))

    def test_a_non_mapping_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_policy(["not", "a", "policy"])


class TestRegistryStorage(TempCase):
    def test_save_creates_the_registry_and_load_reads_it_back(self):
        self.assertEqual(save_policy(self.archive_root, policy()), "inserted")
        path = self.archive_root / POLICIES_FILE
        self.assertTrue(path.is_file())
        self.assertEqual(load_policies(self.archive_root), [policy()])

    def test_a_missing_registry_reads_as_no_policies(self):
        self.assertEqual(load_policies(self.archive_root), [])

    def test_the_latest_record_for_a_source_wins(self):
        save_policy(self.archive_root, policy())
        save_policy(self.archive_root,
                    policy(resolved_status="BLOCKED", reason="User said no"))
        records = load_policies(self.archive_root)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["resolved_status"], "BLOCKED")

    def test_an_invalid_saved_policy_is_refused_before_it_is_written(self):
        with self.assertRaises(ValueError):
            save_policy(self.archive_root, policy(resolved_by="agent"))
        self.assertFalse((self.archive_root / POLICIES_FILE).exists())

    def test_a_corrupt_registry_raises_instead_of_reading_as_permission(self):
        path = self.archive_root / POLICIES_FILE
        path.parent.mkdir(parents=True)
        path.write_text("{not json}\n", encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            load_policies(self.archive_root)
        self.assertIn("source_policies.jsonl", str(caught.exception))

    def test_an_invalid_record_in_the_registry_raises(self):
        path = self.archive_root / POLICIES_FILE
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(policy(resolved_by="agent")) + "\n",
                        encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            load_policies(self.archive_root)
        self.assertIn("resolved_by", str(caught.exception))


class TestResolvePolicy(unittest.TestCase):
    def resolve(self, policies, path="/public/feed", access_method="web",
                source_id="src_1"):
        return resolve_policy(
            policies, source_id=source_id, path=path,
            access_method=access_method, now=NOW,
        )

    def test_a_live_in_scope_allowed_policy_resolves(self):
        self.assertIsNotNone(self.resolve([policy()]))

    def test_a_blocked_policy_never_yields_permission(self):
        self.assertIsNone(self.resolve([policy(resolved_status="BLOCKED")]))

    def test_an_expired_policy_does_not_resolve(self):
        record = policy(scope={"paths": ["/public"], "access_method": "web",
                               "expires_at": "2026-10-01T00:00:00+00:00"})
        self.assertIsNone(self.resolve([record]))

    def test_the_expiry_is_exclusive(self):
        record = policy(scope={"paths": ["/public"], "access_method": "web",
                               "expires_at": NOW.isoformat()})
        self.assertIsNone(self.resolve([record]))

    def test_an_access_method_mismatch_does_not_resolve(self):
        self.assertIsNone(self.resolve([policy()], access_method="rss"))

    def test_a_path_outside_the_scope_does_not_resolve(self):
        self.assertIsNone(self.resolve([policy()], path="/private/feed"))

    def test_path_matching_respects_segment_boundaries(self):
        self.assertIsNone(self.resolve([policy()], path="/publicity/feed"))
        self.assertIsNotNone(self.resolve([policy()], path="/public"))

    def test_the_root_scope_covers_every_path(self):
        record = policy(scope={"paths": ["/"], "access_method": "web",
                               "expires_at": "2026-11-01T00:00:00+00:00"})
        self.assertIsNotNone(self.resolve([record], path="/anything/at/all"))

    def test_the_latest_record_for_the_source_wins(self):
        policies = [policy(), policy(resolved_status="BLOCKED")]
        self.assertIsNone(self.resolve(policies))
        policies = [policy(resolved_status="BLOCKED"), policy()]
        self.assertIsNotNone(self.resolve(policies))

    def test_another_source_does_not_resolve(self):
        self.assertIsNone(self.resolve([policy(source_id="src_other")]))

    def test_an_unreadable_expiry_fails_closed(self):
        record = policy()
        record["scope"]["expires_at"] = "not-a-date"
        self.assertIsNone(self.resolve([record]))


if __name__ == "__main__":
    unittest.main()
