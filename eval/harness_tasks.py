"""Controlled error-handling tasks and their calibration patches.

The substrate is generated from these immutable specifications. Held-out tests
are returned separately and are never materialized until oracle grading.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TaskSpec:
    name: str
    function: str
    argument: str
    invalid: str
    calculation: str
    normal_input: int
    expected: int
    invalid_input: int
    fallback: int
    boundary_input: int
    boundary_expected: int

    @property
    def instruction(self) -> str:
        return (
            f"Convert app.service.{self.function} from raising ValueError to returning Result. "
            "Update every caller to consume Result explicitly. Preserve all behavior and public "
            "return values. Make the complete multi-file change."
        )


TASKS: tuple[TaskSpec, ...] = (
    TaskSpec(
        "withdraw", "withdraw", "amount", "amount <= 0", "100 - amount", 20, 80, 0, 100, 1, 99
    ),
    TaskSpec("reserve", "reserve", "qty", "qty <= 0", "50 - qty", 5, 45, 0, 50, 1, 49),
    TaskSpec(
        "discount",
        "discount",
        "subtotal",
        "subtotal < 0",
        "subtotal * 90 // 100",
        200,
        180,
        -1,
        0,
        0,
        0,
    ),
    TaskSpec(
        "parse_port",
        "parse_port",
        "port",
        "port < 1 or port > 65535",
        "port",
        8080,
        8080,
        0,
        80,
        65535,
        65535,
    ),
    TaskSpec("page_end", "page_end", "offset", "offset < 0", "offset + 25", 10, 35, -1, 0, 0, 25),
    TaskSpec(
        "retry_delay", "retry_delay", "attempts", "attempts < 1", "attempts * 2", 3, 6, 0, 1, 1, 2
    ),
    TaskSpec("quota_left", "quota_left", "used", "used < 0", "100 - used", 30, 70, -1, 100, 0, 100),
    TaskSpec("shipping", "shipping", "weight", "weight <= 0", "weight * 5", 4, 20, 0, 0, 1, 5),
    TaskSpec("score", "score", "correct", "correct < 0", "correct * 10", 7, 70, -1, 0, 0, 0),
    TaskSpec(
        "batch_count", "batch_count", "size", "size <= 0", "100 // size", 10, 10, 0, 0, 1, 100
    ),
)


RESULT_MODULE = """from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Result:
    value: int | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None
"""


def baseline_files(task: TaskSpec) -> dict[str, str]:
    service = f"""def {task.function}({task.argument}: int) -> int:
    if {task.invalid}:
        raise ValueError("invalid {task.argument}")
    return {task.calculation}
"""
    caller = f"""from app.service import {task.function}


def run({task.argument}: int) -> int:
    try:
        return {task.function}({task.argument})
    except ValueError:
        return {task.fallback}
"""
    visible = f"""from app.caller import run


def test_normal() -> None:
    assert run({task.normal_input}) == {task.expected}


def test_invalid() -> None:
    assert run({task.invalid_input}) == {task.fallback}
"""
    return {
        "app/__init__.py": "",
        "app/result.py": RESULT_MODULE,
        "app/service.py": service,
        "app/caller.py": caller,
        "tests/__init__.py": "",
        "tests/gate/test_behavior.py": visible,
        "pyrightconfig.json": '{"include":["app"],"typeCheckingMode":"strict"}\n',
    }


def heldout_test(task: TaskSpec) -> str:
    return f"""from app.caller import run


def test_boundary() -> None:
    assert run({task.boundary_input}) == {task.boundary_expected}
"""


def good_patch(task: TaskSpec) -> dict[str, str]:
    service = f"""from app.result import Result


def {task.function}({task.argument}: int) -> Result:
    if {task.invalid}:
        return Result(error="invalid {task.argument}")
    return Result(value={task.calculation})
"""
    caller = f"""from app.service import {task.function}


def run({task.argument}: int) -> int:
    result = {task.function}({task.argument})
    if not result.ok or result.value is None:
        return {task.fallback}
    return result.value
"""
    return {"app/service.py": service, "app/caller.py": caller}


def bad_patches(task: TaskSpec) -> dict[str, dict[str, str]]:
    good = good_patch(task)
    behavior = dict(good)
    behavior["app/service.py"] = behavior["app/service.py"].replace(
        f"Result(value={task.calculation})", f"Result(value=({task.calculation}) + 1)"
    )

    missed_caller = {"app/service.py": good["app/service.py"]}

    # A deliberately held-out-only defect: normal and visible invalid examples
    # still pass, but the oracle's boundary value is off by one.
    boundary = dict(good)
    boundary["app/service.py"] = boundary["app/service.py"].replace(
        f"Result(value={task.calculation})",
        f"Result(value=(({task.calculation}) + 1) if {task.argument} == "
        f"{task.boundary_input} else ({task.calculation}))",
    )

    gate_defect = dict(good)
    index = TASKS.index(task) % 3
    if index == 0:
        gate_defect["app/service.py"] += "\ndef broken(:\n    pass\n"
    elif index == 1:
        gate_defect["app/service.py"] = gate_defect["app/service.py"].replace(
            f"Result(value={task.calculation})", "Result(value=undefined_name)"
        )
    else:
        gate_defect["app/caller.py"] = gate_defect["app/caller.py"].replace(
            "return result.value", 'return "wrong"'
        )
    return {
        "behavior": behavior,
        "missed_caller": missed_caller,
        "gate_defect": gate_defect,
        "heldout_boundary": boundary,
    }


def materialize(task: TaskSpec, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    for relative, content in baseline_files(task).items():
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return destination
