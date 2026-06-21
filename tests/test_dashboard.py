"""Targeted tests for v3 dashboard rendering (render_audit / render_plan / render_campaign)."""

from refactorika.dashboard import render_audit, render_campaign, render_plan


def _sample_audit(total: int = 3) -> dict:
    return {
        "repo": "demo",
        "files_scanned": 2,
        "total_opportunities": total,
        "by_kind": {"long_function": 2, "deep_nesting": 1},
        "dominant_finding": "long functions dominate",
        "entries": [
            {
                "file": "src/god.py",
                "score": 9,
                "opportunities": [
                    {"kind": "long_function", "location": "L1", "detail": "120 lines", "rank": 1},
                    {"kind": "deep_nesting", "location": "L40", "detail": "depth 5", "rank": 2},
                ],
            },
            {
                "file": "src/util.py",
                "score": 3,
                "opportunities": [
                    {"kind": "long_function", "location": "L1", "detail": "60 lines", "rank": 1},
                ],
            },
        ],
    }


def _sample_plan(confirmed: bool, decision: str | None = None) -> dict:
    return {
        "repo": "demo",
        "dominant_finding": "long functions dominate",
        "confirmed": confirmed,
        "decision": decision,
        "tasks": [
            {
                "file": "src/util.py",
                "opportunities": [{"kind": "long_function"}],
                "dependents": [],
                "order": 1,
            },
            {
                "file": "src/god.py",
                "opportunities": [{"kind": "long_function"}, {"kind": "deep_nesting"}],
                "dependents": ["src/util.py"],
                "order": 2,
            },
        ],
    }


def _sample_log() -> list[dict]:
    return [
        {
            "file": "src/god.py",
            "refactor_kind": "extract_function",
            "checks": {"parse": True, "lint": True, "typecheck": True, "tests": True},
            "retries": 0,
            "status": "committed",
            "failure_reason": None,
        }
    ]


def test_render_audit_nonempty():
    out = render_audit(_sample_audit())
    assert isinstance(out, str)
    assert out
    assert "src/god.py".split("/")[-1] in out


def test_render_plan_confirmed():
    out = render_plan(_sample_plan(True, "approved by user"))
    assert isinstance(out, str)
    assert out
    assert "CONFIRMED" in out
    assert "UNCONFIRMED" not in out


def test_render_plan_unconfirmed():
    out = render_plan(_sample_plan(False))
    assert isinstance(out, str)
    assert out
    assert "UNCONFIRMED" in out


def test_render_campaign_health_delta():
    before = _sample_audit(total=10)
    after = _sample_audit(total=4)
    after["entries"][0]["score"] = 1  # god.py improved
    out = render_campaign(before, _sample_plan(True), _sample_log(), after)
    assert isinstance(out, str)
    assert out
    assert "→" in out
    assert "%" in out
    assert "HEALTH" in out


def test_empty_inputs_do_not_crash():
    empty_audit = {"repo": "demo", "files_scanned": 0, "total_opportunities": 0, "by_kind": {}, "dominant_finding": None, "entries": []}
    empty_plan = {"repo": "demo", "dominant_finding": None, "tasks": [], "confirmed": False, "decision": None}

    assert isinstance(render_audit(empty_audit), str)
    assert render_audit(empty_audit)
    assert isinstance(render_plan(empty_plan), str)
    assert render_plan(empty_plan)
    assert isinstance(render_campaign(empty_audit, empty_plan, [], empty_audit), str)
    assert render_campaign(empty_audit, empty_plan, [], empty_audit)


def test_fully_empty_dicts_do_not_crash():
    assert isinstance(render_audit({}), str)
    assert isinstance(render_plan({}), str)
    assert isinstance(render_campaign({}, {}, [], {}), str)
