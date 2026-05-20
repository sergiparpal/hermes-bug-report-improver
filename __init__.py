"""hermes-bug-report-improver — a Hermes Agent plugin.

Registers a single tool, ``improve_bug_report``, in the ``qa`` toolset. Given a
poorly written or incomplete bug report, the tool returns a structured version
(title, reproduction steps, expected/actual behavior, suggested severity, and a
list of missing evidence) by delegating the rewrite to the agent's own model via
``ctx.llm.complete_structured`` — no provider keys live in this plugin.
"""

from __future__ import annotations

import logging
from typing import Any

from . import schema
from .handler import make_handler

logger = logging.getLogger(__name__)


def register(ctx: Any) -> None:
    """Entry point Hermes calls once at plugin load time."""
    ctx.register_tool(
        name=schema.TOOL_NAME,
        toolset=schema.TOOLSET,
        schema=schema.TOOL_SCHEMA,
        handler=make_handler(ctx),
    )
    logger.debug(
        "hermes-bug-report-improver: registered %s in toolset %r",
        schema.TOOL_NAME,
        schema.TOOLSET,
    )
