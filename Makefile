PY ?= .venv/bin/python
PYTEST ?= .venv/bin/pytest

.PHONY: setup db api web test e2e demo-check toolchain samples samples-zip

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
