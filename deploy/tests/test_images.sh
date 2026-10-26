#!/usr/bin/env bash
# Image acceptance checks for exiqo-platform and exiqo-web (run by `make image-test`).
#
# Builds both images for the local architecture (tags :test), then runs each container the way the
# cluster runs it: UID 10001, read-only root filesystem, writable /tmp (emptyDir) and /data (the
# DATA_DIR volume) only. Checks that the user is right, that no package manager is left in the
# platform image, that the API answers /healthz without a database, that the baked embedding model
# loads with no network at all, that the native saxonche library and the step toolchains run, that
# the test-only job handlers are not shipped, and that the web bundle calls the API on its own
# origin. Exits non-zero when any check fails; every check runs so one report shows them all.
#
# SKIP_BUILD=1 tests images already built; PLATFORM_IMAGE and WEB_IMAGE name other tags.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PLATFORM="${PLATFORM_IMAGE:-exiqo-platform:test}"
WEB="${WEB_IMAGE:-exiqo-web:test}"
TERRAFORM_VERSION="1.17.0"
TERRAGRUNT_VERSION="1.2.1"
# The cluster's runtime constraints, applied to every container started here.
# /data stands in for the DATA_DIR volume, which the pod gets writable through its fsGroup.
HARDENED=(--read-only --tmpfs /tmp --tmpfs /data:mode=1777 --user 10001:10001 --cap-drop ALL
          --security-opt no-new-privileges:true)

failures=0
containers=()
cleanup() {
  if [ "${#containers[@]}" -gt 0 ]; then docker rm -f "${containers[@]}" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT

pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; [ -z "${2:-}" ] || printf '      %s\n' "$2"; failures=$((failures + 1)); }

# expect_output NAME EXPECTED CMD...: CMD succeeds and its trimmed output equals EXPECTED.
expect_output() {
  local name="$1" expected="$2"; shift 2
  local out
  if out="$("$@" 2>&1)" && [ "$(printf '%s' "$out" | tr -d '\r' | tail -1)" = "$expected" ]; then
    pass "$name"
  else
    fail "$name" "expected '$expected', got: $(printf '%s' "$out" | tail -3 | tr '\n' ' ')"
  fi
}

# expect_ok NAME CMD...: CMD exits 0.
expect_ok() {
  local name="$1"; shift
  local out
  if out="$("$@" 2>&1)"; then pass "$name"; else fail "$name" "$(printf '%s' "$out" | tail -5 | tr '\n' ' ')"; fi
}

# expect_status NAME STATUS CMD...: CMD exits with exactly STATUS.
expect_status() {
  local name="$1" expected="$2"; shift 2
  local status=0
  "$@" >/dev/null 2>&1 || status=$?
  if [ "$status" -eq "$expected" ]; then pass "$name"; else fail "$name" "exit status $status, expected $expected"; fi
}

# http_status URL: the HTTP status the URL answers within SECONDS (default 90), or 000.
http_status() {
  local url="$1" seconds="${2:-90}" code="000"
  for _ in $(seq "$seconds"); do
    code="$(curl -s -o /dev/null -w '%{http_code}' -m 2 "$url" || true)"
    [ "$code" = "000" ] || break
    sleep 1
  done
  printf '%s' "$code"
}

if [ "${SKIP_BUILD:-0}" != "1" ]; then
  echo "== building $PLATFORM and $WEB (native architecture)"
  docker build -f "$ROOT/deploy/docker/platform.Dockerfile" -t "$PLATFORM" "$ROOT"
  docker build -f "$ROOT/deploy/docker/web.Dockerfile" -t "$WEB" "$ROOT"
fi

for image in "$PLATFORM" "$WEB"; do
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    echo "FAIL  image $image does not exist (build failed or SKIP_BUILD=1 without a build)"
    exit 1
  fi
done

echo "== $PLATFORM"
expect_output "platform runs as UID 10001" "10001" docker run --rm --entrypoint id "$PLATFORM" -u
expect_output "platform image config sets USER 10001" "10001:10001" \
  docker image inspect -f '{{.Config.User}}' "$PLATFORM"
# Exit 3 proves the shell ran and did not find the command (a missing shell is 127 as well).
expect_status "platform has no apt-get" 3 docker run --rm --entrypoint sh "$PLATFORM" -c 'command -v apt-get || exit 3'
expect_status "platform has no apt" 3 docker run --rm --entrypoint sh "$PLATFORM" -c 'command -v apt || exit 3'
expect_status "platform has no pip (python -m pip fails)" 3 docker run --rm --entrypoint sh "$PLATFORM" -c \
  'python -m pip --version >/dev/null 2>&1 || exit 3'
