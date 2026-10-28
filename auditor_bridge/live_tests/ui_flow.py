# ruff: noqa: S101, PT018
# Executable acceptance assertions: run with ordinary Python, never python -O.
import json
import re
import uuid

import httpx
from common import load, request, save, secret, wait_services

wait_services()
base = "http://dojo-web:8080"
client = httpx.Client(base_url=base, follow_redirects=True, timeout=120)


def csrf(text):
    return re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', text).group(1)


login = client.get("/login")
response = client.post("/login", data={"username": "admin", "password": secret("dojo-admin"),
    "csrfmiddlewaretoken": csrf(login.text)}, headers={"Referer": base + "/login"})
assert response.status_code == 200 and "Logout" in response.text, response.status_code
page = client.get("/auditor/profiles/")
assert page.status_code == 200
assert "Auditor profiles" in page.text
key = "ui-" + uuid.uuid4().hex[:8]
desired = {"name": key, "steps": [{"job_slug": "code-downloader"}, {"job_slug": "gitleaks"}],
    "asoc_integration_ids": [load("integration.json")["asoc_id"]], "automations": []}
rejected = client.post("/auditor/profiles/", data={"key": key, "desired": json.dumps(desired), "action": "save"})
assert rejected.status_code == 403
results = []
for action in ["save", "sync", "disable"]:
    response = client.post("/auditor/profiles/", data={"key": key, "desired": json.dumps(desired),
        "action": action, "csrfmiddlewaretoken": csrf(page.text)}, headers={"Referer": base + "/auditor/profiles/"})
    assert response.status_code == 200 and "Actual result:" in response.text, response.status_code
    actual = request("dojo", "GET", f"/api/v2/auditor/profiles/{key}/")
    assert actual["state"] in (["pending", "synced"] if action == "save" else ["synced"]), actual
    results.append({"action": action, "http_status": response.status_code, "readback": actual})
    page = response
request("dojo", "DELETE", f"/api/v2/auditor/profiles/{key}/")
save("dojo-ui-flow.json", {"login": True, "csrf_missing_status": rejected.status_code, "results": results})
