PY ?= .venv/bin/python
PYTEST ?= .venv/bin/pytest

# Ports for `make api`, `make web` and `make demo-check`. The web calls the API from the browser.
# The API's CORS_ORIGINS defaults to http://localhost:3000; with another WEB_PORT the API is started
# with that origin, unless already set. The API passes the same origins to the generated service it
# starts in Verify (as Cors__Origins__<i>), so the service's Development CORS follows CORS_ORIGINS.
API_PORT ?= 8000
WEB_PORT ?= 3000
API_URL ?= http://localhost:$(API_PORT)
WEB_ORIGIN := http://localhost:$(WEB_PORT)
WEB_CORS_ENV := $(if $(filter-out 3000,$(WEB_PORT)),CORS_ORIGINS=$${CORS_ORIGINS:-$(WEB_ORIGIN)})

.PHONY: setup db api web test e2e demo-check toolchain samples samples-zip nuget-cache

setup:
	python3 -m venv .venv
	.venv/bin/pip install -e ".[dev]"
	@if [ -f web/package.json ]; then cd web && npm install; fi

db:
	@if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then \
		docker compose up -d --wait postgres; \
	else echo "docker not available: using the Postgres at DATABASE_URL"; fi
	.venv/bin/alembic -c api/alembic.ini upgrade head

api:
	env $(WEB_CORS_ENV) .venv/bin/uvicorn api.app.main:app --reload --port $(API_PORT)

web:
	cd web && ([ -d node_modules ] || npm install) && \
		NEXT_PUBLIC_API_URL=$${NEXT_PUBLIC_API_URL:-$(API_URL)} npm run dev -- -p $(WEB_PORT)

test:
	$(PYTEST)

# Playwright starts its own API (PROVIDER=fake, EMBEDDINGS_BACKEND=hash) and web on two free ports,
# so a server someone left on 8000 or 3000 is never reused. Override with E2E_API_PORT/E2E_WEB_PORT;
# E2E_REUSE_SERVERS=1 reuses servers you started yourself on those ports.
TWO_FREE_PORTS := python3 -c 'import socket; a, b = socket.socket(), socket.socket(); \
	a.bind(("127.0.0.1", 0)); b.bind(("127.0.0.1", 0)); print(a.getsockname()[1], b.getsockname()[1])'

e2e:
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

demo-check: samples samples-zip
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
