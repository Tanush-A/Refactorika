"""Cross-session agent memory: module context + refactor history."""

from __future__ import annotations

import json
from pathlib import Path

from refactorika.core.schema import EditRecord, ModuleContext
from refactorika.core.storage import Storage

_CTX_KEY = "refactorika:memory:context"


class AgentMemory:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    # --- module context -------------------------------------------------------

    def put_context(self, module: str, ctx: ModuleContext) -> None:
        """Persist a ModuleContext for a module (survives sessions)."""
        payload = json.dumps(ctx.to_dict())
        if self._storage._redis:
            self._storage._redis.hset(_CTX_KEY, module, payload)
        else:
            data = self._storage._read_state()
            data.setdefault("context", {})[module] = ctx.to_dict()
            self._storage._write_state(data)
        self._write_md(module, ctx)

    def get_context(self, module: str) -> ModuleContext | None:
        """Retrieve a prior ModuleContext, or None on cold cache."""
        if self._storage._redis:
            raw = self._storage._redis.hget(_CTX_KEY, module)
            if raw:
                return ModuleContext.from_dict(json.loads(raw))
        else:
            data = self._storage._read_state()
            entry = data.get("context", {}).get(module)
            if entry:
                return ModuleContext.from_dict(entry)
        return None

    def all_contexts(self) -> dict[str, ModuleContext]:
        if self._storage._redis:
            raw = self._storage._redis.hgetall(_CTX_KEY)
            return {k: ModuleContext.from_dict(json.loads(v)) for k, v in raw.items()}
        data = self._storage._read_state()
        return {
            k: ModuleContext.from_dict(v)
            for k, v in data.get("context", {}).items()
        }

    # --- refactor history ----------------------------------------------------

    def history(self, file: str | None = None) -> list[dict]:
        """Return edit records (optionally filtered by file path)."""
        log = self._storage.get_log()
        if file is None:
            return log
        return [r for r in log if file in r.get("files", [r.get("file", "")])]

    # --- .md file sidebar ----------------------------------------------------

    def _write_md(self, module: str, ctx: ModuleContext) -> None:
        ctx_dir = Path(".refactorika/context")
        ctx_dir.mkdir(parents=True, exist_ok=True)
        slug = module.replace("/", ".").removesuffix(".py")
        md_path = ctx_dir / f"{slug}.md"
        exports_block = "\n".join(
            f"- `{e.name}` ({e.kind}): `{e.signature}`" for e in ctx.exports
        ) or "_(none detected)_"
        dependents_block = "\n".join(f"- {d}" for d in ctx.dependents) or "_(none detected)_"
        flagged_block = "\n".join(f"- {f}" for f in ctx.flagged) or "_(none detected)_"
        decisions_block = "\n".join(f"- {d}" for d in ctx.decisions) or "<!-- claude: fill -->"
        text = f"""# {slug}

## Purpose
{ctx.purpose_hint or "<!-- claude: fill -->"}

## Exports
{exports_block}

## Dependents
{dependents_block}

## Flagged patterns
{flagged_block}

## Decisions / Why
{decisions_block}
"""
        md_path.write_text(text)
