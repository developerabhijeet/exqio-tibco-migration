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

.PHONY: setup db api web test e2e demo-check toolchain samples samples-zip nuget-cache

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

test:
	$(PYTEST)

# Playwright starts its own API (PROVIDER=fake, EMBEDDINGS_BACKEND=hash) and web on two free ports,
# so a server someone left on 9000 or 3000 is never reused. Override with E2E_API_PORT/E2E_WEB_PORT;
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
