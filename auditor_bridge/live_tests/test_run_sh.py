"""Exercise harness orchestration with sudo-style environment filtering."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class RunScriptTests(unittest.TestCase):
    def invoke(self, mode):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "harness").mkdir()
            (root / "bin").mkdir()
            (root / "secrets").mkdir()
            (root / "evidence").mkdir()
            for name in ["auditor-admin", "callback-token"]:
                (root / "secrets" / name).write_text("fixture-only\n", encoding="utf-8")
            (root / "harness/bootstrap_gitlab.rb").touch()
            trace = root / "docker-calls.jsonl"
            shutil.copy2(Path(__file__).with_name("run.sh"), root / "harness/run.sh")
            (root / "config.sh").write_text(
                "export COMPOSE_PROJECT_NAME=auditor-asoc-live-regression\nexport APP_IMAGE=fixture-image\n"
                "export BRIDGE_IMAGE=fixture-bridge AUDITOR_SOURCE=/fixture DOJO_SOURCE=/fixture REGISTRY_PORT=18084\n",
                encoding="utf-8",
            )
            commands = {
                "sudo": '#!/bin/sh\nshift\nexec env -i PATH="$PATH" "$@"\n',
                "docker": """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path
args = sys.argv[1:]
with Path(TRACE_PATH).open("a", encoding="utf-8") as output:
    output.write(json.dumps(args) + "\\n")
environment = {}
for index, arg in enumerate(args[:-1]):
    if arg == "-e":
        key, separator, value = args[index + 1].partition("=")
        environment[key] = value if separator else os.environ.get(key)
print(json.dumps(environment))
""".replace("TRACE_PATH", repr(str(trace))),
            }
            for name, source in commands.items():
                executable = root / "bin" / name
                executable.write_text(source, encoding="utf-8")
                executable.chmod(0o755)
            result = subprocess.run(
                ["/bin/bash", str(root / "harness/run.sh"), mode, "fixture.py"],
                env={**os.environ, "PATH": str(root / "bin") + ":" + os.environ["PATH"], "LIVE_DOCKER_SUDO": "1"},
                capture_output=True, text=True, check=True,
            )
            return result.stdout, [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]

    def test_scenario_receives_project_after_sudo_filters_environment(self):
        output, _calls = self.invoke("scenario")
        environment = json.loads(output.splitlines()[0])
        self.assertEqual(environment["COMPOSE_PROJECT_NAME"], "auditor-asoc-live-regression")

    def test_setup_refreshes_nginx_before_bootstrap_after_backend_recreation(self):
        _output, calls = self.invoke("setup")
        backend = next(index for index, args in enumerate(calls) if "up" in args and "dojo" in args)
        refresh = next((index for index, args in enumerate(calls) if args[-2:] == ["restart", "dojo-web"]), None)
        bootstrap = next(index for index, args in enumerate(calls) if "gitlab-rails" in args)
        self.assertIsNotNone(refresh, "The nginx upstream must be refreshed after backend recreation")
        self.assertLess(backend, refresh)
        self.assertLess(refresh, bootstrap)


if __name__ == "__main__":
    unittest.main()
