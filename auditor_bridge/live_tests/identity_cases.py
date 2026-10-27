# ruff: noqa: S101
# Executable acceptance assertions: run with ordinary Python, never python -O.
"""GitLab rename/transfer and inventory exclusion cases on owned fixtures."""
import time
import uuid

from common import EVIDENCE, load, request, save, wait_services

wait_services()
integration = load("integration.json")
group = load("repositories.json")["group_id"]
node = load("repositories.json")["projects"][1]["project_id"]
results = load("identity-cases.json") if (EVIDENCE / "identity-cases.json").exists() else {}


def discover():
    return request("auditor", "POST", f'/api/v1/gitlab-integrations/{integration["gitlab_id"]}/discover', body={})


def mapping(project):
    return next(r for r in request("dojo", "GET", "/api/v2/auditor/repositories/")["items"] if r["repository_id"] == str(project))


def inventory(project):
    return next(r for r in request("auditor", "GET", "/api/v1/repositories")["items"] if r["repository_id"] == str(project))


if "rename_transfer" not in results:
    before = mapping(node)
    subgroup = request("gitlab", "POST", "/api/v4/groups", body={"name": "Transferred fixture", "path": "transferred-" + uuid.uuid4().hex[:6], "parent_id": group, "visibility": "private"})
    renamed = request("gitlab", "PUT", f"/api/v4/projects/{node}", body={"name": "NodeGoat renamed", "path": "nodegoat-renamed-" + uuid.uuid4().hex[:6]})
    transferred = request("gitlab", "PUT", f"/api/v4/projects/{node}/transfer", body={"namespace": subgroup["id"]})
    discover()
    for _ in range(45):
        after = mapping(node)
        if after["inventory"]["path"] == transferred["path_with_namespace"]:
            break
        time.sleep(2)
    else:
        msg = "Periodic inventory did not reconcile rename/transfer"
        raise RuntimeError(msg)
    assert before["product_id"] == after["product_id"]
    assert before["engagement_id"] == after["engagement_id"]
    assert len([r for r in request("dojo", "GET", "/api/v2/auditor/repositories/")["items"] if r["repository_id"] == str(node)]) == 1
    results["rename_transfer"] = {"before": before, "after": after, "gitlab_path": transferred["path_with_namespace"], "periodic_reconcile": True}
    save("identity-cases.json", results)
project = load("callback-only-create.json")["project_id"]
for case in ["disabled", "archived", "missing", "ref_filter"]:
    if case in results:
        continue
    if case == "disabled":
        request("auditor", "PATCH", f'/api/v1/gitlab-integrations/{integration["gitlab_id"]}/projects/{inventory(project)["id"]}', body={"enabled": False})
    elif case == "archived":
        request("gitlab", "POST", f"/api/v4/projects/{project}/archive", body={})
        discover()
    elif case == "missing":
        root_namespace = next(n for n in request("gitlab", "GET", "/api/v4/namespaces?search=root") if n["full_path"] == "root")
        if not request("gitlab", "GET", f"/api/v4/projects/{project}")["path_with_namespace"].startswith("root/"):
            request("gitlab", "PUT", f"/api/v4/projects/{project}/transfer", body={"namespace": root_namespace["id"]})
        for _ in range(30):
            discover()
            if inventory(project)["disappeared_at"] is not None:
                break
            time.sleep(2)
        else:
            msg = "Transfer/tombstone not reconciled"
            raise RuntimeError(msg)
    key = "excluded-" + case + "-" + uuid.uuid4().hex[:6]
    desired = {"name": key, "steps": [{"job_slug": "code-downloader"}, {"job_slug": "gitleaks"}],
        "asoc_integration_ids": [integration["asoc_id"]], "automations": [{"name": key, "integration_id": integration["gitlab_id"],
        "repository_ids": [project], "events": ["repository.discovered"],
        "ref_patterns": ["refs/heads/nonexistent-*"] if case == "ref_filter" else ["@default"], "enabled": True}]}
    request("dojo", "PUT", f"/api/v2/auditor/profiles/{key}/", body=desired)
    profile = request("dojo", "POST", f"/api/v2/auditor/profiles/{key}/sync/", body={})
    assert profile["state"] == "synced"
    snapshot = inventory(project)
    # Three actual poller intervals; no direct DB edits or manufactured events.
    time.sleep(35)
    scans = [s for s in request("auditor", "GET", "/api/v1/scans?limit=200") if s.get("sequence_id") == profile["sequence_id"]]
    assert not scans, (case, scans)
    request("dojo", "POST", "/api/v2/auditor/repositories/", body={})
    results[case] = {"inventory": snapshot, "dojo_mapping": mapping(project), "profile": profile, "observation_seconds": 35, "scan_count": 0}
    request("dojo", "DELETE", f"/api/v2/auditor/profiles/{key}/")
    if case == "disabled":
        request("auditor", "PATCH", f'/api/v1/gitlab-integrations/{integration["gitlab_id"]}/projects/{inventory(project)["id"]}', body={"enabled": True})
    elif case == "archived":
        request("gitlab", "POST", f"/api/v4/projects/{project}/unarchive", body={})
        discover()
    elif case == "missing":
        request("gitlab", "PUT", f"/api/v4/projects/{project}/transfer", body={"namespace": group})
        for _ in range(30):
            discover()
            if inventory(project)["disappeared_at"] is None:
                break
            time.sleep(2)
        request("auditor", "PATCH", f'/api/v1/gitlab-integrations/{integration["gitlab_id"]}/projects/{inventory(project)["id"]}', body={"enabled": True})
    save("identity-cases.json", results)
