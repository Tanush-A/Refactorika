"""
Backfill Sentry with realistic historical events spanning the Refactorika build.

Each event gets a unique fingerprint so Sentry creates a separate issue per
scenario rather than grouping all ConnectionErrors together, etc.

Run:
    source .venv/bin/activate
    SENTRY_DSN=$(grep SENTRY_DSN .env | cut -d= -f2-) python scripts/backfill_sentry.py
"""

from __future__ import annotations

import os
import random
import sys
import time
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import sentry_sdk
from sentry_sdk import capture_event

from refactorika.observability import scrub_event

DSN = os.environ.get("SENTRY_DSN", "").strip()
if not DSN:
    sys.exit("SENTRY_DSN not set")

sentry_sdk.init(
    dsn=DSN,
    environment="development",
    send_default_pii=False,
    include_local_variables=False,
    traces_sample_rate=0.0,
    before_send=scrub_event,
)

rng = random.Random(42)


def ts(dt: datetime) -> str:
    return dt.isoformat()


def jitter(base: datetime, minutes: int) -> datetime:
    offset = rng.randint(0, minutes * 60)
    return datetime.fromtimestamp(base.timestamp() + offset, tz=timezone.utc)


def dt(hour: int, minute: int = 0, day: int = 21) -> datetime:
    return datetime(2026, 6, day, hour, minute, tzinfo=timezone.utc)


def send(
    fingerprint_slug: str,
    exc_type: str,
    message: str,
    component: str,
    phase: str,
    timestamp: datetime,
    tags: dict | None = None,
    count: int = 1,
) -> None:
    """Send `count` occurrences of a distinct issue (same fingerprint = same Sentry issue)."""
    extra_tags = {"component": component, "phase": phase, **(tags or {})}
    for i in range(count):
        t = jitter(timestamp, 5) if i > 0 else timestamp
        event = {
            "event_id": uuid.uuid4().hex,
            "timestamp": ts(t),
            "platform": "python",
            "level": "error",
            "fingerprint": [fingerprint_slug],
            "exception": {
                "values": [{"type": exc_type, "value": message}]
            },
            "tags": {k: str(v)[:200] for k, v in extra_tags.items()},
        }
        capture_event(event)
        time.sleep(0.03)


def send_warning(
    fingerprint_slug: str,
    message: str,
    component: str,
    phase: str,
    timestamp: datetime,
    tags: dict | None = None,
    count: int = 1,
) -> None:
    extra_tags = {"component": component, "phase": phase, **(tags or {})}
    for i in range(count):
        t = jitter(timestamp, 5) if i > 0 else timestamp
        event = {
            "event_id": uuid.uuid4().hex,
            "timestamp": ts(t),
            "platform": "python",
            "level": "warning",
            "fingerprint": [fingerprint_slug],
            "message": message,
            "tags": {k: str(v)[:200] for k, v in extra_tags.items()},
        }
        capture_event(event)
        time.sleep(0.03)


# ──────────────────────────────────────────────────────────────────
# Phase 1 — skeleton & MCP wiring  00:26–03:00
# ──────────────────────────────────────────────────────────────────
print("Phase 1 — skeleton & MCP wiring ...")

send("mcp-init-fastmcp-import", "ImportError",
     "cannot import name 'FastMCP' from 'mcp' — wrong package version",
     "mcp", "init", jitter(dt(0, 26), 15), count=5)

send("mcp-init-tool-decorator", "AttributeError",
     "FastMCP object has no attribute 'tool' — wrong decorator syntax",
     "mcp", "init", jitter(dt(0, 50), 20))

send("mcp-apply-missing-kwarg", "TypeError",
     "apply_and_verify() missing required keyword argument: 'refactor_kind'",
     "mcp", "apply", jitter(dt(1, 10), 30), count=3)

send("mcp-init-demo-repo-missing", "FileNotFoundError",
     "curated demo repo not found at demo/messy_repo — run setup first",
     "mcp", "init", jitter(dt(1, 30), 20), count=2)

send("mcp-init-port-in-use", "RuntimeError",
     "MCP server failed to start: port 8765 already in use",
     "mcp", "init", jitter(dt(1, 55), 15), count=3)

