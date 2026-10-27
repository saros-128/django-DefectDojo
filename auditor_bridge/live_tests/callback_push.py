# ruff: noqa: S101
# Executable acceptance assertions: run with ordinary Python, never python -O.
import time
import uuid
from pathlib import Path

from common import load, request, save, wait_services

wait_services()
integration = load("integration.json")
group_id = load("repositories.json")["group_id"]
project = request("gitlab", "POST", "/api/v4/projects", body={
    "name": "callback-only-" + uuid.uuid4().hex[:6], "namespace_id": group_id, "visibility": "private", "default_branch": "main"})
source = Path("/fixtures/nodegoat/config/env/development.js").read_text(encoding="utf-8")
commit = request("gitlab", "POST", f'/api/v4/projects/{project["id"]}/repository/commits', body={
    "branch": "main", "commit_message": "test: import pinned NodeGoat configuration subset",
    "actions": [{"action": "create", "file_path": "config/env/development.js", "content": source}]})
request("auditor", "POST", f'/api/v1/gitlab-integrations/{integration["gitlab_id"]}/discover', body={})
before = request("dojo", "GET", "/api/v2/auditor/repositories/")
assert str(project["id"]) not in [r["repository_id"] for r in before["items"]]
key = "callback-only-" + str(project["id"])
profile = {"name": key, "steps": [{"job_slug": "code-downloader"}, {"job_slug": "gitleaks"}],
           "asoc_integration_ids": [integration["asoc_id"]], "automations": [{
           "name": key, "integration_id": integration["gitlab_id"], "repository_ids": [project["id"]],
           "events": ["repository.discovered"], "ref_patterns": ["@default"], "enabled": True}]}
request("dojo", "PUT", f"/api/v2/auditor/profiles/{key}/", body=profile)
state = request("dojo", "POST", f"/api/v2/auditor/profiles/{key}/sync/", body={})
assert state["state"] == "synced", state
for _ in range(120):
    scans = [s for s in request("auditor", "GET", "/api/v1/scans?limit=200") if s.get("sequence_id") == state["sequence_id"]]
    if scans and scans[0]["status"] == "COMPLETED":
        scan = scans[0]
        cb = request("auditor", "GET", f'/api/v1/scans/{scan["id"]}/asoc/callbacks')
        if any((c.get("acknowledgement") or {}).get("report_status") == "imported" for c in cb["callbacks"]):
            break
    time.sleep(3)
else:
    msg = "Callback-only create/import timed out"
    raise RuntimeError(msg)
after = request("dojo", "GET", "/api/v2/auditor/repositories/")
mapping = next(r for r in after["items"] if r["repository_id"] == str(project["id"]))
receipt = next(r for r in request("dojo", "GET", "/api/v2/auditor/imports/") if r["scan_id"] == scan["id"])
assert receipt["finding_count"] >= 1
actual = request("dojo", "GET", f'/api/v2/findings/?test={receipt["test_id"]}&limit=1')
assert actual["count"] == receipt["finding_count"]
report = request("auditor", "GET", f'/api/v1/scans/{scan["id"]}/findings?format=json')
assert report["repository_identity"]["repository_id"] == str(project["id"])
assert report["commit_sha"] == commit["id"]
save("callback-only-create.json", {"periodic_worker_stopped": True, "project_id": project["id"],
    "upstream": "https://github.com/OWASP/NodeGoat", "upstream_sha": load("repositories.json")["projects"][1]["upstream_sha"],
    "source_file": "config/env/development.js", "before_mapping_ids": [r["repository_id"] for r in before["items"]],
    "mapping": mapping, "scan": scan, "callbacks": cb, "import": receipt, "api_findings_count": actual["count"],
    "strict_report_snapshot": {"repository_identity": report["repository_identity"], "commit_sha": report["commit_sha"]}})
request("dojo", "POST", f"/api/v2/auditor/profiles/{key}/disable/", body={})
