.PHONY: help install fetch eval-smoke eval-inscope eval-ablation eval-all clean-eval

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

clean-eval:  ## Remove fetched benchmark data (keeps eval/results/)
	rm -rf eval/external
