# ruff: noqa: S101
# Executable acceptance assertions: run with ordinary Python, never python -O.
import uuid

import httpx
from common import load, request, save, secret, wait_services

wait_services()
integration = load("integration.json")
before = request("auditor", "GET", "/api/v1/sequences")
result = {}
for case in ["never_attempted", "rejected"]:
    key = "delete-" + case.replace("_", "-") + "-" + uuid.uuid4().hex[:6]
    desired = {"name": key, "steps": [{"job_slug": "code-downloader"}, {"job_slug": "gitleaks"}],
        "asoc_integration_ids": [integration["asoc_id"]], "automations": []}
    if case == "rejected":
        desired["automations"] = [{"name": key, "integration_id": integration["gitlab_id"], "events": [],
            "schedule": {"kind": "weekly", "weekdays": [0], "hour": 0, "minute": 0, "timezone": "Invalid/Live-Zone"}, "enabled": False}]
    state = request("dojo", "PUT", f"/api/v2/auditor/profiles/{key}/", body=desired)
    if case == "rejected":
        state = request("dojo", "POST", f"/api/v2/auditor/profiles/{key}/sync/", body={})
        assert state["last_error"] == "auditor_http_422"
        assert state["sequence_id"] is None
        refused = request("dojo", "POST", f"/api/v2/auditor/profiles/{key}/disable/", body={}, expected=(409,))
        assert refused["error"] == "rejected_profile_correct_or_delete"
    else:
        assert state["attempts"] == 0
    deleted = request("dojo", "DELETE", f"/api/v2/auditor/profiles/{key}/")
    assert deleted["state"] == "deleted"
    assert deleted["sequence_id"] is None
    result[case] = {"before": state, "deleted": deleted}
after = request("auditor", "GET", "/api/v1/sequences")
assert {s["id"] for s in before} == {s["id"] for s in after}
scan = load("callback-only-create.json")["scan"]
body = {"callback_id": str(uuid.uuid4()), "scan_id": scan["id"], "scan_status": "COMPLETED",
    **scan["repository_identity"], "commit_sha": scan["commit_sha"], "context": {},
    "report_url": f'http://auditor-api:8000/api/v1/scans/{scan["id"]}/findings?format=json'}
origin_cases = []
for instance, status in [("https://git:bad", 400), ("https://git:65536", 400), ("https://[invalid", 400), ("http://GITLAB.:80/", 200)]:
    response = httpx.post("http://dojo-web:8080/api/v2/auditor/callback/", json={**body, "callback_id": str(uuid.uuid4()), "instance": instance},
        headers={"Authorization": "Bearer " + secret("callback-token")}, timeout=120)
    assert response.status_code == status, (instance, response.status_code)
    origin_cases.append({"instance": instance, "status": response.status_code, "body": response.json()})
save("review-cases.json", {"deletions": result, "sequence_set_unchanged": True, "origin_cases": origin_cases})
