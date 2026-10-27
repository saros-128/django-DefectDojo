# ruff: noqa: S101
"""Read back native Dojo API counts for every durable import; do not export findings."""
from common import load, request, save, wait_services

wait_services()
rows = []
for imported in request("dojo", "GET", "/api/v2/auditor/imports/"):
    scan_id = imported["scan_id"]
    scan = request("auditor", "GET", f"/api/v1/scans/{scan_id}")
    report = request("auditor", "GET", f"/api/v1/scans/{scan_id}/findings?format=json")
    count = request("dojo", "GET", f"/api/v2/findings/?test={imported['test_id']}&limit=1")["count"]
    assert count == imported["finding_count"] == report["finding_count"]
    assert report["repository_identity"] == scan["repository_identity"]
    assert report["commit_sha"] == scan["commit_sha"] == imported["commit_sha"]
    rows.append({"scan_id": scan_id, "test_id": imported["test_id"], "findings": count,
        "commit_sha": scan["commit_sha"], "repository_identity": scan["repository_identity"],
        "job_statuses": [{"slug": j["job_slug"], "status": j["status"]} for j in report["job_runs"]]})
hidden = str(load("service-account.json")["inaccessible_project_id"])
assert hidden not in [r["repository_id"] for r in request("auditor", "GET", "/api/v1/repositories")["items"]]
assert hidden not in [r["repository_id"] for r in request("dojo", "GET", "/api/v2/auditor/repositories/")["items"]]
save("final-counts.json", {"imports": rows, "test_count": len(rows), "finding_count": sum(r["findings"] for r in rows),
    "hidden_project_absent": True, "api_counts_match": True})
