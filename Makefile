.PHONY: demo-deploy demo-teardown demo-reset demo-crash demo-bad-deploy demo-oom demo-scale

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
