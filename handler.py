"""Handler for the ``improve_bug_report`` tool.

Tool handlers in Hermes receive a single ``args`` dict and must return a string,
never raising. They do not receive ``ctx`` directly, so the handler is produced
by ``make_handler(ctx)`` and closes over ``ctx`` to reach ``ctx.llm`` (the same
pattern the official ``plugin-llm-example`` uses).

The rewrite is delegated to ``ctx.llm.complete_structured`` with the output
schema, so the host enforces the JSON shape and returns ``result.parsed``. We add
one retry (with a stricter directive) for the case where ``parsed`` is missing or
fails validation, then fall back to a structured error.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from typing import Any, Callable

from . import prompts, schema

logger = logging.getLogger(__name__)

# Generation settings for the structured call. The retry uses a larger token
# budget so a first attempt that failed by truncation (e.g. a long verbatim
# stack trace) has room to complete the second time.
_MAX_TOKENS = 2048
_RETRY_MAX_TOKENS = 4096
_TEMPERATURE = 0.0
_SCHEMA_NAME = "bug_report.improved"
_PURPOSE = "hermes-bug-report-improver.improve_bug_report"
_RETRY_SUFFIX = (
    "Your previous response was not valid JSON matching the schema. Reply with "
    "valid JSON only — no Markdown, no code fences, no commentary — using exactly "
    "the required fields."
)


class LlmUnavailable(RuntimeError):
    """Raised when ``ctx.llm`` is not present so we can report it cleanly."""


@dataclass
class ImprovedBugReport:
    """Canonical output structure, independent of the chosen format."""

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

    context = args.get("context")
    if context is None:  # absent or JSON null -> treat as empty
        context = ""
    if not isinstance(context, str):
        raise ValueError("`context` must be a string.")

    fmt = args.get("format")
    if fmt is None or fmt == "":  # absent, null, or empty -> default
        fmt = schema.DEFAULT_FORMAT
    if not isinstance(fmt, str):
        raise ValueError("`format` must be a string.")
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

    # Flatten the title to a single line (a stray newline would break the
    # Markdown heading) and enforce the documented length cap.
    title = " ".join(str(data["title"]).split())
    if len(title) > schema.MAX_TITLE_CHARS:
        title = title[: schema.MAX_TITLE_CHARS - 1].rstrip() + "…"

    report = ImprovedBugReport(
        title=title,
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
    lines: list[str] = [
        f"# {r['title']}",
        "",
        r["summary"] or "_Not provided._",
        "",
        "## Reproduction Steps",
        "",
    ]
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
        r["severity_rationale"] or "_Not provided._",
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


# --- LLM call ------------------------------------------------------------------
def _input_blocks(raw_text: str, context: str) -> list[dict[str, str]]:
    text = raw_text
    if context.strip():
        text = f"{raw_text}\n\n[Additional context: {context.strip()}]"
    return [{"type": "text", "text": text}]


def _attempt(
    ctx: Any, instructions: str, input_blocks: list, max_tokens: int
) -> dict[str, Any] | None:
    """One structured call. Returns a coerced report, or None if unusable.

    A ``ValueError`` from ``complete_structured`` — e.g. a host that enforces
    the schema with the optional ``jsonschema`` package rejected the model's
    output — is treated as an unusable result so the caller retries, mirroring
    the path taken when the host returns ``parsed`` for us to validate.
    Non-``ValueError`` failures (trust gate, auth, network) still propagate.
    """
    try:
        result = ctx.llm.complete_structured(
            instructions=instructions,
            input=input_blocks,
            json_schema=schema.BUG_REPORT_OUTPUT_SCHEMA,
            schema_name=_SCHEMA_NAME,
            purpose=_PURPOSE,
            temperature=_TEMPERATURE,
            max_tokens=max_tokens,
        )
    except ValueError as exc:  # host-side schema/parse rejection -> retryable
        logger.debug("complete_structured rejected the call: %s", exc)
        return None
    parsed = getattr(result, "parsed", None)
    if parsed is None:
        logger.debug("complete_structured returned no parsed JSON")
        return None
    try:
        return _coerce_report(parsed)
    except ValueError as exc:
        logger.debug("parsed output failed validation: %s", exc)
        return None


def _build_report(ctx: Any, raw_text: str, context: str) -> dict[str, Any]:
    """Delegate the rewrite to the agent's model. Raise on unrecoverable failure."""
    if getattr(ctx, "llm", None) is None:
        raise LlmUnavailable("ctx.llm is not available")

    instructions = prompts.build_instructions()
    blocks = _input_blocks(raw_text, context)

    report = _attempt(ctx, instructions, blocks, _MAX_TOKENS)
    if report is None:  # retry once with a stricter directive and more room
        report = _attempt(
            ctx, f"{instructions}\n\n{_RETRY_SUFFIX}", blocks, _RETRY_MAX_TOKENS
        )
    if report is None:
        raise ValueError("model did not return a valid structured report after one retry")
    return report


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
        except LlmUnavailable as exc:
            return _error(f"LLM is unavailable: {exc}")
        except ValueError as exc:
            return _error(f"could not build a structured report: {exc}")
        except Exception as exc:  # noqa: BLE001 - tool handlers must never raise
            logger.warning("improve_bug_report failed: %s", exc)
            return _error(f"unexpected error while improving the report: {exc}")

        return _format_output(report, fmt)

    improve_bug_report.__name__ = schema.TOOL_NAME
    return improve_bug_report


def make_command(ctx: Any, tool_handler: Callable[..., str] | None = None) -> Callable[[str], str]:
    """Build the optional ``/improve-bug`` slash-command handler.

    Slash-command handlers receive the raw argument string and return a string
    shown directly to the user, so this renders Markdown and unwraps the tool's
    JSON error envelope into a readable line.
    """
    handler_fn = tool_handler or make_handler(ctx)

    def improve_bug(raw_args: str = "") -> str:
        text = (raw_args or "").strip()
        if not text:
            return "Usage: /improve-bug <paste the raw bug report text>"
        result = handler_fn({"raw_text": text, "format": "markdown"})
        try:
            payload = json.loads(result)
        except (ValueError, TypeError):
            return result  # Markdown success output (not JSON).
        if isinstance(payload, dict) and "error" in payload:
            return f"Could not improve the report: {payload['error']}"
        return result

    return improve_bug
