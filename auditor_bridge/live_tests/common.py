import json
import os
import time
from pathlib import Path

import httpx

SECRETS = Path("/run/live-secrets")
EVIDENCE = Path(os.environ.get("LIVE_EVIDENCE_DIR", "/evidence"))


def wait_services():
    for url in ["http://auditor-api:8000/api/v1/health/live", "http://dojo-web:8080/login"]:
        for _ in range(60):
            try:
                if httpx.get(url, timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("Service readiness timed out: " + url)


def secret(name):
    return (SECRETS / name).read_text().strip()


def request(service, method, path, *, body=None, expected=(200, 201, 202, 204), headers=None, **kwargs):
    urls = {"gitlab": "http://gitlab", "dojo": "http://dojo-web:8080", "auditor": "http://auditor-api:8000"}
    auth = {"gitlab": {"PRIVATE-TOKEN": secret("gitlab-token")},
            "auditor": {"Authorization": "Bearer " + secret("auditor-admin")}}
    if service == "dojo" and (SECRETS / "dojo-token").exists():
        auth["dojo"] = {"Authorization": "Token " + secret("dojo-token")}
    merged = {**auth.get(service, {}), **(headers or {})}
    for attempt in range(20):
        try:
            response = httpx.request(method, urls[service] + path, json=body, headers=merged, timeout=120, **kwargs)
            break
        except httpx.TransportError:
            if method != "GET" or attempt == 19:
                raise
            time.sleep(1)
    if response.status_code not in expected:
        # Bodies can echo sensitive submitted credentials. Keep logs bounded and redacted.
        detail = response.text[:2000]
        for f in SECRETS.iterdir():
            if f.is_file() and not f.name.endswith((".env", ".log")):
                value = f.read_text().strip()
                if len(value) >= 16:
                    detail = detail.replace(value, "[REDACTED]")
        msg = f"{service} {method} {path}: HTTP {response.status_code}: {detail}"
        raise RuntimeError(msg)
    return response.json() if response.content else None


def save(name, value):
    (EVIDENCE / name).write_text(json.dumps(value, indent=2) + "\n")


def load(name):
    return json.loads((EVIDENCE / name).read_text())
