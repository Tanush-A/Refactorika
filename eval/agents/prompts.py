"""Prompt contracts shared by the full-system benchmark arms."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

FOUR_ARM_CONTRACT = """Primary benchmark arms:
- off: one model call, no harness
- on: one model call, Refactorika harness context and verification
- agentic: shared multi-turn developer loop, no harness
- agentic+harness: shared multi-turn developer loop plus Refactorika

Controlled variables: model, temperature, repository, trial, and verbatim user
prompt. Agentic arms receive identical developer exploration tools. Harness arms
add planning, context, verification, rollback, and completion checks. Hidden tests
and grading expectations are unavailable to every arm.
"""


AGENTIC_SYSTEM = (
    "You are an autonomous Python refactoring agent. "
    "Use the provided developer tools to inspect the repository, select and plan a "
    "behavior-preserving refactor, submit a complete multi-file patch, and verify it. "
    "Do not edit or create files under tests/. Stop only after verification and a "
    "completion audit confirm the selected refactor is complete. "
    "Workflow protocol: use at most five discovery turns, then call workflow_action "
    "with next_state=select. During planning, call workflow_action with "
    "next_state=execute and a complete structured plan. During execution or repair, "
    "use submit_patch for one planned step; verification and completion auditing are "
    "orchestrated automatically. After a rejected patch, you may request one bounded "
    "replan with workflow_action next_state=plan and a non-empty replan_rationale."
)


AGENTIC_HARNESS_SYSTEM = (
    "You are an autonomous Python refactoring agent using Refactorika. "
    "Use the provided repository audit, plan, architecture context, and developer tools "
    "to select a coherent behavior-preserving refactor. Submit mutations as complete "
    "multi-file patches through the harness verification tool. Repair concise diagnostics "
    "when verification rolls back a patch. Do not edit or create files under tests/. "
    "Stop only after every plan step is complete and the completion audit passes. "
    "Workflow protocol: use the preloaded harness context and at most five discovery "
    "turns, then call workflow_action with next_state=select. During planning, call "
    "workflow_action with next_state=execute and a complete structured plan. During "
    "execution or repair, use submit_patch for one planned step; verification and "
    "completion auditing are orchestrated automatically. After a rejected patch, you may "
    "request one bounded replan with workflow_action next_state=plan and a non-empty "
    "replan_rationale."
)


def build_off_prompt(user_prompt: str, snapshot: Mapping[str, str]) -> str:
    """Build the single-call control prompt."""

    return (
        "You are an autonomous refactoring agent.\n"
        f"User request (verbatim): {user_prompt}\n\n"
        "Inspect the repository snapshot, choose the highest-value behavior-preserving "
        "refactor, update all affected call sites, and preserve compatibility. Hidden tests "
        "may exist. Return ONLY a JSON object mapping changed relative Python file paths to "
        "their complete new contents. Do not return markdown or edit tests.\n\n"
        f"Repository snapshot:\n{json.dumps(snapshot, sort_keys=True)}"
    )


def build_harness_context_prompt(
    user_prompt: str,
    *,
    audit_plan: Mapping[str, Any],
    architecture_notes: Mapping[str, str],
    context_map: Mapping[str, Any] | None = None,
    memory: list[Mapping[str, Any]] | None = None,
) -> str:
    """Build bounded repository-level context without benchmark oracle data."""

    context: dict[str, Any] = {
        "audit_plan": audit_plan,
        "architecture_notes": architecture_notes,
    }
    if context_map is not None:
        context["context_map"] = context_map
    if memory is not None:
        context["memory"] = memory
    return (
        "Refactorika received this user request verbatim: "
        f"{user_prompt}\n\n"
        "Refactorika audit and planning context follows. Select a coherent, scoped "
        "behavior-preserving refactor. Update every affected call site and preserve public "
        "compatibility. Do not assume visible tests are complete.\n\n"
        f"Harness context:\n{json.dumps(context, sort_keys=True)}"
    )


def build_edit_prompt(
    user_prompt: str,
    snapshot: Mapping[str, str],
    plan: str,
    *,
    failure: str | None = None,
) -> str:
    """Build a single-call harness edit or diagnostic-repair prompt."""

    prompt = (
        f"User request (verbatim): {user_prompt}\n\n"
        f"Refactoring plan/context:\n{plan}\n\n"
        "Return ONLY a JSON object mapping changed relative Python file paths to their "
        "complete new contents. Do not return markdown. Change at least one file, preserve "
        "behavior, and do not edit tests.\n\n"
        f"Repository snapshot:\n{json.dumps(snapshot, sort_keys=True)}"
    )
    if failure:
        prompt += (
            "\n\nRefactorika rejected the previous proposal. Use these exact diagnostics to "
            f"repair the proposal without broadening scope:\n{failure}"
        )
    return prompt
