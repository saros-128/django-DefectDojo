# ruff: noqa: S101, PT018
# Executable acceptance assertions: run with ordinary Python, never python -O.
import copy
import time
from datetime import UTC, datetime

from common import load, request, save, wait_services

wait_services()
integration = load("integration.json")
evidence = {}


def store(key, desired):
    request("dojo", "PUT", f"/api/v2/auditor/profiles/{key}/", body=desired)
    return request("dojo", "POST", f"/api/v2/auditor/profiles/{key}/sync/", body={})


base = {"name": "Profile controls", "steps": [{"job_slug": "code-downloader"}, {"job_slug": "gitleaks"}],
        "asoc_integration_ids": [integration["asoc_id"]],
        "automations": [{"name": "First rule", "integration_id": integration["gitlab_id"],
                         "repository_ids": [2, 1, 2], "events": ["push", "push"], "ref_patterns": ["main", "main"], "enabled": False},
                        {"name": "Second rule", "integration_id": integration["gitlab_id"],
                         "repository_ids": [2], "events": [], "enabled": False,
                         "schedule": {"kind": "weekly", "weekdays": [5, 2, 5], "hour": 23, "minute": 59, "timezone": "UTC"}}]}
initial = store("control-normalized", base)
assert initial["state"] == "synced", initial
assert initial["desired"]["automations"][0]["repository_ids"] == [1, 2]
assert initial["desired"]["automations"][0]["events"] == ["push"]
assert initial["desired"]["automations"][1]["schedule"]["weekdays"] == [2, 5]
reordered = copy.deepcopy(base)
reordered["automations"].reverse()
after = store("control-normalized", reordered)
assert after["state"] == "synced", after
assert {r["id"] for r in after["remote"]["automations"]} == {r["id"] for r in initial["remote"]["automations"]}
evidence["normalized_and_reordered"] = {"input": base, "initial": initial, "reordered": after}
updated = copy.deepcopy(reordered)
updated["name"] = "Profile controls updated"
updated["automations"][0]["name"] = "Second rule updated"
edited = store("control-normalized", updated)
assert edited["state"] == "synced"
assert edited["remote"]["sequence"]["name"] == updated["name"]
evidence["updated"] = edited
deleted = request("dojo", "DELETE", "/api/v2/auditor/profiles/control-normalized/")
assert deleted["state"] == "deleted", deleted
request("auditor", "GET", "/api/v1/sequences/" + deleted["sequence_id"], expected=(404,))
evidence["unused_profile_delete"] = deleted
save("profile-control-cases.json", evidence)

bad = copy.deepcopy(base)
bad["name"] = "Correctable rejected create"
bad["automations"] = [bad["automations"][1]]
bad["automations"][0]["schedule"]["timezone"] = "Invalid/Timezone"
rejected = store("control-rejected", bad)
assert rejected["state"] == "error" and rejected["last_error"] == "auditor_http_422" and rejected["sequence_id"] is None, rejected
fixed = copy.deepcopy(bad)
fixed["automations"][0]["schedule"]["timezone"] = "UTC"
recovered = store("control-rejected", fixed)
assert recovered["state"] == "synced", recovered
evidence["rejected_edit_recovery"] = {"rejected": rejected, "recovered": recovered}
save("profile-control-cases.json", evidence)

drift = copy.deepcopy(fixed)
drift["name"] = "Drift reconciliation"
synced = store("control-drift", drift)
rule_id = synced["remote"]["automations"][0]["id"]
request("auditor", "DELETE", "/api/v1/scan-automations/" + rule_id)
assert request("auditor", "GET", "/api/v1/sequences/" + synced["sequence_id"] + "/automations") == []
started = datetime.now(UTC).isoformat()
for _ in range(36):
    rules = request("auditor", "GET", "/api/v1/sequences/" + synced["sequence_id"] + "/automations")
    if rules:
        break
    time.sleep(3)
else:
    msg = "Periodic read-back did not repair deleted rule"
    raise RuntimeError(msg)
assert rules[0]["id"] != rule_id
evidence["periodic_rule_drift_repair"] = {"started_at": started, "deleted_rule_id": rule_id, "restored_rules": rules}
request("auditor", "DELETE", "/api/v1/sequences/" + synced["sequence_id"])
for _ in range(36):
    missing = request("dojo", "GET", "/api/v2/auditor/profiles/control-drift/")
    if missing["state"] == "error" and missing["last_error"] == "auditor_http_404":
        break
    time.sleep(3)
else:
    msg = "Missing remote sequence remained falsely synced"
    raise RuntimeError(msg)
evidence["periodic_sequence_missing"] = missing
save("profile-control-cases.json", evidence)
request("dojo", "DELETE", "/api/v2/auditor/profiles/control-drift/")

refused = request("dojo", "DELETE", "/api/v2/auditor/profiles/baseline/")
assert refused["state"] == "delete_error" and refused["last_error"] == "auditor_http_409", refused
disabled = request("dojo", "POST", "/api/v2/auditor/profiles/baseline/disable/", body={})
assert disabled["state"] == "synced"
assert all(not r["enabled"] for r in disabled["remote"]["automations"])
evidence["historical_sequence_delete_refused_disable_allowed"] = {"refused": refused, "disabled": disabled}
save("profile-control-cases.json", evidence)
