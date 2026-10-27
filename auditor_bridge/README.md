# Auditor bridge for DefectDojo

Opt-in Django application for a single-domain Auditor installation. Development
is based on upstream `dev`; the compatibility target is DefectDojo **3.4.300**.
Upstream code and licenses remain intact. The bridge uses existing Dojo models
and the standard Generic Findings importer; it adds its own tables and routes.

## Installation

1. Configure a normal Dojo PostgreSQL deployment and its secrets. Back up that
   database before enabling an additional application.
2. Provide separate secret files for the Auditor admin API token and the callback
   bearer token (at least 24 characters). Mount them read-only, readable by the
   Dojo container user (`1001:1337` in the compatibility image). Local Compose
   file secrets retain host permissions; do not make token files world-readable.
3. Set `AUDITOR_BRIDGE_ENABLED=true`, `AUDITOR_BRIDGE_URL` to the reachable Auditor
   origin, and the two `*_TOKEN_PATH` variables used by the override. The URL
   must also be Auditor's `AUDITOR_PUBLIC_URL`. Production connections should
   use HTTPS with a trusted CA. Redirects are never followed.
4. Build and run from the repository root:

   ```sh
   docker compose -f docker-compose.yml -f auditor_bridge/deploy/compose.override.yaml build uwsgi
   docker compose -f docker-compose.yml -f auditor_bridge/deploy/compose.override.yaml --profile auditor-bridge up -d
   ```

   The initializer applies `auditor_bridge` migrations. The feature defaults to
   disabled. The dedicated worker runs inventory reconciliation and profile
   retries; it does not launch scans itself. Existing deployments with custom
   `local_settings.py` keep their existing files and mounts. The explicit
   `DJANGO_SETTINGS_MODULE=auditor_bridge.deploy.settings` wrapper first loads
   upstream settings and those operator customizations, then adds the bridge
   app and prepends its routes to the existing URL configuration. No bridge
   file is copied over an operator's `local_settings.py`.

5. Configure Auditor's `custom_http` ASOC integration. Put the callback bearer
   credential in encrypted `config.auth_header`, **not** `api_token`. Set
   `endpoint_url` and `completion_signal.url` to Dojo's
   `/api/v2/auditor/callback/`, with:

   ```json
   {
     "scanner_mappings": {"*": {"enabled": false}},
     "completion_signal": {
       "method": "POST", "repository_ack": true,
       "headers": {"Authorization": "{integration.auth_header}"},
       "body": {}, "run_when": ["COMPLETED", "FAILED", "CANCELLED", "EXPIRED"],
       "verify_tls": true, "timeout_s": 120, "max_attempts": 5
     }
   }
   ```

   Include that ASOC integration ID in each bridge profile. Permit only the
   specific Dojo/GitLab hosts in Auditor's outbound allowlist. The GitLab token
   used by Auditor should be a service account with `read_api` and
   `read_repository`, scoped to the intended projects/groups. GitLab event
   administration and CI runner bootstrap use separate credentials.

## API and operator interface

All administration routes require an active **Dojo superuser** using normal
Dojo token or session authentication. This first version is deliberately an
installation-wide integration, not a per-product delegation mechanism.
Session writes retain CSRF protection. The callback uses its separate bearer
credential. No token is exposed in these responses or persisted in bridge rows.

| Route under `/api/v2/auditor/` | Behavior |
| --- | --- |
| `GET catalog/` | Read Auditor scanner jobs and the nine trigger types |
| `GET profiles/`, `GET profiles/{key}/` | Desired profile, actual read-back, state, attempts, safe error code |
| `PUT profiles/{key}/` | Save desired profile and queue synchronization; 202 means pending |
| `POST profiles/{key}/sync/` | Attempt synchronization now; inspect returned state |
| `POST profiles/{key}/disable/` | Disable all rules, retaining the sequence and scan history |
| `DELETE profiles/{key}/` | Request remote deletion and verify 404; a historical sequence may remain `delete_error` after Auditor 409 |
| `GET repositories/` | Paginated durable identity/Product/Engagement mapping and inventory metadata |
| `POST repositories/` | Full inventory reconciliation now |
| `GET imports/` | Latest 200 committed imports, Test IDs, counts, report hashes and commit SHAs |
| `POST callback/` | Validate identity/report snapshot, import transactionally, return RepositoryAcknowledgement |

