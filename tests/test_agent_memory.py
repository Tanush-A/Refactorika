"""Tests for AgentMemory: put/get round-trips, history filter, cross-session persistence."""

from pathlib import Path

from refactorika.core.schema import ExportRef, ModuleContext
from refactorika.core.storage import Storage
from refactorika.memory.agent_memory import AgentMemory


def _ctx(path: str = "svc/foo.py") -> ModuleContext:
    return ModuleContext(
        path=path,
        purpose_hint="Test module",
        exports=[ExportRef(name="do_thing", kind="function", signature="do_thing(x) -> int")],
        dependents=["api/main.py"],
        flagged=["line 10: bare except"],
        changed_since_last=[],
        decisions=["Use integers, not floats"],
    )


def _make(tmp_path: Path) -> AgentMemory:
    storage = Storage(redis_url=None, json_path=tmp_path / "state.json")
    return AgentMemory(storage)


def test_put_get_round_trip(tmp_path: Path) -> None:
    mem = _make(tmp_path)
    ctx = _ctx()
    mem.put_context("svc.foo", ctx)
    got = mem.get_context("svc.foo")
    assert got is not None
    assert got.purpose_hint == "Test module"
    assert got.exports[0].name == "do_thing"
    assert "api/main.py" in got.dependents


def test_get_missing_returns_none(tmp_path: Path) -> None:
    mem = _make(tmp_path)
    assert mem.get_context("nonexistent.module") is None


def test_history_filter_by_file(tmp_path: Path) -> None:
    storage = Storage(redis_url=None, json_path=tmp_path / "state.json")
    storage.append_log({"file": "a.py", "files": ["a.py"], "status": "committed"})
    storage.append_log({"file": "b.py", "files": ["b.py"], "status": "rolled-back"})
    mem = AgentMemory(storage)
    hist = mem.history(file="a.py")
    assert len(hist) == 1
    assert hist[0]["file"] == "a.py"


def test_all_history_no_filter(tmp_path: Path) -> None:
    storage = Storage(redis_url=None, json_path=tmp_path / "state.json")
    storage.append_log({"file": "a.py", "files": ["a.py"], "status": "committed"})
    storage.append_log({"file": "b.py", "files": ["b.py"], "status": "committed"})
    mem = AgentMemory(storage)
    assert len(mem.history()) == 2


def test_cross_session_persistence(tmp_path: Path) -> None:
    """Two AgentMemory instances on the same json_path share context."""
    p = tmp_path / "state.json"
    s1 = Storage(redis_url=None, json_path=p)
    mem1 = AgentMemory(s1)
    mem1.put_context("mymod", _ctx("mymod.py"))

    s2 = Storage(redis_url=None, json_path=p)
    mem2 = AgentMemory(s2)
    got = mem2.get_context("mymod")
    assert got is not None
    assert got.purpose_hint == "Test module"


def test_md_file_written(tmp_path: Path) -> None:
    """put_context writes a .md file to .refactorika/context/."""
    import os  # noqa: PLC0415
    orig_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        mem = _make(tmp_path)
        mem.put_context("svc.foo", _ctx())
        md = tmp_path / ".refactorika" / "context" / "svc.foo.md"
        assert md.exists()
        content = md.read_text()
        assert "do_thing" in content
    finally:
        os.chdir(orig_cwd)
