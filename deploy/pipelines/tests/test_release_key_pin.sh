#!/usr/bin/env bash
# release.sh deploy trusts the committed cosign public key only when its sha256 matches
# COSIGN_PUBLIC_KEY_SHA256 (held outside the repository). Runs release.sh with docker, az and make
# stubbed (each call is logged) and checks that a missing or mismatched pin stops it before any
# registry, cluster or helm call, and that the right pin lets it go on to cosign verify.
# Run by make pipeline-lint.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RELEASE="$HERE/../release.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin"
for tool in docker az make; do
  printf '#!/bin/sh\necho "%s $*" >>"%s/calls"\nexit 0\n' "$tool" "$TMP" >"$TMP/bin/$tool"
  chmod +x "$TMP/bin/$tool"
done

KEY=deploy/pipelines/tests/fixture-cosign.pub
if command -v sha256sum >/dev/null 2>&1; then
  RIGHT="$(sha256sum "$HERE/fixture-cosign.pub" | cut -d' ' -f1)"
else
  RIGHT="$(shasum -a 256 "$HERE/fixture-cosign.pub" | cut -d' ' -f1)"
fi
WRONG="$(printf '0%.0s' $(seq 64))"
DIGEST="sha256:$(printf 'a%.0s' $(seq 64))"

fails=0
run() {  # run <name> <expected exit> <pin or "unset">
  : >"$TMP/calls"
  local pin=()
  if [ "$3" != unset ]; then pin=(COSIGN_PUBLIC_KEY_SHA256="$3"); fi
  env -u COSIGN_PUBLIC_KEY_SHA256 PATH="$TMP/bin:$PATH" RUNNER_TEMP="$TMP" HOME="$TMP" \
    TOOL_REGISTRY=mirror.example REGISTRY=acr.example ACR_NAME=acr \
    PLATFORM_DIGEST="$DIGEST" WEB_DIGEST="$DIGEST" AKS_RESOURCE_GROUP=rg AKS_NAME=aks \
    NAMESPACE=exiqo HELM_VALUES_FILES=values.yaml NEO4J_CHART_REPOSITORY=https://charts.example \
    COSIGN_PUBLIC_KEY_FILE="$KEY" ${pin[@]+"${pin[@]}"} "$RELEASE" deploy >"$TMP/out" 2>&1
  local rc=$?
  echo "$1: exit $rc"
  if [ "$rc" -ne "$2" ]; then echo "  FAIL: expected exit $2"; cat "$TMP/out"; fails=$((fails + 1)); fi
}

run "pin unset" 2 unset
if [ -s "$TMP/calls" ]; then echo "  FAIL: tools ran without a pin:"; cat "$TMP/calls"; fails=$((fails + 1)); fi
grep -q "COSIGN_PUBLIC_KEY_SHA256 is not set" "$TMP/out" || { echo "  FAIL: no message"; fails=$((fails + 1)); }

run "pin mismatched" 3 "$WRONG"
if [ -s "$TMP/calls" ]; then echo "  FAIL: tools ran with a wrong pin:"; cat "$TMP/calls"; fails=$((fails + 1)); fi
grep -q "refusing to deploy" "$TMP/out" || { echo "  FAIL: no refusal message"; fails=$((fails + 1)); }

# The right pin (upper case accepted) gets past the check to cosign verify; it stops later at
# the subchart checksum (the stubs fetch nothing), so its exit code is not asserted.
: >"$TMP/calls"
env -u COSIGN_PUBLIC_KEY_SHA256 PATH="$TMP/bin:$PATH" RUNNER_TEMP="$TMP" HOME="$TMP" \
  TOOL_REGISTRY=mirror.example REGISTRY=acr.example ACR_NAME=acr \
  PLATFORM_DIGEST="$DIGEST" WEB_DIGEST="$DIGEST" AKS_RESOURCE_GROUP=rg AKS_NAME=aks \
  NAMESPACE=exiqo HELM_VALUES_FILES=values.yaml NEO4J_CHART_REPOSITORY=https://charts.example \
  COSIGN_PUBLIC_KEY_FILE="$KEY" COSIGN_PUBLIC_KEY_SHA256="$(echo "$RIGHT" | tr '[:lower:]' '[:upper:]')" \
  "$RELEASE" deploy >"$TMP/out" 2>&1
if grep -q "cosign.* verify --key /keys/cosign.pub" "$TMP/calls"; then
  echo "pin matching: reached cosign verify"
else
  echo "  FAIL: the matching pin did not reach cosign verify"; cat "$TMP/out" "$TMP/calls"; fails=$((fails + 1))
fi
if grep -q "upgrade --install" "$TMP/calls"; then
  echo "  FAIL: helm ran without the subchart"; fails=$((fails + 1))
fi

if [ "$fails" -ne 0 ]; then echo "test_release_key_pin: $fails failure(s)"; exit 1; fi
echo "test_release_key_pin: ok"
