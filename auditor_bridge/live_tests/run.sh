#!/usr/bin/env bash
# Host orchestration only. All application code, scanners and API scenarios run in Docker.
set -euo pipefail
cd "$(dirname "$0")/.."
source config.sh
[[ "$COMPOSE_PROJECT_NAME" == auditor-asoc-live-* ]]
dock() { if [[ "${LIVE_DOCKER_SUDO:-1}" == 1 ]]; then sudo -n docker "$@"; else command docker "$@"; fi; }
compose() { dock compose -f compose.json "$@"; }
scenario() {
  dock run --rm --user "$(id -u):$(id -g)" --network "${COMPOSE_PROJECT_NAME}_default" \
    -e "COMPOSE_PROJECT_NAME=$COMPOSE_PROJECT_NAME" -v "$PWD/harness:/harness:ro" -v "$PWD/secrets:/run/live-secrets" \
    -v "$PWD/evidence:/evidence" -v "$PWD/runtime/fixtures:/fixtures" -w /harness "$APP_IMAGE" python "$1"
  printf 'PASS: %s\n' "$1"
}
recovery() {
  dock run --rm --user "$(id -u):$(id -g)" --group-add "$(stat -c %g /var/run/docker.sock)" \
    --network "${COMPOSE_PROJECT_NAME}_default" -e "COMPOSE_PROJECT_NAME=$COMPOSE_PROJECT_NAME" -e RECOVERY_CASE="$1" \
    -v /var/run/docker.sock:/var/run/docker.sock -v "$PWD/harness:/harness:ro" \
    -v "$PWD/secrets:/run/live-secrets:ro" -v "$PWD/evidence:/evidence" -w /harness "$APP_IMAGE" python recovery_cases.py
}
restore_faults() {
  compose up -d --no-deps dojo dojo-web auditor-api bridge-worker >/dev/null
  compose restart dojo-web >/dev/null
  for suffix in create-proxy callback-proxy; do
    if dock container inspect "$COMPOSE_PROJECT_NAME-$suffix" >/dev/null 2>&1; then
      dock rm -f "$COMPOSE_PROJECT_NAME-$suffix" >/dev/null
    fi
  done
}
case "${1:-}" in
  setup)
    source_revision="${AUDITOR_SOURCE_REVISION:-$(git -C "$AUDITOR_SOURCE" rev-parse HEAD 2>/dev/null || true)}"
    if [[ -z "$source_revision" && -f "$AUDITOR_SOURCE/SOURCE_REVISION" ]]; then
      source_revision="$(cat "$AUDITOR_SOURCE/SOURCE_REVISION")"
    fi
    [[ "$source_revision" =~ ^[0-9a-f]{40}$ ]] || { echo 'Set AUDITOR_SOURCE_REVISION to the exact runtime source commit' >&2; exit 2; }
    dock build --build-arg "APP_VERSION=${AUDITOR_VERSION:-0.3.5}" --build-arg "SOURCE_REVISION=$source_revision" \
      -f "$AUDITOR_SOURCE/docker/app.Dockerfile" -t "$APP_IMAGE" "$AUDITOR_SOURCE" > evidence/build-auditor.log 2>&1
    dock build -f "$DOJO_SOURCE/auditor_bridge/deploy/Dockerfile" -t "$BRIDGE_IMAGE" "$DOJO_SOURCE" > evidence/build-dojo.log 2>&1
    sudo -n install -d -o 1001 -g 1337 -m 0700 secrets/dojo-service
    for secret_file in auditor-admin callback-token; do sudo -n install -o 1001 -g 1337 -m 0600 "secrets/$secret_file" "secrets/dojo-service/$secret_file"; done
    compose up -d --wait gitlab dojo-db dojo-redis auditor-db auditor-redis openbao scanner-registry > evidence/infra-up.log 2>&1
    compose up --no-deps --exit-code-from dojo-init dojo-init > secrets/dojo-initializer.log 2>&1
    compose run --rm --no-deps --entrypoint python auditor-api scripts/run_migrations.py > evidence/auditor-migrations.log 2>&1
    # Copy existing scanner images by exact names. Their source registry is read only.
    for image_ref in gitleaks:8.30.1 detect-secrets:1.5.0 opengrep:1.22.0-rules-f1d2b56; do
      dock image inspect "${SCANNER_SOURCE_REGISTRY:-localhost:5000}/auditor/$image_ref" >/dev/null
      dock tag "${SCANNER_SOURCE_REGISTRY:-localhost:5000}/auditor/$image_ref" "localhost:$REGISTRY_PORT/auditor/$image_ref"
      dock push "localhost:$REGISTRY_PORT/auditor/$image_ref" >> evidence/scanner-copy.log 2>&1
    done
    compose run --rm --no-deps --entrypoint python auditor-api scripts/seed.py > evidence/catalog-seed.log 2>&1
    compose up -d --no-deps dojo dojo-worker dojo-web auditor-api auditor-worker auditor-poller bridge-worker > evidence/app-up.log 2>&1
    # The upstream nginx image resolves its uwsgi host only when it starts.
    compose restart dojo-web >> evidence/app-up.log 2>&1
    compose exec -T -u root gitlab install -o git -g git -m 0600 /run/live-secrets/gitlab-token /var/opt/gitlab/.auditor-api-token
    sed 's@/run/live-secrets/gitlab-token@/var/opt/gitlab/.auditor-api-token@g' harness/bootstrap_gitlab.rb | compose exec -T gitlab gitlab-rails runner - > evidence/gitlab-bootstrap.json
    scenario bootstrap_api.py
    scenario prepare_repositories.py
    scenario service_account.py
    scenario configure_integration.py
    scenario register_runner.py
    compose up -d --no-deps gitlab-runner > evidence/runner-up.log 2>&1
    ;;
  core)
    scenario baseline.py
    scenario events.py
    scenario schedules.py
    scenario profile_cases.py
    compose stop bridge-worker > evidence/callback-worker-stop.log 2>&1
    trap 'compose start bridge-worker >/dev/null' EXIT
    scenario callback_push.py
    compose start bridge-worker > evidence/callback-worker-start.log 2>&1
    trap - EXIT
    scenario report_cases.py
    scenario negative_callbacks.py
    scenario webhook_dedup.py
    scenario identity_cases.py
    scenario ui_flow.py
    ;;
  recovery)
    trap restore_faults EXIT
    dock run -d --name "$COMPOSE_PROJECT_NAME-create-proxy" --network "${COMPOSE_PROJECT_NAME}_default" --network-alias auditor-proxy \
      --user "$(id -u):$(id -g)" -e UPSTREAM=http://auditor-api:8000 -e DROP_PATH=/api/v1/sequences \
      -e EVIDENCE_FILE=lost-create-proxy.json -v "$PWD/harness:/harness:ro" -v "$PWD/evidence:/evidence" \
      -w /harness --entrypoint python "$APP_IMAGE" fault_proxy.py > evidence/create-proxy-container.txt
    printf '%s\n' '{"services":{"dojo":{"environment":{"AUDITOR_BRIDGE_URL":"http://auditor-proxy:8000"}}}}' > compose.fault.json
    compose stop bridge-worker >/dev/null
    dock compose -f compose.json -f compose.fault.json up -d --no-deps dojo >/dev/null
    compose restart dojo-web >/dev/null
    scenario lost_create.py
    compose up -d --no-deps dojo >/dev/null
    compose restart dojo-web >/dev/null
    compose start bridge-worker >/dev/null
    dock run -d --name "$COMPOSE_PROJECT_NAME-callback-proxy" --network "container:$COMPOSE_PROJECT_NAME-dojo-web-1" \
      --user "$(id -u):$(id -g)" -e UPSTREAM=http://127.0.0.1:8080 -e DROP_PATH=/api/v2/auditor/callback/ \
      -e EVIDENCE_FILE=lost-callback-proxy.json -v "$PWD/harness:/harness:ro" -v "$PWD/evidence:/evidence" \
      -w /harness --entrypoint python "$APP_IMAGE" fault_proxy.py > evidence/callback-proxy-container.txt
    recovery lost_callback
    dock rm -f "$COMPOSE_PROJECT_NAME-callback-proxy" >/dev/null
    recovery dojo_outage
    recovery auditor_outage
    ;;
  review)
    compose stop bridge-worker >/dev/null
    trap 'compose start bridge-worker >/dev/null' EXIT
    scenario review_cases.py
    ;;
  scenario) scenario "${2:?Provide a harness Python filename}" ;;
  status) compose ps ;;
  down) compose down ;; # Preserve volumes unless the operator explicitly discards this temporary stand.
  *) echo 'Usage: run.sh setup|core|recovery|review|scenario filename.py|status|down' >&2; exit 2 ;;
esac
