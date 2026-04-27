.PHONY: help setup dev backend frontend test lint clean

help:
	@echo "Michelle - AI-native Web Test Platform"
	@echo ""
	@echo "Targets:"
	@echo "  setup     - install backend + frontend deps, verify CLI tools"
	@echo "  dev       - run backend + frontend together (Ctrl+C to stop both)"
	@echo "  backend   - run only FastAPI backend (8000)"
	@echo "  frontend  - run only Vite dev server (5173)"
	@echo "  test      - run all tests"
	@echo "  lint      - lint backend + frontend"
	@echo "  clean     - remove caches"

setup:
	@echo "==> backend (uv sync)"
	cd backend && uv sync
	@echo "==> frontend (pnpm install)"
	cd frontend && pnpm install
	@echo "==> verify claude CLI"
	@claude --version || (echo "claude CLI not found"; exit 1)
	@echo "==> verify @playwright/mcp can be fetched"
	@npx -y -p @playwright/mcp@latest -- echo ok >/dev/null 2>&1 || echo "warn: first @playwright/mcp fetch may take a moment on first dev run"
	@echo "==> setup complete"

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
	cd frontend && pnpm test --run || true

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .
	cd frontend && pnpm lint || true

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name node_modules -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name dist -exec rm -rf {} + 2>/dev/null || true
