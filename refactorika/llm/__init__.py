"""The judgment layer: a thin Anthropic client with a record/replay cache.

The LLM is used only for judgment (which god function to split, how to decompose, what
to name things) and always returns structured specs, never diffs. The cache makes runs
reproducible and cheap; a stub seam keeps tests and the demo fully offline.
"""

from refactorika.llm.client import LLMClient

__all__ = ["LLMClient"]
