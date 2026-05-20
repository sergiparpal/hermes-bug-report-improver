"""Handler for the ``improve_bug_report`` tool.

Tool handlers in Hermes receive a single ``args`` dict and must return a string,
never raising. They do not receive ``ctx`` directly, so the handler is produced
by ``make_handler(ctx)`` and closes over ``ctx`` to reach ``ctx.llm`` (the same
pattern the official ``plugin-llm-example`` uses).

Phase 2 returns a hardcoded sample. Phase 4 replaces ``_build_report`` with a
real ``ctx.llm.complete_structured`` call. Everything else (validation, output
coercion, Markdown rendering, format dispatch, error envelope) is final.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from typing import Any, Callable

from . import schema

logger = logging.getLogger(__name__)


@dataclass
class ImprovedBugReport:
    """Canonical output structure (§2.1), independent of the chosen format."""

    title: str
    summary: str
    reproduction_steps: list[str]
    expected_behavior: str
    actual_behavior: str
    severity: str
    severity_rationale: str
    missing_evidence: list[str]


# --- Error envelope ------------------------------------------------------------
def _error(message: str, **extra: Any) -> str:
    """Structured error string. Handlers must return a string, never raise."""
    payload: dict[str, Any] = {"error": message}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


# --- Input validation ----------------------------------------------------------
def _validate_input(args: Any) -> tuple[str, str, str]:
    """Return (raw_text, context, format) or raise ValueError with a clear message."""
    if not isinstance(args, dict):
        raise ValueError("Tool arguments must be a JSON object.")

    raw_text = args.get("raw_text")
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise ValueError("`raw_text` is required and must be a non-empty string.")
    if len(raw_text.encode("utf-8")) > schema.MAX_RAW_TEXT_BYTES:
        kb = schema.MAX_RAW_TEXT_BYTES // 1024
        raise ValueError(f"`raw_text` exceeds the {kb} KB limit.")

    context = args.get("context", "") or ""
    if not isinstance(context, str):
        raise ValueError("`context` must be a string.")

    fmt = args.get("format") or schema.DEFAULT_FORMAT
    if fmt not in schema.ALLOWED_FORMATS:
        allowed = ", ".join(schema.ALLOWED_FORMATS)
        raise ValueError(f"`format` must be one of: {allowed}.")

    return raw_text, context, fmt


# --- Output coercion / validation ----------------------------------------------
def _coerce_report(data: Any) -> dict[str, Any]:
    """Validate and normalize a structured report. Raise ValueError if invalid."""
    if not isinstance(data, dict):
        raise ValueError("model output was not a JSON object")

    missing = [f for f in schema.REQUIRED_OUTPUT_FIELDS if f not in data]
    if missing:
        raise ValueError(f"model output missing fields: {', '.join(missing)}")

    severity = data.get("severity")
    if severity not in schema.SEVERITY_LEVELS:
        allowed = ", ".join(schema.SEVERITY_LEVELS)
        raise ValueError(f"invalid severity {severity!r}; must be one of: {allowed}")

    for arr in ("reproduction_steps", "missing_evidence"):
        if not isinstance(data.get(arr), list):
            raise ValueError(f"`{arr}` must be an array")

    report = ImprovedBugReport(
        title=str(data["title"]),
        summary=str(data["summary"]),
        reproduction_steps=[str(s) for s in data["reproduction_steps"]],
        expected_behavior=str(data["expected_behavior"]),
        actual_behavior=str(data["actual_behavior"]),
        severity=str(severity),
        severity_rationale=str(data["severity_rationale"]),
        missing_evidence=[str(s) for s in data["missing_evidence"]],
    )
    return asdict(report)


# --- Rendering -----------------------------------------------------------------
def _render_markdown(r: dict[str, Any]) -> str:
    lines: list[str] = [f"# {r['title']}", "", r["summary"], "", "## Reproduction Steps", ""]
    if r["reproduction_steps"]:
        lines += [f"{i}. {step}" for i, step in enumerate(r["reproduction_steps"], 1)]
    else:
        lines.append("_None provided._")
    lines += [
        "",
        "## Expected Behavior",
        "",
        r["expected_behavior"] or "_Not provided._",
        "",
        "## Actual Behavior",
        "",
        r["actual_behavior"] or "_Not provided._",
        "",
        f"## Severity: {r['severity']}",
        "",
        r["severity_rationale"],
        "",
        "## Missing Evidence",
        "",
    ]
    if r["missing_evidence"]:
        lines += [f"- {item}" for item in r["missing_evidence"]]
    else:
        lines.append("_None — the report appears complete._")
    return "\n".join(lines)


def _format_output(report: dict[str, Any], fmt: str) -> str:
    if fmt == "json":
        return json.dumps(report, ensure_ascii=False, indent=2)
    return _render_markdown(report)


# --- Report production (Phase 2: hardcoded; Phase 4: ctx.llm) ------------------
_SAMPLE_REPORT = {
    "title": "Sample structured bug report",
    "summary": "Hardcoded Phase 2 sample so the tool contract is observable end-to-end.",
    "reproduction_steps": ["Open the editor", "Click Save"],
    "expected_behavior": "The document saves successfully.",
    "actual_behavior": "The application crashes.",
    "severity": "unknown",
    "severity_rationale": "Hardcoded sample; severity is not assessed in Phase 2.",
    "missing_evidence": ["Operating system and version", "Exact error message"],
}


def _build_report(ctx: Any, raw_text: str, context: str) -> dict[str, Any]:
    """Phase 2 stand-in. Phase 4 calls ctx.llm.complete_structured here."""
    return _coerce_report(_SAMPLE_REPORT)


# --- Public factory ------------------------------------------------------------
def make_handler(ctx: Any) -> Callable[..., str]:
    """Build the tool handler, closing over ``ctx`` for ``ctx.llm`` access."""

    def improve_bug_report(args: Any = None, **kwargs: Any) -> str:
        if not isinstance(args, dict):
            args = kwargs or {}
        try:
            raw_text, context, fmt = _validate_input(args)
        except ValueError as exc:
            return _error(str(exc))

        try:
            report = _build_report(ctx, raw_text, context)
        except ValueError as exc:
            return _error(f"could not build a structured report: {exc}")

        return _format_output(report, fmt)

    improve_bug_report.__name__ = schema.TOOL_NAME
    return improve_bug_report
