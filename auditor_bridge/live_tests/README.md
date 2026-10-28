# Linux live acceptance harness

This directory recreates an **isolated, disposable** GitLab CE, Docker Runner,
Auditor, DefectDojo and scanner registry stand. Application code, API scenarios,
migrations and scanners execute in Docker. Run configuration generation in a
container too, preserving absolute host paths for subsequent Docker bind mounts.

## Requirements and isolation

- Linux Docker Engine and Compose v2, Git. No pip,
  npm or system package installation on the host. Passwordless `sudo docker`
  is the default; set `LIVE_DOCKER_SUDO=0` when Docker is directly accessible.
- Recommended: 32 GiB RAM, 20 GiB free disk before pulling images. GitLab alone
  consumes about 6 GiB of image storage. Stop if the chosen ports are occupied;
  select another explicitly authorized stand rather than changing an existing service.
- An Auditor source checkout with the immutable report snapshot API, commit
  `7b2e8d4611983828c051723600ddefb55721521d` or newer. The complete packaged
  fresh run used `161eaf30a17e9ff9dcd09ae220bb616dad2a2095`.
- Three already prepared scanner images in a local source registry:
  `auditor/gitleaks:8.30.1`, `auditor/detect-secrets:1.5.0`,
  `auditor/opengrep:1.22.0-rules-f1d2b56`. Set `SCANNER_SOURCE_REGISTRY`
  (default `localhost:5000`). Setup inspects those exact images, tags copies
  into the new registry and pushes only there. Use Auditor's existing image
  preparation scripts in Docker if these images have not been prepared.
- GitLab fixtures use the pinned Juice Shop and NodeGoat source revisions in
  `prepare_repositories.py`. They are imported as source snapshots because
  GitLab rejects shallow-history pushes. The harness adds a controlled CI
  definition; Runner executes real verify/deploy jobs. Scanner findings come
  from the upstream applications, not injected findings JSON.

Only GitLab, Dojo, Auditor and the new registry are published, on loopback
ports 18080, 18081, 18082 and 18084 by default. Proxies have no published ports.
Every Docker resource belongs to `auditor-asoc-live-*`; there is no global prune.
The application worker and CI Runner use the local Docker socket and therefore
must run only on a trusted, disposable test machine.

## Recreate

From the Dojo checkout, provide a new directory and the existing Auditor source:

```sh
# /absolute/path contains both source directories and the new stand directory.
sudo docker run --rm --user "$(id -u):$(id -g)" \
  -v /absolute/path:/absolute/path -w "$PWD" \
  --entrypoint python auditor-app:ubuntu auditor_bridge/live_tests/prepare.py \
  --root /absolute/path/auditor-live-stand \
  --auditor-source /absolute/path/Auditor \
  --project auditor-asoc-live-repro
bash /absolute/path/auditor-live-stand/harness/run.sh setup
bash /absolute/path/auditor-live-stand/harness/run.sh core
bash /absolute/path/auditor-live-stand/harness/run.sh recovery
bash /absolute/path/auditor-live-stand/harness/run.sh review
bash /absolute/path/auditor-live-stand/harness/run.sh scenario final_evidence.py
```

`prepare.py` refuses an existing destination. `--port-base` changes all four
loopback ports together. Source checkouts are read, not modified. Secrets are
randomly generated under a 0700 directory, in 0600 files, and never printed.
Dojo's separate token directory is owned by container user `1001:1337`.
For a source archive without `.git`, export `AUDITOR_SOURCE_REVISION` from its
release manifest before setup; `AUDITOR_VERSION` defaults to `0.3.5`.

Setup can take several minutes for GitLab and Dojo migrations. It creates a
separate Reporter service account restricted to the fixture group, with only
`read_api` and `read_repository`. Administrative GitLab credentials are used
only by fixture/bootstrap scripts. A private project outside that scope must
return 404 to the service account and remain absent from both inventories.

If outbound GitHub access is unavailable inside Docker, prepare public source
snapshots with Git in `runtime/upstream/juice-shop` and `runtime/upstream/nodegoat`
at the exact pinned commits, preserving `.git`. Export `LIVE_SNAPSHOT_ROOT`
with that directory's absolute path; `run.sh` mounts it at `/snapshots:ro`.
Each checkout must have only the upstream URL as its `origin` remote.
This is a source-download
fallback; it does not run either application or install host dependencies.

Use a fresh stand directory for a fresh acceptance run. Individual scenario
scripts retain evidence/checkpoints for diagnosis and are not a general fixture
reset mechanism. Run modes sequentially: recovery deliberately stops **only**
the new stand's services. Do not run recovery during another scenario.

## What the modes verify

- `core`: three actual baseline scanners; nine GitLab trigger types; actual
  interval/weekly scheduler; profile CRUD/read-back/normalization/drift repair;
  callback-only auto-create while reconciliation is stopped; valid empty report;
  failed scan and completed scan with a failed scanner; concurrent duplicate
  callbacks, exact real webhook payload replay, auth/input/SSRF refusal; rename,
  subgroup transfer, disabled/archived/missing/ref exclusions; native session UI
  Save/Sync/Disable and CSRF rejection.
