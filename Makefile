PY ?= .venv/bin/python
PYTEST ?= .venv/bin/pytest

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
	.venv/bin/uvicorn api.app.main:app --reload --port 8000

web:
	cd web && npm run dev

test:
	$(PYTEST)

e2e:
	@if [ -d web ]; then cd web && npx playwright test; else echo "web/ not scaffolded yet"; fi

demo-check:
	$(PY) scripts/demo_check.py

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
