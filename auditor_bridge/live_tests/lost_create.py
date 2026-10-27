# ruff: noqa: S101, PT018
# Executable acceptance assertions: run with ordinary Python, never python -O.
import copy
import uuid

from common import load, request, save, wait_services

wait_services()
integration = load("integration.json")
key = "lost-create-" + uuid.uuid4().hex[:8]
original = {"name": key, "steps": [{"job_slug": "code-downloader"}, {"job_slug": "gitleaks"}],
    "asoc_integration_ids": [integration["asoc_id"]], "automations": [{"name": key, "integration_id": integration["gitlab_id"],
        "repository_ids": [2], "events": ["push"], "ref_patterns": ["*"], "enabled": False}]}
request("dojo", "PUT", f"/api/v2/auditor/profiles/{key}/", body=original)
first = request("dojo", "POST", f"/api/v2/auditor/profiles/{key}/sync/", body={})
assert first["state"] == "error" and first["sequence_id"] is None and first["last_error"] == "auditor_transport_or_json_error", first
upstream = load("lost-create-proxy.json")
assert upstream["upstream_status"] == 201
changed = copy.deepcopy(original)
changed["name"] += "-edited"
changed["automations"][0]["name"] += "-edited"
changed["automations"][0]["ref_patterns"] = ["main"]
request("dojo", "PUT", f"/api/v2/auditor/profiles/{key}/", body=changed)
recovered = request("dojo", "POST", f"/api/v2/auditor/profiles/{key}/sync/", body={})
assert recovered["state"] == "synced" and recovered["sequence_id"] == upstream["upstream_result"]["id"], recovered
matching = [s for s in request("auditor", "GET", "/api/v1/sequences") if s["name"].startswith(key)]
assert len(matching) == 1
assert matching[0]["name"] == changed["name"]
assert len(recovered["remote"]["automations"]) == 1
assert recovered["remote"]["automations"][0]["ref_patterns"] == ["main"]
save("lost-create-recovery.json", {"first": first, "upstream_commit": upstream, "edited_desired": changed,
    "recovered": recovered, "sequence_count": len(matching), "automation_count": len(recovered["remote"]["automations"])})
request("dojo", "DELETE", f"/api/v2/auditor/profiles/{key}/")
