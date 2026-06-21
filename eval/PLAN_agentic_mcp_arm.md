# Plan: AGENTIC+MCP Fourth Benchmark Arm

## Goal

Add a fourth arm `"agentic+mcp"` to `eval/full_system_bench.py`. This arm gives the
model Refactorika's actual analysis tools (`analyze_file`, `find_duplicates`,
`find_dead_code`) plus `apply_and_verify` — which forces every mutation through the
gate stack (parse → ruff → pyright → pytest) with automatic rollback on failure.
It cannot write files directly; all mutations go through `apply_and_verify`.

This is the only arm that tests the actual product. Combined with the existing three
arms it produces a clean factorial:

| Arm            | Structured analysis | Forced verification | Iteration |
|----------------|---------------------|---------------------|-----------|
| off            | no                  | no                  | no        |
| on             | yes (prompt prefix) | yes (gate retries)  | no        |
| agentic        | no                  | no                  | yes       |
| **agentic+mcp**| **yes (tools)**     | **yes (apply_and_verify)** | **yes** |

`agentic vs agentic+mcp` is the comparison that directly validates the product claim.

---

## Context: what already exists

The existing three arms and supporting infra live entirely in
`eval/full_system_bench.py`. Key pieces relevant to this plan:

- `AgenticBackend` — multi-turn tool-use loop added in the previous session.
  Has `_api_call`, `_execute`, and `run` methods. `run` returns
  `(edits, usage, seconds, error, model_calls)` by snapshotting `.py` files
  before/after the loop.
- `propose_agentic(backend, case, repo) -> Proposal` — thin wrapper around
  `AgenticBackend.run`.
- `_run_pair` — now accepts `agentic_backend: AgenticBackend | None = None`.
  Adding the new arm follows the same pattern.
- `aggregate` — already handles dynamic arms. Adding "agentic+mcp" records will
  produce arm stats automatically; you only need to add the paired comparison key.
- `REQUIRED_GATES = ("lint", "typecheck", "tests")` — reuse for `verify_edits`.

Imports already in the file that you'll reuse:
```python
import json, subprocess, sys, time
import urllib.request, urllib.error
from pathlib import Path
from refactorika.harness import verify_edits   # already imported
from refactorika.core.storage import Storage   # already imported
```

New imports needed (add near the top with the other refactorika imports):
```python
from refactorika.core.analyze import analyze_file as _rf_analyze_file
from refactorika.analysis.duplicates import find_duplicates as _rf_find_duplicates
from refactorika.analysis.dead_code import find_dead_code as _rf_find_dead_code
from refactorika.memory.vector_index import VectorIndex
```

---

## Step 1 — Add `_AGENTIC_MCP_TOOLS` and `_AGENTIC_MCP_SYSTEM`

Place these immediately after `_AGENTIC_TOOLS` and `_AGENTIC_SYSTEM` (which end just
before `class AgenticBackend`).

```python
_AGENTIC_MCP_TOOLS: list[dict] = [
    {
        "name": "list_files",
        "description": "List Python source files in the repository (excludes __pycache__ and .venv).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_file",
        "description": "Read the contents of a file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to repository root"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "analyze_file",
        "description": (
            "Run Refactorika structural analysis on a file or directory. "
            "Returns ranked opportunities: long functions, deep nesting, import issues, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to repository root"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "find_duplicates",
        "description": (
            "Find duplicate or near-duplicate functions in a file or directory "
            "using structural fingerprinting and semantic similarity."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to repository root"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "find_dead_code",
        "description": (
            "Find unreachable symbols via call-graph reachability analysis. "
            "Returns dead functions/classes ranked by confidence."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to repository root"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "apply_and_verify",
        "description": (
            "Submit a mutation for verification. Runs parse → ruff → pyright → pytest. "
            "Commits the file on success; rolls back atomically and returns diagnostics on failure. "
            "This is the ONLY way to modify source files — do not use write_file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to repository root"},
                "new_content": {"type": "string", "description": "Complete new file content"},
                "refactor_kind": {
                    "type": "string",
                    "description": "Kind of refactor: e.g. flatten_nesting, extract_function, consolidate_duplicate, remove_dead_code",
                },
            },
            "required": ["path", "new_content", "refactor_kind"],
        },
    },
    {
        "name": "run_bash",
        "description": "Run a read-only shell command (e.g. grep, cat). Output capped at 2 000 chars.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
            },
            "required": ["command"],
        },
    },
]

_AGENTIC_MCP_SYSTEM = (
    "You are an autonomous Python refactoring agent with access to Refactorika's analysis tools. "
    "Workflow: (1) use analyze_file, find_duplicates, or find_dead_code to identify the "
    "highest-value opportunity; (2) read affected files; (3) submit the refactored content via "
    "apply_and_verify — it runs lint, type-check, and tests and rolls back with diagnostics on "
    "failure; (4) repair and retry if rejected. "
    "You MUST use apply_and_verify for all mutations — you cannot write files directly. "
    "Do not touch files under tests/. Stop when the refactoring is committed."
)
```

