"""Bounded developer tools shared by both agentic benchmark arms.

The tools deliberately expose structured operations instead of a general-purpose
shell.  Every path is confined to the fixture repository and every result is
bounded so a tool response cannot consume the agent's remaining context.
"""

from __future__ import annotations

import fnmatch
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence


@dataclass(frozen=True)
class ToolResult:
    """A serializable result suitable for conversion into a ``ToolEvent``."""

    status: str
    data: Any = None
    error: str | None = None
    error_class: str | None = None
    seconds: float = 0.0
    truncated: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class DeveloperTools:
    """Repository-confined exploration, validation, and mutation operations."""

    def __init__(
        self,
        repo: Path | str,
        *,
        output_limit: int = 20_000,
        max_files: int = 500,
        max_batch_files: int = 20,
        timeout: float = 60.0,
        test_command: Sequence[str] = ("pytest", "-q"),
        lint_command: Sequence[str] = ("ruff", "check", "."),
        typecheck_command: Sequence[str] = ("pyright",),
    ) -> None:
        self.repo = Path(repo).resolve(strict=True)
        if not self.repo.is_dir():
            raise ValueError("repository root must be a directory")
        self.output_limit = max(1, output_limit)
        self.max_files = max(1, max_files)
        self.max_batch_files = max(1, max_batch_files)
        self.timeout = timeout
        self.test_command = tuple(test_command)
        self.lint_command = tuple(lint_command)
        self.typecheck_command = tuple(typecheck_command)

    def _result(self, started: float, *, data: Any = None, **kwargs: Any) -> ToolResult:
        return ToolResult(seconds=time.monotonic() - started, data=data, **kwargs)

    def _error(self, started: float, exc: Exception) -> ToolResult:
        return self._result(
            started,
            status="error",
            error=str(exc),
            error_class=type(exc).__name__,
        )

    def _path(self, relative: str, *, must_exist: bool = True) -> Path:
        if not relative or Path(relative).is_absolute():
            raise ValueError("path must be a non-empty repository-relative path")
        candidate = self.repo / relative
        # Resolve the parent separately for new patch targets, detecting symlink escapes.
        resolved = candidate.resolve(strict=must_exist)
        if not resolved.is_relative_to(self.repo):
            raise ValueError(f"path escapes repository: {relative}")
        return resolved

    @staticmethod
    def _is_hidden_repo_path(path: Path) -> bool:
        return ".git" in path.parts

    @staticmethod
    def _is_test_path(relative: str) -> bool:
        path = Path(relative)
        return any(part in {"test", "tests"} for part in path.parts) or path.name.startswith(
            "test_"
        )

    def _bounded(self, value: str) -> tuple[str, bool]:
        encoded = value.encode("utf-8")
        if len(encoded) <= self.output_limit:
            return value, False
        suffix = "\n...[output truncated]"
        body = encoded[: max(0, self.output_limit - len(suffix.encode()))]
        return body.decode("utf-8", errors="ignore") + suffix, True

    def list_files(self, *, pattern: str = "*") -> ToolResult:
        started = time.monotonic()
        try:
            files = [
                path.relative_to(self.repo).as_posix()
                for path in self.repo.rglob("*")
                if path.is_file()
                and not self._is_hidden_repo_path(path.relative_to(self.repo))
                and fnmatch.fnmatch(path.relative_to(self.repo).as_posix(), pattern)
            ]
            files.sort()
            truncated = len(files) > self.max_files
            return self._result(
                started,
                status="ok",
                data=files[: self.max_files],
                truncated=truncated,
                metadata={"matched": len(files)},
            )
        except Exception as exc:
            return self._error(started, exc)

    def glob_files(self, pattern: str) -> ToolResult:
        return self.list_files(pattern=pattern)

    def read_file(
        self, path: str, *, start_line: int = 1, end_line: int | None = None
    ) -> ToolResult:
        started = time.monotonic()
        try:
            if start_line < 1 or (end_line is not None and end_line < start_line):
                raise ValueError("invalid line range")
            resolved = self._path(path)
            if not resolved.is_file():
                raise ValueError("path is not a file")
            lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
            selected = lines[start_line - 1 : end_line]
            rendered = "\n".join(
                f"{number}: {line}" for number, line in enumerate(selected, start=start_line)
            )
            rendered, truncated = self._bounded(rendered)
            return self._result(
                started,
                status="ok",
                data=rendered,
                truncated=truncated,
                metadata={"path": path, "total_lines": len(lines)},
            )
        except Exception as exc:
            return self._error(started, exc)

    def read_files(self, paths: Sequence[str]) -> ToolResult:
        started = time.monotonic()
        if len(paths) > self.max_batch_files:
            return self._error(started, ValueError("too many files requested"))
        results: dict[str, str] = {}
        for path in paths:
            result = self.read_file(path)
            if not result.ok:
                return self._result(
                    started,
                    status="error",
                    error=f"{path}: {result.error}",
                    error_class=result.error_class,
                )
            results[path] = str(result.data)
        rendered, truncated = self._bounded(
            "\n\n".join(f"## {path}\n{content}" for path, content in results.items())
        )
        return self._result(started, status="ok", data=rendered, truncated=truncated)

    def search_code(self, query: str, *, glob: str | None = None) -> ToolResult:
        started = time.monotonic()
        if not query:
            return self._error(started, ValueError("query must not be empty"))
        command = ["rg", "--line-number", "--color", "never", "--glob", "!.git/**"]
        if glob:
            command.extend(("--glob", glob))
        command.extend(("--", query, "."))
        return self._run(command, started=started, success_codes={0, 1})

    def find_references(self, symbol: str) -> ToolResult:
        if not symbol.isidentifier():
            started = time.monotonic()
            return self._error(started, ValueError("symbol must be a valid identifier"))
        return self.search_code(rf"\b{symbol}\b")

    def git_status(self) -> ToolResult:
        if not (self.repo / ".git").exists():
            return self._result(
                time.monotonic(),
                status="ok",
                data="",
                metadata={"git_repository": False, "benchmark_baseline_clean": True},
            )
        return self._run(("git", "status", "--short"))

    def git_diff(self, *, staged: bool = False) -> ToolResult:
        if not (self.repo / ".git").exists():
            return self._result(
                time.monotonic(),
                status="ok",
                data="",
                metadata={"git_repository": False, "benchmark_baseline_clean": True},
            )
        command = ["git", "diff", "--no-ext-diff"]
        if staged:
            command.append("--cached")
        return self._run(command)

    def run_tests(self, paths: Sequence[str] = ()) -> ToolResult:
        started = time.monotonic()
        try:
            safe_paths = [self._path(path).relative_to(self.repo).as_posix() for path in paths]
        except Exception as exc:
            return self._error(started, exc)
        return self._run((*self.test_command, *safe_paths), started=started)

    def run_lint(self) -> ToolResult:
        return self._run(self.lint_command)

    def run_typecheck(self) -> ToolResult:
        return self._run(self.typecheck_command)

    def _run(
        self,
        command: Sequence[str],
        *,
        started: float | None = None,
        success_codes: set[int] | None = None,
    ) -> ToolResult:
        started = time.monotonic() if started is None else started
        success_codes = {0} if success_codes is None else success_codes
        try:
            pythonpath = str(self.repo)
            if inherited_pythonpath := os.environ.get("PYTHONPATH"):
                pythonpath += os.pathsep + inherited_pythonpath
            completed = subprocess.run(
                list(command),
                cwd=self.repo,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
                env={
                    **os.environ,
                    "GIT_OPTIONAL_LOCKS": "0",
                    "PYTHONPATH": pythonpath,
                },
            )
            output, truncated = self._bounded(completed.stdout + completed.stderr)
            status = "ok" if completed.returncode in success_codes else "error"
            return self._result(
                started,
                status=status,
                data=output,
                error=None if status == "ok" else f"command exited {completed.returncode}",
                error_class=None if status == "ok" else "CommandFailure",
                truncated=truncated,
                metadata={"returncode": completed.returncode, "command": list(command)},
            )
        except subprocess.TimeoutExpired:
            return self._result(
                started,
                status="error",
                error=f"command exceeded {self.timeout:g}s timeout",
                error_class="ToolTimeout",
                metadata={"command": list(command)},
            )
        except Exception as exc:
            return self._error(started, exc)

    def submit_patch(
        self,
        *,
        edits: dict[str, str],
        refactor_kind: str,
        plan_step: str,
    ) -> ToolResult:
        """Atomically apply complete-file edits for the non-harness control arm."""

        started = time.monotonic()
        try:
            if not edits:
                raise ValueError("patch must contain at least one edit")
            if not refactor_kind or not plan_step:
                raise ValueError("refactor_kind and plan_step are required")
            targets: dict[Path, str] = {}
            for relative, content in edits.items():
                if self._is_test_path(relative):
                    raise ValueError(f"test-file mutation is forbidden: {relative}")
                target = self._path(relative, must_exist=False)
                if target.is_dir():
                    raise ValueError(f"edit target is a directory: {relative}")
                targets[target] = content

            # Validation above makes ordinary failures all-or-nothing. Preserve existing
            # bytes as a defensive rollback for exceptional filesystem failures.
            originals = {
                target: target.read_bytes() if target.exists() else None for target in targets
            }
            written: list[Path] = []
            try:
                for target, content in targets.items():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
                    written.append(target)
            except Exception:
                for target in reversed(written):
                    original = originals[target]
                    if original is None:
                        target.unlink(missing_ok=True)
                    else:
                        target.write_bytes(original)
                raise
            changed = sorted(path.relative_to(self.repo).as_posix() for path in targets)
            return self._result(
                started,
                status="ok",
                data={"changed_paths": changed},
                metadata={
                    "refactor_kind": refactor_kind,
                    "plan_step": plan_step,
                    "edit_count": len(changed),
                },
            )
        except Exception as exc:
            return self._error(started, exc)
