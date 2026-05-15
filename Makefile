.PHONY: dev test test-unit eval perf audit audit-check smoke clean \
        demo-deploy demo-teardown demo-reset demo-crash demo-bad-deploy demo-oom demo-scale \
        gui-install gui-dev gui-build gui-serve

# ---------------------------------------------------------------------------
# Routed triage workflow targets  (feature 002 / quickstart.md)
# ---------------------------------------------------------------------------

## dev — bring up the local stack (agent + slack-mock)
dev:
	docker compose -f deploy/docker-compose.yml up --build

## test — full test suite: unit + contract + integration + eval + hallucination
test:
	uv run pytest tests/ -v --tb=short

## test-unit — fast feedback; no live services or LLM required
test-unit:
	uv run pytest tests/unit/ -m unit -v --tb=short \
		--cov=src --cov-report=term-missing

## eval — LLM quality golden-sets + hallucination gate
eval:
	uv run pytest tests/eval/ -v --tb=short

## perf — latency benchmark; fails if p50 > 30s or p95 > 60s (SC-003)
perf:
	uv run pytest tests/perf/ -m perf -v --tb=short

## audit — query the audit trail for a given correlation ID
##   Usage: make audit CORRELATION=01J...
##   Reads from the SQLite database at SQLITE_PATH (default: ./data/agent.sqlite3).
audit:
	@test -n "$(CORRELATION)" || { echo "Usage: make audit CORRELATION=<uuid>"; exit 1; }
	@sqlite3 "$${SQLITE_PATH:-./data/agent.sqlite3}" \
		"SELECT sequence_no, stage, outcome, at FROM audit_record \
		 WHERE correlation_id = '$(CORRELATION)' ORDER BY sequence_no;" \
		".mode column" ".headers on"

## audit-check — audit-completeness invariants (SC-006, contracts/audit_record.md §Invariants)
audit-check:
	uv run pytest tests/eval/audit_completeness.py -v --tb=short

## smoke — fire a synthetic webhook against the running stack
##   Usage: make smoke [INCIDENT=network|database|application]
smoke:
	@test -f tests/fixtures/fire_webhook.py || { echo "T055 not yet implemented; run after T055."; exit 1; }
	uv run python tests/fixtures/fire_webhook.py $(if $(INCIDENT),--incident $(INCIDENT),)

## clean — tear down the compose stack
clean:
	docker compose -f deploy/docker-compose.yml down -v --remove-orphans || true

# ---------------------------------------------------------------------------
# Demo targets — podinfo failure scenario runner (feature 003)
# ---------------------------------------------------------------------------

demo-deploy:
	@bash scripts/demo/deploy.sh

demo-teardown:
	@bash scripts/demo/teardown.sh

demo-reset: demo-teardown demo-deploy

demo-crash: demo-deploy
	@bash scripts/demo/trigger-crash.sh

demo-bad-deploy: demo-deploy
	@bash scripts/demo/trigger-bad-deploy.sh

demo-oom: demo-deploy
	@bash scripts/demo/trigger-oom.sh

demo-scale: demo-deploy
	@bash scripts/demo/trigger-scale.sh

# ---------------------------------------------------------------------------
# GUI targets — React SPA + FastAPI (feature 008)
# ---------------------------------------------------------------------------

## gui-install — install frontend npm dependencies (first time only)
gui-install:
	cd gui && npm install

## gui-dev — start Vite dev server (port 5173) + agent API (port 8000) concurrently
gui-dev: gui-install
	GUI_DEV_MODE=true uvicorn src.agent.api:create_app --factory --reload --port 8000 &
	cd gui && npm run dev

## gui-build — compile the React SPA into gui/dist/
gui-build: gui-install
	cd gui && npm run build

## gui-serve — serve the built SPA + API from a single origin (port 8000)
gui-serve: gui-build
	GUI_STATIC_DIR=gui/dist uvicorn src.agent.api:create_app --factory --port 8000
