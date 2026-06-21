"""Controlled repositories for the full-system benchmark."""

from .behavior import BEHAVIOR_CASES, BehaviorCase
from .multifile import MULTIFILE_CASES, MultiFileCase
from .recovery import RECOVERY_CASES, RecoveryCase
from .stress import STRESS_CASES, StressCase
from .stress_contracts_extra import STRESS_CASES as CONTRACT_STRESS_CASES
from .stress_semantics_extra import STRESS_CASES as SEMANTIC_STRESS_CASES
from .stress_systems_extra import STRESS_CASES as SYSTEM_STRESS_CASES

USER_PROMPT = "refactor this codebase"
ALL_CASES = (
    *BEHAVIOR_CASES,
    *MULTIFILE_CASES,
    *RECOVERY_CASES,
    *STRESS_CASES,
    *SEMANTIC_STRESS_CASES,
    *CONTRACT_STRESS_CASES,
    *SYSTEM_STRESS_CASES,
)

__all__ = [
    "ALL_CASES",
    "BEHAVIOR_CASES",
    "CONTRACT_STRESS_CASES",
    "MULTIFILE_CASES",
    "RECOVERY_CASES",
    "SEMANTIC_STRESS_CASES",
    "STRESS_CASES",
    "SYSTEM_STRESS_CASES",
    "USER_PROMPT",
    "BehaviorCase",
    "MultiFileCase",
    "RecoveryCase",
    "StressCase",
]
