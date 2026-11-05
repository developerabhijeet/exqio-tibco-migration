#!/usr/bin/env bash
# In-cluster end to end of the chart on kind: `make kind-e2e` (and .github/workflows/kind-e2e.yml).
#
#   deploy/kind/setup.sh tools   kind, kubectl and helm, pinned and SHA256-verified, into .tools/bin
#                                (only those not already on PATH; KIND_TOOLS_FORCE=1 installs all)
#   deploy/kind/setup.sh e2e     the run below; tears the cluster down at the end
#   deploy/kind/setup.sh down    deletes the cluster and the registry container
#
# The run: a one-node kind cluster (deploy/kind/cluster.yaml, ingress on a free 127.0.0.1 port),
# ingress-nginx from the vendored manifest (its SHA256 checked), a local registry container the
# node pulls from, both images built by `make images` and pushed there (the chart takes digests
# only, and a registry is what gives an image a pullable digest), Postgres with pgvector in the
# exiqo-db namespace (deploy/kind/postgres.yaml), the secrets as plain Kubernetes Secrets mounted as
# files (keyVault.mode kubernetesSecret, CI overlay only) from random values generated here and
# never written outside a temporary directory, then
#   helm upgrade --install --atomic -f values.gmf.example.yaml -f ci/values.kind.yaml
# with 2 api and 2 worker pods, scripts/demo_check.py --mode toolchain-off through the ingress, and
# deploy/kind/worker_kill.py (a worker force-deleted mid-build; the job must not stay running).
#
# Re-running deletes a leftover cluster of the same name first. KEEP_CLUSTER=1 keeps the cluster
# (and the registry) after the run; on any failure the pods, events and logs are printed first.
# Settings: KIND_CLUSTER (exiqo-e2e), KIND_HTTP_PORT (free port), KIND_REGISTRY_PORT (free port),
# IMAGE_TAG (git short HEAD, as `make images`), PY (.venv/bin/python, else python3).
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"

KIND_VERSION=v0.32.0
KUBECTL_VERSION=v1.36.5
HELM_VERSION=v3.22.1
# SHA256 of the release binaries (kind, kubectl) and archives (helm), from the projects' published
# checksum files; a download that does not match is deleted and the run stops.
sha_for() {
  case "$1" in
    kind-darwin-arm64) echo dca67911095a110c2b5c36e26df6cac860c602033e456c0db47be498cdef1ebb ;;
    kind-darwin-amd64) echo 295ac6d0d634c9819c9907df45e3017d1f13166bd13c3404c45e79f7faa47498 ;;
    kind-linux-amd64) echo 50030de23cf40a18505f20426f6a8506bedf13c6e509244bd1fa9463721b0f54 ;;
    kind-linux-arm64) echo b92cd615e97585de8ddade28ed5cd7feb4248d717c233eea5b03c37298900f5d ;;
    kubectl-darwin-arm64) echo c5850a4a6b9469b26cce47c1ab456285f58e308283dcdf72b4ddda3db49b0ec0 ;;
    kubectl-darwin-amd64) echo 03338828c3ba74c37bf5964573fbfbab132eb5bb15da2f821c25a4043d6eb3f0 ;;
    kubectl-linux-amd64) echo 33bc88a24c3b09cf55bfd59e6ca977881b61fc3f25ab65bc80c65b430248648e ;;
    kubectl-linux-arm64) echo 88fc1aca8fd0c1b44fe379f67c39bd6858bdfc50241dc55775f84d0f5b31da3a ;;
    helm-darwin-arm64) echo e73da8a0b0d5193774fdff17ce6319c7436bb22ee229b45fd64f3c40368c753a ;;
    helm-darwin-amd64) echo cf850a81b908f8eef0331e6b18776da2767bcac639ca84472311dad20008495c ;;
    helm-linux-amd64) echo 896cf63684a42a723d61c980369d6e588330927deb13676ca0d85006dd6da0ea ;;
    helm-linux-arm64) echo 4c29e286b279d170fa048ddfc3e4e83dcd069ca9c52102766ba672d7149e694d ;;
    *) echo "kind-e2e: no pinned checksum for $1" >&2; return 1 ;;
  esac
}
# controller-v1.15.1 deploy/static/provider/kind/deploy.yaml, vendored as deploy/kind/ingress-nginx.yaml
INGRESS_SHA256=2a3ae008c8786431115502644e77ab398fdebfb721a5d1195ed3089cde3299df
REGISTRY_IMAGE=docker.io/library/registry:3.0.0@sha256:6c5666b861f3505b116bb9aa9b25175e71210414bd010d92035ff64018f9457e