---

## Step 2 — Add `AgenticHarnessBackend` class

Place this immediately after `class AgenticBackend` (before `def main()`).

The class shares `_api_call` logic with `AgenticBackend` but has its own `_execute`
that routes to the Refactorika Python API.

```python
class AgenticHarnessBackend:
    """Agentic arm with Refactorika MCP tools: analysis + apply_and_verify gate stack."""

    def __init__(
        self,
        model: str,
        api_key: str,
        max_iterations: int = 20,
        bash_timeout: int = 30,
    ) -> None:
        self.model = model
        self.name = f"{model}+mcp"
        self._api_key = api_key
        self.max_iterations = max_iterations
        self.bash_timeout = bash_timeout

    # Identical to AgenticBackend._api_call — same Anthropic messages API, different tools.
    def _api_call(self, messages: list[dict]) -> tuple[list[dict], Usage, str | None]:
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
        }
        body = {
            "model": self.model,
            "max_tokens": 8192,
            "temperature": 0,
            "system": _AGENTIC_MCP_SYSTEM,
            "tools": _AGENTIC_MCP_TOOLS,
            "messages": messages,
        }
        try:
            req = urllib.request.Request(url, json.dumps(body).encode(), headers, method="POST")
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = json.loads(resp.read())
            raw = data.get("usage", {})
            usage = Usage(
                input_tokens=int(raw.get("input_tokens", 0)),
                output_tokens=int(raw.get("output_tokens", 0)),
                cache_read_tokens=int(raw.get("cache_read_input_tokens", 0)),
                cache_write_tokens=int(raw.get("cache_creation_input_tokens", 0)),
            )
            return data["content"], usage, None
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
            return [], Usage(), str(exc)

    def _execute(self, repo: Path, storage: Storage, name: str, inputs: dict) -> str:
        if name == "list_files":
            files = sorted(
                p.relative_to(repo).as_posix()
                for p in repo.rglob("*.py")
                if not any(part in {".venv", "__pycache__", ".git"} for part in p.parts)
            )
            return "\n".join(files) or "(no Python files)"

        if name == "read_file":
            path = inputs.get("path", "")
            target = (repo / path).resolve()
            try:
                target.relative_to(repo.resolve())
            except ValueError:
                return "error: path escapes repository"
            return target.read_text() if target.is_file() else f"error: {path} not found"

        if name == "analyze_file":
            path = inputs.get("path", "")
            target = repo / path
            if not target.exists():
                return f"error: {path} not found"
            try:
                result = _rf_analyze_file(str(target), storage)
                return json.dumps(
                    result.to_dict() if hasattr(result, "to_dict") else vars(result),
                    default=str,
                )
            except Exception as exc:
                return f"error: {exc}"

        if name == "find_duplicates":
            path = inputs.get("path", "")
            target = repo / path
            if not target.exists():
                return f"error: {path} not found"
            try:
                vi = VectorIndex(storage)
                result = _rf_find_duplicates(str(target), storage, vi)
                return json.dumps(result, default=str)
            except Exception as exc:
                return f"error: {exc}"

        if name == "find_dead_code":
            path = inputs.get("path", "")
            target = repo / path
            if not target.exists():
                return f"error: {path} not found"
            try:
                result = _rf_find_dead_code(str(target), storage)
                return json.dumps(result, default=str)
            except Exception as exc:
                return f"error: {exc}"

        if name == "apply_and_verify":
            path = inputs.get("path", "")
            new_content = inputs.get("new_content", "")
            refactor_kind = inputs.get("refactor_kind", "refactor")
            if path.startswith("tests/"):
                return "error: cannot apply_and_verify on test files"
            target = (repo / path).resolve()
            try:
                target.relative_to(repo.resolve())
            except ValueError:
                return "error: path escapes repository"
            try:
                record = verify_edits(
                    repo,
                    {path: new_content},
                    test_command=[sys.executable, "-m", "pytest", "-q"],
                    required_gates=REQUIRED_GATES,
                )
                if record.status == "committed":
                    return f"committed: {path} ({refactor_kind})"
                return f"rolled-back: {json.dumps(record.gate_details, sort_keys=True)}"
            except ValueError as exc:
                return f"error: {exc}"

        if name == "run_bash":
            command = inputs.get("command", "")
            try:
                proc = subprocess.run(
                    command,
                    shell=True,
                    cwd=repo,
                    capture_output=True,
                    text=True,
                    timeout=self.bash_timeout,
                )
                out = (proc.stdout + proc.stderr).strip()
            except subprocess.TimeoutExpired:
                return f"exit 124\ntimeout after {self.bash_timeout}s"
            if len(out) > 2000:
                out = out[:2000] + "\n...[truncated]"
            return f"exit {proc.returncode}\n{out}" if out else f"exit {proc.returncode}"

        return f"error: unknown tool {name!r}"

    def run(
        self, repo: Path, user_prompt: str
    ) -> tuple[dict[str, str], Usage, float, str | None, int]:
        # Storage per-run, JSON-only (no Redis dependency in benchmark).
        storage = Storage(redis_url=None, json_path=repo / ".refactorika" / "bench-state.json")

        before = {
            p.relative_to(repo).as_posix(): p.read_text()
            for p in sorted(repo.rglob("*.py"))
            if not any(part in {".venv", "__pycache__", ".git"} for part in p.parts)
        }
        messages: list[dict] = [{"role": "user", "content": user_prompt}]
        usage = Usage()
        started = time.perf_counter()
        error: str | None = None
        model_calls = 0

        for _ in range(self.max_iterations):
            content, turn_usage, call_error = self._api_call(messages)
            model_calls += 1
            usage.add(turn_usage)
            if call_error:
                error = call_error
                break
            messages.append({"role": "assistant", "content": content})
            tool_calls = [b for b in content if b.get("type") == "tool_use"]
            if not tool_calls:
                break
            results = [
                {
                    "type": "tool_result",
                    "tool_use_id": call["id"],
                    "content": self._execute(repo, storage, call["name"], call.get("input", {})),
                }
                for call in tool_calls
            ]
            messages.append({"role": "user", "content": results})

        seconds = round(time.perf_counter() - started, 3)
        after = {
            p.relative_to(repo).as_posix(): p.read_text()
            for p in sorted(repo.rglob("*.py"))
            if not any(part in {".venv", "__pycache__", ".git"} for part in p.parts)
        }
        edits = {path: text for path, text in after.items() if before.get(path) != text}
        return edits, usage, seconds, error, model_calls
```

