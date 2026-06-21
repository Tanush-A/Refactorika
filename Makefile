.PHONY: help install fetch eval-smoke eval-inscope eval-ablation eval-all \
	benchmark benchmark-agent benchmark-full-calibrate benchmark-full-agent test clean-eval

PY ?= .venv/bin/python

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install:  ## Create the project venv and install (engine + dev tools)
	python3 -m venv .venv && $(PY) -m pip install -e ".[dev]"

fetch:  ## Fetch RefactorBench into eval/external/ (gitignored, ~53MB)
	bash eval/fetch_benchmarks.sh

eval-smoke:  ## RefactorBench: 5 in-scope tasks (quick harness check)
	$(PY) eval/run_eval.py --smoke

eval-inscope:  ## RefactorBench: all in-scope tasks
	$(PY) eval/run_eval.py --in-scope

eval-ablation:  ## RefactorBench: in-scope, decision-memory ON vs OFF
	$(PY) eval/run_eval.py --in-scope --ablation

eval-all:  ## RefactorBench: every task (out-of-scope declined honestly)
	$(PY) eval/run_eval.py --all

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
		--agentic-max-iter "$${AGENTIC_MAX_ITER:-20}" \
		--agentic-mcp-model "$${AGENTIC_MCP_MODEL:-$${MODEL:-claude-sonnet-4-5-20250929}}" \
		--agentic-mcp-max-iter "$${AGENTIC_MCP_MAX_ITER:-20}" \
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

clean-eval:  ## Remove fetched benchmark data + eval venv (keeps eval/results/)
	rm -rf eval/external eval/.venv