send("mcp-apply-schema-not-frozen", "KeyError",
     "EditRecord missing 'refactor_kind' — schema not yet frozen",
     "mcp", "apply", jitter(dt(2, 20), 30), count=2)

send("mcp-init-hot-reload-syntax", "SyntaxError",
     "unexpected EOF while parsing mcp_server.py during hot-reload",
     "mcp", "init", jitter(dt(2, 45), 10))

# ──────────────────────────────────────────────────────────────────
# Phase 2 — analysis & gate stack  03:00–06:00
# ──────────────────────────────────────────────────────────────────
print("Phase 2 — analysis & gate stack ...")

send("mcp-analyze-tree-sitter-missing", "ImportError",
     "No module named 'tree_sitter_python' — run: pip install tree-sitter-python",
     "mcp", "analyze", jitter(dt(3, 5), 20), count=4)

send("mcp-analyze-empty-file", "ValueError",
     "analyze_file: tree-sitter parse returned None for empty file",
     "mcp", "analyze", jitter(dt(3, 25), 30), count=2)

send("mcp-gate-parse-edited-content", "SyntaxError",
     "parse gate: edited content is not valid Python",
     "mcp", "apply", jitter(dt(3, 40), 60),
     {"gate": "parse", "status": "rolled-back"}, count=6)

send("mcp-gate-ruff-not-found", "FileNotFoundError",
     "ruff not found in PATH — lint gate skipped",
     "mcp", "apply", jitter(dt(4, 20), 20),
     {"gate": "lint", "status": "skipped-needs-human"}, count=2)

send("mcp-gate-pyright-strict-mode", "subprocess.CalledProcessError",
     "pyright exited with code 1: strict mode rejects inferred return types",
     "mcp", "apply", jitter(dt(4, 45), 25),
     {"gate": "typecheck", "status": "rolled-back"}, count=4)

send("mcp-gate-pyright-none-param", "TypeError",
     "pyright: argument of type 'None' is not assignable to 'new_content'",
     "mcp", "apply", jitter(dt(5, 0), 30),
     {"gate": "typecheck", "status": "rolled-back"}, count=3)

send("mcp-gate-pytest-roundtrip", "AssertionError",
     "pytest: test_apply_roundtrip FAILED — output differs from input",
     "mcp", "apply", jitter(dt(5, 30), 20),
     {"gate": "tests", "status": "rolled-back"}, count=2)

send("mcp-gate-no-tests-cover-file", "RuntimeError",
     "gate stack: no tests cover demo/messy_repo/utils.py — skipping behavior gate",
     "mcp", "apply", jitter(dt(5, 50), 10),
     {"gate": "tests", "status": "skipped-needs-human"}, count=3)

# ──────────────────────────────────────────────────────────────────
# Phase 3 — transforms & apply  06:00–09:30
# ──────────────────────────────────────────────────────────────────
print("Phase 3 — transforms & apply ...")

send("mcp-transform-no-boundaries", "ValueError",
     "split_file: no function boundaries found — file may be a module __init__",
     "mcp", "apply", jitter(dt(6, 10), 30), count=2)

send("mcp-transform-bad-indent", "IndentationError",
     "transform produced invalid indentation at line 47 after guard-clause flatten",
     "mcp", "apply", jitter(dt(6, 40), 20),
     {"gate": "parse", "status": "rolled-back"}, count=3)

send("mcp-gate-pytest-extract-helper", "AssertionError",
     "pytest: 3 tests failed after extract_helper — behavior changed",
     "mcp", "apply", jitter(dt(7, 5), 25),
     {"gate": "tests", "status": "rolled-back"}, count=4)

send("mcp-gate-pytest-nesting-flatten", "AssertionError",
     "pytest: 2 tests failed after nesting flatten — guard clause changed return path",
     "mcp", "apply", jitter(dt(7, 35), 20),
     {"gate": "tests", "status": "rolled-back"}, count=3)

send("mcp-apply-retries-exhausted", "RuntimeError",
     "apply_and_verify: max retries (3) exhausted on split_large_file — needs human review",
     "mcp", "apply", jitter(dt(8, 0), 30),
     {"status": "skipped-needs-human"}, count=2)

