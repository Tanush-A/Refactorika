"""refactorika scan <path> — run all advisory tools and print a ranked report."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core.analyze import analyze_file
from .core.storage import Storage


def _header(text: str, width: int = 60) -> str:
    bar = "─" * width
    return f"\n{bar}\n  {text}\n{bar}"


def _gate_hint() -> str:
    return "(gates: parse → ruff → pyright → pytest  |  apply with: apply_and_verify)"


def cmd_scan(path: str, no_dupes: bool, no_dead: bool, no_docs: bool) -> None:
    p = Path(path).resolve()
    storage = Storage()

    print(f"\nRefactorika scan: {p}")
    print(_gate_hint())

    # ── 1. Structural analysis ────────────────────────────────────────────────
    print(_header("1 / 4  Structural opportunities"))
    py_files = sorted(p.rglob("*.py")) if p.is_dir() else [p]
    py_files = [f for f in py_files if not any(
        part in {".venv", "__pycache__", ".git"} for part in f.parts
    )]

    total_opps = 0
    for f in py_files:
        try:
            result = analyze_file(str(f), storage)
        except Exception as exc:
            print(f"  [error] {f.name}: {exc}")
            continue
        if result.opportunities:
            rel = f.relative_to(p.parent) if p.is_dir() else f.name
            print(f"\n  {rel}")
            for opp in sorted(result.opportunities, key=lambda o: -o.rank):
                print(f"    [{opp.rank:>3}]  {opp.kind:<22}  {opp.location}")
                print(f"           {opp.detail}")
            total_opps += len(result.opportunities)

    if total_opps == 0:
        print("  ✓ No structural issues found.")
    else:
        print(f"\n  → {total_opps} opportunities total")

    # ── 2. Duplicate detection ────────────────────────────────────────────────
    if not no_dupes:
        print(_header("2 / 4  Duplicate functions"))
        try:
            from .analysis.duplicates import find_duplicates  # noqa: PLC0415
            from .memory.vector_index import VectorIndex  # noqa: PLC0415
            vi = VectorIndex(storage)
            dup_result = find_duplicates(str(p), storage, vi)
            pairs = dup_result.get("pairs", [])
            if pairs:
                for pair in sorted(pairs, key=lambda x: -x["rank"]):
                    a, b = pair["a"], pair["b"]
                    kind = pair["match_type"]
                    sim = pair["similarity"]
                    target = pair["consolidation_target"]["name"]
                    print(f"\n  [{pair['rank']:>3}] {kind}  similarity={sim:.2f}")
                    print(f"       {Path(a['file']).name}:{a['name']}  (line {a['line']})")
                    print(f"       {Path(b['file']).name}:{b['name']}  (line {b['line']})")
                    print(f"       → keep: {target}  |  {pair['reason']}")
                if "semantic" in dup_result:
                    print(f"\n  note: {dup_result['semantic']}")
                print(f"\n  → {len(pairs)} duplicate pair(s)")
            else:
                print("  ✓ No duplicates found.")
        except Exception as exc:
            print(f"  [error] {exc}")

    # ── 3. Dead-code detection ────────────────────────────────────────────────
    if not no_dead:
        print(_header("3 / 4  Dead code"))
        try:
            from .analysis.dead_code import find_dead_code  # noqa: PLC0415
            dead_result = find_dead_code(str(p), storage)
            dead = dead_result.get("dead_symbols", [])
            high = [d for d in dead if d["confidence"] == "high"]
            medium = [d for d in dead if d["confidence"] == "medium"]
            low = [d for d in dead if d["confidence"] == "low"]

            if high:
                print("\n  HIGH confidence (safe to remove — private + unreferenced):")
                for d in high:
                    print(f"    [{d['rank']}]  {d['name']}  ({Path(d['file']).name}:{d['line']})")
                    print(f"          {d['reason'][:90]}")
            if medium:
                print("\n  MEDIUM confidence (public + unreferenced — verify before removing):")
                for d in medium:
                    print(f"    [{d['rank']}]  {d['name']}  ({Path(d['file']).name}:{d['line']})")
            if low:
                print("\n  LOW confidence (name in string — possible dynamic dispatch):")
                for d in low:
                    print(f"    [{d['rank']}]  {d['name']}  ({Path(d['file']).name}:{d['line']})")

            if not dead:
                print("  ✓ No dead code found.")
            else:
                print(f"\n  → {len(high)} high, {len(medium)} medium, {len(low)} low")
        except Exception as exc:
            print(f"  [error] {exc}")

    # ── 4. Living docs ────────────────────────────────────────────────────────
    if not no_docs:
        print(_header("4 / 4  Module context (generate_docs preview)"))
        try:
            from .docs_gen import generate_docs  # noqa: PLC0415
            from .memory.agent_memory import AgentMemory  # noqa: PLC0415
            from .memory.context import ContextRetriever  # noqa: PLC0415
            mem = AgentMemory(storage)
            ret = ContextRetriever(storage, mem)
            for f in py_files[:5]:  # cap at 5 files for scan speed
                try:
                    r = generate_docs(str(f), storage, mem, ret)
                    mod = r.get("module", {})
                    rel = f.relative_to(p.parent) if p.is_dir() else f.name
                    exports = [e["name"] for e in mod.get("exports", [])]
                    flagged = mod.get("flagged", [])
                    print(f"\n  {rel}")
                    print(f"    purpose : {mod.get('purpose_hint', '?')[:70]}")
                    print(f"    exports : {', '.join(exports[:6]) or '(none)'}")
                    if flagged:
                        print(f"    flagged : {len(flagged)} pattern(s)")
                        for flag in flagged[:3]:
                            print(f"              {flag}")
                    print(f"    context : {r.get('context_file', '?')}")
                except Exception as exc:
                    print(f"  [error] {f.name}: {exc}")
            if len(py_files) > 5:
                print(f"\n  (showing 5 of {len(py_files)} files — run generate_docs per-file for full output)")
        except Exception as exc:
            print(f"  [error] {exc}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(_header("Next steps"))
    print("  • Apply a fix (in a Claude session with MCP wired up):")
    print('      apply_and_verify("<file>", <new_content>, "<kind>")')
    print("  • Multi-file consolidation:")
    print('      apply_and_verify_multi({"<fileA>": ..., "<fileB>": ...}, "consolidate_duplicate")')
    print("  • See the edit log:")
    print("      python -m refactorika.dashboard")
    print()


def cmd_fix(path: str, dry_run: bool, kinds: list[str], multi_agent: bool = False) -> None:
    """Auto-apply mechanical fixes: reorder_imports and remove_dead_code (high confidence only)."""
    from .analysis.dead_code import find_dead_code  # noqa: PLC0415
    from .core.apply import apply_and_verify  # noqa: PLC0415
    from .transforms.dead import remove_dead_symbols  # noqa: PLC0415
    from .transforms.imports import reorder_imports  # noqa: PLC0415

    p = Path(path).resolve()
    storage = Storage()

    if multi_agent:
        from .agents.orchestrator import dispatch_plan
        print(f"\nRefactorika fix (multi-agent): {p}")
        result = dispatch_plan(storage)
        if "error" in result:
            print(f"  error: {result['error']}")
            print("  Tip: run `get_plan` then `confirm_plan` first, or omit --multi-agent.")
        else:
            print(f"  committed: {result['committed']}  rolled-back: {result['rolled_back']}  skipped: {result['skipped']}")
            if result["rolled_back"]:
                print("  run: python -m refactorika.dashboard  to see failure reasons")
        print()
        return

    py_files = sorted(p.rglob("*.py")) if p.is_dir() else [p]
    py_files = [f for f in py_files if not any(
        part in {".venv", "__pycache__", ".git", "tests"} for part in f.parts
    )]

    committed = 0
    rolled_back = 0
    skipped = 0

    print(f"\nRefactorika fix: {p}{'  [dry-run]' if dry_run else ''}")
    print(f"Kinds: {', '.join(kinds)}\n")

    # ── reorder_imports ───────────────────────────────────────────────────────
    if "imports" in kinds:
        print("── reorder_imports " + "─" * 42)
        for f in py_files:
            try:
                new_content = reorder_imports(str(f))
            except Exception as exc:
                print(f"  [error]  {f.name}: {exc}")
                continue
            if new_content == f.read_text():
                continue  # nothing changed
            print(f"  {f.name} ... ", end="", flush=True)
            if dry_run:
                print("(dry-run: would apply)")
                skipped += 1
                continue
            record = apply_and_verify(str(f), new_content, "reorder_imports", storage)
            _print_record(record)
            if record.status == "committed":
                committed += 1
            elif record.status == "rolled-back":
                rolled_back += 1
            else:
                skipped += 1

    # ── remove_dead_code (high confidence only) ───────────────────────────────
    if "dead" in kinds:
        print("\n── remove_dead_code (high confidence) " + "─" * 23)
        try:
            dead_result = find_dead_code(str(p), storage)
        except Exception as exc:
            print(f"  [error] {exc}")
            dead_result = {"dead_symbols": []}

        # Group high-confidence dead symbols by file.
        by_file: dict[str, set[str]] = {}
        for sym in dead_result.get("dead_symbols", []):
            if sym["confidence"] != "high":
                continue
            file_str = sym["file"]
            unqualified = sym["name"].split(".")[-1]
            by_file.setdefault(file_str, set()).add(unqualified)

        if not by_file:
            print("  ✓ No high-confidence dead symbols found.")
        for file_str, names in by_file.items():
            rel = Path(file_str).name
            print(f"  {rel}: removing {sorted(names)} ... ", end="", flush=True)
            if dry_run:
                print("(dry-run: would apply)")
                skipped += 1
                continue
            try:
                new_content = remove_dead_symbols(file_str, names)
            except Exception as exc:
                print(f"[error] {exc}")
                continue
            record = apply_and_verify(file_str, new_content, "remove_dead_code", storage)
            _print_record(record)
            if record.status == "committed":
                committed += 1
            elif record.status == "rolled-back":
                rolled_back += 1
            else:
                skipped += 1

    # ── summary ───────────────────────────────────────────────────────────────
    print(f"\n{'─' * 60}")
    if dry_run:
        print(f"  dry-run complete — {skipped} change(s) would be applied")
    else:
        print(f"  committed: {committed}   rolled-back: {rolled_back}   skipped: {skipped}")
        if rolled_back:
            print("  run: python -m refactorika.dashboard  to see failure reasons")
    print()

    if not dry_run:
        _print_remaining_hints()


def _print_record(record) -> None:
    gates = record.checks
    parts = []
    for name, val in [("parse", gates.parse), ("lint", gates.lint),
                      ("type", gates.typecheck), ("tests", gates.tests)]:
        if val is True:
            parts.append(f"{name}:✓")
        elif val is False:
            parts.append(f"{name}:✗")
        else:
            parts.append(f"{name}:-")
    status = "✓ committed" if record.status == "committed" else "✗ rolled-back"
    print(f"{status}  [{' '.join(parts)}]")
    if record.status == "rolled-back" and record.failure_reason:
        reason = (record.failure_reason or "")[:80]
        print(f"         reason: {reason}")


def _print_remaining_hints() -> None:
    print("  Remaining issues need Claude to write new content:")
    print("    • flatten_nesting  — guard-clause rewrite of deep conditionals")
    print("    • split_function   — extract sub-functions from long bodies")
    print("    • consolidate_duplicate — merge duplicate pair into one canonical function")
    print()
    print("  Run these with the MCP server wired into Claude:")
    print('    apply_and_verify("<file>", <new_content>, "<kind>")')
    print('    apply_and_verify_multi({...}, "consolidate_duplicate")')
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="refactorika",
        description="Refactorika — safe structural refactoring for Python",
    )
    sub = parser.add_subparsers(dest="command")

    scan_p = sub.add_parser("scan", help="Scan a file or directory and report all issues")
    scan_p.add_argument("path", help="File or directory to scan")
    scan_p.add_argument("--no-dupes", action="store_true", help="Skip duplicate detection")
    scan_p.add_argument("--no-dead", action="store_true", help="Skip dead-code detection")
    scan_p.add_argument("--no-docs", action="store_true", help="Skip module-context generation")

    fix_p = sub.add_parser(
        "fix",
        help="Auto-apply mechanical fixes (reorder_imports + remove_dead_code)",
    )
    fix_p.add_argument("path", help="File or directory to fix")
    fix_p.add_argument(
        "--dry-run", action="store_true",
        help="Show what would change without writing anything",
    )
    fix_p.add_argument(
        "--kinds", default="imports,dead",
        help="Comma-separated fix kinds to apply (default: imports,dead)",
    )
    fix_p.add_argument(
        "--multi-agent", action="store_true",
        help="Dispatch confirmed plan via parallel specialist agents (requires get_plan + confirm_plan first)",
    )

    sub.add_parser("serve", help="Start the MCP server")

    args = parser.parse_args()

    if args.command == "scan":
        cmd_scan(args.path, args.no_dupes, args.no_dead, args.no_docs)
    elif args.command == "fix":
        kinds = [k.strip() for k in args.kinds.split(",")]
        cmd_fix(args.path, args.dry_run, kinds, getattr(args, "multi_agent", False))
    elif args.command == "serve" or args.command is None:
        from .mcp_server import main as mcp_main  # noqa: PLC0415
        mcp_main()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
