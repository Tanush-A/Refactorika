"""Calibrated OFF-vs-ON benchmark for the Refactorika verification harness."""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from eval.harness_tasks import (  # noqa: E402
    TASKS,
    TaskSpec,
    bad_patches,
    good_patch,
    heldout_test,
    materialize,
)
from refactorika.harness import mark_escalated, verify_edits  # noqa: E402

REQUIRED_GATES = ("lint", "typecheck", "tests")


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class Proposal:
    edits: dict[str, str]
    usage: Usage
    seconds: float
    error: str | None = None


class Proposer(Protocol):
    name: str

    def propose(self, task: TaskSpec, repo: Path, failure: str | None = None) -> Proposal: ...


class ReferenceProposer:
    """Deterministic smoke proposer; never include in model-performance claims."""

    name = "reference-control"

    def propose(self, task: TaskSpec, repo: Path, failure: str | None = None) -> Proposal:
        return Proposal(good_patch(task), Usage(), 0.0)


class OpenAICompatibleProposer:
    """Minimal proposer for Ollama, LM Studio, vLLM, and compatible endpoints."""

    def __init__(self, model: str, base_url: str, seed: int, timeout: int = 300) -> None:
        self.model = model
        self.name = model
        self.base_url = base_url.rstrip("/")
        self.seed = seed
        self.timeout = timeout

    def propose(self, task: TaskSpec, repo: Path, failure: str | None = None) -> Proposal:
        files = {}
        for path in sorted(repo.rglob("*.py")):
            relative = path.relative_to(repo).as_posix()
            if relative.startswith("tests/oracle/"):
                continue
            files[relative] = path.read_text()
        prompt = (
            f"Instruction: {task.instruction}\n\n"
            "Return ONLY a JSON object mapping every changed relative file path to its complete "
            "new contents. Do not use markdown. Preserve behavior.\n\nFiles:\n" + json.dumps(files)
        )
        if failure:
            prompt += (
                f"\n\nThe verifier rejected the prior patch: {failure}. Return a corrected patch."
            )
        body = json.dumps(
            {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "seed": self.seed,
                "stream": False,
            }
        ).encode()
        request = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read())
            raw = data["choices"][0]["message"]["content"].strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
            edits = json.loads(raw)
            if not isinstance(edits, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in edits.items()
            ):
                raise ValueError("response is not a string-to-string patch object")
            usage = data.get("usage") or {}
            return Proposal(
                edits,
                Usage(int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))),
                round(time.perf_counter() - started, 3),
            )
        except (
            urllib.error.URLError,
            TimeoutError,
            ValueError,
            KeyError,
            json.JSONDecodeError,
        ) as exc:
            return Proposal({}, Usage(), round(time.perf_counter() - started, 3), str(exc))


def _load_env(name: str) -> str | None:
    if value := os.environ.get(name):
        return value
    env_file = REPO_ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            if line.strip().startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    return None


class AnthropicProposer:
    """Direct Anthropic Messages API adapter with exact token accounting."""

    def __init__(self, model: str, seed: int, timeout: int = 300) -> None:
        self.model = model
        self.name = model
        self.seed = seed
        self.timeout = timeout
        self.api_key = _load_env("ANTHROPIC_API_KEY")

    def propose(self, task: TaskSpec, repo: Path, failure: str | None = None) -> Proposal:
        if not self.api_key:
            return Proposal({}, Usage(), 0.0, "ANTHROPIC_API_KEY is not configured")
        files = {}
        for path in sorted(repo.rglob("*.py")):
            relative = path.relative_to(repo).as_posix()
            if not relative.startswith("tests/oracle/"):
                files[relative] = path.read_text()
        prompt = (
            f"Instruction: {task.instruction}\n\n"
            "Return ONLY a JSON object mapping every changed relative file path to its complete "
            "new contents. Do not use markdown. Preserve behavior. The only valid editable paths "
            "are app/service.py and app/caller.py.\n\nFiles:\n" + json.dumps(files)
        )
        if failure:
            prompt += (
                f"\n\nThe verifier rejected the prior patch: {failure}. "
                "Return a corrected patch."
            )
        body = json.dumps(
            {
                "model": self.model,
                "max_tokens": 8192,
                "temperature": 0,
                "messages": [{"role": "user", "content": prompt}],
            }
        ).encode()
        request = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read())
            raw = "".join(
                block.get("text", "")
                for block in data.get("content", [])
                if block.get("type") == "text"
            ).strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
            edits = json.loads(raw)
            if not isinstance(edits, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in edits.items()
            ):
                raise ValueError("response is not a string-to-string patch object")
            usage = data.get("usage") or {}
            return Proposal(
                edits,
                Usage(
                    int(usage.get("input_tokens", 0)),
                    int(usage.get("output_tokens", 0)),
                ),
                round(time.perf_counter() - started, 3),
            )
        except (
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
            ValueError,
            KeyError,
            json.JSONDecodeError,
        ) as exc:
            detail = str(exc)
            if isinstance(exc, urllib.error.HTTPError):
                try:
                    payload = json.loads(exc.read())
                    detail = payload.get("error", {}).get("message", detail)
                except (json.JSONDecodeError, AttributeError):
                    pass
            return Proposal({}, Usage(), round(time.perf_counter() - started, 3), detail)