expect_status "platform cannot bootstrap pip (python -m ensurepip fails)" 3 docker run --rm --entrypoint sh \
  "$PLATFORM" -c 'python -m ensurepip --help >/dev/null 2>&1 || exit 3'
expect_status "platform has no curl or wget" 3 docker run --rm --entrypoint sh "$PLATFORM" -c \
  'command -v curl || command -v wget || exit 3'
expect_status "platform does not ship api/tests (EXIQO_TEST_HANDLERS module)" 1 \
  docker run --rm --entrypoint python "$PLATFORM" -c 'import api.tests.support_handlers'
expect_status "entrypoint refuses an unknown EXIQO_ROLE with 64" 64 \
  docker run --rm "${HARDENED[@]}" -e EXIQO_ROLE=bogus "$PLATFORM"
# Role migrate reaches the database (refused here: nothing listens), so Settings accepted it.
out="$(docker run --rm --network none "${HARDENED[@]}" -e EXIQO_ROLE=migrate \
  -e DATABASE_URL='postgresql+asyncpg://stub:stub@127.0.0.1:1/stub' "$PLATFORM" 2>&1 || true)"
if printf '%s' "$out" | grep -qiE 'connect call failed|connection refused' \
   && ! printf '%s' "$out" | grep -q 'ValidationError'; then
  pass "role migrate runs alembic against DATABASE_URL"
else
  fail "role migrate runs alembic against DATABASE_URL" "$(printf '%s' "$out" | tail -3 | tr '\n' ' ')"
fi

cid="$(docker run -d "${HARDENED[@]}" -e EXIQO_ROLE=api \
  -e DATABASE_URL='postgresql+asyncpg://stub:stub@127.0.0.1:1/stub' -p 127.0.0.1::8000 "$PLATFORM")"
containers+=("$cid")
port="$(docker port "$cid" 8000/tcp | head -1 | sed 's/.*://')"
code="$(http_status "http://127.0.0.1:$port/healthz")"
if [ "$code" = "200" ]; then
  pass "role api on a read-only root answers /healthz 200 (no database)"
else
  fail "role api on a read-only root answers /healthz 200 (no database)" \
    "got $code; logs: $(docker logs "$cid" 2>&1 | tail -5 | tr '\n' ' ')"
fi
if docker logs "$cid" 2>&1 | grep -qi 'read-only file system'; then
  fail "role api writes nothing outside /tmp and /data" "$(docker logs "$cid" 2>&1 | grep -i 'read-only' | head -2)"
else
  pass "role api writes nothing outside /tmp and /data"
fi

expect_output "fastembed model loads offline (--network none, HF_HUB_OFFLINE=1)" "fastembed:BAAI/bge-small-en-v1.5 384" \
  docker run --rm --network none "${HARDENED[@]}" -e EMBEDDINGS_BACKEND=fastembed --entrypoint python \
  "$PLATFORM" -c 'from engine.knowledge import embeddings as e; v = e.embed(["credit application"]); print(e.backend_name(), len(v[0]))'
expect_ok "saxonche native library loads" \
  docker run --rm --network none "${HARDENED[@]}" --entrypoint python "$PLATFORM" \
  -c 'from saxonche import PySaxonProcessor; p = PySaxonProcessor(license=False); assert p.version, "no version"'
expect_ok "toolchain detect() finds dotnet, terraform and terragrunt" \
  docker run --rm --network none "${HARDENED[@]}" --entrypoint python "$PLATFORM" -c \
  'import sys; sys.path.insert(0, "scripts"); import toolchain; d = toolchain.detect(); missing = [k for k in ("dotnet", "terraform", "terragrunt") if not d[k]["present"]]; assert not missing, missing'
expect_ok "dotnet SDK runs as 10001 on a read-only root" \
  docker run --rm --network none "${HARDENED[@]}" --entrypoint dotnet "$PLATFORM" --list-sdks
expect_ok "dotnet builds and runs a project as 10001, offline, writing only /tmp" \
  docker run --rm --network none "${HARDENED[@]}" --entrypoint sh "$PLATFORM" -c \
  'cd /tmp && dotnet new console -o hello >/dev/null && dotnet build hello -nologo -v q >/dev/null && dotnet hello/bin/Debug/net10.0/hello.dll | grep -q "Hello, World!"'
expect_ok "terraform is $TERRAFORM_VERSION" \
  docker run --rm --network none "${HARDENED[@]}" --entrypoint sh "$PLATFORM" -c \
  "terraform version | grep -q 'Terraform v$TERRAFORM_VERSION'"
expect_ok "terragrunt is $TERRAGRUNT_VERSION" \
  docker run --rm --network none "${HARDENED[@]}" --entrypoint sh "$PLATFORM" -c \
  "terragrunt --version | grep -q 'v$TERRAGRUNT_VERSION'"
