.PHONY: help setup dev dev-litellm dev-all backend frontend litellm test lint clean

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
	@echo "  dev       - run backend + frontend together (Ctrl+C to stop all)"
	@echo "  dev-litellm - alias for dev-all"
	@echo "  dev-all   - run litellm + backend + frontend together"
	@echo "  backend   - run only FastAPI backend (8000)"
	@echo "  frontend  - run only Vite dev server (5173)"
	@echo "  litellm   - run only LiteLLM proxy (4000)"
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
	@echo "==> pre-download LiteLLM + Python 3.12 (so first 'make dev-all' is fast)"
	@uv python install 3.12 >/dev/null 2>&1 || true
	@cd backend && UV_HTTP_TIMEOUT=600 uv run --python 3.12 --with 'litellm[proxy]' python -c "import litellm" >/dev/null 2>&1 \
	  && echo "    LiteLLM cached" || echo "    warn: LiteLLM pre-cache failed; first 'make dev-all' will install it"
	@echo "==> verify @playwright/mcp can be fetched"
	@npx -y -p @playwright/mcp@latest -- echo ok >/dev/null 2>&1 || echo "warn: first @playwright/mcp fetch may take a moment on first dev run"
	@echo "==> setup complete"

dev:
	@trap 'kill 0' INT TERM EXIT; \
	  (cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload) & \
	  (cd frontend && pnpm dev --host 127.0.0.1 --port 5173) & \
	  wait

dev-litellm: dev-all

dev-all:
	@trap 'kill 0' INT TERM EXIT; \
	  ($(MAKE) --no-print-directory litellm) & \
	  (cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload) & \
	  (cd frontend && pnpm dev --host 127.0.0.1 --port 5173) & \
	  wait

backend:
	cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

frontend:
	cd frontend && pnpm dev --host 127.0.0.1 --port 5173

# LiteLLM proxy: claude CLI -> :4000 -> Flywheel /v1/chat/completions
# Uses Python 3.12 to avoid uvloop incompatibility with Python 3.14.
# Reads FLYWHEEL_TOKEN from .env so secrets stay in one place.
# Strips $http_proxy/$socks_proxy that some shells set globally and would
# break LiteLLM's outbound HTTP client (httpx + uvloop init).
litellm:
	@set -e; \
	  if [ -f .env ]; then set -a; . ./.env; set +a; fi; \
	  if [ -z "$$FLYWHEEL_TOKEN" ]; then \
	    echo "[litellm] ERROR: FLYWHEEL_TOKEN not set in .env"; exit 1; \
	  fi; \
	  unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy SOCKS_PROXY socks_proxy DATABASE_URL; \
	  export FLYWHEEL_TOKEN; \
	  export NO_PROXY="*"; \
	  export UV_HTTP_TIMEOUT=600; \
	  echo "[litellm] starting proxy on http://127.0.0.1:4000"; \
	  echo "[litellm] (first run downloads ~80 packages over slow network — be patient)"; \
	  cd backend && uv run --python 3.12 --with 'litellm[proxy]' litellm \
	    --config ../scripts/litellm/config.yaml \
	    --port 4000 --host 127.0.0.1

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
