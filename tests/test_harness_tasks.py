from eval.harness_bench import aggregate
from eval.harness_tasks import TASKS, bad_patches, baseline_files, good_patch, heldout_test


def test_task_manifest_is_complete() -> None:
    assert len(TASKS) == 10
    assert len({task.name for task in TASKS}) == len(TASKS)
    for task in TASKS:
        assert set(good_patch(task)) == {"app/service.py", "app/caller.py"}
        assert set(bad_patches(task)) == {
            "behavior",
            "missed_caller",
            "gate_defect",
            "heldout_boundary",
        }
        assert "tests/oracle" not in baseline_files(task)
        assert "test_boundary" in heldout_test(task)


def test_aggregate_reports_catches_and_false_rejections() -> None:
    records = [
        {
            "task": "a",
            "trial": 0,
            "arm": "off",
            "landed": True,
            "oracle_pass": False,
            "correct_landed": False,
            "regression_shipped": True,
            "status": "shipped",
            "tokens": 1,
            "seconds": 1.0,
            "retries": 0,
        },
        {
            "task": "a",
            "trial": 0,
            "arm": "on",
            "landed": False,
            "oracle_pass": None,
            "correct_landed": False,
            "regression_shipped": False,
            "status": "skipped-needs-human",
            "tokens": 2,
            "seconds": 2.0,
            "retries": 2,
        },
        {
            "task": "b",
            "trial": 0,
            "arm": "off",
            "landed": True,
            "oracle_pass": True,
            "correct_landed": True,
            "regression_shipped": False,
            "status": "shipped",
            "tokens": 1,
            "seconds": 1.0,
            "retries": 0,
        },
        {
            "task": "b",
            "trial": 0,
            "arm": "on",
            "landed": True,
            "oracle_pass": True,
            "correct_landed": True,
            "regression_shipped": False,
            "status": "committed",
            "tokens": 1,
            "seconds": 1.0,
            "retries": 0,
        },
    ]
    result = aggregate(records)
    assert result["safety"]["catch_rate"] == 1.0
    assert result["safety"]["false_rejection_rate"] == 0.0
