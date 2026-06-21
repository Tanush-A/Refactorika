"""Real-agent proposers (Phase 1).

A "model" is a proposer configured with a model id pointed at an OpenAI-compatible
endpoint. Default target is a **local** server (Ollama / LM Studio / vLLM) so the
benchmark runs offline, free, and reproducibly (temperature 0 + fixed seed).

Only the standard library is used (urllib) so no extra dependency is needed in the
eval venv. The endpoint must implement POST /v1/chat/completions and return a
`usage` block (Ollama does) so token/cost numbers are real, not estimated.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_MODEL = "qwen2.5-coder:7b"
DEFAULT_TIMEOUT = 300.0

_CODE_BLOCK = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)
_SR_BLOCK = re.compile(
    r"<<<<<<< SEARCH\s*?\n(.*?)\n?=======\s*?\n(.*?)\n?>>>>>>> REPLACE", re.DOTALL
)
# A file-tagged search/replace block: "### FILE: path" then one SR block.
_FILE_SR_BLOCK = re.compile(
    r"###\s*FILE:\s*(?P<path>\S+)\s*?\n"
    r"<<<<<<< SEARCH\s*?\n(?P<search>.*?)\n?=======\s*?\n(?P<replace>.*?)\n?>>>>>>> REPLACE",
    re.DOTALL,
)


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, other: "Usage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens


@dataclass
class Proposal:
    content: Optional[str]  # full new file contents, or None if unparseable/errored
    usage: Usage
    seconds: float
    raw: str = field(default="", repr=False)
    error: Optional[str] = None  # set if the model call failed (timeout/connection)


@dataclass
class Patch:
    content: Optional[str]  # file content after applying blocks, or None on failure
    usage: Usage
    seconds: float
    blocks_total: int = 0
    blocks_applied: int = 0
    error: Optional[str] = None
    raw: str = field(default="", repr=False)


@dataclass
class MultiPatch:
    edits: dict[str, str]  # path -> new content (only files actually changed)
    usage: Usage
    seconds: float
    blocks_total: int = 0
    blocks_applied: int = 0
    error: Optional[str] = None
    raw: str = field(default="", repr=False)


def _apply_search_replace(content: str, blocks: list[tuple[str, str]]) -> tuple[str, int]:
    """Apply SEARCH/REPLACE blocks via exact first-match substitution."""
    applied = 0
    for search, replace in blocks:
        if search and search in content:
            content = content.replace(search, replace, 1)
            applied += 1
    return content, applied


def _extract_code(text: str) -> Optional[str]:
    """Pull the file body out of the model's reply: prefer a fenced block, else
    fall back to the whole reply if it looks like code."""
    blocks = _CODE_BLOCK.findall(text)
    if blocks:
        return max(blocks, key=len).strip() + "\n"
    stripped = text.strip()
    if stripped.startswith(("import ", "from ", "def ", "class ", '"""', "#")):
        return stripped + "\n"
    return None


class ProposerMixin:
    """Edit-proposing logic shared by every backend. Subclasses provide:
    `_chat(messages) -> (text, Usage)`, the `id` property, `available()`, and
    `_call_errors` (exception types that mean a recoverable call failure)."""

    _call_errors: tuple = (Exception,)

    def _safe_chat(self, messages: list[dict]) -> tuple[Optional[str], Usage, float, Optional[str]]:
        t0 = time.time()
        try:
            text, usage = self._chat(messages)  # type: ignore[attr-defined]
        except self._call_errors as exc:
            return None, Usage(), round(time.time() - t0, 1), f"{type(exc).__name__}: {exc}"
        return text, usage, round(time.time() - t0, 1), None

    def propose_edit(self, instruction: str, file_path: str, file_content: str,
                     failure_reason: Optional[str] = None) -> Proposal:
        system = (
            "You are a precise Python refactoring agent. You are given one file and a "
            "refactoring instruction. Rewrite the ENTIRE file applying ONLY that change, "
            "preserving all existing behavior and public APIs. Return the complete new file "
            "in a single ```python code block and nothing else."
        )
        user = f"# Instruction\n{instruction}\n\n# File: {file_path}\n```python\n{file_content}```\n"
        if failure_reason:
            user += (f"\n# Your previous attempt was REJECTED by the verification harness:\n"
                     f"{failure_reason}\n\nFix the problem and return the full corrected file.")
        text, usage, secs, err = self._safe_chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}])
        if err is not None:
            return Proposal(content=None, usage=usage, seconds=secs, error=err)
        return Proposal(content=_extract_code(text), usage=usage, seconds=secs, raw=text)

    def propose_patch(self, instruction: str, file_path: str, file_content: str,
                      failure_reason: Optional[str] = None) -> Patch:
        system = (
            "You are a precise Python refactoring agent. Given one file and an instruction, "
            "output ONLY minimal edits as SEARCH/REPLACE blocks — never the whole file. "
            "Each block is exactly:\n<<<<<<< SEARCH\n<exact existing lines>\n=======\n"
            "<replacement lines>\n>>>>>>> REPLACE\nThe SEARCH text must match the file verbatim. "
            "Emit one block per distinct change. Output nothing except the blocks."
        )
        user = f"# Instruction\n{instruction}\n\n# File: {file_path}\n```python\n{file_content}```\n"
        if failure_reason:
            user += (f"\n# Your previous attempt was REJECTED:\n{failure_reason}\n\n"
                     "Return corrected SEARCH/REPLACE blocks.")
        text, usage, secs, err = self._safe_chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}])
        if err is not None:
            return Patch(content=None, usage=usage, seconds=secs, error=err)
        blocks = _SR_BLOCK.findall(text)
        if not blocks:
            return Patch(content=None, usage=usage, seconds=secs, error="no SEARCH/REPLACE blocks", raw=text)
        new_content, applied = _apply_search_replace(file_content, blocks)
        if applied == 0:
            return Patch(content=None, usage=usage, seconds=secs, blocks_total=len(blocks),
                         error="no SEARCH block matched the file", raw=text)
        return Patch(content=new_content, usage=usage, seconds=secs,
                     blocks_total=len(blocks), blocks_applied=applied, raw=text)

    def propose_multi_patch(self, instruction: str, files: dict[str, str],
                            failure_reason: Optional[str] = None) -> MultiPatch:
        """Edit across several files. `files` is {path: content}. The model returns
        file-tagged SEARCH/REPLACE blocks; we apply each to its file."""
        system = (
            "You are a precise multi-file Python refactoring agent. You are given several "
            "files and one instruction. Apply the instruction across ALL files that need it "
            "(e.g. the definition AND every call site), using minimal edits only. For each "
            "change output a file-tagged block exactly:\n"
            "### FILE: <path>\n<<<<<<< SEARCH\n<exact existing lines>\n=======\n"
            "<replacement lines>\n>>>>>>> REPLACE\n"
            "Use the exact paths given. SEARCH must match verbatim. Output only blocks."
        )
        joined = "\n\n".join(f"### FILE: {p}\n```python\n{c}```" for p, c in files.items())
        user = f"# Instruction\n{instruction}\n\n# Files\n{joined}\n"
        if failure_reason:
            user += (f"\n# Your previous attempt was REJECTED:\n{failure_reason}\n\n"
                     "Return corrected file-tagged SEARCH/REPLACE blocks.")
        text, usage, secs, err = self._safe_chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}])
        if err is not None:
            return MultiPatch(edits={}, usage=usage, seconds=secs, error=err)
        matches = list(_FILE_SR_BLOCK.finditer(text))
        if not matches:
            return MultiPatch(edits={}, usage=usage, seconds=secs,
                              error="no file-tagged SEARCH/REPLACE blocks", raw=text)
        working = dict(files)
        applied = 0
        for m in matches:
            path, search, replace = m.group("path"), m.group("search"), m.group("replace")
            if path in working and search and search in working[path]:
                working[path] = working[path].replace(search, replace, 1)
                applied += 1
        edits = {p: c for p, c in working.items() if c != files[p]}
        if applied == 0 or not edits:
            return MultiPatch(edits={}, usage=usage, seconds=secs, blocks_total=len(matches),
                              error="no SEARCH block matched its file", raw=text)
        return MultiPatch(edits=edits, usage=usage, seconds=secs,
                          blocks_total=len(matches), blocks_applied=applied, raw=text)


