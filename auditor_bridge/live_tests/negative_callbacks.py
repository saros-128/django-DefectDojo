# ruff: noqa: S101
# Executable acceptance assertions: run with ordinary Python, never python -O.
"""Exercise real committed imports and callback authorization; never fabricate findings."""
import concurrent.futures
import uuid

import httpx
from common import load, request, save, secret, wait_services

wait_services()
fixture = load("callback-only-create.json")
scan = fixture["scan"]
body = {"callback_id": str(uuid.uuid4()), "scan_id": scan["id"], "scan_status": scan["status"],
    **scan["repository_identity"], "commit_sha": scan["commit_sha"],
    "report_url": f'http://auditor-api:8000/api/v1/scans/{scan["id"]}/findings?format=json', "context": {}}
url = "http://dojo-web:8080/api/v2/auditor/callback/"


def callback(payload, *, valid_token=True):
    response = httpx.post(url, json=payload, headers={"Authorization": "Bearer " + (secret("callback-token") if valid_token else "invalid-test-token")}, timeout=120)
    return {"status_code": response.status_code, "body": response.json()}


before = request("dojo", "GET", "/api/v2/auditor/imports/")
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
    duplicates = list(pool.map(lambda _: callback(body), range(6)))
assert all(r["status_code"] == 200 and r["body"]["report_status"] == "imported" for r in duplicates), duplicates
different_id = callback({**body, "callback_id": str(uuid.uuid4())})
assert different_id["status_code"] == 200
cases = {}
for name, changed, code in [
    ("same_callback_changed_body", {"context": {"changed": True}}, 409),
    ("different_callback_wrong_sha", {"callback_id": str(uuid.uuid4()), "commit_sha": "0" * 40}, 409),
    ("different_callback_wrong_identity", {"callback_id": str(uuid.uuid4()), "repository_id": "2"}, 409),
    ("report_url_ssrf", {"callback_id": str(uuid.uuid4()), "report_url": "http://169.254.169.254/latest/meta-data"}, 400),
    ("invalid_repository_zero", {"repository_id": "0"}, 400),
    ("invalid_repository_overlong", {"repository_id": "1" * 21}, 400),
    ("invalid_scan_id", {"scan_id": "invalid"}, 400),
    ("unknown_field", {"unexpected": True}, 400),
]:
    result = callback({**body, **changed})
    assert result["status_code"] == code, (name, result)
    cases[name] = result
cases["invalid_callback_token"] = callback(body, valid_token=False)
assert cases["invalid_callback_token"]["status_code"] == 401
overlong = request("dojo", "PUT", "/api/v2/auditor/profiles/" + "a" * 101 + "/", body={}, expected=(400,))
after = request("dojo", "GET", "/api/v2/auditor/imports/")
assert next(i for i in before if i["scan_id"] == scan["id"]) == next(i for i in after if i["scan_id"] == scan["id"])
assert len([i for i in after if i["scan_id"] == scan["id"]]) == 1
count = request("dojo", "GET", f'/api/v2/findings/?test={fixture["import"]["test_id"]}&limit=1')["count"]
assert count == fixture["import"]["finding_count"]
integration = load("integration.json")
hook_url = "http://auditor-api:8000/api/v1/gitlab/webhooks/" + integration["gitlab_id"]
invalid_token = httpx.post(hook_url, json={"object_kind": "push"}, headers={"X-Gitlab-Token": "incorrect", "X-Gitlab-Event": "Push Hook"}, timeout=30)
assert invalid_token.status_code == 401, invalid_token.status_code
invalid_body = httpx.post(hook_url, content=b"{", headers={"X-Gitlab-Token": secret("webhook-token"), "X-Gitlab-Event": "Push Hook", "Content-Type": "application/json"}, timeout=30)
assert invalid_body.status_code in {400, 422}, invalid_body.status_code
hidden = str(load("service-account.json")["inaccessible_project_id"])
inventory = request("auditor", "GET", "/api/v1/repositories")
dojo = request("dojo", "GET", "/api/v2/auditor/repositories/")
assert hidden not in [r["repository_id"] for r in inventory["items"]]
assert hidden not in [r["repository_id"] for r in dojo["items"]]
save("negative-callbacks.json", {"duplicates": duplicates, "different_callback_id": different_id, "cases": cases,
    "overlong_profile_key": overlong, "imports_unchanged": True, "native_finding_count": count,
    "invalid_webhook_token_status": invalid_token.status_code, "invalid_webhook_json_status": invalid_body.status_code,
    "hidden_project_absent_from_auditor_and_dojo": hidden})
