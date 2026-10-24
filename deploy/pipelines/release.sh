#!/usr/bin/env bash
# The release steps shared by azure-pipelines.yml and github-release.yml, so both pipelines build,
# scan, sign and deploy the same way. Each stage of a pipeline calls one subcommand:
#
#   release.sh build    hadolint (make image-lint); build linux/amd64 images, push them under a
#                       candidate tag, print their digests (platform_digest=..., web_digest=...)
#   release.sh scan     pull by digest; make egress-audit; Trivy, failing on CRITICAL (make image-scan)
#   release.sh sign     syft SBOM attached with cosign attest; cosign sign; add the release tag
#   release.sh deploy   check the committed public key against COSIGN_PUBLIC_KEY_SHA256; cosign
#                       verify; helm upgrade --install --atomic
#   release.sh lint     hadolint only; release.sh verify: cosign verify only
#
# Images move between stages by digest only. An image that fails the scan stays an unsigned
# candidate, and deploy refuses anything that does not verify against GMF's signing key, so the
# candidate tag never reaches the cluster. The release tag is added after signing; ACR retention of
# untagged manifests can therefore not delete a running image.
#
# Each stage runs as its own identity (deploy/azure README.md): build/scan has AcrPush only; sign
# has AcrPush and Key Vault Crypto User on the signing key; deploy has AcrPull and its AKS role, and
# verifies with the public key committed in the repository (COSIGN_PUBLIC_KEY_FILE), so it needs no
# Key Vault access and cannot sign.
#
# Nothing here pulls from a public registry or download site: the base images, the BuildKit
# Dockerfile frontend, every tool image and the Trivy database come from GMF's mirror
# (TOOL_REGISTRY and the variables below), and cosign runs without the public transparency log.
# The tool images are pinned by digest, which is the same whichever mirror serves them.
#
# Runner prerequisites: bash, docker (the default "docker" buildx builder: no BuildKit container
# image to pull), the Azure CLI already signed in (azure/login or the AzureCLI task), GNU make,
# python3 (scripts/egress_audit.py uses the standard library only) and sha256sum. Pulls from
# TOOL_REGISTRY use the runner's docker credentials: when it is the ACR (cache rules), az acr
# login covers it; another mirror needs a docker login before these steps.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

need() {
  local name
  for name in "$@"; do
    if [ -z "${!name:-}" ]; then
      echo "release.sh: $name is not set (see the variables at the top of the pipeline)" >&2
      exit 2
    fi
  done
}

need TOOL_REGISTRY REGISTRY

# ---------------------------------------------------------------- tool images (via the mirror)
HADOLINT_IMAGE="${HADOLINT_IMAGE:-$TOOL_REGISTRY/hadolint/hadolint:v2.12.0@sha256:30a8fd2e785ab6176eed53f74769e04f125afb2f74a6c52aef7d463583b6d45e}"
TRIVY_IMAGE="${TRIVY_IMAGE:-$TOOL_REGISTRY/aquasec/trivy:0.58.1@sha256:ab70a02200597efa04748f210f793936eb647cbcdb0ea69cc30b226d6f5a22c7}"
SYFT_IMAGE="${SYFT_IMAGE:-$TOOL_REGISTRY/anchore/syft:v1.55.0@sha256:4cc92b64ca74ffca4c94d4c8fcc01f217731375300be14d0b6eeb2e82eaa110a}"
# cosign 2.6 (the maintained 2.x line): its sign/attest/verify flags are the ones used below.
COSIGN_IMAGE="${COSIGN_IMAGE:-$TOOL_REGISTRY/sigstore/cosign/cosign:v2.6.5@sha256:ad281047f85c5e1fc6ffbc30c2b55be3b07b4032bef715a12122ce5829619aca}"
# crane adds the release tag by digest with plain push rights (az acr import would need the
# importImage action, which AcrPush lacks).
CRANE_IMAGE="${CRANE_IMAGE:-$TOOL_REGISTRY/go-containerregistry/crane:v0.22.1@sha256:1f968817b95790bed063f71175aa6b8ff879fa17064020415f3e18bb6e6a36e1}"
HELM_IMAGE="${HELM_IMAGE:-$TOOL_REGISTRY/alpine/helm:3.16.4@sha256:9b25e60ae264940b276e32866d37e3088e70c4e2d1784b964dc3f90346281a74}"
# The Dockerfiles' "# syntax=docker/dockerfile:1.10" line, served by the mirror (BUILDKIT_SYNTAX).
DOCKERFILE_FRONTEND="${DOCKERFILE_FRONTEND:-$TOOL_REGISTRY/docker/dockerfile:1.10@sha256:865e5dd094beca432e8c0a1d5e1c465db5f998dca4e439981029b3b81fb39ed5}"