CLUSTER=${KIND_CLUSTER:-exiqo-e2e}
REGISTRY=${CLUSTER}-registry
NS=exiqo
DB_NS=exiqo-db
HOST=exiqo.localtest.me  # ci/values.kind.yaml ingress.host; *.localtest.me resolves to 127.0.0.1
CHART=deploy/helm/exiqo
TOOLS="$ROOT/.tools/bin"
WORK="$ROOT/data/kind-e2e"
IMAGE_TAG=${IMAGE_TAG:-$(git rev-parse --short HEAD 2>/dev/null || echo dev)}
PY=${PY:-$([ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)}

log() { printf '\nkind-e2e: %s\n' "$*"; }
die() { printf 'kind-e2e: %s\n' "$*" >&2; exit 1; }
sha256() { if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }
free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }
platform() {
  local os arch
  os=$(uname -s | tr '[:upper:]' '[:lower:]')
  case "$(uname -m)" in x86_64|amd64) arch=amd64 ;; arm64|aarch64) arch=arm64 ;; *) die "unsupported CPU $(uname -m)" ;; esac
  echo "$os-$arch"
}

# A tool from PATH is used when present, except a kubectl more than one minor version away from the
# node image's Kubernetes (1.36; outside kubectl's supported skew), or with KIND_TOOLS_FORCE=1.
on_path() {
  [ "${KIND_TOOLS_FORCE:-0}" != 1 ] && command -v "$1" >/dev/null 2>&1 || return 1
  if [ "$1" = kubectl ]; then
    local minor
    minor=$(kubectl version --client -o json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["clientVersion"]["minor"].rstrip("+"))' 2>/dev/null)
    case "$minor" in 35|36|37) ;; *) return 1 ;; esac
  fi
}
tool() { if on_path "$1"; then command -v "$1"; else echo "$TOOLS/$1"; fi; }

fetch_verified() {  # url sha256 destination
  local tmp="$3.download"
  curl -fsSL --retry 3 -o "$tmp" "$1"
  if [ "$(sha256 "$tmp")" != "$2" ]; then rm -f "$tmp"; die "checksum mismatch for $1"; fi
  mv "$tmp" "$3"
}

install_tools() {
  local p want
  p=$(platform)
  mkdir -p "$TOOLS"
  for name in kind kubectl helm; do
    if on_path "$name"; then echo "kind-tools: $name from PATH ($(command -v "$name"))"; continue; fi
    want=$(sha_for "$name-$p")
    if [ -x "$TOOLS/$name" ] && [ -f "$TOOLS/.$name.sha256" ] && [ "$(cat "$TOOLS/.$name.sha256")" = "$want" ]; then
      echo "kind-tools: $name already in .tools/bin"; continue
    fi
    case "$name" in
      kind) fetch_verified "https://github.com/kubernetes-sigs/kind/releases/download/$KIND_VERSION/kind-$p" "$want" "$TOOLS/kind" ;;
      kubectl) fetch_verified "https://dl.k8s.io/release/$KUBECTL_VERSION/bin/${p%-*}/${p#*-}/kubectl" "$want" "$TOOLS/kubectl" ;;
      helm)
        fetch_verified "https://get.helm.sh/helm-$HELM_VERSION-$p.tar.gz" "$want" "$TOOLS/helm.tar.gz"
        tar -xzf "$TOOLS/helm.tar.gz" -C "$TOOLS" --strip-components 1 "$p/helm"
        rm -f "$TOOLS/helm.tar.gz" ;;
    esac
    chmod 0755 "$TOOLS/$name"
    echo "$want" >"$TOOLS/.$name.sha256"
    echo "kind-tools: $name installed in .tools/bin (sha256 verified)"
  done
}