def _write_patch(repo: Path, edits: dict[str, str]) -> str | None:
    for relative, content in edits.items():
        path = (repo / relative).resolve()
        try:
            path.relative_to(repo.resolve())
        except ValueError:
            return f"path escapes repository: {relative}"
        if not path.is_file():
            return f"file does not exist: {relative}"
        path.write_text(content)
    return None


def oracle_grade(task: TaskSpec, repo: Path) -> tuple[bool, str]:
    """Inject held-out tests only for grading, then remove them."""

    oracle_dir = repo / "tests" / "oracle"
    oracle_dir.mkdir(parents=True, exist_ok=True)
    test_file = oracle_dir / "test_heldout.py"
    test_file.write_text(heldout_test(task))
    command = [sys.executable, "-m", "pytest", "-q", "tests/gate", "tests/oracle"]
    result = subprocess.run(command, cwd=repo, text=True, capture_output=True, check=False)
    shutil.rmtree(oracle_dir)
    tail = (result.stdout + "\n" + result.stderr).strip().splitlines()
    return result.returncode == 0, tail[-1] if tail else f"exit {result.returncode}"


def calibrate(tasks: tuple[TaskSpec, ...] = TASKS) -> dict:
    records: list[dict] = []
    for task in tasks:
        with tempfile.TemporaryDirectory(prefix=f"cal-good-{task.name}-") as tmp:
            repo = materialize(task, Path(tmp) / "repo")
            result = verify_edits(repo, good_patch(task), required_gates=REQUIRED_GATES)
            oracle, detail = oracle_grade(task, repo)
            passed = result.status == "committed" and oracle
            records.append(
                {
                    "task": task.name,
                    "control": "good",
                    "passed": passed,
                    "status": result.status,
                    "oracle": oracle,
                    "detail": detail,
                    "checks": asdict(result.checks),
                }
            )

        for label, patch in bad_patches(task).items():
            with tempfile.TemporaryDirectory(prefix=f"cal-bad-{task.name}-") as tmp:
                repo = materialize(task, Path(tmp) / "repo")
                result = verify_edits(repo, patch, required_gates=REQUIRED_GATES)
                if result.status != "committed":
                    # Grade the actual bad patch, not the rolled-back baseline.
                    error = _write_patch(repo, patch)
                    if error:
                        records.append(
                            {
                                "task": task.name,
                                "control": label,
                                "passed": False,
                                "status": result.status,
                                "oracle": None,
                                "detail": error,
                                "checks": asdict(result.checks),
                            }
                        )
                        continue
                oracle, detail = oracle_grade(task, repo)
                passed = not oracle
                records.append(
                    {
                        "task": task.name,
                        "control": label,
                        "passed": passed,
                        "status": result.status,
                        "oracle": oracle,
                        "detail": detail,
                        "caught_by_harness": result.status != "committed",
                        "checks": asdict(result.checks),
                    }
                )
    failed = [r for r in records if not r["passed"]]
    return {
        "valid": not failed,
        "passed": len(records) - len(failed),
        "total": len(records),
        "failed": failed,
        "records": records,
    }


def _cost(usage: Usage, input_per_mtok: float, output_per_mtok: float) -> float:
    return round(
        usage.input_tokens / 1_000_000 * input_per_mtok
        + usage.output_tokens / 1_000_000 * output_per_mtok,
        6,
    )


