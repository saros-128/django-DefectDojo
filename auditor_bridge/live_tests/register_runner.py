import os

from common import SECRETS, load, request, save

project = os.environ["COMPOSE_PROJECT_NAME"]

if not (SECRETS / "runner-token").exists():
    runner = request("gitlab", "POST", "/api/v4/user/runners", body={
        "runner_type": "instance_type", "description": project,
        "run_untagged": True, "tag_list": ["auditor-live"], "access_level": "not_protected"})
    p = SECRETS / "runner-token"
    p.write_text(runner["token"])
    p.chmod(0o600)
    save("runner.json", {"id": runner["id"], "description": project, "executor": "docker"})
else:
    runner = {"token": (SECRETS / "runner-token").read_text().strip(), **load("runner.json")}
directory = SECRETS / "runner-config"
directory.mkdir(mode=0o700, exist_ok=True)
config = directory / "config.toml"
config.write_text(f"""concurrent = 2
check_interval = 3
[[runners]]
  name = "{project}"
  url = "http://gitlab"
  id = {runner['id']}
  token = "{runner['token']}"
  executor = "docker"
  [runners.docker]
    image = "alpine:3.22.1"
    privileged = false
    disable_cache = true
    network_mode = "{project}_default"
    pull_policy = "if-not-present"
    allowed_images = ["alpine:3.22.1"]
    memory = "256m"
    cpus = "1"
""")
config.chmod(0o600)
