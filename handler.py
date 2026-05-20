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
import unicodedata
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
def _error(message: str) -> str:
    """Structured error string. Handlers must return a string, never raise."""
    return json.dumps({"error": message}, ensure_ascii=True)


# --- Input validation ----------------------------------------------------------
def _check_size(field: str, value: str, limit: int) -> None:
    """Raise ValueError if ``value`` is larger than ``limit`` bytes (UTF-8)."""
    if len(value.encode("utf-8")) > limit:
        raise ValueError(f"`{field}` exceeds the {limit // 1024} KB limit.")


def _validate_input(args: Any) -> tuple[str, str, str]:
    """Return (raw_text, context, format) or raise ValueError with a clear message."""
    if not isinstance(args, dict):
        raise ValueError("Tool arguments must be a JSON object.")

    raw_text = args.get("raw_text")
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise ValueError("`raw_text` is required and must be a non-empty string.")
    _check_size("raw_text", raw_text, schema.MAX_RAW_TEXT_BYTES)

    context = args.get("context")
    if context is None:  # absent or JSON null -> treat as empty
        context = ""
    if not isinstance(context, str):
        raise ValueError("`context` must be a string.")
    _check_size("context", context, schema.MAX_CONTEXT_BYTES)

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

    # Flatten to one line and cap the length here at coercion (not only in
    # rendering): the `json` output skips `_sanitize_for_md`, so this is what
    # keeps its title single-line and length-capped, and it makes Markdown
    # truncation count visible characters rather than a stray newline. The
    # Markdown path collapses whitespace again in `_sanitize_for_md` — a cheap,
    # intentional overlap.
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
def _sanitize_for_md(text: str) -> str:
    """Make a model-produced string safe to embed in the rendered Markdown.

    The report text is ultimately attacker-influenced (a malicious bug report can
    steer the model, and verbatim error text is echoed back), and the Markdown is
    shown to humans — in a terminal for ``/improve-bug``, or rendered as HTML
    downstream. So before interpolating any field we:

    1. collapse all whitespace to single spaces, so a stray newline cannot forge a
       new block (a fake ``## Severity`` heading or list item);
    2. drop Unicode control/format characters, removing the ESC byte (ANSI escape
       injection in a terminal) and bidi overrides (text-spoofing);
    3. HTML-escape ``& < >``, so the text cannot smuggle live HTML / ``<script>``
       through a renderer that passes raw HTML.

    This does NOT neutralize Markdown link/image syntax (``[x](javascript:…)``,
    ``![](http://attacker/?leak)``); escaping those would mangle legitimate report
    text, so downstream HTML renderers must still use a sanitizing renderer (no raw
    HTML, restricted URL schemes) — see the README's Security section. The ``json``
    output is exempt: it returns the exact text, structurally escaped by the JSON
    encoder, and leaves display escaping to the consumer.
    """
    text = " ".join(text.split())
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _text_block(title: str, body: str) -> str:
    """A ``## heading`` followed by one sanitized text block (fallback if empty)."""
    return f"## {title}\n\n{_sanitize_for_md(body) or '_Not provided._'}"


def _list_block(title: str, items: list[str], empty: str, *, ordered: bool) -> str:
    """A ``## heading`` followed by a sanitized list (fallback text if empty)."""
    if not items:
        body = empty
    elif ordered:
        body = "\n".join(f"{i}. {_sanitize_for_md(s)}" for i, s in enumerate(items, 1))
    else:
        body = "\n".join(f"- {_sanitize_for_md(s)}" for s in items)
    return f"## {title}\n\n{body}"


