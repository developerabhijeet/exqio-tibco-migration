PY ?= .venv/bin/python
PYTEST ?= .venv/bin/pytest

.PHONY: setup db api web test e2e demo-check toolchain

setup:
	python3 -m venv .venv
	.venv/bin/pip install -e ".[dev]"
	@if [ -f web/package.json ]; then cd web && npm install; fi

db:
	docker compose up -d postgres

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