Open **`/auditor/profiles/`** while signed in as a superuser to list profiles,
enter a key and desired JSON, and use Save, Sync or Disable. The page displays
the real state, safe error and remote read-back. It shares the API serializer
and synchronization services; all form writes require a valid CSRF token.
The standard Django admin also lists profiles, repository mappings, imports
and receipts under **Auditor bridge**, read-only.

Example profile body (replace IDs and use catalog job slugs):

```json
{
  "name": "Main security scan",
  "steps": [{"job_slug": "code-downloader"}, {"job_slug": "gitleaks"}],
  "asoc_integration_ids": ["11111111-1111-4111-8111-111111111111"],
  "automations": [{
    "name": "Merge and weekly", "integration_id": "22222222-2222-4222-8222-222222222222",
    "repository_ids": [42], "events": ["merge_request.merged"], "ref_patterns": ["@default"],
    "schedule": {"kind": "weekly", "weekdays": [0], "hour": 3, "minute": 0, "timezone": "UTC"},
    "enabled": true, "max_scans_per_hour": 6
  }]
}
```

For interval schedules use `{"kind":"interval","interval_seconds":60}` or a
larger interval. Events include push, MR opened/updated/merged, tag creation,
successful pipeline, published release, successful deployment, and repository
discovery. GitLab hooks and a running Auditor poller are required. Commands and
container images cannot be submitted through a profile.

## Consistency and failure behavior

- Repository identity is `(provider, normalized instance, immutable repository
  ID)`. Paths are metadata. Renames and group transfers retain Product and
  Engagement IDs. Periodic full inventory reconciliation handles tombstones.
- With `AUDITOR_BRIDGE_AUTO_CREATE=true` (default), an unknown callback identity
  is resolved from the trusted Auditor inventory before Product creation. This
  supports push delivery while periodic pull is stopped. Setting it to `false`
  returns `not_found` for an unmapped repository.
- `Idempotency-Key` on Auditor sequence creation is stable and independent of
  profile names. The original POST body survives edits and transport failures.
  A proven HTTP 422 rejection permits a corrected create generation. Deleting
  a never-attempted or definitively rejected profile removes only the local
  intent. Disable refuses an unsynced rejected profile: correct it or delete it.
  A transport-uncertain creation must recover its original receipt before deletion. The bridge
  reads both sequence and rules before claiming `synced`; it compares rule sets
  independent of ordering and keeps step order significant.
- Synced profiles are checked again at least once per reconciliation interval
  (60 seconds plus worker scheduling). Drift is repaired with read-back. A
  missing sequence becomes an explicit error, not a silently recreated profile.
- Callback and scan locks are PostgreSQL transaction locks. Findings, Test,
  import receipt and callback acknowledgement commit together. Lost responses
  and concurrent duplicate deliveries return the committed receipt without a
  second import. Conflicting identity or snapshot is refused.
- Report URLs must exactly match the configured origin and canonical scan
  findings path. The JSON report must have the same scan ID, immutable repository
  identity and commit SHA. Snapshot verification is mandatory and requires
  Auditor's immutable report snapshot API (commit `7b2e8d4` or newer).
- Reports are bounded to 32 MiB and 50,000 findings. Incomplete scanner execution,
  malformed findings, parse errors or count mismatches cannot produce an
  `imported` acknowledgement. A valid completed empty report creates an empty
  Test; a failed scan returns `failed` without an import.

## Validation and recovery

Run behavioral contract tests through the upstream wrapper in an enabled dev
deployment: `./run-unittest.sh --test-case auditor_bridge.tests`. Also run Docker
`manage.py check`, `makemigrations --check --dry-run` and the OpenAPI schema guard.
Unit tests are separate from live GitLab/scanner/import acceptance evidence.
The [Linux live harness](live_tests/README.md) contains setup, actual GitLab
actions, failure injection, read-back and a sanitized acceptance summary.

Keep PostgreSQL and media persistent for real deployments. The Linux acceptance
stand uses explicitly temporary, bounded RAM volumes; its separate harness
runbook recreates users, repositories, hooks, profiles and fixtures. Source SHAs,
scenario results and sanitized evidence stay on disk. Never use global Docker
prune or remove volumes belonging to another installation.
