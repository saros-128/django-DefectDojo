# ruff: noqa: S101
# Executable acceptance assertions: run with ordinary Python, never python -O.
import hashlib
import json
import time
import uuid

import httpx
from common import load, request, save, secret, wait_services

wait_services()
integration = load("integration.json")
for old in request("dojo", "GET", "/api/v2/auditor/profiles/"):
    if old["key"].startswith("duplicate-hook-") and old["state"] != "deleted":
        request("dojo", "POST", f'/api/v2/auditor/profiles/{old["key"]}/disable/', body={})
project = load("repositories.json")["projects"][1]["project_id"]
hook = next(h["hook_id"] for h in load("hooks.json") if h["project_id"] == project)
key = "duplicate-hook-" + uuid.uuid4().hex[:8]
desired = {"name": key, "steps": [{"job_slug": "code-downloader"}, {"job_slug": "gitleaks"}],
    "asoc_integration_ids": [integration["asoc_id"]], "automations": [{"name": key, "integration_id": integration["gitlab_id"],
    "repository_ids": [project], "events": ["push"], "ref_patterns": ["@default"], "enabled": True}]}
request("dojo", "PUT", f"/api/v2/auditor/profiles/{key}/", body=desired)
profile = request("dojo", "POST", f"/api/v2/auditor/profiles/{key}/sync/", body={})
assert profile["state"] == "synced"
commit = request("gitlab", "POST", f"/api/v4/projects/{project}/repository/commits", body={
    "branch": "main", "commit_message": "test: duplicate webhook receipt",
    "actions": [{"action": "create", "file_path": "_auditor_live/" + uuid.uuid4().hex + ".txt", "content": "Deduplication acceptance\n"}]})
for _ in range(120):
    scans = [s for s in request("auditor", "GET", "/api/v1/scans?limit=200") if s.get("sequence_id") == profile["sequence_id"]]
    events = request("gitlab", "GET", f"/api/v4/projects/{project}/hooks/{hook}/events?per_page=20")
    delivery = next((e for e in events if e["request_data"].get("after") == commit["id"]), None)
    if scans and scans[0]["status"] == "COMPLETED" and delivery:
        break
    time.sleep(2)
else:
    msg = "Real push did not complete"
    raise RuntimeError(msg)
assert len(scans) == 1
payload = delivery["request_data"]
responses = []
for _ in range(3):
    response = httpx.post(f'http://auditor-api:8000/api/v1/gitlab/webhooks/{integration["gitlab_id"]}', json=payload,
        headers={"X-Gitlab-Token": secret("webhook-token"), "X-Gitlab-Event": "Push Hook"}, timeout=120)
    assert response.status_code in {200, 202}, (response.status_code, response.text[:300])
    responses.append(response.json())
time.sleep(12)
after = [s for s in request("auditor", "GET", "/api/v1/scans?limit=200") if s.get("sequence_id") == profile["sequence_id"]]
assert [s["id"] for s in after] == [s["id"] for s in scans]
save("webhook-dedup.json", {"real_gitlab_event_id": delivery["id"], "commit_sha": commit["id"],
    "payload_sha256": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(), "exact_payload_replays": 3,
    "responses": responses, "scan_ids_before": [s["id"] for s in scans], "scan_ids_after": [s["id"] for s in after]})
request("dojo", "POST", f"/api/v2/auditor/profiles/{key}/disable/", body={})
