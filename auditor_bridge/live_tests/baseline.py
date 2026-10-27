# ruff: noqa: S101
# Executable acceptance assertions: run with ordinary Python, never python -O.
import time

from common import load, request, save, wait_services

wait_services()
integration = load("integration.json")
projects = load("repositories.json")["projects"]
profile = {"name": "Live upstream baseline",
           "steps": [{"job_slug": name} for name in ["code-downloader", "gitleaks", "detect-secrets", "opengrep"]],
           "asoc_integration_ids": [integration["asoc_id"]], "sg_blocking": False,
           "automations": [{"name": "Upstream discovery baseline", "integration_id": integration["gitlab_id"],
                            "repository_ids": [p["project_id"] for p in projects], "events": ["repository.discovered"],
                            "ref_patterns": ["@default"], "enabled": True, "max_scans_per_hour": 240}]}
request("dojo", "PUT", "/api/v2/auditor/profiles/baseline/", body=profile)
state = request("dojo", "POST", "/api/v2/auditor/profiles/baseline/sync/", body={})
save("baseline-profile.json", state)
assert state["state"] == "synced", state.get("last_error")
sequence_id = state["sequence_id"]
last = None
for _ in range(150):
    scans = [s for s in request("auditor", "GET", "/api/v1/scans?limit=100") if s.get("sequence_id") == sequence_id]
    summary = [(s["id"], s["status"]) for s in scans]
    if summary != last:
        last = summary
    save("baseline-scans.json", scans)
    if any(s["status"] in {"FAILED", "CANCELLED", "EXPIRED"} for s in scans):
        msg = "Baseline scan did not complete; see evidence"
        raise RuntimeError(msg)
    imports = request("dojo", "GET", "/api/v2/auditor/imports/")
    save("baseline-imports.json", imports)
    if len(scans) >= len(projects) and all(s["status"] == "COMPLETED" for s in scans):
        callbacks = {s["id"]: request("auditor", "GET", f'/api/v1/scans/{s["id"]}/asoc/callbacks') for s in scans}
        save("baseline-callbacks.json", callbacks)
        imported = {str(i["scan_id"]) for i in imports}
        if all(s["id"] in imported for s in scans):
            reports = {}
            for scan in scans:
                report = request("auditor", "GET", f'/api/v1/scans/{scan["id"]}/findings?format=json')
                reports[scan["id"]] = {k: v for k, v in report.items() if k != "findings"}
                reports[scan["id"]]["findings"] = [{k: f.get(k) for k in ["id", "job_slug", "title", "severity", "path", "line"]} for f in report["findings"]]
            save("baseline-reports.json", reports)
            assert any(r["finding_count"] > 0 for r in reports.values()), "Expected real upstream findings"
            save("baseline-inventory.json", request("auditor", "GET", "/api/v1/repositories"))
            break
    time.sleep(6)
else:
    msg = "Baseline timed out"
    raise RuntimeError(msg)
