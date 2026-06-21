.PHONY: help setup fetch eval eval-no-fetch benchmark benchmark-agent benchmark-refactorbench clean-eval

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

benchmark:  ## Run the Phase-0 benchmark harness + terminal report (no fetch needed)
	eval/.venv/bin/python eval/run_eval.py --benchmark

benchmark-agent:  ## Phase-0 + Phase-1 real-agent arms vs a local model (needs `ollama serve`)
	eval/.venv/bin/python eval/run_eval.py --agent

benchmark-refactorbench:  ## Phase-2 RefactorBench slice via Claude (needs ANTHROPIC_API_KEY in .env)
	eval/.venv/bin/python eval/run_eval.py --refactorbench

clean-eval:  ## Remove the eval venv (keeps fetched benchmark data)
	rm -rf eval/.venv