def _render_markdown(report: dict[str, Any]) -> str:
    """Render the canonical report as Markdown; blocks are joined by blank lines."""
    blocks = [
        f"# {_sanitize_for_md(report['title'])}",
        _sanitize_for_md(report["summary"]) or "_Not provided._",
        _list_block(
            "Reproduction Steps", report["reproduction_steps"], "_None provided._", ordered=True
        ),
        _text_block("Expected Behavior", report["expected_behavior"]),
        _text_block("Actual Behavior", report["actual_behavior"]),
        _text_block(
            f"Severity: {_sanitize_for_md(report['severity'])}", report["severity_rationale"]
        ),
        _list_block(
            "Missing Evidence",
            report["missing_evidence"],
            "_None — the report appears complete._",
            ordered=False,
        ),
    ]
    return "\n\n".join(blocks)


def _format_output(report: dict[str, Any], fmt: str) -> str:
    if fmt == "json":
        # ensure_ascii=True escapes U+2028/U+2029: valid in JSON but they break
        # JavaScript string literals if this JSON is later embedded in a page.
        return json.dumps(report, ensure_ascii=True, indent=2)
    return _render_markdown(report)


# --- LLM call ------------------------------------------------------------------
def _input_blocks(raw_text: str, context: str) -> list[dict[str, str]]:
    extra = context.strip()
    text = f"{raw_text}\n\n[Additional context: {extra}]" if extra else raw_text
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


# --- Orchestration -------------------------------------------------------------
@dataclass
class _Outcome:
    """Result of an improve request: either a ``report`` to format or an ``error``.

    Both entry points (the ``improve_bug_report`` tool and the ``/improve-bug``
    command) build one of these and format it at their own edge, so neither has
    to recover structure by re-parsing the other's serialized output.
    """

    report: dict[str, Any] | None = None
    error: str | None = None
    fmt: str = schema.DEFAULT_FORMAT


def _improve(ctx: Any, args: Any) -> _Outcome:
    """Validate the input and build the structured report. Never raises."""
    try:
        raw_text, context, fmt = _validate_input(args)
    except ValueError as exc:
        return _Outcome(error=str(exc))

    try:
        report = _build_report(ctx, raw_text, context)
    except LlmUnavailable as exc:
        return _Outcome(error=f"LLM is unavailable: {exc}")
    except ValueError as exc:
        return _Outcome(error=f"could not build a structured report: {exc}")
    except Exception:  # noqa: BLE001 - the tool path must never raise
        # Log the detail (with traceback) but report a generic message: the
        # exception text may carry host/library internals we should not leak.
        logger.warning("improve_bug_report failed", exc_info=True)
        return _Outcome(error="internal error while improving the report")

    return _Outcome(report=report, fmt=fmt)


# --- Public factory ------------------------------------------------------------
def make_handler(ctx: Any) -> Callable[..., str]:
    """Build the tool handler, closing over ``ctx`` for ``ctx.llm`` access."""

    def improve_bug_report(args: Any = None, **kwargs: Any) -> str:
        # Hermes may invoke a handler positionally with the args dict or via
        # kwargs; accept either, then run the shared validate+build pipeline.
        if not isinstance(args, dict):
            args = kwargs or {}
        outcome = _improve(ctx, args)
        if outcome.error is not None:
            return _error(outcome.error)
        return _format_output(outcome.report, outcome.fmt)

    improve_bug_report.__name__ = schema.TOOL_NAME
    return improve_bug_report


def make_command(ctx: Any) -> Callable[[str], str]:
    """Build the optional ``/improve-bug`` slash-command handler.

    Slash-command handlers receive the raw argument string and return a string
    shown directly to the user, so this renders Markdown on success and turns a
    failure into one readable line — sharing ``_improve`` with the tool rather
    than re-parsing the tool's serialized output.
    """

    def improve_bug(raw_args: str = "") -> str:
        text = (raw_args or "").strip()
        if not text:
            return "Usage: /improve-bug <paste the raw bug report text>"
        outcome = _improve(ctx, {"raw_text": text, "format": "markdown"})
        if outcome.error is not None:
            return f"Could not improve the report: {outcome.error}"
        return _render_markdown(outcome.report)

    return improve_bug
