"""Substrates — isolated, git-initialized copies of an eval source tree.

Each (candidate x tier) run mutates files, so it needs its own throwaway copy.
The `full` tier and any future use of `core/apply.py` expect a real git repo
(rollback/commit live there), so copies are git-initialized by default.

Phase 0 ships one substrate: the curated `demo_repo/`. RefactorBench substrates
(Phase 2) plug in here by adding entries to `available_substrates()`.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_REPO = REPO_ROOT / "demo_repo"

_IGNORE = shutil.ignore_patterns("__pycache__", ".git", ".pytest_cache", ".ruff_cache")


@dataclass(frozen=True)
class SubstrateSpec:
    """A source tree the benchmark can run against."""

    name: str
    path: Path

    def __post_init__(self) -> None:
        if not self.path.is_dir():
            raise FileNotFoundError(f"substrate source not found: {self.path}")


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


@contextmanager
def checkout(source: Path, git_init: bool = True) -> Iterator[Path]:
    """Copy `source` into a temp dir, optionally git-init it, yield the copy.

    The temp dir is removed on exit. The yielded path is the copied tree root
    (so relative paths like ``orders.py`` resolve directly under it).
    """
    tmp = Path(tempfile.mkdtemp(prefix="refactorika-sub-"))
    dst = tmp / source.name
    shutil.copytree(source, dst, ignore=_IGNORE)
    if git_init:
        _git(["init", "-q"], dst)
        _git(["add", "-A"], dst)
        _git(
            [
                "-c",
                "user.email=eval@refactorika.local",
                "-c",
                "user.name=refactorika-eval",
                "commit",
                "-q",
                "-m",
                "substrate base",
            ],
            dst,
        )
    try:
        yield dst
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def available_substrates() -> list[SubstrateSpec]:
    """Substrates present in this checkout. Phase 0: demo_repo only."""
    specs: list[SubstrateSpec] = []
    if DEMO_REPO.is_dir():
        specs.append(SubstrateSpec("demo_repo", DEMO_REPO))
    return specs
