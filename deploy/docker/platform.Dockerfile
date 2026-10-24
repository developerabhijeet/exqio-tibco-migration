# syntax=docker/dockerfile:1.10
# exiqo-platform: the engine, the API and the job worker (EXIQO_ROLE=api|worker|migrate), with the
# toolchains the migration steps run: the .NET 10 SDK, Python, Terraform and Terragrunt.
#
# Build (context: the repo root; .dockerignore is an allowlist):
#   docker build -f deploy/docker/platform.Dockerfile -t exiqo-platform:<tag> .
#   docker buildx build --platform linux/amd64 ...            (AKS; the pipelines build this)
#
# Base images are build arguments pinned by digest. The defaults below are the public upstream
# images, so the file builds anywhere; GM Financial's pipeline overrides them with the same images
# mirrored into its ACR (--build-arg BASE_IMAGE_DOTNET=<acr>/dotnet/sdk@sha256:...), and likewise
# points PIP_INDEX_URL and HF_ENDPOINT at its package mirrors. The digest is a multi-arch index,
# so one value serves linux/amd64 and linux/arm64.
#
# Stages:
#   toolchain  the SDK base plus Python 3.12 (Ubuntu 24.04's), tini, Terraform and Terragrunt,
#              each download checked against a SHA256 build argument for the target architecture
#   deps       the Python dependencies in /opt/venv and the fastembed model in /opt/models
#   final      the application, no apt, UID 10001, read-only by design (writes: /tmp and /data)

# mcr.microsoft.com/dotnet/sdk:10.0 (Ubuntu 24.04 "noble"), resolved 2026-10-24
ARG BASE_IMAGE_DOTNET=mcr.microsoft.com/dotnet/sdk:10.0@sha256:747cbade5bfd918a175ac32f8f1d931edec2c7d0d5397a3de72eaef1ac1f66d2

# ------------------------------------------------------------------------------------- toolchain
# The tag and digest come from the build argument (DL3006 cannot see through it).
# hadolint ignore=DL3006
FROM ${BASE_IMAGE_DOTNET} AS toolchain

