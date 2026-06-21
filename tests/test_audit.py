"""Tests for repo-wide audit aggregation."""

from pathlib import Path

from refactorika.analysis.audit import audit_repo
from refactorika.core.storage import Storage

# A file with a deeply-nested function (flatten_nesting) + duplicate import.
MESSY = """\
import os
import os


def deep(a):
    if a:
        if a > 1:
            if a > 2:
                if a > 3:
                    return a
    return 0
"""

CLEAN = """\
def tidy(x: int) -> int:
    return x + 1
"""


def _storage(tmp_path: Path) -> Storage:
    return Storage(redis_url=None, json_path=tmp_path / "state.json")


def test_audit_aggregates_and_ranks(tmp_path: Path) -> None:
    (tmp_path / "messy.py").write_text(MESSY)
    (tmp_path / "clean.py").write_text(CLEAN)

    audit = audit_repo(str(tmp_path), _storage(tmp_path))

    assert audit.files_scanned == 2
    assert audit.total_opportunities >= 1
    # Only the deviating file shows up as an entry.
    assert [e.file for e in audit.entries] == [str(tmp_path / "messy.py")]
    assert audit.entries[0].score == sum(o.rank for o in audit.entries[0].opportunities)
    assert audit.dominant_finding is not None
    assert audit.by_kind  # non-empty kind counts


def test_audit_entries_sorted_by_score(tmp_path: Path) -> None:
    # Two messy files -> entries sorted by score desc.
    (tmp_path / "a.py").write_text(MESSY)
    (tmp_path / "b.py").write_text(MESSY + "\n\ndef extra(b):\n    return b\n")
    audit = audit_repo(str(tmp_path), _storage(tmp_path))
    scores = [e.score for e in audit.entries]
    assert scores == sorted(scores, reverse=True)


def test_audit_empty_repo(tmp_path: Path) -> None:
    (tmp_path / "clean.py").write_text(CLEAN)
    audit = audit_repo(str(tmp_path), _storage(tmp_path))
    assert audit.entries == []
    assert audit.dominant_finding is None
    assert audit.total_opportunities == 0
