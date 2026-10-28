"""Durable repository mapping, transactional import, and retryable profile sync."""
import contextlib
import hashlib
import json
import os
import re
import uuid
from collections import Counter
from datetime import timedelta
from itertools import starmap
from types import SimpleNamespace
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, transaction
from django.utils import timezone

from auditor_bridge import client
from auditor_bridge.config import BridgeError, auditor_url, origin
from auditor_bridge.models import CallbackReceipt, InventoryState, Profile, RepositoryLink, ScanImport


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def identity(data):
    provider = data.get("provider")
    repository_id = str(data.get("repository_id", ""))
    if provider != "gitlab" or not re.fullmatch(r"[1-9][0-9]{0,19}", repository_id):
        msg = "invalid_repository_identity"
        raise BridgeError(msg)
    instance = origin(data["instance"])
    return {"provider": provider, "instance": instance, "repository_id": repository_id}


def lock(key):
    # Transaction-scoped locks also serialize requests across uWSGI workers.
    number = int(hashlib.sha256(key.encode()).hexdigest()[:16], 16)
    if number >= 2**63:
        number -= 2**64
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [number])


@transaction.atomic
def upsert_repository(data):
    from dojo.models import Engagement, Product, Product_Type  # noqa: PLC0415

    ident = identity(data)
    key = digest(ident)
    lock("repository:" + key)
    row = RepositoryLink.objects.filter(identity_key=key).first()
    description = f"Auditor repository {ident['instance']} / {data.get('path', ident['repository_id'])}"[:4000]
    if row is None:
        lock("product_type:Auditor repositories")
        product_type, _ = Product_Type.objects.get_or_create(name="Auditor repositories")
        product = Product.objects.create(name="Auditor " + key, description=description, prod_type=product_type)
        today = timezone.localdate()
        engagement = Engagement.objects.create(product=product, name="Auditor continuous assessment",
                                               target_start=today, target_end=today + timedelta(days=3650),
                                               engagement_type="CI/CD", status="In Progress", active=True)
        row = RepositoryLink.objects.create(identity_key=key, **ident, product=product,
                                            engagement=engagement, inventory=data)
    else:
        # Path is display metadata. A rename or transfer never changes identity.
        if row.product_managed:
            Product.objects.filter(pk=row.product_id).update(description=description)
        row.inventory = data
        row.save(update_fields=["inventory", "updated_at"])
    return row


@transaction.atomic
def bind_repository(repository_pk, product_id, expected_product_id):
    """Group discovered repositories before importing their first scan.

    Each repository keeps its own Engagement and identity. Reparenting existing
    findings is a separate Dojo operation, never a side effect of inventory sync.
    """
    from dojo.models import Product  # noqa: PLC0415 -- avoid model import cycles

    row = RepositoryLink.objects.get(pk=repository_pk)
    lock("repository:" + row.identity_key)
    row = RepositoryLink.objects.select_for_update().get(pk=repository_pk)
    if row.product_id == product_id:
        return row
    if row.product_id != expected_product_id:
        msg = "repository_binding_conflict"
        raise BridgeError(msg)
    if ScanImport.objects.filter(repository=row).exists():
        msg = "repository_has_imports_conflict"
        raise BridgeError(msg)
    product = Product.objects.get(pk=product_id)
    engagement = row.engagement
    engagement.product = product
    engagement.save(update_fields=["product"])
    row.product = product
    row.product_managed = False
    row.save(update_fields=["product", "product_managed", "updated_at"])
    return row


def pull_inventory():
    state, _ = InventoryState.objects.get_or_create(key="default")
    count, offset = 0, 0
    try:
        for _ in range(500):
            page = client.request("GET", "/api/v1/repositories?" + urlencode({"limit": 200, "offset": offset}))
            if not isinstance(page, dict) or not isinstance(page.get("items"), list):
                msg = "invalid_inventory_response"
                raise BridgeError(msg)
            for item in page["items"]:
                upsert_repository(item)
                count += 1
            following = page.get("next_offset")
            if following is None:
                break
            if not isinstance(following, int) or following <= offset:
                msg = "invalid_inventory_cursor"
                raise BridgeError(msg)
            offset = following
        else:
            msg = "inventory_page_limit"
            raise BridgeError(msg)
    except (BridgeError, KeyError, TypeError, ValueError) as exc:
        state.last_error = str(exc) if isinstance(exc, BridgeError) else "invalid_inventory_response"
        state.save(update_fields=["last_error"])
        raise BridgeError(state.last_error) from exc
    state.last_success = timezone.now()
    state.watermark = state.last_success
    state.last_error = ""
    state.save()
    return count


