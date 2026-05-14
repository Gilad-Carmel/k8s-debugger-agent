.PHONY: dev test test-unit eval perf audit audit-check smoke clean \
        demo-deploy demo-teardown demo-reset demo-crash demo-bad-deploy demo-oom demo-scale

# ---------------------------------------------------------------------------
# Routed triage workflow targets  (feature 002 / quickstart.md)
# ---------------------------------------------------------------------------

## dev — bring up the full local stack (agent + mcp-server + slack-mock + kind)
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

## clean — tear down the compose stack and delete the kind cluster
clean:
	docker compose -f deploy/docker-compose.yml down -v --remove-orphans || true
	kind delete cluster --name k8s-debugger 2>/dev/null || true

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
