# ruff: noqa: S101
# Executable acceptance assertions: run with ordinary Python, never python -O.
import time
from datetime import UTC, datetime, timedelta

from common import load, request, save, wait_services

wait_services()
integration = load("integration.json")
now = datetime.now(UTC)
target = (now + timedelta(minutes=2)).replace(second=0, microsecond=0)
schedules = {"interval": {"kind": "interval", "interval_seconds": 60},
             "weekly": {"kind": "weekly", "weekdays": [target.weekday()], "hour": target.hour,
                        "minute": target.minute, "timezone": "UTC"}}
evidence = {"configured_at": now.isoformat(), "weekly_target": target.isoformat(), "profiles": {}, "results": {}}
for kind, schedule in schedules.items():
    key = "live-schedule-" + kind
    profile = {"name": key, "steps": [{"job_slug": "code-downloader"}, {"job_slug": "gitleaks"}],
               "asoc_integration_ids": [integration["asoc_id"]],
               "automations": [{"name": key, "integration_id": integration["gitlab_id"], "repository_ids": [2],
                                "events": [], "ref_patterns": ["@default"], "schedule": schedule,
                                "enabled": True, "max_scans_per_hour": 240}]}
    request("dojo", "PUT", f"/api/v2/auditor/profiles/{key}/", body=profile)
    state = request("dojo", "POST", f"/api/v2/auditor/profiles/{key}/sync/", body={})
    assert state["state"] == "synced", state
    evidence["profiles"][kind] = state
save("schedules.json", evidence)
for _ in range(90):
    scans = request("auditor", "GET", "/api/v1/scans?limit=200")
    for kind, state in evidence["profiles"].items():
        if kind in evidence["results"]:
            continue
        candidates = [s for s in scans if s.get("sequence_id") == state["sequence_id"]]
        for scan in candidates:
            if scan["status"] == "FAILED":
                msg = "Scheduled scan failed"
                raise RuntimeError(msg)
            if scan["status"] != "COMPLETED":
                continue
            callbacks = request("auditor", "GET", f'/api/v1/scans/{scan["id"]}/asoc/callbacks')
            if not any((c.get("acknowledgement") or {}).get("report_status") == "imported" for c in callbacks["callbacks"]):
                continue
            report = request("auditor", "GET", f'/api/v1/scans/{scan["id"]}/findings?format=json')
            assert report["repository_identity"]["repository_id"] == "2"
            assert report["commit_sha"]
            evidence["results"][kind] = {"scan": scan, "callbacks": callbacks,
                "next_rule_state": request("auditor", "GET", f'/api/v1/sequences/{state["sequence_id"]}/automations'),
                "repository_identity": report["repository_identity"], "commit_sha": report["commit_sha"],
                "finding_count": report["finding_count"]}
            request("dojo", "POST", f"/api/v2/auditor/profiles/live-schedule-{kind}/disable/", body={})
            save("schedules.json", evidence)
            break
    if len(evidence["results"]) == 2:
        break
    time.sleep(4)
else:
    msg = "Real scheduler did not complete both schedules"
    raise RuntimeError(msg)
