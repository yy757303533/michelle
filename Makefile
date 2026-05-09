.PHONY: help setup postgres dev backend frontend test lint e2e-smoke clean

# uv default: 10-min HTTP timeout for slow networks.
# If you need a PyPI mirror, export UV_INDEX_URL before make; we don't set one
# here because uv records the registry URL in uv.lock.
export UV_HTTP_TIMEOUT ?= 600
export UV_CACHE_DIR ?= $(CURDIR)/.uv/cache

help:
	@echo "Michelle - AI-native Web Test Platform"
	@echo ""
	@echo "Targets:"
	@echo "  setup     - install backend + frontend deps, verify CLI tools"
	@echo "  postgres  - start local PostgreSQL (:5432) via docker compose"
	@echo "  dev       - run backend + frontend together (Ctrl+C to stop all)"
	@echo "  backend   - run only FastAPI backend (8000)"
	@echo "  frontend  - run only Vite dev server (5173)"
	@echo "  test      - run all tests"
	@echo "  lint      - lint backend + frontend"
	@echo "  e2e-smoke - run real target E2E smoke (backend/frontend must be running)"
	@echo "  clean     - remove caches"

setup:
	@echo "==> backend (uv sync)"
	cd backend && uv sync
	@echo "==> frontend (pnpm install)"
	cd frontend && pnpm install
	@echo "==> verify claude CLI"
	@claude --version || (echo "claude CLI not found"; exit 1)
	@echo "==> verify codex CLI (optional)"
	@codex --version || echo "warn: codex CLI not found; enable later with CODEX_ENABLED=true"
	@echo "==> verify @playwright/mcp can be fetched"
	@npx -y -p @playwright/mcp@0.0.40 -- echo ok >/dev/null 2>&1 || echo "warn: first @playwright/mcp fetch may take a moment on first dev run"
	@echo "==> setup complete"

postgres:
	docker compose up -d postgres

dev:
	@trap 'kill 0' INT TERM EXIT; \
	  (cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload) & \
	  (cd frontend && pnpm dev --host 127.0.0.1 --port 5173) & \
	  wait

backend:
	cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

frontend:
	cd frontend && pnpm dev --host 127.0.0.1 --port 5173

test:
	cd backend && uv run pytest -x
	cd frontend && pnpm test --run

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .
	cd backend && uv run ruff check ../scripts/day13_e2e_smoke.py && uv run ruff format --check ../scripts/day13_e2e_smoke.py
	cd frontend && pnpm lint

e2e-smoke:
	cd backend && uv run python ../scripts/day13_e2e_smoke.py

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name node_modules -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name dist -exec rm -rf {} + 2>/dev/null || true
