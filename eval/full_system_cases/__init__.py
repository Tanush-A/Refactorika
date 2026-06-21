"""Controlled repositories for the full-system benchmark."""

from .behavior import BEHAVIOR_CASES, BehaviorCase
from .multifile import MULTIFILE_CASES, MultiFileCase
from .recovery import RECOVERY_CASES, RecoveryCase

USER_PROMPT = "refactor this codebase"
ALL_CASES = (*BEHAVIOR_CASES, *MULTIFILE_CASES, *RECOVERY_CASES)

__all__ = [
    "ALL_CASES",
    "BEHAVIOR_CASES",
    "MULTIFILE_CASES",
    "RECOVERY_CASES",
    "USER_PROMPT",
    "BehaviorCase",
    "MultiFileCase",
    "RecoveryCase",
]
