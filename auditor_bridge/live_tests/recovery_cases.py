# ruff: noqa: S101, PT018
# Executable acceptance assertions: run with ordinary Python, never python -O.
"""Real network/container outages. Docker controls are restricted to this stand."""
import os
import time
import uuid

import httpx
from common import load, request, save, secret, wait_services

wait_services()
integration = load("integration.json")
prefix = os.environ["COMPOSE_PROJECT_NAME"]
assert prefix.startswith("auditor-asoc-live-")
docker = httpx.Client(transport=httpx.HTTPTransport(uds="/var/run/docker.sock"), base_url="http://docker", timeout=60)


def control(service, action):
    assert service in {"auditor-api", "dojo-web"}
    assert action in {"stop", "start"}
    response = docker.post(f"/v1.47/containers/{prefix}-{service}-1/{action}", params={"t": 3})
    assert response.status_code in {204, 304}, response.status_code


def profile(label, *, automated=True):
    key = label + "-" + uuid.uuid4().hex[:8]
    desired = {"name": key, "steps": [{"job_slug": "code-downloader"}, {"job_slug": "gitleaks"}],
        "asoc_integration_ids": [integration["asoc_id"]], "automations": []}
    if automated:
        desired["automations"] = [{"name": key, "integration_id": integration["gitlab_id"],
            "repository_ids": [load("report-cases.json")["project"]["id"]], "events": ["repository.discovered"],
            "ref_patterns": ["@default"], "enabled": True}]
    request("dojo", "PUT", f"/api/v2/auditor/profiles/{key}/", body=desired)
    return key, request("dojo", "POST", f"/api/v2/auditor/profiles/{key}/sync/", body={})


def scan_for(sequence, *, delivered):
    for _ in range(180):
        scans = [s for s in request("auditor", "GET", "/api/v1/scans?limit=200") if s.get("sequence_id") == sequence]
        if scans and scans[0]["status"] == "COMPLETED":
            scan = scans[0]
            callbacks = request("auditor", "GET", f'/api/v1/scans/{scan["id"]}/asoc/callbacks')["callbacks"]
            if callbacks and callbacks[0]["attempts"] >= 1:
                ack = (callbacks[0].get("acknowledgement") or {}).get("report_status")
                if (delivered and ack == "imported") or (not delivered and not ack):
                    return scan, callbacks
        time.sleep(2)
    msg = "Callback recovery timed out"
    raise RuntimeError(msg)


def asoc(url):
    current = request("auditor", "GET", "/api/v1/asoc-integrations/" + integration["asoc_id"])
    config = {"endpoint_url": url, "auth_header": "Bearer " + secret("callback-token"), "verify_tls": False,
        "scanner_mappings": {"*": {"enabled": False}}, "completion_signal": {"url": url, "method": "POST",
        "repository_ack": True, "headers": {"Authorization": "{integration.auth_header}"}, "body": {},
        "run_when": ["COMPLETED", "FAILED", "CANCELLED", "EXPIRED"], "verify_tls": False, "timeout_s": 120, "max_attempts": 5}}
    request("auditor", "PUT", "/api/v1/asoc-integrations/" + integration["asoc_id"], body={
        "name": current["name"], "provider": "custom_http", "is_active": True, "config": config})


mode = os.environ["RECOVERY_CASE"]
if mode == "lost_callback":
    try:
        asoc("http://dojo-web:8000/api/v2/auditor/callback/")
        key, state = profile("lost-callback")
        scan, callbacks = scan_for(state["sequence_id"], delivered=True)
        imports = [i for i in request("dojo", "GET", "/api/v2/auditor/imports/") if i["scan_id"] == scan["id"]]
        assert len(imports) == 1 and callbacks[0]["attempts"] >= 2, callbacks
        proxy = load("lost-callback-proxy.json")
        assert proxy["upstream_result"]["report_status"] == "imported"
        save("lost-callback-recovery.json", {"scan": scan, "callbacks": callbacks, "imports": imports, "proxy": proxy})
        request("dojo", "POST", f"/api/v2/auditor/profiles/{key}/disable/", body={})
    finally:
        asoc("http://dojo-web:8080/api/v2/auditor/callback/")
elif mode == "dojo_outage":
    key, state = profile("dojo-outage")
    try:
        control("dojo-web", "stop")
        scan, unavailable = scan_for(state["sequence_id"], delivered=False)
    finally:
        control("dojo-web", "start")
    wait_services()
    scan, delivered = scan_for(state["sequence_id"], delivered=True)
    imports = [i for i in request("dojo", "GET", "/api/v2/auditor/imports/") if i["scan_id"] == scan["id"]]
    assert len(imports) == 1
    assert delivered[0]["attempts"] >= 2
    save("dojo-outage.json", {"scan": scan, "during_outage": unavailable, "recovered": delivered, "imports": imports})
    request("dojo", "POST", f"/api/v2/auditor/profiles/{key}/disable/", body={})
elif mode == "auditor_outage":
    try:
        control("auditor-api", "stop")
        key, state = profile("auditor-outage", automated=False)
        assert state["state"] == "error" and state["last_error"] == "auditor_transport_or_json_error", state
    finally:
        control("auditor-api", "start")
    wait_services()
    for _ in range(120):
        recovered = request("dojo", "GET", f"/api/v2/auditor/profiles/{key}/")
        if recovered["state"] == "synced":
            break
        time.sleep(2)
    else:
        msg = "Periodic recovery timed out"
        raise RuntimeError(msg)
    matching = [s for s in request("auditor", "GET", "/api/v1/sequences") if s["name"] == key]
    assert len(matching) == 1
    save("auditor-outage.json", {"during_outage": state, "periodic_recovery": recovered, "sequence_count": len(matching)})
    request("dojo", "DELETE", f"/api/v2/auditor/profiles/{key}/")
else:
    msg = "Unknown recovery case"
    raise RuntimeError(msg)
