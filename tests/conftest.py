"""Pytest fixtures: a configurable fake ``ctx.llm`` so no real LLM is ever called.

Tests import the plugin by its real package name (``hermes_bug_report_improver``).
``pyproject.toml`` puts the repo root on ``sys.path`` for the session
(``[tool.pytest.ini_options] pythonpath = ["."]``), so no custom loader is needed
here — this module only provides the fakes.
"""

from __future__ import annotations

import pytest


class _FakeResult:
    """Mimics PluginLlmStructuredResult."""

    def __init__(self, parsed=None, text="", provider="mock", model="mock-model"):
        self.parsed = parsed
        self.text = text
        self.provider = provider
        self.model = model
        self.content_type = "json" if parsed is not None else "text"


class _FakeLLM:
    """Configurable fake ``ctx.llm``.

    ``responses`` is consumed one item per ``complete_structured`` call. Each item
    may be a dict (-> parsed JSON), ``None`` (-> unparseable), an ``Exception``
    instance (-> raised), or a ``_FakeResult``.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def complete_structured(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError(
                "complete_structured called more times than configured responses"
            )
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, _FakeResult):
            return item
        return _FakeResult(parsed=item)


class _FakeCtx:
    def __init__(self, llm):
        self.llm = llm


@pytest.fixture
def mock_ctx():
    """Factory fixture: ``mock_ctx([dict|None|Exception|result, ...])`` -> ctx."""

    def _make(responses=None):
        return _FakeCtx(_FakeLLM(responses or []))

    return _make


@pytest.fixture
def llm_result():
    """Factory for a raw result object (when a test needs to set ``.text``)."""

    def _make(parsed=None, text=""):
        return _FakeResult(parsed=parsed, text=text)

    return _make


@pytest.fixture
def no_llm_ctx():
    """A ctx whose ``llm`` attribute is None (ctx.llm unavailable)."""
    return _FakeCtx(None)
