.PHONY: help setup fetch eval eval-no-fetch benchmark benchmark-agent \
	benchmark-full-calibrate benchmark-full-agent test clean-eval

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup:  ## Create eval venv and install dependencies
	bash eval/run_eval.sh --setup

fetch:  ## Fetch benchmark data into eval/external/ (gitignored)
	bash eval/fetch_benchmarks.sh

eval:  ## Full evaluation: setup -> fetch benchmarks -> run
	bash eval/run_eval.sh

eval-no-fetch:  ## Run evaluation using already-fetched benchmark data
	bash eval/run_eval.sh --no-fetch

benchmark:  ## Calibrate the shared-patch verification ablation
	@test -x eval/.venv/bin/python || bash eval/run_eval.sh --setup
	PATH="$(CURDIR)/eval/.venv/bin:$$PATH" eval/.venv/bin/python -m eval.harness_bench --calibrate-only

benchmark-agent:  ## Run the shared-patch verification ablation
	@test -x eval/.venv/bin/python || bash eval/run_eval.sh --setup
	PATH="$(CURDIR)/eval/.venv/bin:$$PATH" eval/.venv/bin/python -m eval.harness_bench \
		--provider "$${PROVIDER:-anthropic}" \
		--model "$${MODEL:-claude-sonnet-4-5-20250929}" \
		--base-url "$${BASE_URL:-http://localhost:11434/v1}" --trials "$${TRIALS:-3}" \
		--input-cost-per-mtok "$${INPUT_COST_PER_MTOK:-0}" \
		--output-cost-per-mtok "$${OUTPUT_COST_PER_MTOK:-0}"

benchmark-full-calibrate:  ## Validate all full-system case baselines
	@test -x eval/.venv/bin/python || bash eval/run_eval.sh --setup
	PATH="$(CURDIR)/eval/.venv/bin:$$PATH" eval/.venv/bin/python \
		-m eval.full_system_bench --calibrate-only

benchmark-full-agent:  ## Run independent harness OFF-vs-ON full-system agents
	@test -x eval/.venv/bin/python || bash eval/run_eval.sh --setup
	PATH="$(CURDIR)/eval/.venv/bin:$$PATH" eval/.venv/bin/python \
		-m eval.full_system_bench \
		--provider "$${PROVIDER:-anthropic}" \
		--model "$${MODEL:-claude-sonnet-4-5-20250929}" \
		--base-url "$${BASE_URL:-http://localhost:11434/v1}" \
		--trials "$${TRIALS:-3}" --max-retries "$${MAX_RETRIES:-2}" \
		--request-timeout "$${REQUEST_TIMEOUT:-180}" \
		--agent-timeout "$${AGENT_TIMEOUT:-900}" \
		--shell-timeout "$${SHELL_TIMEOUT:-30}" \
		--gate-timeout "$${GATE_TIMEOUT:-180}" \
		--parallel-fallback-delay "$${PARALLEL_FALLBACK_DELAY:-2}" \
		--agentic-model "$${AGENTIC_MODEL:-$${MODEL:-claude-sonnet-4-5-20250929}}" \
		--agentic-max-iter "$${AGENTIC_MAX_ITER:-30}" \
		--agentic-mcp-model "$${AGENTIC_MCP_MODEL:-$${MODEL:-claude-sonnet-4-5-20250929}}" \
		--agentic-mcp-max-iter "$${AGENTIC_MCP_MAX_ITER:-30}" \
		--input-cost-per-mtok "$${INPUT_COST_PER_MTOK:-0}" \
		--output-cost-per-mtok "$${OUTPUT_COST_PER_MTOK:-0}" \
		--cache-read-cost-per-mtok "$${CACHE_READ_COST_PER_MTOK:-0}" \
		--cache-write-cost-per-mtok "$${CACHE_WRITE_COST_PER_MTOK:-0}" \
		$${AGENTIC:+--agentic} \
		$${AGENTIC_MCP:+--agentic-mcp} \
		$${PARALLEL_ARMS:+--parallel-arms} \
		$${BASELINE:+--baseline "$${BASELINE}"}

test:  ## Run harness and benchmark unit tests
	@test -x eval/.venv/bin/python || bash eval/run_eval.sh --setup
	PATH="$(CURDIR)/eval/.venv/bin:$$PATH" eval/.venv/bin/python -m pytest -v tests

clean-eval:  ## Remove the eval venv (keeps fetched benchmark data)
	rm -rf eval/.venv