ARG TARGETARCH
ARG TERRAFORM_VERSION=1.9.8
# From https://releases.hashicorp.com/terraform/1.9.8/terraform_1.9.8_SHA256SUMS
ARG TERRAFORM_SHA256_AMD64=186e0145f5e5f2eb97cbd785bc78f21bae4ef15119349f6ad4fa535b83b10df8
ARG TERRAFORM_SHA256_ARM64=f85868798834558239f6148834884008f2722548f84034c9b0f62934b2d73ebb
ARG TERRAGRUNT_VERSION=0.68.17
# From https://github.com/gruntwork-io/terragrunt/releases/download/v0.68.17/SHA256SUMS
ARG TERRAGRUNT_SHA256_AMD64=a2ec458416e9560e3a7c8376818d07c5cc62abaa5627e884913b719622c1d7ae
ARG TERRAGRUNT_SHA256_ARM64=b0f326a91e9d5944d8e3634bc4306984cc4f6ec25eb9ced4e6583957cfdedba1
# GMF mirrors of the two release sites (Artifactory generic remotes, for example); same paths.
ARG TERRAFORM_BASE_URL=https://releases.hashicorp.com/terraform
ARG TERRAGRUNT_BASE_URL=https://github.com/gruntwork-io/terragrunt/releases/download

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Python from the distribution (security updates follow Ubuntu's), tini as PID 1. The versions are
# the distribution's: the base digest pins the package set, and Trivy reports what it contains.
# hadolint ignore=DL3008
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv tini \
 && apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/*

# Terraform and Terragrunt: fetched for the build's target architecture and refused unless the
# checksum matches. Terraform's zip is unpacked with Python's zipfile (no unzip package needed).
RUN case "${TARGETARCH}" in \
      amd64) tf_sha="${TERRAFORM_SHA256_AMD64}"; tg_sha="${TERRAGRUNT_SHA256_AMD64}" ;; \
      arm64) tf_sha="${TERRAFORM_SHA256_ARM64}"; tg_sha="${TERRAGRUNT_SHA256_ARM64}" ;; \
      *) echo "unsupported TARGETARCH '${TARGETARCH}'" >&2; exit 1 ;; \
    esac \
 && work="$(mktemp -d)" \
 && curl -fsSL --proto '=https' --tlsv1.2 -o "$work/terraform.zip" \
      "${TERRAFORM_BASE_URL}/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_${TARGETARCH}.zip" \
 && echo "${tf_sha}  $work/terraform.zip" | sha256sum -c - \
 && python3 -m zipfile -e "$work/terraform.zip" "$work" \
 && install -m 0755 "$work/terraform" /usr/local/bin/terraform \
 && curl -fsSL --proto '=https' --tlsv1.2 -o "$work/terragrunt" \
      "${TERRAGRUNT_BASE_URL}/v${TERRAGRUNT_VERSION}/terragrunt_linux_${TARGETARCH}" \
 && echo "${tg_sha}  $work/terragrunt" | sha256sum -c - \
 && install -m 0755 "$work/terragrunt" /usr/local/bin/terragrunt \
 && rm -rf "$work" \
 && terraform version && terragrunt --version

# ------------------------------------------------------------------------------------------ deps
FROM toolchain AS deps

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG HF_ENDPOINT=https://huggingface.co
# Kept in step with engine/knowledge/embeddings.py (FASTEMBED_MODEL); the image test loads the
# engine's model offline, so a mismatch fails `make image-test`.
ARG FASTEMBED_MODEL=BAAI/bge-small-en-v1.5

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    HF_ENDPOINT=${HF_ENDPOINT} \
    HF_HUB_DISABLE_TELEMETRY=1 \
    FASTEMBED_CACHE_PATH=/opt/models

# Only pyproject.toml first: the dependency layer is rebuilt when the dependencies change, not on
# every source edit. The project's own code is not installed into the venv: it runs from /app,
# which the engine treats as the repository root (scripts/toolchain.py, .tools lookups).
COPY pyproject.toml /src/pyproject.toml
RUN python3 -m venv /opt/venv \
 && python3 -c 'import tomllib; print("\n".join(tomllib.load(open("/src/pyproject.toml", "rb"))["project"]["dependencies"]))' \
      > /src/requirements.txt \
 && /opt/venv/bin/pip install --no-cache-dir -r /src/requirements.txt \
 && /opt/venv/bin/pip check

# The embedding model is part of the image: no pod ever calls Hugging Face.
RUN /opt/venv/bin/python -c "import sys; from fastembed import TextEmbedding; TextEmbedding(sys.argv[1])" \
      "${FASTEMBED_MODEL}" \
 && chmod -R a+rX /opt/models

# ----------------------------------------------------------------------------------------- final
FROM toolchain AS final

ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="exiqo-platform" \
      org.opencontainers.image.description="ExiQO Migrate engine, API and job worker" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"

# No package manager at run time: apt goes (and its state and caches), so nothing can fetch and
# install packages. dpkg stays (it cannot download anything) because its database is what Trivy
# and syft read to list the OS packages; without it the image would scan as empty. Setuid
# and setgid bits are cleared so nothing in the image can raise privileges.
RUN dpkg --purge --force-remove-essential --force-depends apt \
 && rm -rf /var/lib/apt /var/cache/apt /etc/apt /var/log/apt /var/cache/debconf/*-old \
 && find / -xdev -perm /6000 -type f -exec chmod a-s {} + \
 && groupadd --gid 10001 exiqo \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp --shell /usr/sbin/nologin exiqo \
 && install -d -o 10001 -g 10001 -m 0750 /data

COPY --from=deps /opt/venv /opt/venv
COPY --from=deps /opt/models /opt/models
# The application, owned by root and not writable by the process user.
COPY --chmod=0755 deploy/docker/entrypoint.sh /entrypoint.sh
COPY scripts/toolchain.py /app/scripts/toolchain.py
COPY pyproject.toml /app/pyproject.toml
COPY engine /app/engine
COPY api /app/api
RUN /opt/venv/bin/python -m compileall -q /app/engine /app/api /app/scripts

# HOME, DOTNET_CLI_HOME and NUGET_PACKAGES live on /tmp (an emptyDir): dotnet and terraform write
# caches there and the root filesystem is read-only. DATA_DIR is the shared volume (Azure Files).
# CHECKPOINT_DISABLE stops Terraform's update check (no egress to HashiCorp); the dotnet settings
# switch off telemetry, the first-run experience, the workload update check and the long-lived
# build servers.
ENV PATH=/opt/venv/bin:/usr/local/bin:/usr/bin:/bin:/usr/share/dotnet \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp \
    DATA_DIR=/data \
    EXIQO_ROLE=api \
    EMBEDDINGS_BACKEND=fastembed \
    FASTEMBED_CACHE_PATH=/opt/models \
    HF_HUB_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    NEXT_TELEMETRY_DISABLED=1 \
    DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_CLI_HOME=/tmp \
    DOTNET_NOLOGO=1 \
    DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1 \
    DOTNET_GENERATE_ASPNET_CERTIFICATE=false \
    DOTNET_CLI_UI_LANGUAGE=en \
    DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE=true \
    MSBUILDDISABLENODEREUSE=1 \
    DOTNET_CLI_USE_MSBUILD_SERVER=0 \
    UseSharedCompilation=false \
    NUGET_PACKAGES=/tmp/nuget/packages \
    CHECKPOINT_DISABLE=1 \
    TF_IN_AUTOMATION=1

WORKDIR /app
USER 10001:10001
EXPOSE 8000 9100
ENTRYPOINT ["/entrypoint.sh"]
