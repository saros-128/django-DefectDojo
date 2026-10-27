"""Generate an isolated stand using only Python's standard library on the host."""
import argparse
import base64
import json
import os
import re
import secrets
import shlex
import shutil
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", type=Path, required=True)
parser.add_argument("--auditor-source", type=Path, required=True)
parser.add_argument("--project", default="auditor-asoc-live-repro")
parser.add_argument("--port-base", type=int, default=18080)
args = parser.parse_args()
if not re.fullmatch(r"auditor-asoc-live-[a-z0-9-]+", args.project):
    parser.error("Use an isolated auditor-asoc-live-* project name")
root = args.root.resolve()
source = args.auditor_source.resolve()
dojo = Path(__file__).resolve().parents[2]
if root.exists() or not (source / "docker/app.Dockerfile").is_file():
    parser.error("Root must not exist and Auditor source must contain docker/app.Dockerfile")
os.umask(0o077)
root.mkdir(parents=True)
for name in ["secrets", "secrets/runner-config", "runtime", "runtime/fixtures", "evidence"]:
    (root / name).mkdir(exist_ok=True)
shutil.copytree(Path(__file__).parent, root / "harness", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
shutil.copy2(Path(__file__).with_name("compose.template.json"), root / "compose.json")
values = {name: secrets.token_hex(24) for name in ["auditor-admin", "auditor-db", "bao-token", "callback-token",
    "dojo-admin", "dojo-db", "dojo-secret", "gitlab-root", "gitlab-token", "webhook-token"]}
values["dojo-aes"] = secrets.token_hex(16)
for name, value in values.items():
    (root / "secrets" / name).write_text(value + "\n")


def envfile(name, data):
    (root / "secrets" / name).write_text("".join(f"{key}={value}\n" for key, value in data.items()))


envfile("dojo-db.env", {"POSTGRES_DB": "dojo", "POSTGRES_USER": "dojo", "POSTGRES_PASSWORD": values["dojo-db"]})
envfile("auditor-db.env", {"POSTGRES_DB": "auditor", "POSTGRES_USER": "auditor", "POSTGRES_PASSWORD": values["auditor-db"]})
envfile("bao.env", {"BAO_DEV_ROOT_TOKEN_ID": values["bao-token"], "SKIP_CHOWN": "true", "SKIP_SETCAP": "true"})
envfile("dojo.env", {
    "DD_DATABASE_URL": f"postgresql://dojo:{values['dojo-db']}@dojo-db:5432/dojo",
    "DD_CELERY_BROKER_URL": "redis://dojo-redis:6379/0", "DD_CACHE_URL": "redis://dojo-redis:6379/1",
    "DD_SECRET_KEY": values["dojo-secret"], "DD_CREDENTIAL_AES_256_KEY": values["dojo-aes"],
    "DD_ADMIN_USER": "admin", "DD_ADMIN_PASSWORD": values["dojo-admin"], "DD_ADMIN_MAIL": "admin@example.invalid",
    "DD_INITIALIZE": "true", "DD_ALLOWED_HOSTS": "dojo,dojo-web,localhost,127.0.0.1",
    "DD_UWSGI_NUM_OF_PROCESSES": 2, "DD_UWSGI_NUM_OF_THREADS": 2, "DD_CELERY_WORKER_CONCURRENCY": 2,
    "AUDITOR_BRIDGE_URL": "http://auditor-api:8000", "AUDITOR_BRIDGE_IMPORT_USER": "admin",
    "AUDITOR_BRIDGE_TOKEN_FILE": "/run/live-secrets/auditor-admin", "AUDITOR_BRIDGE_CALLBACK_TOKEN_FILE": "/run/live-secrets/callback-token",
})
envfile("auditor.env", {
    "DATABASE_URL": f"postgresql+asyncpg://auditor:{values['auditor-db']}@auditor-db:5432/auditor",
    "REDIS_URL": "redis://auditor-redis:6379/0", "ARTIFACT_ROOT": "/data/artifacts", "WORKSPACE_ROOT": "/data/workspaces",
    "OPENBAO_ADDR": "http://openbao:8200", "OPENBAO_USE_PROXY": "false", "OPENBAO_TOKEN_FILE": "/run/live-secrets/bao-token",
    "AUDITOR_PUBLIC_URL": "http://auditor-api:8000", "AUTH_DISABLED": "false", "AUDITOR_ADMIN_TOKEN": values["auditor-admin"],
    "ALLOWED_HOSTS": "auditor-api,localhost,127.0.0.1", "AUTH_RATE_LIMIT_PER_MINUTE": 10000,
    "AUTH_PUBLIC_RATE_LIMIT_PER_MINUTE": 600, "EXECUTOR": "docker", "PREFLIGHT_REQUIRED": "true", "REQUIRE_IMAGE_DIGESTS": "true",
    "AUDITOR_CATALOG_TIER": "minimal", "AUDITOR_CATALOG_ONLY": "code-downloader,gitleaks,detect-secrets,opengrep",
    "HARBOR_INSTALL_MODE": "preinstalled", "HARBOR_CONNECTION_MODE": "embedded",
    "HARBOR_EMBEDDED_REGISTRY_URL": "http://scanner-registry:5000", "HARBOR_EMBEDDED_UI_URL": "http://scanner-registry:5000",
    "HARBOR_RUNTIME_REGISTRY_HOST": f"localhost:{args.port_base + 4}", "HARBOR_PROJECT": "auditor", "HARBOR_VERIFY_TLS": "false",
    "OUTBOUND_ALLOWED_HOSTS": "gitlab,dojo-web,auditor-api,scanner-registry", "GITLAB_POLL_TICK_SECONDS": 10,
    "ASOC_CALLBACK_SWEEP_INTERVAL_S": 10, "LOG_FORMAT": "json", "AUDITOR_SESSION_SECRET": secrets.token_hex(32),
    "FIELD_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
})
config = {"COMPOSE_PROJECT_NAME": args.project, "AUDITOR_SOURCE": str(source), "DOJO_SOURCE": str(dojo),
    "APP_IMAGE": args.project + "-app:local", "BRIDGE_IMAGE": args.project + "-dojo:bridge",
    "GITLAB_PORT": str(args.port_base), "DOJO_PORT": str(args.port_base + 1), "AUDITOR_PORT": str(args.port_base + 2),
    "REGISTRY_PORT": str(args.port_base + 4)}
(root / ".env").write_text("".join(f"{k}={json.dumps(v)}\n" for k, v in config.items()))
(root / "config.sh").write_text("".join(f"export {k}={shlex.quote(v)}\n" for k, v in config.items()))
(root / ".gitignore").write_text("secrets/\nruntime/\nevidence/\n.env\nconfig.sh\n")
