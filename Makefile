PY ?= .venv/bin/python
PYTEST ?= .venv/bin/pytest

# Ports for `make api`, `make web` and `make demo-check`. The web calls the API from the browser.
# The API's CORS_ORIGINS defaults to http://localhost:3000; with another WEB_PORT the API is started
# with that origin, unless already set. The API passes the same origins to the generated service it
# starts in Verify (as Cors__Origins__<i>), so the service's Development CORS follows CORS_ORIGINS.
#
# API_PORT and WEB_PORT come from .env when set there, so this file and the web agree on one value:
# web/scripts/root-env.ts reads the same two keys and next.config.ts bakes the API URL into the
# bundle. Change the port in .env (or pass API_PORT=... here) and both ends follow.
# api/tests/test_dev_ports.py fails if the fallbacks below and the web's stop matching.
dotenv = $(shell sed -n 's/^$(1)=//p' .env 2>/dev/null | tr -d ' \r' | tail -1)
API_PORT ?= $(or $(call dotenv,API_PORT),9000)
WEB_PORT ?= $(or $(call dotenv,WEB_PORT),3000)
API_URL ?= http://localhost:$(API_PORT)
WEB_ORIGIN := http://localhost:$(WEB_PORT)
DB_PORT ?= $(or $(shell sed -n 's|^DATABASE_URL=.*:\([0-9][0-9]*\)/.*|\1|p' .env 2>/dev/null | tail -1),5432)
# True when something accepts a TCP connection on the given port of localhost.
PORT_OPEN = python3 -c 'import socket,sys; s=socket.socket(); s.settimeout(2); sys.exit(s.connect_ex(("127.0.0.1", int(sys.argv[1]))) != 0)'
WEB_CORS_ENV := $(if $(filter-out 3000,$(WEB_PORT)),CORS_ORIGINS=$${CORS_ORIGINS:-$(WEB_ORIGIN)})

.PHONY: setup db neo4j neo4j-test api worker web test e2e demo-check toolchain samples samples-zip nuget-cache \
	images image-test image-lint image-scan pilot-up egress-audit chart-check \
	azure-validate pipeline-lint kind-tools kind-e2e

setup:
	python3 -m venv .venv
	.venv/bin/pip install -e ".[dev]"
	@if [ -f web/package.json ]; then cd web && npm install; fi

# `docker compose --wait` only waits for the container's own healthcheck: Docker can report healthy
# while the host-side port forward is missing, and the first symptom is then a connection refused
# deep inside alembic. Probe the port from the host and recreate the container once if it is not
# reachable, which is what restores the forward.
db:
	@if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then \
		docker compose up -d --wait postgres; \
		if ! $(PORT_OPEN) $(DB_PORT); then \
			echo "db: Postgres is healthy but port $(DB_PORT) is not reachable from the host; recreating the container"; \
			docker compose up -d --force-recreate --wait postgres; \
			$(PORT_OPEN) $(DB_PORT) || { echo "db: port $(DB_PORT) is still unreachable; restart Docker Desktop"; exit 1; }; \
		fi; \
	else echo "docker not available: using the Postgres at DATABASE_URL"; fi
	.venv/bin/alembic -c api/alembic.ini upgrade head

NEO4J_NOTE = neo4j: docker not available: set NEO4J_URI (or NEO4J_TEST_URI) to a Neo4j 5.26

neo4j:
	@if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then \
		docker compose up -d --wait neo4j; \
		if ! $(PORT_OPEN) 7687; then \
			echo "neo4j: healthy but port 7687 is not reachable from the host; recreating the container"; \
			docker compose up -d --force-recreate --wait neo4j; \
			$(PORT_OPEN) 7687 || { echo "neo4j: port 7687 is still unreachable; restart Docker Desktop"; exit 1; }; \
		fi; \
	else echo "$(NEO4J_NOTE)"; fi

