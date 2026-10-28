import copy
import os
from unittest.mock import patch

from django.test import SimpleTestCase

from auditor_bridge.config import BridgeError, origin
from auditor_bridge.serializers import ProfileSerializer
from auditor_bridge.services import catalog_jobs, generic_report, rules_equal, verify_snapshot


class BridgeContractTests(SimpleTestCase):
    def test_catalog_reads_beyond_the_first_page(self):
        first = [{"slug": f"job-{index}"} for index in range(200)]
        with patch("auditor_bridge.services.client.request", side_effect=[first, [{"slug": "last-job"}]]) as remote:
            self.assertEqual(len(catalog_jobs()), 201)
            self.assertEqual(remote.call_args.args, ("GET", "/api/v1/jobs?limit=200&offset=200"))

    def test_snapshot_validation_cannot_be_disabled(self):
        ident = {"provider": "gitlab", "instance": "https://git.example", "repository_id": "42"}
        with patch.dict(os.environ, {"AUDITOR_BRIDGE_REQUIRE_SNAPSHOT": "false"}), self.assertRaises(BridgeError):
            verify_snapshot({}, {"commit_sha": "a" * 40}, ident)

    def test_completed_empty_scan_is_valid(self):
        report = {"status": "COMPLETED", "finding_count": 0, "findings": [],
                  "job_runs": [{"job_slug": "gitleaks", "status": "COMPLETED"}]}
        self.assertEqual(generic_report(report), {"findings": []})

    def test_partial_scan_cannot_be_imported_as_clean(self):
        for status in ["FAILED", "TIMED_OUT", "SKIPPED", "RUNNING", "PENDING"]:
            with self.subTest(status=status), self.assertRaises(BridgeError):
                generic_report({"status": "COMPLETED", "finding_count": 0, "findings": [],
                                "job_runs": [{"job_slug": "gitleaks", "status": status}]})

    def test_artifact_parse_error_cannot_be_imported_as_clean(self):
        with self.assertRaises(BridgeError):
            generic_report({"status": "COMPLETED", "finding_count": 0, "findings": [],
                            "job_runs": [{"job_slug": "gitleaks", "status": "COMPLETED"}],
                            "artifact_parse_errors": ["unreadable_report"]})

    def test_finding_count_must_match(self):
        with self.assertRaises(BridgeError):
            generic_report({"status": "COMPLETED", "finding_count": 1, "findings": [],
                            "job_runs": [{"job_slug": "gitleaks", "status": "COMPLETED"}]})

    def test_profile_normalizes_sets_and_preserves_step_order(self):
        payload = {"name": "test", "steps": [{"job_slug": "code-downloader"}, {"job_slug": "gitleaks"}],
                   "asoc_integration_ids": [], "automations": [{"name": "test",
                   "integration_id": "11111111-1111-4111-8111-111111111111", "repository_ids": [2, 1, 2],
                   "events": ["push", "push"], "ref_patterns": ["main", "main"],
                   "schedule": {"kind": "weekly", "weekdays": [2, 0, 2], "hour": 3, "minute": 0, "timezone": "UTC"}}]}
        serializer = ProfileSerializer(data=payload)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        rule = serializer.validated_data["automations"][0]
        self.assertEqual(rule["repository_ids"], [1, 2])
        self.assertEqual(rule["events"], ["push"])
        self.assertEqual(rule["ref_patterns"], ["main"])
        self.assertEqual(rule["schedule"]["weekdays"], [0, 2])
        self.assertEqual([s["job_slug"] for s in serializer.validated_data["steps"]], ["code-downloader", "gitleaks"])

    def test_rules_are_a_multiset_not_a_positional_list(self):
        a = {"name": "first", "integration_id": "one", "events": ["push"], "repository_ids": [2, 1, 2]}
        b = {"name": "second", "integration_id": "one", "events": ["tag.created"]}
        self.assertTrue(rules_equal([a, b], [b, {**a, "repository_ids": [1, 2]}]))
        self.assertFalse(rules_equal([a, a], [a, b]))
        self.assertFalse(rules_equal([a], [a, a]))

    def test_snapshot_identity_and_commit_are_both_required(self):
        ident = {"provider": "gitlab", "instance": "https://git.example", "repository_id": "42"}
        payload = {"commit_sha": "a" * 40}
        report = {"repository_identity": ident, "commit_sha": "a" * 40}
        with patch.dict(os.environ, {"AUDITOR_BRIDGE_REQUIRE_SNAPSHOT": "true"}):
            verify_snapshot(report, payload, ident)
            for changed in [{}, {**report, "commit_sha": "b" * 40},
                            {**report, "repository_identity": {**ident, "repository_id": "43"}}]:
                with self.subTest(changed=changed), self.assertRaises(BridgeError):
                    verify_snapshot(changed, payload, ident)

    def test_origin_normalizes_case_and_default_port(self):
        self.assertEqual(origin("https://GIT.EXAMPLE:443/"), "https://git.example")
        self.assertEqual(origin(" https://GIT.EXAMPLE.:443/ "), "https://git.example")
        for value in ["file:///etc/passwd", "https://user:pass@git.example", "https://git.example?token=example",
                      "https://git:bad", "https://git:65536", "https://[invalid", "https://."]:
            with self.subTest(value=value), self.assertRaises(BridgeError):
                origin(value)

    def test_profile_rejects_arbitrary_job_commands(self):
        payload = {"name": "test", "steps": [{"job_slug": "gitleaks", "command": ["sh"]}], "asoc_integration_ids": []}
        serializer = ProfileSerializer(data=copy.deepcopy(payload))
        self.assertFalse(serializer.is_valid())
        self.assertIn("steps", serializer.errors)

    def test_unknown_top_level_field_has_serializable_errors(self):
        serializer = ProfileSerializer(data={"unexpected": True})
        self.assertFalse(serializer.is_valid())
        self.assertIn("non_field_errors", serializer.errors)
