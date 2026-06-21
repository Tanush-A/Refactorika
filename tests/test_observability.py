from __future__ import annotations

import refactorika.observability as observability


class _Scope:
    def __init__(self) -> None:
        self.tags: dict[str, str] = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def set_tag(self, key: str, value: str) -> None:
        self.tags[key] = value


class _FakeSentry:
    def __init__(self) -> None:
        self.init_kwargs = None
        self.messages: list[tuple[str, str]] = []
        self.exceptions: list[BaseException] = []

    def init(self, **kwargs) -> None:
        self.init_kwargs = kwargs

    def set_tag(self, *_args) -> None:
        return None

    def push_scope(self) -> _Scope:
        return _Scope()

    def capture_message(self, message: str, level: str) -> None:
        self.messages.append((message, level))

    def capture_exception(self, error: BaseException) -> None:
        self.exceptions.append(error)


def test_scrubber_removes_source_prompts_paths_and_arbitrary_tags() -> None:
    event = {
        "request": {"data": "secret prompt"},
        "breadcrumbs": ["source"],
        "extra": {"patch": "private code"},
        "contexts": {"repo": "private"},
        "tags": {"component": "benchmark", "secret": "token"},
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "value": "private diagnostic",
                    "stacktrace": {
                        "frames": [
                            {
                                "filename": "/Users/private/repo/app.py",
                                "abs_path": "/Users/private/repo/app.py",
                                "context_line": "API_KEY = secret",
                                "vars": {"prompt": "private"},
                            }
                        ]
                    },
                }
            ]
        },
    }

    scrubbed = observability.scrub_event(event, {})

    assert scrubbed is not None
    assert set(scrubbed["tags"]) == {"component"}
    assert not ({"request", "breadcrumbs", "extra", "contexts"} & set(scrubbed))
    value = scrubbed["exception"]["values"][0]
    assert value["value"] == "ValueError"
    assert "stacktrace" not in value


def test_sentry_is_disabled_without_dsn(monkeypatch) -> None:
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.setattr(observability, "sentry_sdk", _FakeSentry())

    assert observability.init_sentry("benchmark") is False


def test_regression_emits_one_sanitized_warning(monkeypatch) -> None:
    fake = _FakeSentry()
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.invalid/1")
    monkeypatch.setattr(observability, "sentry_sdk", fake)
    result = {
        "meta": {"run_id": "run-1", "model": "sonnet", "provider": "anthropic"},
        "aggregate": {"arms": {"on": {"correct_landed_rate": 0.8, "regressions_shipped": 0}}},
    }
    baseline = {
        "aggregate": {"arms": {"on": {"correct_landed_rate": 1.0, "regressions_shipped": 0}}}
    }

    assert observability.capture_benchmark_regression(result, baseline, threshold=0.1)
    assert fake.messages == [("benchmark_regression", "warning")]


def test_initialization_uses_errors_only_privacy_settings(monkeypatch) -> None:
    fake = _FakeSentry()
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.invalid/1")
    monkeypatch.setattr(observability, "sentry_sdk", fake)

    assert observability.init_sentry("mcp") is True
    assert fake.init_kwargs is not None
    assert fake.init_kwargs["send_default_pii"] is False
    assert fake.init_kwargs["include_local_variables"] is False
    assert fake.init_kwargs["traces_sample_rate"] == 0.0
    assert fake.init_kwargs["before_send"] is observability.scrub_event