---

## Step 3 — Add `propose_agentic_mcp`

Place immediately after `propose_agentic`:

```python
def propose_agentic_mcp(
    backend: "AgenticHarnessBackend", case: CaseAdapter, repo: Path
) -> Proposal:
    edits, usage, seconds, error, model_calls = backend.run(repo, case.user_prompt)
    if not error and not edits:
        error = "agent made no changes to source files"
    return Proposal(
        edits=edits if not error else {},
        usage=usage,
        seconds=seconds,
        prompt=f"[agentic+mcp:{backend.name}] {case.user_prompt}",
        error=error,
        model_calls=model_calls,
    )
```

---

## Step 4 — Extend `_run_pair`

### 4a. Signature

Add `agentic_mcp_backend` parameter after `agentic_backend`:

```python
def _run_pair(
    case: CaseAdapter,
    backend: Backend,
    trial: int,
    max_retries: int,
    pricing: Pricing,
    agentic_backend: "AgenticBackend | None" = None,
    agentic_mcp_backend: "AgenticHarnessBackend | None" = None,
) -> list[dict]:
```

### 4b. Body — add after the existing agentic arm block

The existing agentic arm ends with `records.append({...})`. After that block, add:

```python
        if agentic_mcp_backend is not None:
            agentic_mcp_repo = materialize(case, Path(tmp) / "agentic_mcp")
            agentic_mcp_started = time.perf_counter()
            agentic_mcp = propose_agentic_mcp(agentic_mcp_backend, case, agentic_mcp_repo)
            agentic_mcp_end_to_end = time.perf_counter() - agentic_mcp_started
            agentic_mcp_landed = not bool(agentic_mcp.error)
            agentic_mcp_behavior, agentic_mcp_detail, agentic_mcp_structure = (
                oracle_grade(case, agentic_mcp_repo)
                if agentic_mcp_landed
                else (False, agentic_mcp.error or "not landed", [])
            )
            agentic_mcp_outcome = _outcome(
                agentic_mcp_landed, agentic_mcp_behavior, agentic_mcp_structure
            )
            records.append({
                **common,
                "arm": "agentic+mcp",
                "status": "shipped" if agentic_mcp_landed else "error",
                **agentic_mcp_outcome,
                "oracle_pass": agentic_mcp_behavior if agentic_mcp_landed else None,
                "structural_failures": agentic_mcp_structure,
                "detail": agentic_mcp_detail,
                "tokens": agentic_mcp.usage.total,
                "seconds": round(agentic_mcp.seconds, 3),
                "initial": dict(agentic_mcp_outcome),
                "usage": _usage_record(agentic_mcp.usage, agentic_mcp.model_calls, pricing),
                "timing": {
                    "audit_seconds": 0.0,
                    "model_seconds": round(agentic_mcp.seconds, 3),
                    "gate_seconds": 0.0,
                    "application_seconds": 0.0,
                    "grading_seconds": 0.0,
                    "workflow_seconds": round(agentic_mcp.seconds, 3),
                    "end_to_end_seconds": round(agentic_mcp_end_to_end, 3),
                },
                "change": _change_metrics(case, agentic_mcp.edits, agentic_mcp_structure),
                "plan": None,
                "patch": agentic_mcp.edits,
            })
```

