# ruff: noqa: S101
# Executable acceptance assertions: run with ordinary Python, never python -O.
import secrets

import httpx
from common import EVIDENCE, SECRETS, load, request, save, secret

state = load("service-account.json") if (EVIDENCE / "service-account.json").exists() else {}
users = request("gitlab", "GET", "/api/v4/users?username=auditor-scanner")
if users:
    user = users[0]
else:
    user = request("gitlab", "POST", "/api/v4/users", body={
        "username": "auditor-scanner", "name": "Auditor read only scanner",
        "email": "auditor-scanner@example.invalid", "password": secrets.token_hex(24), "skip_confirmation": True})
group = next(g for g in request("gitlab", "GET", "/api/v4/groups?search=auditor-live") if g["path"] == "auditor-live")
members = request("gitlab", "GET", f'/api/v4/groups/{group["id"]}/members')
if not any(m["id"] == user["id"] for m in members):
    request("gitlab", "POST", f'/api/v4/groups/{group["id"]}/members', body={"user_id": user["id"], "access_level": 20})
if not (SECRETS / "auditor-gitlab-token").exists():
    token = request("gitlab", "POST", f'/api/v4/users/{user["id"]}/personal_access_tokens', body={
        "name": "auditor-live-read-only", "scopes": ["read_api", "read_repository"]})
    f = SECRETS / "auditor-gitlab-token"
    f.write_text(token["token"])
    f.chmod(0o600)
projects = request("gitlab", "GET", "/api/v4/projects?search=inaccessible-fixture")
negative = next((p for p in projects if p["path"] == "inaccessible-fixture"), None)
if negative is None:
    negative = request("gitlab", "POST", "/api/v4/projects", body={"name": "inaccessible-fixture", "visibility": "private", "initialize_with_readme": True})
headers = {"PRIVATE-TOKEN": secret("auditor-gitlab-token")}
response = httpx.get(f'http://gitlab/api/v4/projects/{negative["id"]}', headers=headers)
assert response.status_code == 404
visible = httpx.get("http://gitlab/api/v4/projects?simple=true&per_page=100", headers=headers).json()
assert negative["id"] not in [p["id"] for p in visible]
state.update({"user_id": user["id"], "group_id": group["id"], "group_role": "Reporter",
              "scopes": ["read_api", "read_repository"], "inaccessible_project_id": negative["id"],
              "inaccessible_http_status": response.status_code, "visible_project_ids": [p["id"] for p in visible]})
save("service-account.json", state)