KIND=$(tool kind)
KUBECTL=$(tool kubectl)
HELM=$(tool helm)
kc() { "$KUBECTL" --context "kind-$CLUSTER" "$@"; }

teardown() {
  if "$KIND" get clusters 2>/dev/null | grep -qx "$CLUSTER"; then "$KIND" delete cluster --name "$CLUSTER"; fi
  docker rm -f "$REGISTRY" >/dev/null 2>&1 || true
}

diagnostics() {
  log "diagnostics (the run failed)"
  kc get nodes -o wide || true
  kc get pods -A -o wide || true
  kc -n "$NS" get deploy,rs,hpa,pvc,ingress,networkpolicy,jobs || true
  "$HELM" --kube-context "kind-$CLUSTER" -n "$NS" status exiqo 2>/dev/null || true
  kc get events -A --sort-by=.lastTimestamp 2>/dev/null | tail -60 || true
  for pod in $(kc -n "$NS" get pods -o name 2>/dev/null); do
    echo "---- describe $pod"; kc -n "$NS" describe "$pod" | tail -40 || true
    echo "---- logs $pod"; kc -n "$NS" logs "$pod" --all-containers --tail 150 || true
    kc -n "$NS" logs "$pod" --all-containers --previous --tail 60 2>/dev/null | sed 's/^/[previous] /' || true
  done
  kc -n "$DB_NS" logs deploy/postgres --tail 40 2>/dev/null || true
  # --atomic removes a failed release's pods: what the watcher saved while helm ran
  if ls "$WORK"/watch/*.log >/dev/null 2>&1; then
    for f in "$WORK"/watch/*.log; do echo "---- saved during the install: $(basename "$f")"; tail -80 "$f"; done
  fi
}

# While helm installs, keep the pods' state and logs: --atomic deletes them on failure.
watch_install() {
  mkdir -p "$WORK/watch"
  while :; do
    kc -n "$NS" get pods -o wide >"$WORK/watch/pods.log" 2>&1 || true
    kc -n "$NS" get events --sort-by=.lastTimestamp >"$WORK/watch/events.log" 2>&1 || true
    for pod in $(kc -n "$NS" get pods -o name 2>/dev/null); do
      kc -n "$NS" logs "$pod" --all-containers --tail 200 >"$WORK/watch/${pod#pod/}.log" 2>&1 || true
    done
    sleep 10
  done
}

finish() {
  local rc=$?
  [ -n "${WATCHER:-}" ] && kill "$WATCHER" 2>/dev/null
  [ -n "${SECRETS_DIR:-}" ] && rm -rf "$SECRETS_DIR"
  if [ "$rc" -ne 0 ] && [ "${STARTED:-0}" = 1 ]; then diagnostics; fi
  if [ "${STARTED:-0}" = 1 ]; then
    if [ "${KEEP_CLUSTER:-0}" = 1 ]; then
      log "KEEP_CLUSTER=1: cluster kind-$CLUSTER kept; API at http://$HOST:$HTTP_PORT/api (deploy/kind/setup.sh down removes it)"
    else
      log "tearing down kind-$CLUSTER"; teardown
      # the cluster config and the watcher's pod logs; kept after a failure for diagnosis
      if [ "$rc" -eq 0 ]; then rm -rf "$WORK"; fi
    fi
  fi
  if [ "$rc" -eq 0 ]; then log "PASS"; else log "FAIL (exit $rc)"; fi
  exit "$rc"
}

random_secret() { python3 -c 'import secrets; print(secrets.token_urlsafe(32), end="")'; }
fernet_key() { python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode(), end="")'; }

create_secrets() {
  SECRETS_DIR=$(mktemp -d)
  chmod 0700 "$SECRETS_DIR"
  local pg neo
  pg=$(random_secret); neo=$(random_secret)
  printf '%s' "$pg" >"$SECRETS_DIR/password"
  printf 'postgresql+asyncpg://exiqo:%s@postgres.%s.svc.cluster.local:5432/exiqo' "$pg" "$DB_NS" >"$SECRETS_DIR/database_url"
  fernet_key >"$SECRETS_DIR/app_secret"
  random_secret >"$SECRETS_DIR/session_secret"
  random_secret >"$SECRETS_DIR/internal_token"
  printf '%s' "$neo" >"$SECRETS_DIR/neo4j_password"
  printf 'neo4j/%s' "$neo" >"$SECRETS_DIR/NEO4J_AUTH"
  kc create namespace "$DB_NS" --dry-run=client -o yaml | kc apply -f - >/dev/null
  kc create namespace "$NS" --dry-run=client -o yaml | kc apply -f - >/dev/null
  # --from-file: the values never appear on a command line
  kc -n "$DB_NS" create secret generic postgres-auth --from-file=password="$SECRETS_DIR/password"
  kc -n "$NS" create secret generic exiqo-ci-secrets \
    --from-file=database_url="$SECRETS_DIR/database_url" --from-file=app_secret="$SECRETS_DIR/app_secret" \
    --from-file=session_secret="$SECRETS_DIR/session_secret" --from-file=internal_token="$SECRETS_DIR/internal_token" \
    --from-file=neo4j_password="$SECRETS_DIR/neo4j_password"
  kc -n "$NS" create secret generic exiqo-neo4j-auth --from-file=NEO4J_AUTH="$SECRETS_DIR/NEO4J_AUTH"
  rm -rf "$SECRETS_DIR"; SECRETS_DIR=
}

push_image() {  # local image, repository in the registry -> prints the pushed digest
  local target="localhost:$REGISTRY_PORT/$2:$IMAGE_TAG" digest
  docker tag "$1" "$target"
  digest=$(docker push "$target" | sed -n 's/.*digest: \(sha256:[0-9a-f]\{64\}\).*/\1/p' | tail -1)
  [ -n "$digest" ] || die "no digest from pushing $target"
  echo "$digest"
}