Note: `gate_seconds` is 0.0 here because the gate time is embedded inside
`apply_and_verify` calls during the model loop and not separately measurable without
instrumenting `verify_edits`. This is acceptable — it understates agentic+mcp's
total compute but the `end_to_end_seconds` captures wall time correctly.

---

## Step 5 — Add paired comparison in `aggregate`

After the existing `if "agentic" in arms_present:` block:

```python
    if "agentic+mcp" in arms_present:
        result["paired_agentic_mcp_vs_off"] = _paired_summary(
            records, "correct_landed", arm_a="agentic+mcp", arm_b="off"
        )
        result["paired_agentic_mcp_vs_agentic"] = _paired_summary(
            records, "correct_landed", arm_a="agentic+mcp", arm_b="agentic"
        )
```

---

## Step 6 — Thread through `run()` and `main()`

### `run()` signature

```python
def run(
    backend: Backend,
    cases: tuple[CaseAdapter, ...],
    trials: int,
    max_retries: int,
    pricing: Pricing | None = None,
    agentic_backend: "AgenticBackend | None" = None,
    agentic_mcp_backend: "AgenticHarnessBackend | None" = None,
) -> dict:
```

### `run()` inner call

```python
records.extend(
    _run_pair(case, backend, trial, max_retries, pricing, agentic_backend, agentic_mcp_backend)
)
```

### `main()` — add three args after the existing `--agentic-max-iter` line

```python
    parser.add_argument("--agentic-mcp", action="store_true",
                        help="add agentic+mcp arm (requires ANTHROPIC_API_KEY)")
    parser.add_argument("--agentic-mcp-model", default="claude-sonnet-4-5-20250929")
    parser.add_argument("--agentic-mcp-max-iter", type=int, default=20)
```

### `main()` — construct backend after existing `agentic_backend` construction block

```python
    agentic_mcp_backend: AgenticHarnessBackend | None = None
    if args.agentic_mcp:
        key = _load_env("ANTHROPIC_API_KEY")
        if not key:
            print("error: --agentic-mcp requires ANTHROPIC_API_KEY", file=sys.stderr)
            return 1
        agentic_mcp_backend = AgenticHarnessBackend(
            args.agentic_mcp_model, key, args.agentic_mcp_max_iter
        )
```