def contains(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and contains(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(
            starmap(contains, zip(expected, actual, strict=True)))
    return expected == actual


def catalog_jobs():
    jobs = []
    for offset in range(0, 10000, 200):
        page = client.request("GET", f"/api/v1/jobs?limit=200&offset={offset}")
        if not isinstance(page, list):
            msg = "invalid_catalog_response"
            raise BridgeError(msg)
        jobs.extend(page)
        if len(page) < 200:
            return jobs
    msg = "catalog_page_limit"
    raise BridgeError(msg)


def policy(rule):
    schedule = rule.get("schedule")
    if schedule:
        if schedule["kind"] == "interval":
            schedule = {"kind": "interval", "interval_seconds": schedule["interval_seconds"]}
        else:
            schedule = {"kind": "weekly", "weekdays": sorted(set(schedule["weekdays"])),
                        "hour": schedule.get("hour", 0), "minute": schedule.get("minute", 0),
                        "timezone": schedule.get("timezone", "UTC")}
    return {"name": rule["name"], "integration_id": str(rule["integration_id"]),
            "enabled": rule.get("enabled", True), "repository_ids": sorted(set(rule.get("repository_ids", []))),
            "events": sorted(set(rule.get("events", []))), "ref_patterns": sorted(set(rule.get("ref_patterns", ["@default"]))),
            "schedule": schedule, "max_scans_per_hour": rule.get("max_scans_per_hour", 6)}


def rules_equal(expected, actual):
    return isinstance(actual, list) and Counter(digest(policy(r)) for r in expected) == Counter(digest(policy(r)) for r in actual)


@transaction.atomic
def save_profile(key, desired):
    lock("profile:" + key)
    profile = Profile.objects.select_for_update().filter(key=key).first()
    if profile is None:
        return Profile.objects.create(key=key, desired=desired, creation_payload=desired)
    if profile.state == "deleted":
        msg = "profile_deleted_use_new_key"
        raise BridgeError(msg)
    if profile.desired != desired:
        # Only a definitive 422 allows changing the original creation receipt.
        if profile.sequence_id is None and profile.last_error == "auditor_http_422":
            profile.creation_payload = desired
            profile.sync_key = uuid.uuid4()
        profile.desired, profile.state, profile.last_error = desired, "pending", ""
        profile.next_attempt_at = None
        profile.save()
    return profile


def disable_profile(key):
    with transaction.atomic():
        profile = Profile.objects.select_for_update().get(key=key)
        if profile.state == "deleted":
            msg = "profile_deleted"
            raise BridgeError(msg)
        if profile.sequence_id is None and profile.last_error == "auditor_http_422":
            msg = "rejected_profile_correct_or_delete"
            raise BridgeError(msg)
        for rule in profile.desired.get("automations", []):
            rule["enabled"] = False
        profile.state, profile.next_attempt_at = "pending", None
        profile.save()
    return synchronize_profile(key)


@transaction.atomic
def synchronize_profile(key):
    profile = Profile.objects.select_for_update().get(key=key)
    deleting = profile.state in {"delete_pending", "delete_error"}
    if profile.state == "deleted":
        return profile
    if deleting and profile.sequence_id is None and (profile.attempts == 0 or profile.last_error == "auditor_http_422"):
        # No successful remote create is possible. A transport error must instead
        # recover the original receipt below before attempting remote deletion.
        profile.state, profile.last_error, profile.next_attempt_at = "deleted", "", None
        profile.remote = {}
        profile.save()
        return profile
    profile.attempts += 1
    try:
        if profile.sequence_id is None:
            created = client.request("POST", "/api/v1/sequences", body=profile.creation_payload,
                                     idempotency_key="dojo-" + str(profile.sync_key))
            profile.sequence_id = created["id"]
        path = "/api/v1/sequences/" + str(profile.sequence_id)
        if deleting:
            try:
                client.request("DELETE", path)
            except BridgeError as exc:
                if str(exc) != "auditor_http_404":
                    raise
            try:
                client.request("GET", path)
            except BridgeError as exc:
                if str(exc) != "auditor_http_404":
                    raise
            else:
                msg = "delete_readback_mismatch"
                raise BridgeError(msg)
            profile.state, profile.last_error, profile.next_attempt_at = "deleted", "", None
            profile.remote = {}
            profile.save()
            return profile
        sequence = client.request("GET", path)
        rules = client.request("GET", path + "/automations")
        desired = profile.desired
        expected_sequence = {k: v for k, v in desired.items() if k != "automations"}
        if not contains(expected_sequence, sequence) or not rules_equal(desired.get("automations", []), rules):
            client.request("PUT", path, body=desired)
            sequence = client.request("GET", path)
            rules = client.request("GET", path + "/automations")
        if not contains(expected_sequence, sequence) or not rules_equal(desired.get("automations", []), rules):
            msg = "profile_readback_mismatch"
            raise BridgeError(msg)
        profile.remote = {"sequence": sequence, "automations": rules}
        profile.state, profile.last_error, profile.next_attempt_at = "synced", "", None
    except (BridgeError, KeyError, TypeError, ValueError) as exc:
        profile.state = "delete_error" if deleting else "error"
        profile.last_error = str(exc) if isinstance(exc, BridgeError) else "invalid_profile_response"
        profile.next_attempt_at = timezone.now() + timedelta(seconds=min(300, 2 ** min(profile.attempts, 8)))
    profile.save()
    return profile


def generic_report(report):
    if "coverage" in report and (not isinstance(report["coverage"], dict)
                                  or report["coverage"].get("status") not in {"complete", "partial"}):
        msg = "unknown_scan_coverage"
        raise BridgeError(msg)
    findings = report.get("findings")
    if (not isinstance(findings, list) or report.get("finding_count") != len(findings)
            or len(findings) > 50000 or report.get("artifact_parse_errors")):
        msg = "invalid_or_incomplete_report"
        raise BridgeError(msg)
    if report.get("status") != "COMPLETED" or not report.get("job_runs"):
        msg = "scan_not_completed"
        raise BridgeError(msg)
    jobs = report["job_runs"]
    if (not isinstance(jobs, list) or any(not isinstance(j, dict)
                                        or not isinstance(j.get("job_slug"), str)
                                        or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", j["job_slug"])
                                        or j.get("status") != "COMPLETED" for j in jobs)):
        msg = "incomplete_scanner_execution"
        raise BridgeError(msg)
    converted = []
    executed_slugs = {job["job_slug"] for job in jobs}
    for finding in findings:
        if not isinstance(finding, dict) or not all(finding.get(k) for k in ("id", "job_slug", "title", "severity")):
            msg = "invalid_finding"
            raise BridgeError(msg)
        if finding["job_slug"] not in executed_slugs:
            msg = "finding_scanner_not_executed"
            raise BridgeError(msg)
        severity = str(finding["severity"]).capitalize()
        if severity == "Informational":
            severity = "Info"
        if severity not in {"Critical", "High", "Medium", "Low", "Info"}:
            msg = "invalid_finding_severity"
            raise BridgeError(msg)
        item = {"title": str(finding["title"])[:511], "severity": severity,
                "description": str(finding.get("description") or finding["title"]),
                "unique_id_from_tool": digest([finding["job_slug"], finding.get("fingerprint") or finding["id"]]),
                "vuln_id_from_tool": str(finding["id"])[:500], "active": True, "verified": False,
                "static_finding": finding.get("finding_class") != "dast",
                "dynamic_finding": finding.get("finding_class") == "dast"}
        for source, target in [("path", "file_path"), ("line", "line"), ("package", "component_name"),
                               ("installed_version", "component_version"), ("remediation", "mitigation")]:
            if finding.get(source) is not None:
                item[target] = finding[source]
        cwes = finding.get("cwe", [])
        if cwes:
            with contextlib.suppress(ValueError):
                item["cwe"] = int(str(cwes[0]).removeprefix("CWE-"))
        converted.append(item)
    return {"findings": converted}


def trusted_inventory_repository(ident):
    offset = 0
    for _ in range(500):
        page = client.request("GET", "/api/v1/repositories?" + urlencode({"limit": 200, "offset": offset}))
        for item in page["items"]:
            if identity(item) == ident:
                return item
        following = page.get("next_offset")
        if following is None:
            return None
        if not isinstance(following, int) or following <= offset:
            msg = "invalid_inventory_cursor"
            raise BridgeError(msg)
        offset = following
    msg = "inventory_page_limit"
    raise BridgeError(msg)


def verify_snapshot(report, payload, ident):
    if not isinstance(report.get("repository_identity"), dict) or identity(report["repository_identity"]) != ident:
        msg = "report_repository_mismatch"
        raise BridgeError(msg)
    if not payload.get("commit_sha") or report.get("commit_sha") != payload["commit_sha"]:
        msg = "report_commit_mismatch"
        raise BridgeError(msg)


def callback(payload):
    ident = identity(payload)
    scan_id = str(payload["scan_id"])
    callback_id = str(payload["callback_id"])
    payload = {**payload, **ident, "scan_id": scan_id, "callback_id": callback_id}
    fingerprint = digest(payload)
    path = f"/api/v1/scans/{scan_id}/findings?format=json"
    # Exact canonical URL; never follow redirects or let a callback choose a host.
    if payload["report_url"] != auditor_url() + path:
        msg = "untrusted_report_url"
        raise BridgeError(msg)
    with transaction.atomic():
        lock("callback:" + callback_id)
        previous = CallbackReceipt.objects.filter(pk=callback_id).first()
        if previous:
            if previous.payload_digest != fingerprint:
                msg = "callback_payload_conflict"
                raise BridgeError(msg)
            return previous.acknowledgement
        lock("scan:" + scan_id)
        lock("repository:" + digest(ident))
        repository = RepositoryLink.objects.filter(identity_key=digest(ident)).first()
        if repository is None and os.environ.get("AUDITOR_BRIDGE_AUTO_CREATE", "true").lower() == "true":
            metadata = trusted_inventory_repository(ident)
            if metadata is not None:
                repository = upsert_repository(metadata)
        ack = {"scan_id": scan_id, **ident, "project_found": repository is not None,
               "project_id": str(repository.product_id) if repository else None,
               "report_status": "not_found"}
        if repository:
            existing = ScanImport.objects.filter(pk=scan_id).first()
            if existing and existing.repository_id != repository.pk:
                msg = "scan_identity_conflict"
                raise BridgeError(msg)
            if existing:
                if existing.commit_sha != payload.get("commit_sha") or payload["scan_status"] != "COMPLETED":
                    msg = "scan_snapshot_conflict"
                    raise BridgeError(msg)
                ack["report_status"] = "imported"
            elif payload["scan_status"] != "COMPLETED":
                ack["report_status"] = "failed"
            else:
                report = client.request("GET", path)
                if not isinstance(report, dict) or report.get("scan_id") != scan_id:
                    msg = "report_scan_mismatch"
                    raise BridgeError(msg)
                verify_snapshot(report, payload, ident)
                converted = generic_report(report)
                test_id, count = import_report(repository, scan_id, payload.get("commit_sha"), converted)
                ScanImport.objects.create(scan_id=scan_id, repository=repository, test_id=test_id,
                                          report_digest=digest(report), finding_count=count,
                                          coverage_status=report.get("coverage", {}).get("status", "unknown"),
                                          commit_sha=payload.get("commit_sha") or "")
                ack["report_status"] = "imported"
        CallbackReceipt.objects.create(callback_id=callback_id, payload_digest=fingerprint, acknowledgement=ack)
        return ack


def import_report(repository, scan_id, commit_sha, report):
    from dojo.api_v2.serializers import ImportScanSerializer  # noqa: PLC0415
    from dojo.models import Test  # noqa: PLC0415

    user = get_user_model().objects.get(username=os.environ["AUDITOR_BRIDGE_IMPORT_USER"], is_active=True, is_superuser=True)
    request = SimpleNamespace(user=user)
    data = {"engagement": repository.engagement_id, "scan_type": "Generic Findings Import",
            "file": SimpleUploadedFile("auditor.json", json.dumps(report).encode(), content_type="application/json"),
            "test_title": "Auditor " + scan_id, "scan_date": timezone.localdate().isoformat(),
            "deduplication_execution_mode": "sync", "close_old_findings": False}
    if commit_sha:
        data["commit_hash"] = commit_sha
    serializer = ImportScanSerializer(data=data, context={"request": request})
    serializer.is_valid(raise_exception=True)
    serializer.validated_data["user"] = user
    serializer.save()
    test_id = serializer.validated_data["test_id"]
    test = Test.objects.get(pk=test_id, engagement_id=repository.engagement_id)
    count = test.finding_set.count()
    if count != len(report["findings"]):
        msg = "import_readback_count_mismatch"
        raise BridgeError(msg)
    return test.pk, count
