"""Pytest fixtures: load the (hyphenated) plugin dir as a package the way Hermes
does, and provide a configurable fake ``ctx.llm`` so no real LLM is ever called.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
# This unit has three names, all intentional: the plugin/directory is
# ``hermes-bug-report-improver`` (hyphenated, so not importable as-is); pip
# installs it as ``hermes_bug_report_improver`` (see pyproject.toml); and at
# runtime Hermes loads the directory under a plain module name. Relative imports
# (``from . import schema``) keep the modules agnostic to which name is used, so
# the tests pick the short ``bug_report_improver`` and load it explicitly below.
PKG = "bug_report_improver"


def _load_plugin():
    """Mirror hermes_cli/plugins.py: load the dir as a package so relative
    imports (``from . import schema``) resolve, then register it in sys.modules."""
    if PKG in sys.modules:
        return sys.modules[PKG]
    spec = importlib.util.spec_from_file_location(
        PKG,
        PLUGIN_ROOT / "__init__.py",
        submodule_search_locations=[str(PLUGIN_ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[PKG] = module
    spec.loader.exec_module(module)
    return module


# Load at import time so test modules can `from bug_report_improver import ...`.
_load_plugin()


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
