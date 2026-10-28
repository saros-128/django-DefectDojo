# ruff: noqa: S101, PT018
# Executable acceptance assertions: run with ordinary Python, never python -O.
"""Actual scanner executions: valid empty, failed process, permitted failed scanner."""
import copy
import time
import uuid

from common import EVIDENCE, load, request, save, wait_services

wait_services()
integration = load("integration.json")
state = load("report-cases.json") if (EVIDENCE / "report-cases.json").exists() else {}
if "project" not in state:
    project = request("gitlab", "POST", "/api/v4/projects", body={"name": "empty-scan-" + uuid.uuid4().hex[:6],
        "namespace_id": load("repositories.json")["group_id"], "visibility": "private", "default_branch": "main"})
    commit = request("gitlab", "POST", f'/api/v4/projects/{project["id"]}/repository/commits', body={
        "branch": "main", "commit_message": "test: clean empty-findings fixture",
        "actions": [{"action": "create", "file_path": "README.md", "content": "Clean repository for an empty scanner report.\n"}]})
    state["project"] = {"id": project["id"], "commit_sha": commit["id"]}
    save("report-cases.json", state)
request("auditor", "POST", f'/api/v1/gitlab-integrations/{integration["gitlab_id"]}/discover', body={})
source = next(j for j in request("auditor", "GET", "/api/v1/jobs") if j["slug"] == "gitleaks")
for case in ["empty", "failed", "incomplete"]:
    if case in state:
        continue
    jobs = ["code-downloader", "gitleaks"]
    if case != "empty":
        slug = "live-fault-" + case
        if not any(j["slug"] == slug for j in request("auditor", "GET", "/api/v1/jobs")):
            config = copy.deepcopy(source["config"])
            config["command"] = ["--auditor-live-invalid-option"]
            config["allow_failure"] = case == "incomplete"
            request("auditor", "POST", "/api/v1/jobs", body={"slug": slug, "name": "Live fault: " + case,
                "kind": source["kind"], "category": source["category"], "source": "custom", "config": config})
        jobs.append(slug)
    key = "report-case-" + case
    desired = {"name": key, "steps": [{"job_slug": j} for j in jobs], "asoc_integration_ids": [integration["asoc_id"]],
        "automations": [{"name": key, "integration_id": integration["gitlab_id"], "repository_ids": [state["project"]["id"]],
            "events": ["repository.discovered"], "ref_patterns": ["@default"], "enabled": True}]}
    request("dojo", "PUT", f"/api/v2/auditor/profiles/{key}/", body=desired)
    profile = request("dojo", "POST", f"/api/v2/auditor/profiles/{key}/sync/", body={})
    assert profile["state"] == "synced", profile
    for _ in range(150):
        scans = [s for s in request("auditor", "GET", "/api/v1/scans?limit=200") if s.get("sequence_id") == profile["sequence_id"]]
        if scans and scans[0]["status"] in {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED"}:
            scan = scans[0]
            callbacks = request("auditor", "GET", f'/api/v1/scans/{scan["id"]}/asoc/callbacks')["callbacks"]
            if callbacks and callbacks[0]["attempts"] > 0:
                cb = callbacks[0]
                if case == "incomplete" or cb.get("acknowledgement"):
                    break
        time.sleep(3)
    else:
        raise RuntimeError("Report case timed out: " + case)
    report = request("auditor", "GET", f'/api/v1/scans/{scan["id"]}/findings?format=json')
    imports = [i for i in request("dojo", "GET", "/api/v2/auditor/imports/") if i["scan_id"] == scan["id"]]
    if case == "empty":
        assert scan["status"] == "COMPLETED"
        assert report["finding_count"] == 0
        assert cb["acknowledgement"]["report_status"] == "imported"
        assert len(imports) == 1
        assert request("dojo", "GET", f'/api/v2/findings/?test={imports[0]["test_id"]}&limit=1')["count"] == 0
    elif case == "failed":
        assert scan["status"] == "FAILED"
        assert cb["acknowledgement"]["report_status"] == "failed"
        assert not imports
    else:
        assert scan["status"] == "COMPLETED"
        assert any(j["status"] == "FAILED" for j in report["job_runs"])
        assert cb["status_code"] == 400 and not imports and not cb.get("acknowledgement"), cb
    state[case] = {"profile": profile, "scan": scan, "callbacks": callbacks, "report": report, "imports": imports}
    save("report-cases.json", state)
    request("dojo", "POST", f"/api/v2/auditor/profiles/{key}/disable/", body={})
