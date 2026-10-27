import os
import shutil
import subprocess
from pathlib import Path

from common import request, save


def git(args, cwd=None):
    result = subprocess.run(["/usr/bin/git", *args], cwd=cwd, text=True, capture_output=True, check=False,
                            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/harness/git_askpass.py"})
    if result.returncode:
        raise RuntimeError("git operation failed: " + result.stderr[-1500:])
    return result.stdout.strip()


groups = request("gitlab", "GET", "/api/v4/groups?search=auditor-live")
group = next((g for g in groups if g["path"] == "auditor-live"), None)
if group is None:
    group = request("gitlab", "POST", "/api/v4/groups", body={"name": "Auditor live", "path": "auditor-live", "visibility": "private"})
UPSTREAM_PINS = {"https://github.com/juice-shop/juice-shop.git": "717f5efa886b0417eeecb52378b03cf10567b7e2", "https://github.com/OWASP/NodeGoat.git": "c5cb68a7084e4ae7dcc60e6a98768720a81841e8"}
provenance = []
for name, upstream in [("juice-shop", "https://github.com/juice-shop/juice-shop.git"), ("nodegoat", "https://github.com/OWASP/NodeGoat.git")]:
    if os.environ.get("FIXTURE_ONLY") and name != os.environ["FIXTURE_ONLY"]:
        continue
    existing = request("gitlab", "GET", f'/api/v4/groups/{group["id"]}/projects')
    project = next((p for p in existing if p["path"] == name), None)
    if project is None:
        project = request("gitlab", "POST", "/api/v4/projects", body={"name": name, "namespace_id": group["id"], "visibility": "private", "default_branch": "main"})
    path = Path("/fixtures") / name
    if not path.exists():
        snapshot = Path("/snapshots") / name
        if snapshot.exists():
            shutil.copytree(snapshot, path)
        else:
            git(["clone", "--depth=1", "--no-tags", upstream, str(path)])
        git(["fetch", "--depth=1", "origin", UPSTREAM_PINS[upstream]], path)
        git(["checkout", "--detach", "FETCH_HEAD"], path)
        upstream_sha = git(["rev-parse", "HEAD"], path)
        git(["config", "user.name", "Auditor integration test"], path)
        git(["config", "user.email", "auditor-live@example.invalid"], path)
        git(["remote", "rename", "origin", "upstream"], path)
        git(["remote", "add", "origin", project["http_url_to_repo"]], path)
        git(["checkout", "-B", "main"], path)
        (path / ".gitlab-ci.yml").write_text("""workflow:
  rules:
    - if: '$CI_COMMIT_BRANCH'
stages: [verify, deploy]
verify:
  image: alpine:3.22.1
  stage: verify
  script: ["echo real-gitlab-runner-verification"]
deploy:
  image: alpine:3.22.1
  stage: deploy
  script: ["echo real-gitlab-runner-deployment"]
  environment:
    name: live-test
""")
        git(["add", ".gitlab-ci.yml"], path)
        git(["commit", "-m", "test: add isolated live integration pipeline"], path)
    else:
        upstream_sha = UPSTREAM_PINS[upstream]
    if not git(["ls-remote", "--heads", "origin", "main"], path):
        # GitLab rejects shallow-history pushes. Import the pinned source tree
        # as an explicit snapshot while retaining upstream provenance below.
        git(["checkout", "--orphan", "live-import", "HEAD"], path)
        git(["commit", "-m", "test: import pinned upstream source snapshot"], path)
        git(["branch", "-M", "main"], path)
        git(["push", "-u", "origin", "main"], path)
    provenance.append({"project_id": project["id"], "path": project["path_with_namespace"], "upstream": upstream,
                       "upstream_sha": upstream_sha, "fixture_sha": git(["rev-parse", "HEAD"], path), "default_branch": "main"})
save("repositories.json", {"group_id": group["id"], "projects": provenance})