- `recovery`: dropped sequence-create response after remote commit, followed by
  desired edit and receipt recovery; dropped callback response after native
  import; actual Dojo outage and outbox recovery; actual Auditor outage and
  periodic profile recovery. All imports must remain unique.
- `review`: deleting a never-attempted or HTTP-422-rejected intent without a
  remote create, explicit rejected-disable refusal, invalid origin/port refusal,
  trailing-dot/default-port normalization. The background bridge worker is
  stopped briefly so the never-attempted state is deterministic.
- `final_evidence.py`: for every committed receipt, compare Auditor JSON finding
  count with Dojo REST count and stored receipt, plus immutable identity and SHA.
  Direct database counts are a separate read-only check, shown below.

```sh
# Run from the generated stand. This prints counts and IDs, not credentials.
sudo docker compose -f compose.json exec -T dojo python manage.py shell -c \
  'from auditor_bridge.models import ScanImport; print([(str(i.scan_id), i.test_id, i.finding_count, i.test.finding_set.count()) for i in ScanImport.objects.select_related("test")])'
```

The superuser UI is `http://127.0.0.1:18081/auditor/profiles/`. Retrieve the
password privately from `secrets/dojo-admin`; do not paste it into logs or Git.

## Evidence and limits

`evidence/` holds scenario JSON, source revisions, GitLab action/scan IDs,
callback attempts, native Product/Engagement/Test IDs and count read-backs.
Raw findings and runtime artifacts remain local. `secrets/` also holds private
initializer output; never publish that directory or rendered Compose config.
Publish only reviewed, sanitized summaries such as the included `acceptance.json`.
Before publication compare every output with actual secret-file values; require
zero matches. Do not capture GitLab webhook request headers (they contain tokens).

`acceptance.json` preserves the original stand's historical evidence, including
its original packaging limitations. `fresh-acceptance.json` records the complete
packaged fresh installation on 2026-10-27: new directory, Compose project,
secrets, databases and private fixtures; `setup`, all `core`, `recovery`, `review`
scenarios and `final_evidence.py` passed. It contains exact source revisions,
commands, new scan/callback IDs and the independent native database read-back.
The clean run produced **17 native Tests and 544 findings**, with equal Auditor
report, import-receipt, Dojo REST and database counts. All 17 imports have
acknowledged callbacks; the real Runner completed 18 successful jobs. These are
new results, not the original stand's larger totals from repeated scenarios.

Fresh setup exposed two orchestration defects, both fixed in this package:
`sudo` filters inherited environment variables, so project identity is passed
as an explicit Docker `-e NAME=value`; nginx retains the resolved uwsgi address,
so the harness restarts `dojo-web` after backend recreation in setup/recovery.
Setup succeeded on its third invocation after those fixes. Both regressions
fail against the original script and pass after the fix. Four packaging tests
and twelve bridge contract tests passed in Docker.

Run the packaging checks from a generated stand without installing host dependencies:

```sh
source config.sh
sudo docker run --rm --network none --read-only --tmpfs /tmp:exec,size=64m \
  -e PYTHONDONTWRITEBYTECODE=1 -v "$DOJO_SOURCE:/work:ro" -w /work \
  "$APP_IMAGE" python -B -m unittest \
  auditor_bridge.live_tests.test_run_sh auditor_bridge.live_tests.test_git_askpass -v
```

This machine started with about 2.2 GiB free, so existing image layers and bounded
tmpfs were used. The previous dedicated stand was backed up and stopped; its
source and evidence were retained. Unrelated Docker resources were preserved.
The new stand was left running. OpenGrep reported partial analysis of 88 Juice
Shop files and 14 NodeGoat files, plus two Juice Shop files skipped for size and
two for ignore rules. Successful import does not imply complete scanner coverage.
The final packaging review also restored the Git credential helper: it reads
the token file only for the isolated `http://gitlab` service. Its Docker routing
tests and a private-repository `git ls-remote` check cover the packaged helper.
Pre-fetched source snapshots are checked out at the pinned SHA without another
GitHub fetch, so the documented download fallback works without outbound access.

GitLab data/config, both databases, Dojo media and registry storage use bounded
**tmpfs** volumes. Stopping the last mounting container, `down`, or rebooting can
destroy data even if named volumes remain. Recreate with a fresh work directory
and regenerated secrets. This is not a production persistence model. Retain
on-disk evidence before stopping the stand. For a persistent installation use
the normal Dojo/Auditor deployment and persistent PostgreSQL/media volumes.

```sh
bash /absolute/path/auditor-live-stand/harness/run.sh status
bash /absolute/path/auditor-live-stand/harness/run.sh down
```

`down` targets only the generated Compose project. Never use global Docker prune
or remove another installation's containers, volumes, images or build cache.