PLATFORM_REPOSITORY="${PLATFORM_REPOSITORY:-exiqo/exiqo-platform}"
WEB_REPOSITORY="${WEB_REPOSITORY:-exiqo/exiqo-web}"
REVISION="${REVISION:-$(git rev-parse HEAD)}"
# The release tag: the git tag that triggered the run (GitHub or Azure DevOps), else the commit.
if [ -z "${VERSION:-}" ]; then
  if [ "${GITHUB_REF_TYPE:-}" = tag ]; then
    VERSION="$GITHUB_REF_NAME"
  elif [[ "${BUILD_SOURCEBRANCH:-}" == refs/tags/* ]]; then
    VERSION="${BUILD_SOURCEBRANCH#refs/tags/}"
  else
    VERSION="$(git rev-parse --short=12 HEAD)"
  fi
fi
CANDIDATE_TAG="candidate-$REVISION"
WORK="${RUNNER_TEMP:-${AGENT_TEMPDIRECTORY:-/tmp}}/exiqo-release"
mkdir -p "$WORK"
# az acr login writes the registry token here; the tool containers read it (as this user).
DOCKER_CONFIG_DIR="${DOCKER_CONFIG:-$HOME/.docker}"

repo_of() { if [ "$1" = platform ]; then echo "$PLATFORM_REPOSITORY"; else echo "$WEB_REPOSITORY"; fi; }
digest_of() {
  local d
  if [ "$1" = platform ]; then d="${PLATFORM_DIGEST:-}"; else d="${WEB_DIGEST:-}"; fi
  if ! [[ "$d" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    echo "release.sh: $(echo "$1" | tr "[:lower:]" "[:upper:]")_DIGEST is not a sha256 digest: '$d'" >&2
    exit 2
  fi
  echo "$d"
}
need_digests() { digest_of platform >/dev/null && digest_of web >/dev/null; }
ref_of() { echo "$REGISTRY/$(repo_of "$1")@$(digest_of "$1")"; }

acr_login() {
  need ACR_NAME
  az acr login --name "$ACR_NAME" --only-show-errors >&2
}

pull_by_digest() {
  local image
  for image in platform web; do docker pull --quiet "$(ref_of "$image")"; done
}

# Tool containers run as this user: the docker config (registry token) is private to it.
as_me() {
  docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp \
    -e DOCKER_CONFIG=/docker-config -v "$DOCKER_CONFIG_DIR":/docker-config:ro "$@"
}

# cosign for signing, with Workload Identity to Key Vault: AZURE_FEDERATED_TOKEN_FILE holds the
# pipeline's OIDC token for the signer identity.
cosign_signer() {
  need COSIGN_KEY AZURE_CLIENT_ID AZURE_TENANT_ID AZURE_FEDERATED_TOKEN_FILE
  as_me -e AZURE_CLIENT_ID -e AZURE_TENANT_ID -e AZURE_FEDERATED_TOKEN_FILE=/var/run/azure/token \
    -e AZURE_AUTHORITY_HOST="${AZURE_AUTHORITY_HOST:-https://login.microsoftonline.com/}" \
    -v "$AZURE_FEDERATED_TOKEN_FILE":/var/run/azure/token:ro \
    -v "$WORK":/work "$COSIGN_IMAGE" "$@"
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1
}

# The committed public key is trusted only if its sha256 equals COSIGN_PUBLIC_KEY_SHA256, a
# variable held outside the repository (the deploy environment / approval-gated variable group;
# deploy/azure output cosign_public_key_sha256). A tag that swaps cosign.pub cannot change it.
check_public_key() {
  need COSIGN_PUBLIC_KEY_FILE COSIGN_PUBLIC_KEY_SHA256
  local file="$ROOT/$COSIGN_PUBLIC_KEY_FILE" want actual
  want="$(echo "$COSIGN_PUBLIC_KEY_SHA256" | tr '[:upper:]' '[:lower:]')"
  if ! [[ "$want" =~ ^[0-9a-f]{64}$ ]]; then
    echo "release.sh: COSIGN_PUBLIC_KEY_SHA256 is not a sha256: '$COSIGN_PUBLIC_KEY_SHA256'" >&2
    exit 3
  fi
  if [ ! -f "$file" ]; then
    echo "release.sh: $COSIGN_PUBLIC_KEY_FILE does not exist" >&2
    exit 3
  fi
  actual="$(sha256_of "$file")"
  if [ "$actual" != "$want" ]; then
    echo "release.sh: $COSIGN_PUBLIC_KEY_FILE has sha256 $actual, not the pinned $want; refusing to deploy" >&2
    exit 3
  fi
}

# cosign for verifying: the pinned public key from the repository, no Azure credentials.
cosign_verifier() {
  need COSIGN_PUBLIC_KEY_FILE
  as_me -v "$ROOT/$COSIGN_PUBLIC_KEY_FILE":/keys/cosign.pub:ro "$COSIGN_IMAGE" "$@"
}

cmd_lint() {
  make image-lint HADOLINT_IMAGE="$HADOLINT_IMAGE"
}

cmd_build() {
  need BASE_IMAGE_DOTNET BASE_IMAGE_UV BASE_IMAGE_NODE_BUILD BASE_IMAGE_NODE_RUNTIME \
    PIP_INDEX_URL HF_ENDPOINT NPM_CONFIG_REGISTRY TERRAFORM_BASE_URL TERRAGRUNT_BASE_URL ACR_NAME
  acr_login
  cmd_lint >&2
  local common=(--builder default --platform linux/amd64 --provenance=false --sbom=false --load
    --build-arg BUILDKIT_SYNTAX="$DOCKERFILE_FRONTEND"
    --build-arg VERSION="$VERSION" --build-arg REVISION="$REVISION")
  docker buildx build "${common[@]}" -f deploy/docker/platform.Dockerfile >&2 \
    --build-arg BASE_IMAGE_DOTNET="$BASE_IMAGE_DOTNET" --build-arg BASE_IMAGE_UV="$BASE_IMAGE_UV" \
    --build-arg PIP_INDEX_URL="$PIP_INDEX_URL" --build-arg HF_ENDPOINT="$HF_ENDPOINT" \
    --build-arg TERRAFORM_BASE_URL="$TERRAFORM_BASE_URL" \
    --build-arg TERRAGRUNT_BASE_URL="$TERRAGRUNT_BASE_URL" \
    -t "$REGISTRY/$PLATFORM_REPOSITORY:$CANDIDATE_TAG" .
  docker buildx build "${common[@]}" -f deploy/docker/web.Dockerfile >&2 \
    --build-arg BASE_IMAGE_NODE_BUILD="$BASE_IMAGE_NODE_BUILD" \
    --build-arg BASE_IMAGE_NODE_RUNTIME="$BASE_IMAGE_NODE_RUNTIME" \
    --build-arg NPM_CONFIG_REGISTRY="$NPM_CONFIG_REGISTRY" \
    -t "$REGISTRY/$WEB_REPOSITORY:$CANDIDATE_TAG" .

  # The digest is the registry's, read back after the push (RepoDigests), not the local image id.
  local image repo ref digest
  for image in platform web; do
    repo="$REGISTRY/$(repo_of "$image")"
    docker push --quiet "$repo:$CANDIDATE_TAG" >/dev/null
    ref="$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' \
      "$repo:$CANDIDATE_TAG" | grep -m1 "^$repo@sha256:")"
    digest="${ref#*@}"
    echo "release.sh: pushed $repo@$digest" >&2
    echo "${image}_digest=$digest"
  done
}

cmd_scan() {
  need_digests
  need TRIVY_DB_REPOSITORY TRIVY_JAVA_DB_REPOSITORY
  acr_login
  pull_by_digest
  make egress-audit PY=python3 PLATFORM_IMAGE="$(ref_of platform)" WEB_IMAGE="$(ref_of web)"
  # --scanners vuln: no misconfiguration bundle to fetch. The databases come from the mirror.
  make image-scan TRIVY_IMAGE="$TRIVY_IMAGE" \
    PLATFORM_IMAGE="$(ref_of platform)" WEB_IMAGE="$(ref_of web)" \
    TRIVY_DOCKER_ARGS="-v $DOCKER_CONFIG_DIR:/root/.docker:ro" \
    TRIVY_SCAN_FLAGS="--scanners vuln --db-repository $TRIVY_DB_REPOSITORY --java-db-repository $TRIVY_JAVA_DB_REPOSITORY"
}

cmd_sign() {
  need_digests
  acr_login
  pull_by_digest
  local image ref
  for image in platform web; do
    ref="$(ref_of "$image")"
    # SBOM of exactly this image (read from the daemon by digest), attached as an in-toto
    # attestation (an OCI artifact next to the image in ACR).
    docker run --rm -e SYFT_CHECK_FOR_APP_UPDATE=false \
      -v /var/run/docker.sock:/var/run/docker.sock -v "$WORK":/out \
      "$SYFT_IMAGE" "docker:$ref" -o "spdx-json=/out/$image.spdx.json"
    # --tlog-upload=false: no upload to the public Rekor log; trust rests on GMF's key.
    cosign_signer attest --yes --key "$COSIGN_KEY" --tlog-upload=false --type spdxjson \
      --predicate "/work/$image.spdx.json" "$ref"
    cosign_signer sign --yes --key "$COSIGN_KEY" --tlog-upload=false \
      -a "org.opencontainers.image.version=$VERSION" -a "org.opencontainers.image.revision=$REVISION" "$ref"
    # The release tag on the signed digest (a manifest push, no layers move).
    as_me "$CRANE_IMAGE" tag "$ref" "$VERSION"
  done
}

cmd_verify() {
  need_digests
  check_public_key
  local image
  for image in platform web; do
    cosign_verifier verify --key /keys/cosign.pub --insecure-ignore-tlog=true "$(ref_of "$image")" >/dev/null
    cosign_verifier verify-attestation --key /keys/cosign.pub --insecure-ignore-tlog=true --type spdxjson \
      "$(ref_of "$image")" >/dev/null
    echo "release.sh: $(ref_of "$image") is signed and has its SBOM" >&2
  done
}

# The Neo4j subchart is not committed (.gitignore): fetched from GMF's Helm mirror at the version
# Chart.lock pins and refused unless it is the archive this chart was tested with.
NEO4J_CHART_SHA256="c7402735f1a4219db507d6c2c40a5106154092d680c7ff564da3db3c120312d7"

helm() {
  docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$ROOT":/work -w /work \
    -e HELM_KUBEAPISERVER -e HELM_KUBECAFILE -e HELM_KUBETOKEN "$HELM_IMAGE" "$@"
}

cmd_deploy() {
  need_digests
  need AKS_RESOURCE_GROUP AKS_NAME NAMESPACE HELM_VALUES_FILES NEO4J_CHART_REPOSITORY \
    COSIGN_PUBLIC_KEY_FILE
  check_public_key
  acr_login
  cmd_verify

  local version
  version="$(sed -n 's/^  version: //p' deploy/helm/exiqo/Chart.lock | head -1)"
  mkdir -p deploy/helm/exiqo/charts
  if [[ "$NEO4J_CHART_REPOSITORY" == oci://* ]]; then
    helm pull "$NEO4J_CHART_REPOSITORY/neo4j" --version "$version" --destination deploy/helm/exiqo/charts
  else
    helm pull neo4j --repo "$NEO4J_CHART_REPOSITORY" --version "$version" \
      --destination deploy/helm/exiqo/charts
  fi
  echo "$NEO4J_CHART_SHA256  deploy/helm/exiqo/charts/neo4j-$version.tgz" | sha256sum -c -

  # The API server and CA from the cluster, and an Entra token for it (AKS with Entra ID and
  # Azure RBAC). The deployer's role needs cluster scope for the chart's StorageClass, unless the
  # install sets data.storageClass.create=false (deploy/azure README.md). No kubelogin: helm gets
  # a bearer token.
  local kubeconfig="$WORK/kubeconfig"
  trap 'rm -f "$WORK/kubeconfig" "$ROOT/deploy/helm/.cluster-ca.crt"' EXIT
  az aks get-credentials --resource-group "$AKS_RESOURCE_GROUP" --name "$AKS_NAME" \
    --file "$kubeconfig" --overwrite-existing --only-show-errors
  HELM_KUBEAPISERVER="$(sed -n 's/^ *server: //p' "$kubeconfig" | head -1)"
  sed -n 's/^ *certificate-authority-data: //p' "$kubeconfig" | head -1 | base64 -d \
    >"$ROOT/deploy/helm/.cluster-ca.crt"
  HELM_KUBECAFILE=/work/deploy/helm/.cluster-ca.crt
  # 6dae42f8-...: the AKS Entra server application, the token audience for every AKS cluster.
  HELM_KUBETOKEN="$(az account get-access-token --resource 6dae42f8-4368-4678-94ff-3960e28e3630 \
    --query accessToken --output tsv)"
  if [ -n "${GITHUB_ACTIONS:-}" ]; then echo "::add-mask::$HELM_KUBETOKEN"; fi
  if [ -n "${TF_BUILD:-}" ]; then echo "##vso[task.setsecret]$HELM_KUBETOKEN"; fi
  export HELM_KUBEAPISERVER HELM_KUBECAFILE HELM_KUBETOKEN

  local values=() file
  for file in $HELM_VALUES_FILES; do values+=(-f "$file"); done
  # --atomic rolls back a failed upgrade; 20m covers the migration hook (activeDeadlineSeconds
  # 900) plus the rollout.
  helm upgrade --install "${RELEASE_NAME:-exiqo}" deploy/helm/exiqo --namespace "$NAMESPACE" \
    "${values[@]}" \
    --set-string image.registry="$REGISTRY" \
    --set-string image.platform.repository="$PLATFORM_REPOSITORY" \
    --set-string image.platform.digest="$(digest_of platform)" \
    --set-string image.web.repository="$WEB_REPOSITORY" \
    --set-string image.web.digest="$(digest_of web)" \
    --atomic --timeout 20m --history-max 10
}

case "${1:-}" in
  lint | build | scan | sign | verify | deploy) "cmd_$1" ;;
  *)
    echo "usage: release.sh lint|build|scan|sign|verify|deploy" >&2
    exit 2
    ;;
esac
