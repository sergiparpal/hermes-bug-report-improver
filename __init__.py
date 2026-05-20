"""hermes-bug-report-improver — a Hermes Agent plugin.

Registers a single tool, ``improve_bug_report``, in the ``qa`` toolset. Given a
poorly written or incomplete bug report, the tool returns a structured version
(title, reproduction steps, expected/actual behavior, suggested severity, and a
list of missing evidence) by delegating the rewrite to the agent's own model via
``ctx.llm.complete_structured`` — no provider keys live in this plugin.

Phase 1: loadable skeleton with a stub handler. The real handler is wired in
Phase 4 (see ``handler.py``).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

TOOLSET = "qa"

# Minimal placeholder schema for the skeleton. Replaced by schema.TOOL_SCHEMA in
# Phase 2 so the LLM sees the full parameter set.
_STUB_SCHEMA = {
    "name": "improve_bug_report",
    "description": (
        "Takes a poorly written or incomplete bug report and returns a structured "
        "version with title, reproduction steps, expected and actual behavior, "
        "suggested severity, and a list of missing evidence. Does not invent "
        "missing facts."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "raw_text": {
                "type": "string",
                "description": "The original, unstructured bug report text.",
            },
        },
        "required": ["raw_text"],
    },
}


def _stub_handler(args: dict, **kwargs: Any) -> str:
    """Placeholder until Phase 4. Returns a sentinel string."""
    return "NOT IMPLEMENTED"


def register(ctx: Any) -> None:
    """Entry point Hermes calls once at plugin load time."""
    ctx.register_tool(
        name="improve_bug_report",
        toolset=TOOLSET,
        schema=_STUB_SCHEMA,
        handler=_stub_handler,
    )
    logger.debug("hermes-bug-report-improver: registered improve_bug_report (stub)")