neo4j-test:
	@if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then \
		docker compose --profile test up -d --wait neo4j-test; \
		if ! $(PORT_OPEN) 7688; then \
			echo "neo4j-test: healthy but port 7688 is not reachable from the host; recreating the container"; \
			docker compose --profile test up -d --force-recreate --wait neo4j-test; \
			$(PORT_OPEN) 7688 || { echo "neo4j-test: port 7688 is still unreachable; restart Docker Desktop"; exit 1; }; \
		fi; \
	else echo "$(NEO4J_NOTE)"; fi

# A port left busy by an earlier session shows up as uvicorn's bare "[Errno 48] Address already in
# use", which says nothing about what to do; name the holder and the override instead.
api:
	@if $(PORT_OPEN) $(API_PORT); then \
		echo "api: port $(API_PORT) is already in use by:"; \
		lsof -nP -iTCP:$(API_PORT) -sTCP:LISTEN 2>/dev/null | tail -n +2 | sed 's/^/  /'; \
		echo "  stop it, or start elsewhere with: make api API_PORT=<port> (set API_PORT in .env to move it for good)"; \
		exit 1; \
	fi
	env $(WEB_CORS_ENV) .venv/bin/uvicorn api.app.main:app --reload --port $(API_PORT)

# A job worker on its own, as in a worker pod: run it beside `make api` started with
# EXIQO_ROLE=api to have the API only queue. Its internal listener is on INTERNAL_PORT (9100).
worker:
	EXIQO_ROLE=worker $(PY) -m api.app.worker

# Two things are worth catching before `next dev` starts. A busy WEB_PORT means an older dev server
# still serves this app with the API URL it was started with, so it would keep calling the old port
# whatever this checkout says. And the web is usable before the API is up, so a missing API is only
# a warning, while something that is not this API answering on API_PORT is worth shouting about.
web:
	@if $(PORT_OPEN) $(WEB_PORT); then \
		echo "web: port $(WEB_PORT) is already in use by:"; \
		lsof -nP -iTCP:$(WEB_PORT) -sTCP:LISTEN 2>/dev/null | tail -n +2 | sed 's/^/  /'; \
		echo "  a dev server left from an earlier session serves the API URL it was started with,"; \
		echo "  so it keeps calling the old port: stop it and run make web again,"; \
		echo "  or start elsewhere with make web WEB_PORT=<port> (CORS_ORIGINS follows)."; \
		exit 1; \
	fi
	@echo "web: calling the API at $(API_URL)"
	@if curl -sf -m 3 $(API_URL)/health 2>/dev/null | grep -q '"status"'; then \
		echo "web: the API answers there"; \
	elif $(PORT_OPEN) $(API_PORT); then \
		echo "web: WARNING something is listening on port $(API_PORT) but it is not this API; the web will not work until it is"; \
	else \
		echo "web: nothing answers there yet; start it with: make api"; \
	fi
	cd web && ([ -d node_modules ] || npm install) && \
		NEXT_PUBLIC_API_URL=$${NEXT_PUBLIC_API_URL:-$(API_URL)} npm run dev -- -p $(WEB_PORT)

