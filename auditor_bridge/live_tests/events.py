# ruff: noqa: S101
# Executable acceptance assertions: run with ordinary Python, never python -O.
"""Generate real GitLab actions; verify each event through scan/import/ACK."""
import time
import uuid

from common import EVIDENCE, load, request, save, wait_services

wait_services()
integration = load("integration.json")
project_id = 2
hook_id = next(h["hook_id"] for h in load("hooks.json") if h["project_id"] == project_id)
state = load("events-state.json") if (EVIDENCE / "events-state.json").exists() else {"run": uuid.uuid4().hex[:8], "results": {}}
runtime = request("auditor", "GET", "/api/v1/runtime")
runtime["max_parallel_scans"] = 2
save("live-runtime-config.json", request("auditor", "PUT", "/api/v1/runtime", body=runtime))


def commit(branch, label):
    return request("gitlab", "POST", f"/api/v4/projects/{project_id}/repository/commits", body={
        "branch": branch, "commit_message": "test: live " + label,
        "actions": [{"action": "create", "file_path": "_auditor_live/" + uuid.uuid4().hex + ".txt", "content": label + "\n"}]})


def hooks():
    events = request("gitlab", "GET", f"/api/v4/projects/{project_id}/hooks/{hook_id}/events?per_page=20")
    result = []
    for event in events:
        body = event.get("request_data", {})
        attributes = body.get("object_attributes", {})
        result.append({**{k: event.get(k) for k in ["id", "trigger", "response_status", "response_body", "created_at"]},
                       "request": {k: body.get(k) for k in ["object_kind", "event_type", "event_name", "ref", "after", "checkout_sha", "deployment_id", "status", "tag"]},
                       "attributes": {k: attributes.get(k) for k in ["id", "iid", "action", "state", "status", "oldrev", "sha", "ref", "source_branch", "target_branch"]}})
    return result


def profile(event):
    key = "event-" + event.replace(".", "-")
    body = {"name": key, "steps": [{"job_slug": "code-downloader"}, {"job_slug": "gitleaks"}],
            "asoc_integration_ids": [integration["asoc_id"]], "sg_blocking": False,
            "automations": [{"name": key, "integration_id": integration["gitlab_id"], "repository_ids": [project_id],
                             "events": [event], "ref_patterns": ["v*"] if event in {"tag.created", "release.published"} else ["*"],
                             "enabled": True, "max_scans_per_hour": 240}]}
    request("dojo", "PUT", f"/api/v2/auditor/profiles/{key}/", body=body)
    synced = request("dojo", "POST", f"/api/v2/auditor/profiles/{key}/sync/", body={})
    assert synced["state"] == "synced", synced
    return key, synced


def wait_scan(sequence_id, event, before):
    for _ in range(180):
        scans = [s for s in request("auditor", "GET", "/api/v1/scans?limit=200")
                 if s.get("sequence_id") == sequence_id and s["id"] not in before]
        if scans:
            scan = scans[0]
            assert scan["webhook_context"]["trigger_type"] == event, scan["webhook_context"]
            if scan["status"] in {"FAILED", "CANCELLED", "EXPIRED"}:
                save("event-failure-" + event + ".json", scan)
                raise RuntimeError("Event scan failed: " + event)
            if scan["status"] == "COMPLETED":
                callbacks = request("auditor", "GET", f'/api/v1/scans/{scan["id"]}/asoc/callbacks')["callbacks"]
                imported = [c for c in callbacks if (c.get("acknowledgement") or {}).get("report_status") == "imported"]
                if imported:
                    imports = request("dojo", "GET", "/api/v2/auditor/imports/")
                    receipt = next(i for i in imports if i["scan_id"] == scan["id"])
                    actual = request("dojo", "GET", f'/api/v2/findings/?test={receipt["test_id"]}&limit=1')
                    report = request("auditor", "GET", f'/api/v1/scans/{scan["id"]}/findings?format=json')
                    assert report["repository_identity"] == scan["repository_identity"]
                    assert report["commit_sha"] == scan["commit_sha"]
                    assert actual["count"] == receipt["finding_count"] == report["finding_count"]
                    inventory = request("auditor", "GET", "/api/v1/repositories")["items"]
                    repo = next(r for r in inventory if r["repository_id"] == str(project_id))
                    assert any(link["report_status"] == "imported" and link["last_scan_id"] == scan["id"] for link in repo["asoc_links"])
                    return {"scan": scan, "callbacks": callbacks, "dojo_import": receipt,
                            "dojo_api_finding_count": actual["count"], "report_finding_count": report["finding_count"],
                            "inventory": repo, "webhook_deliveries": hooks()}
        time.sleep(5)
    raise RuntimeError("Event scan timed out: " + event)


events = ["push", "merge_request.opened", "merge_request.updated", "merge_request.merged",
          "tag.created", "release.published", "pipeline.succeeded", "deployment.succeeded"]
for event in events:
    if event in state["results"]:
        continue
    key, synced = profile(event)
    before = {s["id"] for s in request("auditor", "GET", "/api/v1/scans?limit=200")}
    if event == "push":
        action = commit("main", event)
    elif event == "merge_request.opened":
        branch = "live-mr-" + state["run"]
        request("gitlab", "POST", f"/api/v4/projects/{project_id}/repository/branches", body={"branch": branch, "ref": "main"})
        commit(branch, event)
        action = request("gitlab", "POST", f"/api/v4/projects/{project_id}/merge_requests", body={
            "source_branch": branch, "target_branch": "main", "title": "Live integration " + state["run"]})
        state["mr_iid"], state["mr_branch"] = action["iid"], branch
    elif event == "merge_request.updated":
        action = commit(state["mr_branch"], event)
    elif event == "merge_request.merged":
        for _ in range(60):
            mr = request("gitlab", "GET", f'/api/v4/projects/{project_id}/merge_requests/{state["mr_iid"]}')
            if mr["detailed_merge_status"] == "mergeable":
                break
            time.sleep(2)
        action = request("gitlab", "PUT", f'/api/v4/projects/{project_id}/merge_requests/{state["mr_iid"]}/merge', body={})
    elif event == "tag.created":
        tag = "v-live-" + state["run"] + "-" + uuid.uuid4().hex[:6]
        action = request("gitlab", "POST", f"/api/v4/projects/{project_id}/repository/tags", body={"tag_name": tag, "ref": "main"})
        state["tag"] = tag
    elif event == "release.published":
        action = request("gitlab", "POST", f"/api/v4/projects/{project_id}/releases", body={
            "name": "Live release " + state["run"], "tag_name": state["tag"], "description": "Actual GitLab release acceptance"})
    else:
        action = commit("main", event)
    save("events-state.json", state)
    evidence = wait_scan(synced["sequence_id"], event, before)
    evidence["profile"], evidence["gitlab_action"] = synced, action
    save("event-" + event + ".json", evidence)
    state["results"][event] = {"scan_id": evidence["scan"]["id"], "test_id": evidence["dojo_import"]["test_id"],
                               "finding_count": evidence["dojo_api_finding_count"]}
    save("events-state.json", state)
    disabled = request("dojo", "POST", f"/api/v2/auditor/profiles/{key}/disable/", body={})
    assert disabled["state"] == "synced"
    assert all(not a["enabled"] for a in disabled["remote"]["automations"])
