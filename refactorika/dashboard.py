"""Render the edit log — visible verification is the whole product. Run: python -m refactorika.dashboard"""

from __future__ import annotations

from .core.storage import Storage

_MARK = {True: "PASS", False: "FAIL", None: "skip"}
_STATUS = {
    "committed": "COMMITTED ✓",
    "rolled-back": "ROLLED BACK ✗",
    "skipped-needs-human": "NEEDS HUMAN ⚠",
}


def render(log: list[dict]) -> str:
    lines = ["", "  Refactorika — edit log", "  " + "=" * 56]
    for i, r in enumerate(log, 1):
        c = r["checks"]
        gates = "  ".join(f"{g}:{_MARK[c[g]]}" for g in ("parse", "lint", "typecheck", "tests"))
        lines.append("")
        lines.append(f"  #{i}  {r['refactor_kind']}  on  {r['file'].split('/')[-1]}")
        lines.append(f"      gates: {gates}")
        lines.append(f"      status: {_STATUS.get(r['status'], r['status'])}  (retries: {r['retries']})")
        if r["failure_reason"]:
            lines.append(f"      reason: {r['failure_reason']}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    print(render(Storage().get_log()))


if __name__ == "__main__":
    main()
