DEV_LOG_FILE := scratchwork/dev_logs.txt
PLAYGROUND_LOG_FILE := scratchwork/playground_logs.txt

install:
	@command -v uv >/dev/null 2>&1 || { echo "uv is not installed. Installing uv..."; curl -LsSf https://astral.sh/uv/0.6.12/install.sh | sh; source $$HOME/.local/bin/env; }
	@bash -c 'source $$HOME/.local/bin/env 2>/dev/null || true; [ -d .venv ] || uv venv .venv'
	@bash -c 'source $$HOME/.local/bin/env 2>/dev/null || true; uv sync --python .venv/bin/python'
	@npm --prefix frontend install

doctor:
	@echo "Checking local prerequisites..."
	@command -v uv >/dev/null 2>&1 || { echo "Missing: uv"; exit 1; }
	@command -v node >/dev/null 2>&1 || { echo "Missing: node"; exit 1; }
	@command -v npm >/dev/null 2>&1 || { echo "Missing: npm"; exit 1; }
	@echo "OK: uv, node, npm found"

clean-env:
	@echo "Removing local dependency environments..."
	@rm -rf .venv frontend/node_modules
	@echo "Done. Removed: .venv and frontend/node_modules"

dev:
	@mkdir -p scratchwork
	@echo "[$$(date '+%Y-%m-%d %H:%M:%S')] Starting dev stack" >> $(DEV_LOG_FILE)
	@make dev-backend & make dev-frontend
	@make show-dev-urls
	@make dev-health || true

show-dev-urls:
	@echo ""
	@echo "Local Endpoints (make dev)"
	@echo "- Frontend UI:  http://localhost:5173/app/"
	@echo "- Backend API:  http://127.0.0.1:8000/ (expected 404 on root)"
	@echo "- API Docs:     http://127.0.0.1:8000/docs"
	@echo "- OpenAPI:      http://127.0.0.1:8000/openapi.json"
	@echo "- Dev Logs:     scratchwork/dev_logs.txt"
	@echo ""

show-playground-urls:
	@echo ""
	@echo "Local Endpoints (make playground)"
	@echo "- ADK Web UI:   http://127.0.0.1:8501"
	@echo "- Playground Logs: scratchwork/playground_logs.txt"
	@echo ""

dev-health:
	@echo "Checking backend health at /docs ..."
	@bash -c 'for i in $$(seq 1 20); do code=$$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/docs || true); if [ "$$code" = "200" ]; then echo "OK: backend ready"; exit 0; fi; sleep 1; done; echo "WARN: backend not ready yet"; exit 1'

build-frontend:
	@npm --prefix frontend ci
	@npm --prefix frontend run build

serve:
	@.venv/bin/uvicorn server:app --host 0.0.0.0 --port $${PORT:-8080}

test-web:
	@npm --prefix frontend run lint
	@npm --prefix frontend run build
	@.venv/bin/python -m pytest tests/unit -v

dev-logs-tail:
	@mkdir -p scratchwork
	@touch $(DEV_LOG_FILE)
	@tail -f $(DEV_LOG_FILE)

playground-logs-tail:
	@mkdir -p scratchwork
	@touch $(PLAYGROUND_LOG_FILE)
	@tail -f $(PLAYGROUND_LOG_FILE)

dev-backend:
	@mkdir -p scratchwork
	@echo "[$$(date '+%Y-%m-%d %H:%M:%S')] Starting dev-backend" >> $(DEV_LOG_FILE)
	@bash -c 'source $$HOME/.local/bin/env 2>/dev/null || true; uv run --active adk api_server app --allow_origins="*" >> $(DEV_LOG_FILE) 2>&1'

dev-frontend:
	@mkdir -p scratchwork
	@echo "[$$(date '+%Y-%m-%d %H:%M:%S')] Starting dev-frontend" >> $(DEV_LOG_FILE)
	@npm --prefix frontend run dev >> $(DEV_LOG_FILE) 2>&1

playground:
	@lsof -ti:8501 | xargs kill -9 2>/dev/null || true
	@mkdir -p scratchwork
	@sleep 1
	@make show-playground-urls
	@echo "[$$(date '+%Y-%m-%d %H:%M:%S')] Starting playground" >> $(PLAYGROUND_LOG_FILE)
	@bash -c 'source $$HOME/.local/bin/env 2>/dev/null || true; source .env && command -v uv >/dev/null 2>&1 || { echo "uv not found in PATH. Run make install first." >> $(PLAYGROUND_LOG_FILE); exit 127; }; uv run adk web --port 8501 >> $(PLAYGROUND_LOG_FILE) 2>&1'

# Local Testing Commands
test-hitl:
	@echo "Running HITL workflow tests..."
	@bash -c 'set -a; source .env; set +a; .venv/bin/python -m pytest tests/test_hitl_workflow.py -v'

test-tc3:
	@echo "Running TC3 (Error Recovery) - THE CRITICAL TEST"
	@echo "=========================================="
	@bash -c 'set -a; source .env; set +a; .venv/bin/python -m pytest tests/test_hitl_workflow.py::TestHITLWorkflow::test_tc3_error_recovery_no_bypass -v'

test-tc3-quick:
	@echo "Quick TC3 test (direct execution)"
	@bash -c 'set -a; source .env; set +a; .venv/bin/python tests/test_hitl_workflow.py'

test-all:
	@echo "Running ALL tests..."
	@bash -c 'set -a; source .env; set +a; .venv/bin/python -m pytest tests/ -v'

test-watch:
	@echo "Running tests in watch mode..."
	@bash -c 'set -a; source .env; set +a; .venv/bin/python -m pytest_watch tests/test_hitl_workflow.py -v'

test-coverage:
	@echo "Running tests with coverage report..."
	@bash -c 'set -a; source .env; set +a; .venv/bin/python -m pytest tests/test_hitl_workflow.py --cov=app/bigquery_agent --cov-report=html --cov-report=term'

verify-hitl:
	@echo "==================================================="
	@echo "HITL VERIFICATION CHECKLIST"
	@echo "==================================================="
	@echo "1. Starting playground server..."
	@make playground > /dev/null 2>&1 &
	@sleep 5
	@echo "2. Running TC3 test..."
	@make test-tc3
	@echo "3. Killing playground server..."
	@lsof -ti:8501 | xargs kill -9 2>/dev/null || true
	@echo "==================================================="
	@echo "✅ HITL VERIFICATION COMPLETE"
	@echo "==================================================="

portfolio-check:
	@echo "==============================================="
	@echo "Portfolio Readiness Verification"
	@echo "==============================================="
	@make doctor
	@make test-tc3
	@make test-hitl
	@echo "==============================================="
	@echo "✅ Portfolio check complete"
	@echo "==============================================="

lint:
	uv sync --active --dev --extra lint
	uv run codespell
	uv run ruff check . --diff
	uv run ruff format . --check --diff
	uv run mypy .

test:
	uv sync --dev --active
	@bash -c 'set -a; source .env; set +a; .venv/bin/python -m pytest tests/unit'