wait_for_workers() {  # the HPA raises the Deployment to worker.replicas (2) after the install
  local ready
  for _ in $(seq 60); do
    ready=$(kc -n "$NS" get deploy exiqo-worker -o jsonpath='{.status.readyReplicas}')
    [ "${ready:-0}" -ge 2 ] && { echo "kind-e2e: $ready worker pods ready"; return 0; }
    sleep 5
  done
  die "fewer than 2 worker pods ready after 5 minutes"
}

e2e() {
  if ! docker info >/dev/null 2>&1; then die "docker is not running"; fi
  for t in "$KIND" "$KUBECTL" "$HELM"; do [ -x "$t" ] || die "$t missing: run make kind-tools"; done
  [ "$(sha256 deploy/kind/ingress-nginx.yaml)" = "$INGRESS_SHA256" ] \
    || die "deploy/kind/ingress-nginx.yaml does not match its pinned SHA256"
  trap finish EXIT
  STARTED=1
  rm -rf "$WORK"; mkdir -p "$WORK"
  HTTP_PORT=${KIND_HTTP_PORT:-$(free_port)}
  REGISTRY_PORT=${KIND_REGISTRY_PORT:-$(free_port)}

  log "cluster kind-$CLUSTER (ingress on 127.0.0.1:$HTTP_PORT, registry on 127.0.0.1:$REGISTRY_PORT)"
  teardown
  sed -e "s/hostPort: 18080/hostPort: $HTTP_PORT/" -e "s/^name: exiqo-e2e$/name: $CLUSTER/" \
    deploy/kind/cluster.yaml >"$WORK/cluster.yaml"
  "$KIND" create cluster --config "$WORK/cluster.yaml" --wait 180s
  docker run -d --name "$REGISTRY" -p "127.0.0.1:$REGISTRY_PORT:5000" "$REGISTRY_IMAGE" >/dev/null
  docker network connect kind "$REGISTRY"
  for node in $("$KIND" get nodes --name "$CLUSTER"); do
    docker exec "$node" mkdir -p "/etc/containerd/certs.d/localhost:$REGISTRY_PORT"
    printf '[host."http://%s:5000"]\n' "$REGISTRY" \
      | docker exec -i "$node" cp /dev/stdin "/etc/containerd/certs.d/localhost:$REGISTRY_PORT/hosts.toml"
  done

  log "ingress-nginx (vendored controller-v1.15.1 manifest)"
  kc apply -f deploy/kind/ingress-nginx.yaml >/dev/null

  log "images (make images, IMAGE_TAG=$IMAGE_TAG), pushed to the cluster's registry"
  make images IMAGE_TAG="$IMAGE_TAG"
  PLATFORM_DIGEST=$(push_image "exiqo-platform:$IMAGE_TAG" exiqo/exiqo-platform)
  WEB_DIGEST=$(push_image "exiqo-web:$IMAGE_TAG" exiqo/exiqo-web)
  echo "kind-e2e: exiqo-platform@$PLATFORM_DIGEST exiqo-web@$WEB_DIGEST"

  log "secrets (random, as Kubernetes Secrets) and Postgres"
  create_secrets
  kc apply -f deploy/kind/postgres.yaml >/dev/null
  kc -n "$DB_NS" rollout status deploy/postgres --timeout 300s
  kc -n ingress-nginx rollout status deploy/ingress-nginx-controller --timeout 300s
  # The admission jobs delete themselves when done (ttlSecondsAfterFinished: 0); the webhook is
  # usable once the patch job has written its CA bundle, or the chart's Ingresses are refused.
  for _ in $(seq 60); do
    [ -n "$(kc get validatingwebhookconfiguration ingress-nginx-admission \
      -o jsonpath='{.webhooks[0].clientConfig.caBundle}' 2>/dev/null)" ] && break
    sleep 3
  done

  log "helm upgrade --install --atomic (2 api, 2 worker)"
  "$HELM" repo add neo4j https://helm.neo4j.com/neo4j --force-update >/dev/null
  "$HELM" dependency build "$CHART" >/dev/null
  watch_install & WATCHER=$!
  "$HELM" --kube-context "kind-$CLUSTER" upgrade --install exiqo "$CHART" -n "$NS" --atomic --wait --timeout 20m \
    -f "$CHART/values.gmf.example.yaml" -f "$CHART/ci/values.kind.yaml" \
    --set "image.registry=localhost:$REGISTRY_PORT" \
    --set "image.platform.digest=$PLATFORM_DIGEST" --set "image.web.digest=$WEB_DIGEST"
  kill "$WATCHER" 2>/dev/null; WATCHER=
  wait_for_workers
  kc -n "$NS" get pods -o wide

  log "demo check through the ingress (scripts/demo_check.py --mode toolchain-off)"
  make samples samples-zip PY="$PY" >/dev/null
  curl -fsS -m 10 "http://$HOST:$HTTP_PORT/api/health" >/dev/null || die "the API does not answer at http://$HOST:$HTTP_PORT/api"
  "$PY" scripts/demo_check.py --base-url "http://$HOST:$HTTP_PORT" --mode toolchain-off

  log "worker kill during a build (deploy/kind/worker_kill.py)"
  "$PY" deploy/kind/worker_kill.py --api "http://$HOST:$HTTP_PORT/api" --kubectl "$KUBECTL" \
    --context "kind-$CLUSTER" --namespace "$NS" --db-namespace "$DB_NS"
  wait_for_workers
}

case "${1:-e2e}" in
  tools) install_tools ;;
  e2e) e2e ;;
  down) teardown ;;
  *) die "usage: $0 [tools|e2e|down]" ;;
esac