class LocalAgentProposer(ProposerMixin):
    """Calls an OpenAI-compatible chat endpoint to produce edits."""

    _call_errors = (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError)

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        temperature: float = 0.0,
        seed: int = 7,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.seed = seed
        self.timeout = timeout

    @property
    def id(self) -> str:
        return self.model

    # --- connectivity -----------------------------------------------------
    def available(self) -> bool:
        try:
            req = urllib.request.Request(self.base_url.replace("/v1", "") + "/api/version")
            with urllib.request.urlopen(req, timeout=5):
                return True
        except Exception:  # noqa: BLE001
            return False

    # --- core call --------------------------------------------------------
    def _chat(self, messages: list[dict]) -> tuple[str, Usage]:
        body = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
                "seed": self.seed,
                "stream": False,
            }
        ).encode()
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read())
        text = data["choices"][0]["message"]["content"]
        u = data.get("usage") or {}
        usage = Usage(
            prompt_tokens=int(u.get("prompt_tokens", 0)),
            completion_tokens=int(u.get("completion_tokens", 0)),
        )
        return text, usage


class AnthropicProposer(ProposerMixin):
    """Drives Claude via the Anthropic Messages API. Captures real token usage so
    cost numbers are exact. Reads ANTHROPIC_API_KEY from the environment."""

    DEFAULT_MODEL = "claude-sonnet-4-5-20250929"

    def __init__(self, model: Optional[str] = None, api_key: Optional[str] = None,
                 temperature: float = 0.0, max_tokens: int = 8000) -> None:
        import anthropic  # noqa: PLC0415 — optional dependency

        self.model = model or self.DEFAULT_MODEL
        self._api_key = api_key or _env("ANTHROPIC_API_KEY")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._call_errors = (anthropic.APIError, anthropic.APIConnectionError, TimeoutError, OSError)
        self._client = anthropic.Anthropic(api_key=self._api_key) if self._api_key else None

    @property
    def id(self) -> str:
        return self.model

    def available(self) -> bool:
        return bool(self._api_key)

    def _chat(self, messages: list[dict]) -> tuple[str, Usage]:
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        convo = [{"role": m["role"], "content": m["content"]}
                 for m in messages if m["role"] != "system"]
        resp = self._client.messages.create(
            model=self.model, max_tokens=self.max_tokens, temperature=self.temperature,
            system=system, messages=convo,
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        usage = Usage(prompt_tokens=resp.usage.input_tokens,
                      completion_tokens=resp.usage.output_tokens)
        return text, usage


def _env(name: str) -> Optional[str]:
    """Read an env var, loading .env from the repo root on first use."""
    import os  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    if name in os.environ:
        return os.environ[name]
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    return os.environ.get(name)


def make_proposer(provider: str, model: Optional[str] = None, **kwargs):
    """Factory: provider in {'local', 'anthropic'}."""
    if provider == "anthropic":
        return AnthropicProposer(model=model, **kwargs)
    return LocalAgentProposer(model=model or DEFAULT_MODEL,
                              **{k: v for k, v in kwargs.items()
                                 if k in {"base_url", "temperature", "seed", "timeout"}})