# Java stack toolchain (JDK 21, Maven 3.9): runs as 10001 on the read-only root, with the Maven
# repository on the writable /cache/stacks volume (an emptyDir in the cluster).
STACKS=(--tmpfs /cache/stacks:mode=1777,exec)
expect_ok "java is 21 (JAVA_HOME=/opt/java)" \
  docker run --rm --network none "${HARDENED[@]}" --entrypoint sh "$PLATFORM" -c \
  'test "$JAVA_HOME" = /opt/java && java -version 2>&1 | grep -q "version \"21\."'
expect_ok "mvn is Apache Maven 3.9" \
  docker run --rm --network none "${HARDENED[@]}" "${STACKS[@]}" --entrypoint sh "$PLATFORM" -c \
  'mvn -B -v 2>&1 | grep -q "Apache Maven 3.9"'
expect_ok "MAVEN_OPTS puts the local repository on /cache/stacks/m2, writable by 10001" \
  docker run --rm --network none "${HARDENED[@]}" "${STACKS[@]}" --entrypoint sh "$PLATFORM" -c \
  'case "$MAVEN_OPTS" in *-Dmaven.repo.local=/cache/stacks/m2*) ;; *) exit 1 ;; esac; mkdir -p /cache/stacks/m2 && touch /cache/stacks/m2/probe'
# A mirror CA (MIRROR_CA_BUNDLE) is imported into a copy of the JDK trust store under /tmp.
ca_dir="$(mktemp -d)"
openssl req -x509 -newkey rsa:2048 -nodes -keyout "$ca_dir/key.pem" -out "$ca_dir/ca.pem" \
  -subj "/CN=exiqo-test-mirror-ca" -days 2 >/dev/null 2>&1
expect_ok "entrypoint imports MIRROR_CA_BUNDLE into the Maven trust store" \
  docker run --rm --network none "${HARDENED[@]}" -v "$ca_dir:/etc/exiqo-ca:ro" -e MIRROR_CA_BUNDLE=/etc/exiqo-ca/ca.pem \
  -e EXIQO_ROLE=bogus --entrypoint sh "$PLATFORM" -c \
  '/entrypoint.sh >/dev/null 2>&1; test $? -eq 64 && keytool -list -keystore /tmp/exiqo-cacerts -storepass changeit | grep -q exiqo-mirror-1'
rm -rf "$ca_dir"

echo "== $WEB"
expect_output "web image config sets USER 10001" "10001:10001" docker image inspect -f '{{.Config.User}}' "$WEB"
expect_status "web has no shell" 1 sh -c "docker run --rm --entrypoint sh '$WEB' -c true >/dev/null 2>&1 && exit 0; exit 1"

cid="$(docker run -d "${HARDENED[@]}" -p 127.0.0.1::3000 "$WEB")"
containers+=("$cid")
port="$(docker port "$cid" 3000/tcp | head -1 | sed 's/.*://')"
code="$(http_status "http://127.0.0.1:$port/")"
if [ "$code" = "200" ]; then
  pass "web on a read-only root answers / 200"
else
  fail "web on a read-only root answers / 200" "got $code; logs: $(docker logs "$cid" 2>&1 | tail -5 | tr '\n' ' ')"
fi
if docker logs "$cid" 2>&1 | grep -qiE 'EROFS|read-only file system'; then
  fail "web writes nothing outside /tmp" "$(docker logs "$cid" 2>&1 | grep -iE 'EROFS|read-only' | head -2)"
else
  pass "web writes nothing outside /tmp"
fi

bundle="$(mktemp -d)"
created="$(docker create "$WEB")"
containers+=("$created")
docker cp -q "$created:/app/.next/static" "$bundle/static" 2>/dev/null || docker cp "$created:/app/.next/static" "$bundle/static" >/dev/null
# The development API URL (web/scripts/root-env.ts, DEFAULT_API_PORT) must not be baked in. The
# Export panel's http://localhost:8080 (the generated service run on a laptop) is legitimate text.
if grep -rqE 'https?://(localhost|127\.0\.0\.1):9000' "$bundle/static"; then
  fail "web bundle calls the API on its own origin (/api), not the development URL" \
    "$(grep -rhoE 'https?://(localhost|127\.0\.0\.1):9000' "$bundle/static" | sort -u | head -3 | tr '\n' ' ')"
else
  pass "web bundle calls the API on its own origin (/api), not the development URL"
fi
rm -rf "$bundle"

echo
if [ "$failures" -gt 0 ]; then
  echo "image-test: $failures check(s) failed"
  exit 1
fi
echo "image-test: all checks passed"