test: neo4j-test
	NEO4J_TEST_URI=$${NEO4J_TEST_URI:-bolt://localhost:7688} NEO4J_TEST_PASSWORD=$${NEO4J_TEST_PASSWORD:-exiqo-test-neo4j} $(PYTEST)

# Playwright starts its own API (PROVIDER=fake, EMBEDDINGS_BACKEND=hash) and web on two free ports,
# so a server someone left on 9000 or 3000 is never reused. Override with E2E_API_PORT/E2E_WEB_PORT;
# E2E_REUSE_SERVERS=1 reuses servers you started yourself on those ports.
TWO_FREE_PORTS := python3 -c 'import socket; a, b = socket.socket(), socket.socket(); \
	a.bind(("127.0.0.1", 0)); b.bind(("127.0.0.1", 0)); print(a.getsockname()[1], b.getsockname()[1])'

e2e: neo4j
	@ports=$$($(TWO_FREE_PORTS)); api=$(or $(E2E_API_PORT),$${ports% *}); web=$(or $(E2E_WEB_PORT),$${ports#* }); \
	echo "e2e: API on port $$api, web on port $$web"; \
	cd web && ([ -d node_modules ] || npm install) && \
		E2E_API_PORT=$$api E2E_WEB_PORT=$$web NEXT_PUBLIC_API_URL=http://localhost:$$api npx playwright test

# Acceptance run (design spec section 12; scripts/demo_check.py). Uses the API answering at API_URL,
# or starts one on API_PORT for the run and stops it afterwards. Started here, the API uses
# PROVIDER=fake unless PROVIDER is set or a Claude token or Azure OpenAI key is set in the
# environment or .env (then the API's own choice applies), and EMBEDDINGS_BACKEND=hash unless set.
# DEMO_ARGS passes options through, for example DEMO_ARGS="--mode offline".
DEMO_LOG ?= data/demo-check-api.log

demo-check: neo4j samples samples-zip
	@if curl -sf -m 5 $(API_URL)/health >/dev/null 2>&1; then \
		echo "demo-check: using the API already answering at $(API_URL)"; \
		$(PY) scripts/demo_check.py --api $(API_URL) $(DEMO_ARGS); \
	else \
		mkdir -p $$(dirname $(DEMO_LOG)); \
		keys=$$( { env; cat .env 2>/dev/null; } | grep -cE '^(CLAUDE_CODE_OAUTH_TOKEN|AZURE_OPENAI_API_KEY)=.+' ); \
		provider=$${PROVIDER:-}; \
		if [ -z "$$provider" ] && [ "$$keys" -eq 0 ]; then provider=fake; fi; \
		echo "demo-check: starting the API on port $(API_PORT) (provider $${provider:-from .env}, log $(DEMO_LOG))"; \
		env $${provider:+PROVIDER=$$provider} EMBEDDINGS_BACKEND=$${EMBEDDINGS_BACKEND:-hash} $(WEB_CORS_ENV) \
			.venv/bin/uvicorn api.app.main:app --port $(API_PORT) >$(DEMO_LOG) 2>&1 & pid=$$!; \
		trap 'kill $$pid 2>/dev/null; wait $$pid 2>/dev/null' EXIT; \
		up=0; for i in $$(seq 60); do \
			if curl -sf -m 2 $(API_URL)/health >/dev/null 2>&1; then up=1; break; fi; \
			kill -0 $$pid 2>/dev/null || break; sleep 1; \
		done; \
		if [ $$up -ne 1 ]; then echo "demo-check: the API did not start; see $(DEMO_LOG)"; exit 1; fi; \
		$(PY) scripts/demo_check.py --api $(API_URL) $(DEMO_ARGS); \
	fi

toolchain:
	python3 scripts/toolchain.py

samples:
	$(PY) scripts/gen_synthetic_gmf.py

samples-zip:
	rm -f samples/synthetic-gmf.zip
	$(PY) -m zipfile -c samples/synthetic-gmf.zip samples/synthetic-gmf

# Offline NuGet feed for machines where api.nuget.org is refused (run this where NuGet is reachable,
# then copy .tools/nuget-cache to the same path on the offline machine). Generates the synthetic
# estate's solution, restores it into a dedicated packages folder (exactly the packages it needs,
# transitive ones included, in the layout NuGet reads as a local feed) and copies that folder to
# .tools/nuget-cache. DotnetRunner passes it to generated solutions as EXIQO_NUGET_CACHE.
DOTNET ?= $(shell command -v dotnet 2>/dev/null || echo .tools/dotnet/dotnet)
NUGET_CACHE ?= .tools/nuget-cache
NUGET_WORK := .tools/nuget-cache-work

nuget-cache:
	rm -rf $(NUGET_WORK)
	$(PY) -m engine.dotnet_gen.generator samples/synthetic-gmf --app gmf.credit --out $(NUGET_WORK)/GmfCredit
	EXIQO_NUGET_CACHE= NUGET_PACKAGES=$(CURDIR)/$(NUGET_WORK)/packages $(DOTNET) restore $(NUGET_WORK)/GmfCredit/GmfCredit.sln
	mkdir -p $(NUGET_CACHE)
	cp -R $(NUGET_WORK)/packages/. $(NUGET_CACHE)/
	rm -rf $(NUGET_WORK)
	@echo "Offline NuGet feed ready in $(NUGET_CACHE) ($$(ls $(NUGET_CACHE) | wc -l) packages)."

# ---------------------------------------------------------------- container images (deploy/docker)
# `make images` builds both images for this machine's architecture; the pipelines (deploy/pipelines)
# build linux/amd64 for AKS with GMF's mirrored base images (BASE_IMAGE_* build args) and push by
# digest. IMAGE_BUILD_ARGS passes extra --build-arg flags, for example the mirrors:
#   make images IMAGE_BUILD_ARGS="--build-arg PIP_INDEX_URL=https://<mirror>/simple"
# hadolint and Trivy run from their pinned images when they are not on PATH.
IMAGE_TAG ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo dev)
PLATFORM_IMAGE ?= exiqo-platform:$(IMAGE_TAG)
WEB_IMAGE ?= exiqo-web:$(IMAGE_TAG)
IMAGE_BUILD_ARGS ?=
IMAGE_LABEL_ARGS := --build-arg VERSION=$(IMAGE_TAG) \
	--build-arg REVISION=$(shell git rev-parse HEAD 2>/dev/null || echo unknown)
DOCKERFILES := deploy/docker/platform.Dockerfile deploy/docker/web.Dockerfile
HADOLINT_IMAGE ?= hadolint/hadolint:v2.12.0
TRIVY_IMAGE ?= aquasec/trivy:0.58.1
HADOLINT := $(if $(shell command -v hadolint 2>/dev/null),hadolint,\
	docker run --rm -i -v "$(CURDIR)":/work -w /work $(HADOLINT_IMAGE) hadolint)
# TRIVY_SCAN_FLAGS and TRIVY_DOCKER_ARGS: the release pipelines point the vulnerability databases
# at GMF's mirror (--db-repository) and pass the registry credentials (deploy/pipelines/release.sh).
TRIVY_SCAN_FLAGS ?=
TRIVY_DOCKER_ARGS ?=
TRIVY := $(if $(shell command -v trivy 2>/dev/null),trivy,\
	docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v exiqo-trivy-cache:/root/.cache \
		$(TRIVY_DOCKER_ARGS) -v "$(CURDIR)/.trivyignore":/work/.trivyignore:ro -w /work $(TRIVY_IMAGE))
PILOT_COMPOSE := docker compose -f deploy/docker/compose.pilot.yml

images:
	docker build -f deploy/docker/platform.Dockerfile $(IMAGE_LABEL_ARGS) $(IMAGE_BUILD_ARGS) -t $(PLATFORM_IMAGE) .
	docker build -f deploy/docker/web.Dockerfile $(IMAGE_LABEL_ARGS) $(IMAGE_BUILD_ARGS) -t $(WEB_IMAGE) .

# Builds both images as :test and runs the runtime checks (UID, read-only root, no apt, offline
# embeddings, toolchains, same-origin bundle). SKIP_BUILD=1 checks images already built.
image-test:
	deploy/tests/test_images.sh

image-lint:
	$(HADOLINT) --failure-threshold error $(DOCKERFILES)

# Fails on any CRITICAL vulnerability, fixed upstream or not, except those accepted in .trivyignore
# (each with its reason and an expiry date).
image-scan:
	@rc=0; for image in $(PLATFORM_IMAGE) $(WEB_IMAGE); do \
		echo "image-scan: $$image"; \
		$(TRIVY) image --severity CRITICAL --exit-code 1 --no-progress --ignorefile .trivyignore $(TRIVY_SCAN_FLAGS) $$image || rc=1; \
	done; exit $$rc

# The hardened single-VM pilot (deploy/docker/compose.pilot.yml; its header lists the secret files
# to create under deploy/docker/secrets/). Defaults to the images `make images` built here.
pilot-up:
	PLATFORM_IMAGE=$(PLATFORM_IMAGE) WEB_IMAGE=$(WEB_IMAGE) $(PILOT_COMPOSE) up -d --wait

# Build-time egress evidence for GM Financial (scripts/egress_audit.py, deploy/egress-allowlist.txt):
# every host named in our own code (/app of the platform image) and in what the web image ships to
# the browser must be on the allowlist, each entry with its reason. Run `make images` first. Only
# the listed paths are copied out of each image into a temporary directory, removed afterwards;
# third-party runtimes (/opt/venv, the .NET SDK) and the web server's node_modules are out of scope.
# The Claude hosts live in the allowlist's `# optional: claude` section, which this does not enable:
# our own code names none of them. Next.js telemetry lives in node_modules/next/dist/telemetry and
# is driven by the next CLI (build/dev), not `node server.js`; web.Dockerfile sets
# NEXT_TELEMETRY_DISABLED=1 in both stages. Source-level check without Docker: scripts/tests (make test).
WEB_AUDIT_PATHS := --image-path /app/server.js --image-path /app/.next/server \
	--image-path /app/.next/static --image-path /app/public

egress-audit:
	@rc=0; \
	$(PY) scripts/egress_audit.py $(PLATFORM_IMAGE) --allowlist deploy/egress-allowlist.txt || rc=1; \
	$(PY) scripts/egress_audit.py $(WEB_IMAGE) $(WEB_AUDIT_PATHS) --allowlist deploy/egress-allowlist.txt || rc=1; \
	exit $$rc

# ---------------------------------------------------------------- Helm chart (deploy/helm/exiqo)
# Dependencies (the pinned Neo4j subchart), lint, the helm-unittest suites in deploy/helm/tests, the
# schema's refusals, the Neo4j subchart contract (deploy/helm/tests/subchart_contract.py: the pod
# label, Service, data claim and service accounts this chart relies on), and kubeconform -strict on
# what the GMF example (with and without the backup, and with deploy/azure's helm_values output)
# and the CI overlay render.
# helm, the unittest plugin and kubeconform run from pinned images when they are not on PATH (the
# images need network for the Neo4j chart repository and the schemas). SecretProviderClass is a
# CRD, validated against the datreeio CRDs catalog; nothing is skipped as an unknown kind.
HELM_IMAGE ?= alpine/helm:3.16.4
HELM_UNITTEST_IMAGE ?= helmunittest/helm-unittest:3.19.0-1.0.3
KUBECONFORM_IMAGE ?= ghcr.io/yannh/kubeconform:v0.6.7
KUBE_VERSION ?= 1.31.0
CHART := deploy/helm/exiqo
CHART_GMF := $(CHART)/values.gmf.example.yaml
CHART_CI := $(CHART)/ci/values.kind.yaml
HELM := $(if $(shell command -v helm 2>/dev/null),helm,\
	docker run --rm -i -v "$(CURDIR)":/work -w /work -v exiqo-helm-cache:/root/.cache/helm \
		-v exiqo-helm-config:/root/.config/helm $(HELM_IMAGE))
HELM_UNITTEST := $(if $(shell helm plugin list 2>/dev/null | grep -w unittest),helm unittest,\
	docker run --rm -v "$(CURDIR)/deploy/helm":/apps -w /apps $(HELM_UNITTEST_IMAGE))
KUBECONFORM := $(if $(shell command -v kubeconform 2>/dev/null),kubeconform,\
	docker run --rm -i $(KUBECONFORM_IMAGE))
KUBECONFORM_FLAGS := -strict -summary -kubernetes-version $(KUBE_VERSION) -schema-location default \
	-schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
# The suites live beside the chart, not in it (they are not packaged); -f is relative to the chart.
HELM_UNITTEST_CHART := $(if $(findstring docker,$(HELM_UNITTEST)),exiqo,$(CHART))

chart-check:
	$(HELM) repo add neo4j https://helm.neo4j.com/neo4j --force-update >/dev/null
	$(HELM) dependency build $(CHART)
	$(HELM) lint --strict $(CHART) -f $(CHART_GMF)
	$(HELM) lint --strict $(CHART) -f $(CHART_GMF) -f $(CHART_CI)
	$(HELM_UNITTEST) --strict -f '../tests/*_test.yaml' $(HELM_UNITTEST_CHART)
	@for bad in auth.mode=dev deploy.credentialMode=local image.platform.tag=1.0 api.replicas=1 \
		auth.tenantId=gmfinancial.onmicrosoft.com keyVault.mode=kubernetesSecret ciEnv.PROVIDER=fake; do \
		if $(HELM) template exiqo $(CHART) -f $(CHART_GMF) --set $$bad >/dev/null 2>&1; then \
			echo "chart-check: the schema accepted $$bad"; exit 1; \
		fi; \
	done; echo "chart-check: the schema refuses dev auth, local credentials, tags, 1 api replica, a non-GUID tenant, a CI secret source, CI env overrides"
	@if $(HELM) template exiqo $(CHART) -f $(CHART_GMF) -f $(CHART_CI) --skip-schema-validation \
		--set ciEnv.AUTH_MODE=oidc >/dev/null 2>&1; then \
		echo "chart-check: the template accepted ciEnv.AUTH_MODE without the schema"; exit 1; \
	fi; echo "chart-check: the template refuses a ciEnv name outside its list, schema or not"
	$(HELM) template exiqo $(CHART) -n exiqo -f $(CHART_GMF) -f deploy/helm/tests/values/backup-enabled.yaml \
		| $(PY) -I deploy/helm/tests/subchart_contract.py
	$(HELM) template exiqo $(CHART) -n exiqo -f $(CHART_GMF) | $(KUBECONFORM) $(KUBECONFORM_FLAGS)
	$(HELM) template exiqo $(CHART) -n exiqo -f $(CHART_GMF) -f deploy/helm/tests/values/backup-enabled.yaml \
		| $(KUBECONFORM) $(KUBECONFORM_FLAGS)
	$(HELM) template exiqo $(CHART) -n exiqo -f $(CHART_GMF) -f $(CHART_CI) | $(KUBECONFORM) $(KUBECONFORM_FLAGS)
	$(HELM) template exiqo $(CHART) -n exiqo -f $(CHART_GMF) -f deploy/azure/tests/helm_values.expected.json \
		| $(KUBECONFORM) $(KUBECONFORM_FLAGS)

# ---------------------------------------------------- Azure prerequisites and release pipelines
# deploy/azure (optional Terraform for GMF's prerequisites): fmt, init without a backend, validate,
# terraform test (mocked providers: the outputs -> chart values mapping), and tflint with the
# azurerm ruleset (pinned in deploy/azure/.tflint.hcl) against gmf.example.tfvars. pipeline-lint:
# actionlint on the GitHub Actions template, shellcheck on release.sh (from the actionlint image),
# the release.sh key-pin test (a missing or wrong COSIGN_PUBLIC_KEY_SHA256 stops deploy first),
# action pins, every GitHub job in an environment, and a structure check of the Azure DevOps
# template (there is no offline schema check for it).
# Terraform matches the platform image's version (deploy/docker/platform.Dockerfile). Each tool
# runs from its pinned image when it is not on PATH; init and tflint --init need the Terraform
# registry and GitHub (or TF_CLI_CONFIG_FILE pointing at a provider mirror).
TERRAFORM_IMAGE ?= hashicorp/terraform:1.17.0
TFLINT_IMAGE ?= ghcr.io/terraform-linters/tflint:v0.54.0
ACTIONLINT_IMAGE ?= rhysd/actionlint:1.7.12
AZURE_TF := deploy/azure
TERRAFORM := $(if $(shell command -v terraform 2>/dev/null),terraform,\
	docker run --rm -v "$(CURDIR)":/work -w /work -e TF_IN_AUTOMATION=1 -e CHECKPOINT_DISABLE=1 \
		-e TF_PLUGIN_CACHE_DIR=/plugin-cache -v exiqo-terraform-plugins:/plugin-cache $(TERRAFORM_IMAGE))
TFLINT := $(if $(shell command -v tflint 2>/dev/null),tflint,\
	docker run --rm -v "$(CURDIR)":/work -w /work -e GITHUB_TOKEN \
		-v exiqo-tflint-plugins:/root/.tflint.d $(TFLINT_IMAGE))
ACTIONLINT := $(if $(shell command -v actionlint 2>/dev/null),actionlint,\
	docker run --rm -v "$(CURDIR)":/work -w /work $(ACTIONLINT_IMAGE))
SHELLCHECK := $(if $(shell command -v shellcheck 2>/dev/null),shellcheck,\
	docker run --rm -v "$(CURDIR)":/work -w /work --entrypoint shellcheck $(ACTIONLINT_IMAGE))

# The Azure DevOps template parses, has the four stages in order, and pins every task exactly.
define ADO_CHECK
import re, sys, yaml
text = open(sys.argv[1]).read()
stages = [s["stage"] for s in yaml.safe_load(text)["stages"]]
assert stages == ["Build", "Scan", "Sign", "Deploy"], stages
tasks = re.findall(r"task: (\S+)", text)
assert tasks and all(re.search(r"@\d+\.\d+\.\d+$$", t) for t in tasks), tasks
print("pipeline-lint: azure-pipelines.yml ok,", len(tasks), "tasks pinned")
endef
export ADO_CHECK

# Every GitHub job runs in an environment: Entra federated credentials need exact subjects
# (repo:<org>/<repo>:environment:<name>), and each environment carries its own identity.
define GH_CHECK
import sys, yaml
jobs = yaml.safe_load(open(sys.argv[1]))["jobs"]
missing = [name for name, job in jobs.items() if not job.get("environment")]
assert not missing, f"jobs without an environment: {missing}"
print("pipeline-lint: github-release.yml ok, every job runs in an environment")
endef
export GH_CHECK

azure-validate: pipeline-lint
	$(TERRAFORM) -chdir=$(AZURE_TF) fmt -check -recursive -diff
	$(TERRAFORM) -chdir=$(AZURE_TF) init -backend=false -input=false -lockfile=readonly
	$(TERRAFORM) -chdir=$(AZURE_TF) validate
	$(TERRAFORM) -chdir=$(AZURE_TF) test
	$(TFLINT) --chdir=$(AZURE_TF) --init
	$(TFLINT) --chdir=$(AZURE_TF) --var-file=gmf.example.tfvars --format compact

pipeline-lint:
	$(ACTIONLINT) deploy/pipelines/github-release.yml
	$(SHELLCHECK) deploy/pipelines/release.sh deploy/pipelines/tests/test_release_key_pin.sh
	deploy/pipelines/tests/test_release_key_pin.sh
	@if grep -nE '^\s*-?\s*uses:' deploy/pipelines/github-release.yml | grep -vE '@[0-9a-f]{40}( |$$)'; then \
		echo "pipeline-lint: actions above are not pinned by commit"; exit 1; fi
	$(PY) -I -c "$$GH_CHECK" deploy/pipelines/github-release.yml
	$(PY) -I -c "$$ADO_CHECK" deploy/pipelines/azure-pipelines.yml

# ---------------------------------------------------------------- in-cluster end to end (kind)
# deploy/kind/setup.sh: kind, kubectl and helm pinned and SHA256-verified into .tools/bin (those on
# PATH are used instead), then a kind cluster with ingress-nginx, Postgres and a local registry,
# both images built and pushed by digest, `helm upgrade --install --atomic` with the CI overlay
# (2 api, 2 worker, PROVIDER=fake, EMBEDDINGS_BACKEND=hash, BUILD_TOOLCHAIN=false), the demo check
# through the ingress and a worker killed mid-build. Tears the cluster down at the end unless
# KEEP_CLUSTER=1; on failure prints pods, events and logs first. Needs Docker and the network
# (images, the Neo4j chart repository). `deploy/kind/setup.sh down` removes a kept cluster.
kind-tools:
	deploy/kind/setup.sh tools

kind-e2e: kind-tools
	PY=$(PY) IMAGE_TAG=$(IMAGE_TAG) deploy/kind/setup.sh e2e
