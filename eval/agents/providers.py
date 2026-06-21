"""Provider adapters shared by benchmark agent implementations.

The adapters deliberately contain no benchmark orchestration.  They normalize
provider responses, token usage, timing, and failure classification so every arm
observes the same transport behavior.
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from refactorika.observability import capture_exception


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens

    @property
    def total(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )


@dataclass(frozen=True)
class Completion:
    text: str
    usage: Usage
    seconds: float
    error: str | None = None
    error_class: str | None = None


@dataclass(frozen=True)
class ToolCompletion:
    content: list[dict[str, Any]]
    usage: Usage
    seconds: float
    error: str | None = None
    error_class: str | None = None


RequestOpener = Callable[..., Any]


class HttpProvider:
    """Synchronous Anthropic/OpenAI-compatible HTTP adapter."""

    def __init__(
        self,
        provider: str,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str = "http://localhost:11434/v1",
        timeout: int = 180,
        opener: RequestOpener = urllib.request.urlopen,
    ) -> None:
        if provider not in {"anthropic", "openai"}:
            raise ValueError(f"unsupported provider: {provider}")
        self.provider = provider
        self.model = model
        self.name = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener = opener

    def complete(self, prompt: str) -> Completion:
        if self.provider == "anthropic" and not self.api_key:
            return Completion(
                "",
                Usage(),
                0.0,
                "ANTHROPIC_API_KEY is not configured",
                "configuration_failure",
            )

        if self.provider == "anthropic":
            url = "https://api.anthropic.com/v1/messages"
            headers = self._anthropic_headers()
            body = {
                "model": self.model,
                "max_tokens": 8192,
                "temperature": 0,
                "messages": [{"role": "user", "content": prompt}],
            }
        else:
            url = f"{self.base_url}/chat/completions"
            headers = {"Content-Type": "application/json"}
            body = {
                "model": self.model,
                "temperature": 0,
                "messages": [{"role": "user", "content": prompt}],
            }

        started = time.perf_counter()
        try:
            data = self._post(url, headers, body, self.timeout)
            if self.provider == "anthropic":
                text = "".join(
                    block.get("text", "")
                    for block in data["content"]
                    if block.get("type") == "text"
                )
                usage = _anthropic_usage(data.get("usage", {}))
            else:
                text = data["choices"][0]["message"]["content"]
                usage = _openai_usage(data.get("usage", {}))
            return Completion(text, usage, _elapsed(started))
        except _PROVIDER_EXCEPTIONS as exc:
            self._capture(exc, phase="provider_request", arm=None)
            return Completion("", Usage(), _elapsed(started), str(exc), "provider_failure")

    def complete_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str,
        tools: list[dict[str, Any]],
        arm: str,
        timeout: float | None = None,
    ) -> ToolCompletion:
        """Complete one Anthropic tool-use turn with normalized failures."""

        if self.provider != "anthropic":
            return ToolCompletion(
                [],
                Usage(),
                0.0,
                "tool use is currently supported only for Anthropic",
                "configuration_failure",
            )
        if not self.api_key:
            return ToolCompletion(
                [],
                Usage(),
                0.0,
                "ANTHROPIC_API_KEY is not configured",
                "configuration_failure",
            )

        body = {
            "model": self.model,
            "max_tokens": 8192,
            "temperature": 0,
            "system": system,
            "tools": tools,
            "messages": messages,
        }
        started = time.perf_counter()
        try:
            data = self._post(
                "https://api.anthropic.com/v1/messages",
                self._anthropic_headers(),
                body,
                timeout or self.timeout,
            )
            content = data["content"]
            if not isinstance(content, list):
                raise ValueError("provider content must be a list")
            return ToolCompletion(
                content,
                _anthropic_usage(data.get("usage", {})),
                _elapsed(started),
            )
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            self._capture(exc, phase="agentic_provider_request", arm=arm)
            return ToolCompletion(
                [],
                Usage(),
                _elapsed(started),
                f"provider_timeout_or_failure: {exc}",
                "provider_failure",
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._capture(exc, phase="agentic_provider_response", arm=arm)
            return ToolCompletion(
                [],
                Usage(),
                _elapsed(started),
                f"malformed_provider_response: {exc}",
                "malformed_response",
            )

    def _anthropic_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-api-key": self.api_key or "",
            "anthropic-version": "2023-06-01",
        }

    def _post(
        self,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        request = urllib.request.Request(url, json.dumps(body).encode(), headers, method="POST")
        with self._opener(request, timeout=timeout) as response:
            data = json.loads(response.read())
        if not isinstance(data, dict):
            raise ValueError("provider response must be an object")
        return data

    def _capture(self, exc: Exception, *, phase: str, arm: str | None) -> None:
        tags: dict[str, object] = {
            "model": self.model,
            "provider": self.provider,
        }
        if arm is not None:
            tags["arm"] = arm
        capture_exception(exc, component="benchmark", phase=phase, tags=tags)


_PROVIDER_EXCEPTIONS = (
    urllib.error.URLError,
    TimeoutError,
    socket.timeout,
    KeyError,
    TypeError,
    ValueError,
    json.JSONDecodeError,
)


def _anthropic_usage(raw: dict[str, Any]) -> Usage:
    return Usage(
        input_tokens=int(raw.get("input_tokens", 0)),
        output_tokens=int(raw.get("output_tokens", 0)),
        cache_read_tokens=int(raw.get("cache_read_input_tokens", 0)),
        cache_write_tokens=int(raw.get("cache_creation_input_tokens", 0)),
    )


def _openai_usage(raw: dict[str, Any]) -> Usage:
    cached = int(raw.get("prompt_tokens_details", {}).get("cached_tokens", 0))
    return Usage(
        input_tokens=max(int(raw.get("prompt_tokens", 0)) - cached, 0),
        output_tokens=int(raw.get("completion_tokens", 0)),
        cache_read_tokens=cached,
    )


def _elapsed(started: float) -> float:
    return round(time.perf_counter() - started, 3)