def _run_pair(
    task: TaskSpec,
    proposer: Proposer,
    max_retries: int,
    trial: int,
    input_cost: float,
    output_cost: float,
) -> list[dict]:
    with tempfile.TemporaryDirectory(prefix=f"pair-{task.name}-") as tmp:
        initial_repo = materialize(task, Path(tmp) / "initial")
        proposal = proposer.propose(task, initial_repo)

        off_repo = Path(tmp) / "off"
        on_repo = Path(tmp) / "on"
        shutil.copytree(initial_repo, off_repo)
        shutil.copytree(initial_repo, on_repo)

        off_error = proposal.error or _write_patch(off_repo, proposal.edits)
        off_oracle, off_detail = (False, off_error) if off_error else oracle_grade(task, off_repo)
        records = [
            {
                "task": task.name,
                "trial": trial,
                "arm": "off",
                "landed": not bool(off_error),
                "oracle_pass": off_oracle,
                "correct_landed": not bool(off_error) and off_oracle,
                "regression_shipped": not bool(off_error) and not off_oracle,
                "status": "error" if off_error else "shipped",
                "retries": 0,
                "tokens": proposal.usage.total,
                "input_tokens": proposal.usage.input_tokens,
                "output_tokens": proposal.usage.output_tokens,
                "cost_dollars": _cost(proposal.usage, input_cost, output_cost),
                "seconds": proposal.seconds,
                "detail": off_detail,
                "patch": proposal.edits,
            }
        ]

        usage = Usage(proposal.usage.input_tokens, proposal.usage.output_tokens)
        seconds = proposal.seconds
        current = proposal
        final = None
        for attempt in range(max_retries + 1):
            if current.error:
                failure = current.error
                final = None
            else:
                try:
                    final = verify_edits(
                        on_repo, current.edits, required_gates=REQUIRED_GATES, retries=attempt
                    )
                    if final.status == "committed":
                        break
                    failure = final.failure_reason
                except ValueError as exc:
                    final = None
                    failure = str(exc)
            if attempt < max_retries:
                current = proposer.propose(task, on_repo, failure)
                usage.input_tokens += current.usage.input_tokens
                usage.output_tokens += current.usage.output_tokens
                seconds += current.seconds

        committed = final is not None and final.status == "committed"
        if not committed and final is not None:
            mark_escalated(final)
        on_oracle, on_detail = oracle_grade(task, on_repo) if committed else (False, "not landed")
        records.append(
            {
                "task": task.name,
                "trial": trial,
                "arm": "on",
                "landed": committed,
                "oracle_pass": on_oracle if committed else None,
                "correct_landed": committed and on_oracle,
                "regression_shipped": committed and not on_oracle,
                "status": "committed" if committed else "skipped-needs-human",
                "retries": final.retries if final is not None else max_retries,
                "tokens": usage.total,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cost_dollars": _cost(usage, input_cost, output_cost),
                "seconds": round(seconds, 3),
                "detail": on_detail,
                "checks": asdict(final.checks) if final else None,
                "patch": current.edits,
            }
        )
        return records


def _bootstrap_delta(records: list[dict], seed: int = 7, samples: int = 5000) -> list[float]:
    pairs: dict[tuple[str, int], dict[str, bool]] = {}
    for record in records:
        pairs.setdefault((record["task"], record["trial"]), {})[record["arm"]] = record[
            "correct_landed"
        ]
    values = [int(p["on"]) - int(p["off"]) for p in pairs.values() if len(p) == 2]
    if not values:
        return [0.0, 0.0]
    rng = random.Random(seed)
    deltas = sorted(sum(rng.choice(values) for _ in values) / len(values) for _ in range(samples))
    return [round(deltas[int(samples * 0.025)], 3), round(deltas[int(samples * 0.975)], 3)]


