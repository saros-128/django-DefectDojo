from common import EVIDENCE, load, request, save, secret, wait_services

wait_services()

state = load("integration.json") if (EVIDENCE / "integration.json").exists() else {}
callback = "http://dojo-web:8080/api/v2/auditor/callback/"
if "asoc_id" not in state:
    integration = request("auditor", "POST", "/api/v1/asoc-integrations", body={
        "name": "Dojo transactional bridge live", "provider": "custom_http", "is_active": True,
        "config": {"endpoint_url": callback, "auth_header": "Bearer " + secret("callback-token"),
                   "verify_tls": False, "scanner_mappings": {"*": {"enabled": False}},
                   "completion_signal": {"url": callback, "method": "POST", "repository_ack": True,
                                         "headers": {"Authorization": "{integration.auth_header}"},
                                         "body": {}, "run_when": ["COMPLETED", "FAILED", "CANCELLED", "EXPIRED"],
                                         "verify_tls": False, "timeout_s": 120, "max_attempts": 5}}})
    state["asoc_id"] = integration["id"]
    save("integration.json", state)
if "gitlab_id" not in state:
    integration = request("auditor", "POST", "/api/v1/gitlab-integrations", body={
        "name": "GitLab live isolated", "base_url": "http://gitlab", "planning_mode": "auto",
        "verify_tls": False, "sg_blocking": False, "api_token": secret("auditor-gitlab-token"),
        "clone_token": secret("auditor-gitlab-token"), "webhook_secret": secret("webhook-token"),
        "import_mode": "selected", "import_group_paths": ["auditor-live"], "auto_enable_projects": True,
        "auto_scan_enabled": False, "poll_interval_seconds": 30, "discovery_interval_seconds": 60})
    state["gitlab_id"] = integration["id"]
    save("integration.json", state)
discovery = request("auditor", "POST", f'/api/v1/gitlab-integrations/{state["gitlab_id"]}/discover', body={})
save("initial-discovery.json", discovery)
hooks = []
for project in load("repositories.json")["projects"]:
    project_id = project["project_id"]
    url = f'http://auditor-api:8000/api/v1/gitlab/webhooks/{state["gitlab_id"]}'
    existing = request("gitlab", "GET", f"/api/v4/projects/{project_id}/hooks")
    hook = next((h for h in existing if h["url"] == url), None)
    if hook is None:
        hook = request("gitlab", "POST", f"/api/v4/projects/{project_id}/hooks", body={
            "url": url, "token": secret("webhook-token"), "push_events": True, "merge_requests_events": True,
            "tag_push_events": True, "pipeline_events": True, "releases_events": True, "deployment_events": True,
            "enable_ssl_verification": False})
    hooks.append({"project_id": project_id, "hook_id": hook["id"]})
save("hooks.json", hooks)
inventory = request("dojo", "POST", "/api/v2/auditor/repositories/", body={})
save("initial-dojo-inventory.json", request("dojo", "GET", "/api/v2/auditor/repositories/"))
