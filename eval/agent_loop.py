"""A minimal tool-using (multi-turn) refactoring agent — a mini SWE-agent.

Unlike the single-shot proposers, this agent drives itself over many turns using
tools (read_file, grep, list_dir, edit_file, finish), so it can EXPLORE a repo and
ITERATE — the regime RefactorBench is designed for. The edit path is pluggable via
an `apply_edit` callback so the verification harness can sit in different places:

  - off          : edits write straight to the working copy (no gates).
  - per_edit     : each edit_file goes through the gate stack; a rejected edit is
                   rolled back and the failure is returned to the agent mid-loop.
                   (RefactorBench paper §5: this tends to backfire.)
  - atomic_final : edits write freely; the gate stack validates the COMPLETE final
                   state once, at finish. (Our hypothesis: safety without backfire.)

This module only implements the loop + tools + `off` behaviour via the default
callback. Gate modes are supplied by the benchmark that drives it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from eval.proposers import Usage, _env  # noqa: PLC0415 reuse env loader

_SKIP_DIRS = {".git", "__pycache__", "_rb", ".venv", "node_modules", ".tox"}
_MAX_READ_CHARS = 20000
_MAX_GREP_HITS = 60

TOOLS = [
    {
        "name": "read_file",
        "description": "Read a UTF-8 text file in the repo. Returns its contents.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "repo-relative path"}},
            "required": ["path"],
        },
    },
    {
        "name": "grep",
        "description": "Search the repo for a substring or regex. Returns matching path:line: text.",
        "input_schema": {
            "type": "object",
            "properties": {"pattern": {"type": "string"}},
            "required": ["pattern"],
        },
    },
    {
        "name": "list_dir",
        "description": "List files and subdirectories of a repo-relative directory.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "repo-relative dir, '' for root"}},
            "required": ["path"],
        },
    },
    {
        "name": "edit_file",
        "description": ("Replace the FIRST exact occurrence of old_str with new_str in a file. "
                        "old_str must match the file verbatim (include enough context to be unique). "
                        "To create content, old_str may be empty to append."),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_str": {"type": "string"},
                "new_str": {"type": "string"},
            },
            "required": ["path", "old_str", "new_str"],
        },
    },
    {
        "name": "finish",
        "description": "Call when the refactor is complete across all files.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


@dataclass
class LoopResult:
    finished: bool
    turns: int
    usage: Usage
    edited_files: set[str] = field(default_factory=set)
    edit_count: int = 0
    rejected_edits: int = 0
    transcript_tail: str = ""


# An apply_edit callback: (workdir, path, old, new) -> (ok, message).
ApplyEdit = Callable[[Path, str, str, str], tuple[bool, str]]


def default_apply_edit(workdir: Path, path: str, old: str, new: str) -> tuple[bool, str]:
    """`off` mode: write straight to the working copy, no gates."""
    f = workdir / path
    if not f.exists():
        if old == "":
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(new)
            return True, f"created {path}"
        return False, f"file not found: {path}"
    text = f.read_text()
    if old == "":
        f.write_text(text + new)
        return True, f"appended to {path}"
    if old not in text:
        return False, f"old_str not found in {path} (must match verbatim)"
    f.write_text(text.replace(old, new, 1))
    return True, f"edited {path}"


class ToolAgent:
    """Drives Claude over multiple tool-use turns against a working copy."""

    def __init__(self, model: str = "claude-sonnet-4-5-20250929",
                 api_key: Optional[str] = None, max_turns: int = 22,
                 max_tokens: int = 4096, log: Optional[Callable[[str], None]] = None) -> None:
        import anthropic  # noqa: PLC0415
        self.model = model
        self.max_turns = max_turns
        self.max_tokens = max_tokens
        self._log = log or (lambda _m: None)
        self._client = anthropic.Anthropic(api_key=api_key or _env("ANTHROPIC_API_KEY"))

    # --- tool implementations --------------------------------------------
    def _read_file(self, workdir: Path, path: str) -> str:
        f = workdir / path
        if not f.exists() or not f.is_file():
            return f"ERROR: no such file: {path}"
        text = f.read_text(errors="replace")
        return text[:_MAX_READ_CHARS] + ("\n...[truncated]" if len(text) > _MAX_READ_CHARS else "")

    def _grep(self, workdir: Path, pattern: str) -> str:
        try:
            rx = re.compile(pattern)
        except re.error:
            rx = re.compile(re.escape(pattern))
        hits = []
        for f in workdir.rglob("*.py"):
            if any(p in _SKIP_DIRS for p in f.parts):
                continue
            try:
                for i, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
                    if rx.search(line):
                        hits.append(f"{f.relative_to(workdir)}:{i}: {line.strip()[:160]}")
                        if len(hits) >= _MAX_GREP_HITS:
                            return "\n".join(hits) + "\n...[more hits truncated]"
            except OSError:
                continue
        return "\n".join(hits) if hits else "no matches"

    def _list_dir(self, workdir: Path, path: str) -> str:
        d = workdir / path if path else workdir
        if not d.exists() or not d.is_dir():
            return f"ERROR: no such directory: {path}"
        items = []
        for p in sorted(d.iterdir()):
            if p.name in _SKIP_DIRS:
                continue
            items.append(p.name + ("/" if p.is_dir() else ""))
        return "\n".join(items) or "(empty)"

    # --- the loop --------------------------------------------------------
    def run(self, instruction: str, workdir: Path,
            apply_edit: ApplyEdit = default_apply_edit) -> LoopResult:
        system = (
            "You are an expert Python refactoring agent working in a real repository. "
            "Use the tools to explore the repo, then make ALL edits required to complete "
            "the refactor across every affected file (definitions AND call sites AND "
            "imports/exports). Preserve existing behavior. When fully done, call finish. "
            "Do not edit test files unless the task requires it."
        )
        messages = [{"role": "user", "content": f"# Refactoring task\n{instruction}\n\n"
                     "The repository root is your working directory. Begin by exploring."}]
        usage = Usage()
        edited: set[str] = set()
        edits = rejected = 0
        finished = False
        turns = 0
        for turn in range(self.max_turns):
            turns = turn + 1
            resp = self._client.messages.create(
                model=self.model, max_tokens=self.max_tokens, system=system,
                tools=TOOLS, messages=messages,
            )
            usage.prompt_tokens += resp.usage.input_tokens
            usage.completion_tokens += resp.usage.output_tokens
            tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
            text_bits = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
            if text_bits:
                self._log(f"turn {turns}: {text_bits[0][:120]}")
            messages.append({"role": "assistant", "content": resp.content})
            if not tool_uses:
                break  # agent stopped without a tool call
            results = []
            for tu in tool_uses:
                name, args = tu.name, tu.input
                if name == "finish":
                    finished = True
                    out = "ok"
                elif name == "read_file":
                    out = self._read_file(workdir, args.get("path", ""))
                elif name == "grep":
                    out = self._grep(workdir, args.get("pattern", ""))
                elif name == "list_dir":
                    out = self._list_dir(workdir, args.get("path", ""))
                elif name == "edit_file":
                    ok, msg = apply_edit(workdir, args.get("path", ""),
                                         args.get("old_str", ""), args.get("new_str", ""))
                    out = ("OK: " if ok else "REJECTED: ") + msg
                    if ok:
                        edits += 1
                        edited.add(args.get("path", ""))
                    else:
                        rejected += 1
                    self._log(f"  edit {args.get('path','')}: {'ok' if ok else 'REJECTED'} ({msg})")
                else:
                    out = f"ERROR: unknown tool {name}"
                results.append({"type": "tool_result", "tool_use_id": tu.id,
                                "content": out[:_MAX_READ_CHARS]})
            messages.append({"role": "user", "content": results})
            if finished:
                break
        return LoopResult(finished=finished, turns=turns, usage=usage,
                          edited_files=edited, edit_count=edits, rejected_edits=rejected)