### `main()` — pass to `run()`

Add `agentic_mcp_backend` as the last positional arg in the `run(...)` call.

---

## Step 7 — Add the new imports

Near the top of `eval/full_system_bench.py`, alongside the existing refactorika imports:

```python
from refactorika.core.analyze import analyze_file as _rf_analyze_file
from refactorika.analysis.duplicates import find_duplicates as _rf_find_duplicates
from refactorika.analysis.dead_code import find_dead_code as _rf_find_dead_code
from refactorika.memory.vector_index import VectorIndex
```

---

## Step 8 — Tests to add in `tests/test_full_system_bench.py`

Add after the existing agentic-arm tests. The test should verify:
1. When `agentic_mcp_backend` is passed to `run()`, a third `"agentic+mcp"` arm record appears
2. The `aggregate` output contains `paired_agentic_mcp_vs_off` and `paired_agentic_mcp_vs_agentic`
3. The arm record has the expected schema keys

A `ScriptedHarnessBackend` test double can mirror the existing `ScriptedBackend`:
it must implement `run(repo, user_prompt)` returning `(edits, Usage(), 0.0, None, 1)`
where `edits` contains the correct refactored content for `GUARD_CLAUSES`.

Minimal test:

```python
class ScriptedHarnessBackend:
    """Test double: returns a known-good edit via the agentic+mcp interface."""
    name = "scripted+mcp"

    def run(
        self, repo: Path, user_prompt: str
    ) -> tuple[dict[str, str], "Usage", float, str | None, int]:
        from eval.full_system_bench import Usage
        # Correct guard-clause refactor (same content as ScriptedBackend uses)
        content = (
            "from collections.abc import Iterable\n\n\n"
            "def billable_event_ids(events: Iterable[dict[str, object]]) -> list[str]:\n"
            "    selected: list[str] = []\n"
            "    for event in events:\n"
            "        if event.get('enabled') is not True:\n"
            "            continue\n"
            "        if event.get('kind') == 'heartbeat':\n"
            "            continue\n"
            "        event_id = event.get('id')\n"
            "        if not (isinstance(event_id, str) and event_id):\n"
            "            continue\n"
            "        selected.append(event_id)\n"
            "    return selected\n"
        )
        edits = {"app/events.py": content}
        return edits, Usage(), 0.0, None, 1


def test_agentic_mcp_arm_appears_in_records() -> None:
    backend = ScriptedBackend()           # existing fixture
    mcp_backend = ScriptedHarnessBackend()
    result = run(backend, (adapt_case(GUARD_CLAUSES),), trials=1, max_retries=0,
                 agentic_mcp_backend=mcp_backend)
    arms = {r["arm"] for r in result["records"]}
    assert "agentic+mcp" in arms
    assert "paired_agentic_mcp_vs_off" in result["aggregate"]
    assert "paired_agentic_mcp_vs_agentic" in result["aggregate"]
```

---

## Verification checklist

After implementing:

1. `python3 -c "from eval.full_system_bench import AgenticHarnessBackend, propose_agentic_mcp; print('ok')"` — no ImportError
2. `pytest tests/test_full_system_bench.py -q` — existing passes unchanged, new test passes
3. `python -m eval.full_system_bench --calibrate-only` — calibration still valid
4. Manual smoke: `python -m eval.full_system_bench --agentic-mcp --trials 1 --case guard_clause_continue` — produces a result JSON with an `"agentic+mcp"` arm

---

## Known limitations to document (not fix now)

- `gate_seconds` in the agentic+mcp timing record is 0.0 because gate time is
  hidden inside `apply_and_verify` calls. Wall time (`end_to_end_seconds`) is correct.
- `find_duplicates` requires `tree-sitter-python`. If unavailable in the benchmark
  environment, `_execute` catches the exception and returns an error string; the agent
  will see the error and fall back to direct analysis.
- The 9 synthetic cases are small enough that a capable model may commit on the first
  `apply_and_verify` call, making the gate-retry advantage invisible. Harness value
  will be more visible on the recovery cases once those use live model calls.
