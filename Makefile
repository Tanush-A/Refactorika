.PHONY: help setup fetch eval eval-no-fetch benchmark benchmark-agent test clean-eval

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

benchmark:  ## Validate all 50 harness benchmark calibration controls
	@test -x eval/.venv/bin/python || bash eval/run_eval.sh --setup
	PATH="$(CURDIR)/eval/.venv/bin:$$PATH" eval/.venv/bin/python -m eval.harness_bench --calibrate-only

benchmark-agent:  ## Run 10 tasks x 3 trials against an OpenAI-compatible endpoint
	@test -x eval/.venv/bin/python || bash eval/run_eval.sh --setup
	PATH="$(CURDIR)/eval/.venv/bin:$$PATH" eval/.venv/bin/python -m eval.harness_bench \
		--provider "$${PROVIDER:-anthropic}" \
		--model "$${MODEL:-claude-sonnet-4-5-20250929}" \
		--base-url "$${BASE_URL:-http://localhost:11434/v1}" --trials "$${TRIALS:-3}" \
		--input-cost-per-mtok "$${INPUT_COST_PER_MTOK:-0}" \
		--output-cost-per-mtok "$${OUTPUT_COST_PER_MTOK:-0}"

test:  ## Run harness and benchmark unit tests
	@test -x eval/.venv/bin/python || bash eval/run_eval.sh --setup
	PATH="$(CURDIR)/eval/.venv/bin:$$PATH" eval/.venv/bin/python -m pytest -q tests

clean-eval:  ## Remove the eval venv (keeps fetched benchmark data)
	rm -rf eval/.venv