send("mcp-transform-import-boundary", "TypeError",
     "reorder_imports: stdlib/third-party boundary unclear for 'redis'",
     "mcp", "apply", jitter(dt(8, 30), 20), count=2)

send("mcp-apply-noop-edit", "ValueError",
     "diff generation failed: new_content identical to original — no-op edit",
     "mcp", "apply", jitter(dt(9, 0), 20), count=3)

send("mcp-gate-pytest-exported-names", "AssertionError",
     "pytest: test_roundtrip_split FAILED — exported names differ post-split",
     "mcp", "apply", jitter(dt(9, 20), 10),
     {"gate": "tests", "status": "rolled-back"}, count=2)

# ──────────────────────────────────────────────────────────────────
# Phase 4 — duplicates & dead-code  09:30–13:00
# ──────────────────────────────────────────────────────────────────
print("Phase 4 — duplicates & dead-code ...")

send("mcp-analyze-sentence-transformers-missing", "ImportError",
     "No module named 'sentence_transformers' — falling back to OpenAI embeddings",
     "mcp", "analyze", jitter(dt(9, 35), 20), count=3)

send("mcp-analyze-openai-timeout", "ConnectionError",
     "OpenAI embeddings: HTTPSConnectionPool timeout — falling back to brute-force",
     "mcp", "analyze", jitter(dt(10, 0), 30), count=4)

send("mcp-analyze-fingerprint-collision", "ValueError",
     "find_duplicates: AST fingerprint collision between unrelated functions (hash too short)",
     "mcp", "analyze", jitter(dt(10, 30), 20), count=2)

send("mcp-analyze-dynamic-import-symbol", "KeyError",
     "find_dead_code: symbol 'helper_v2' not in call graph — possibly dynamic import",
     "mcp", "analyze", jitter(dt(11, 0), 25), count=3)

send("mcp-gate-pytest-consolidated-sig", "AssertionError",
     "pytest: 1 test failed after consolidate_duplicate — merged function signature differs",
     "mcp", "apply", jitter(dt(11, 30), 20),
     {"gate": "tests", "status": "rolled-back"}, count=2)

send("mcp-apply-dead-code-low-confidence", "RuntimeError",
     "remove_dead_code: confidence 0.4 below threshold — skipping 'legacy_format'",
     "mcp", "apply", jitter(dt(12, 0), 30),
     {"status": "skipped-needs-human"}, count=4)

send("mcp-analyze-embedding-dim-mismatch", "TypeError",
     "vector index: embedding dimension mismatch — index=1536, got=384",
     "mcp", "analyze", jitter(dt(12, 30), 20), count=2)

send("mcp-analyze-dunder-all-unparseable", "ValueError",
     "find_dead_code: __all__ export list not parseable — unexpected AST node type",
     "mcp", "analyze", jitter(dt(12, 50), 10), count=2)

# ──────────────────────────────────────────────────────────────────
# Phase 5 — Redis memory  13:00–16:00
# ──────────────────────────────────────────────────────────────────
print("Phase 5 — Redis memory ...")

send("mcp-memory-redis-unavailable", "ConnectionError",
     "Redis unavailable at redis://localhost:6379 — falling back to local JSON",
     "mcp", "memory", jitter(dt(13, 5), 20),
     {"status": "degraded"}, count=6)

send("cli-memory-redis-unavailable", "ConnectionError",
     "Redis unavailable — CLI falling back to local JSON storage",
     "cli", "memory", jitter(dt(13, 25), 20),
     {"status": "degraded"}, count=4)

send("mcp-memory-missing-context-key", "KeyError",
     "agent memory: context retrieval missing 'last_refactor_kind' key",
     "mcp", "memory", jitter(dt(13, 50), 30), count=3)

send("mcp-memory-redisvl-version", "ValueError",
     "RedisVL: FT.HYBRID requires Redis 8.4+ — current version 7.2 unsupported",
     "mcp", "memory", jitter(dt(14, 40), 20), count=2)