def aggregate(records: list[dict]) -> dict:
    arms = {}
    for arm in ("off", "on"):
        rows = [r for r in records if r["arm"] == arm]
        arms[arm] = {
            "runs": len(rows),
            "correct_landed": sum(r["correct_landed"] for r in rows),
            "correct_landed_rate": round(sum(r["correct_landed"] for r in rows) / len(rows), 3),
            "regressions_shipped": sum(r["regression_shipped"] for r in rows),
            "escalations": sum(r["status"] == "skipped-needs-human" for r in rows),
            "tokens": sum(r["tokens"] for r in rows),
            "cost_dollars": round(sum(r.get("cost_dollars", 0.0) for r in rows), 6),
            "seconds": round(sum(r["seconds"] for r in rows), 3),
            "retries": sum(r["retries"] for r in rows),
        }
    pairs: dict[tuple[str, int], dict[str, dict]] = {}
    for record in records:
        pairs.setdefault((record["task"], record["trial"]), {})[record["arm"]] = record
    initially_bad = [
        p for p in pairs.values() if not p["off"]["oracle_pass"] and p["off"]["landed"]
    ]
    initially_good = [p for p in pairs.values() if p["off"]["oracle_pass"] and p["off"]["landed"]]
    caught = sum(not p["on"]["regression_shipped"] for p in initially_bad)
    false_rejected = sum(not p["on"]["correct_landed"] for p in initially_good)
    return {
        "arms": arms,
        "safety": {
            "initial_bad_proposals": len(initially_bad),
            "caught_or_safely_escalated": caught,
            "catch_rate": round(caught / len(initially_bad), 3) if initially_bad else None,
            "initial_good_proposals": len(initially_good),
            "false_rejections": false_rejected,
            "false_rejection_rate": round(false_rejected / len(initially_good), 3)
            if initially_good
            else None,
        },
        "paired_correct_landed_delta_ci95": _bootstrap_delta(records),
    }


def run(
    proposer: Proposer,
    tasks: tuple[TaskSpec, ...],
    trials: int,
    max_retries: int,
    skip_calibration: bool,
    input_cost: float,
    output_cost: float,
) -> dict:
    calibration = {"valid": True, "skipped": True} if skip_calibration else calibrate(tasks)
    output = {
        "meta": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "proposer": proposer.name,
            "tasks": len(tasks),
            "task_names": [task.name for task in tasks],
            "trials": trials,
            "max_retries": max_retries,
            "oracle": "held-out pytest, unavailable to proposer and harness",
        },
        "calibration": calibration,
        "records": [],
    }
    if not calibration["valid"]:
        output["status"] = "void"
        return output
    for trial in range(trials):
        for task in tasks:
            output["records"].extend(
                _run_pair(
                    task,
                    proposer,
                    max_retries,
                    trial,
                    input_cost,
                    output_cost,
                )
            )
    output["aggregate"] = aggregate(output["records"])
    output["status"] = "valid"
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibrate-only", action="store_true")
    parser.add_argument(
        "--provider", choices=("reference", "openai", "anthropic"), default="reference"
    )
    parser.add_argument("--model", default="qwen2.5-coder:7b")
    parser.add_argument("--base-url", default="http://localhost:11434/v1")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--input-cost-per-mtok", type=float, default=0.0)
    parser.add_argument("--output-cost-per-mtok", type=float, default=0.0)
    parser.add_argument(
        "--task",
        action="append",
        choices=[task.name for task in TASKS],
        help="Run one named task; repeat for a pilot subset. Defaults to all tasks.",
    )
    parser.add_argument("--skip-calibration", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    selected = tuple(task for task in TASKS if not args.task or task.name in args.task)
    if args.calibrate_only:
        result = {"status": "valid", "calibration": calibrate(selected)}
        if not result["calibration"]["valid"]:
            result["status"] = "void"
    else:
        if args.provider == "reference":
            proposer: Proposer = ReferenceProposer()
        elif args.provider == "anthropic":
            proposer = AnthropicProposer(args.model, args.seed)
        else:
            proposer = OpenAICompatibleProposer(args.model, args.base_url, args.seed)
        result = run(
            proposer,
            selected,
            args.trials,
            args.max_retries,
            args.skip_calibration,
            args.input_cost_per_mtok,
            args.output_cost_per_mtok,
        )

    destination = args.output or REPO_ROOT / "eval" / "results" / "harness-latest.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2) + "\n")
    calibration = result.get("calibration", {})
    print(
        f"calibration: {calibration.get('passed', 'skipped')}/{calibration.get('total', 'skipped')}"
    )
    if "aggregate" in result:
        print(json.dumps(result["aggregate"], indent=2))
    print(f"status: {result['status']} | result: {destination}")
    return 0 if result["status"] == "valid" else 1


if __name__ == "__main__":
    raise SystemExit(main())
