.PHONY: help setup fetch eval eval-no-fetch clean-eval

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

clean-eval:  ## Remove the eval venv (keeps fetched benchmark data)
	rm -rf eval/.venv