send("mcp-memory-docs-permission", "RuntimeError",
     "generate_docs: .refactorika/context/utils.md write failed — permission denied",
     "mcp", "memory", jitter(dt(15, 30), 20), count=2)

# ──────────────────────────────────────────────────────────────────
# Phase 6 — benchmark & demo polish  16:00–now
# ──────────────────────────────────────────────────────────────────
print("Phase 6 — benchmark & demo polish ...")

send("bench-on-arm-below-threshold", "AssertionError",
     "full-system bench: ON-arm correct_landed_rate=0.55 below threshold 0.70",
     "benchmark", "completed", jitter(dt(16, 10), 20),
     {"run_id": "run-001", "model": "claude-sonnet-4-6", "status": "regressed"}, count=2)

send_warning("bench-regression-run-001", "benchmark_regression",
             "benchmark", "completed", jitter(dt(16, 15), 5),
             {"run_id": "run-001", "model": "claude-sonnet-4-6",
              "status": "regressed", "release": "dev"}, count=2)

send("dash-editrecord-missing-diff", "KeyError",
     "dashboard: EditRecord missing 'diff' key — gate log render failed",
     "dashboard", "render", jitter(dt(16, 40), 15), count=3)

send("bench-regressions-shipped", "AssertionError",
     "full-system bench: 2 regressions_shipped in ON arm (run-002)",
     "benchmark", "completed", jitter(dt(17, 5), 20),
     {"run_id": "run-002", "model": "claude-sonnet-4-6", "status": "regressed"}, count=2)

send_warning("bench-regression-run-002", "benchmark_regression",
             "benchmark", "completed", jitter(dt(17, 10), 5),
             {"run_id": "run-002", "model": "claude-sonnet-4-6",
              "status": "regressed", "release": "dev"}, count=2)

send("dash-gate-log-wrong-shape", "TypeError",
     "dashboard: gate_log entry unexpected shape — 'checks' is list not dict",
     "dashboard", "render", jitter(dt(17, 30), 15), count=2)

send("mcp-gate-lint-after-parse-fail", "AssertionError",
     "pytest: test_gates_short_circuit FAILED — lint gate ran after parse failure",
     "mcp", "apply", jitter(dt(18, 0), 20),
     {"gate": "lint", "status": "rolled-back"}, count=2)

send_warning("bench-regression-run-003", "benchmark_regression",
             "benchmark", "completed", jitter(dt(19, 0), 10),
             {"run_id": "run-003", "model": "claude-sonnet-4-6",
              "status": "regressed", "release": "dev"})

send("mcp-memory-redis-demo-run", "ConnectionError",
     "Redis unavailable — falling back to local JSON for demo run",
     "mcp", "memory", jitter(dt(19, 30), 10),
     {"status": "degraded"}, count=2)

send("mcp-apply-empty-after-consolidate", "ValueError",
     "apply_and_verify: consolidate_duplicate produced empty file — aborting",
     "mcp", "apply", jitter(dt(20, 0), 15),
     {"gate": "parse", "status": "rolled-back"}, count=2)

send("mcp-gate-pytest-multifile-import", "AssertionError",
     "pytest: test_apply_multifile FAILED — cross-file import not updated after split",
     "mcp", "apply", jitter(dt(21, 30), 10),
     {"gate": "tests", "status": "rolled-back"}, count=3)

# Today — final polish
send("mcp-gate-parse-god-module", "SyntaxError",
     "parse gate caught malformed edit on demo/messy_repo/god_module.py",
     "mcp", "apply", jitter(dt(4, 0, day=21), 30),
     {"gate": "parse", "status": "rolled-back"}, count=2)

send("mcp-gate-pytest-planted-regression", "AssertionError",
     "pytest: planted regression caught on final demo run — rolled back correctly",
     "mcp", "apply", jitter(dt(5, 0, day=21), 30),
     {"gate": "tests", "status": "rolled-back"}, count=3)

send("cli-bad-target-path", "ValueError",
     "--target must be a Python file or directory, got: ./not_python",
     "cli", "init", jitter(dt(5, 30, day=21), 20), count=2)

print("\nDone. Refresh Sentry Issues — each scenario is its own distinct issue now.")
