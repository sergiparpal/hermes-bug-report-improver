"""Tests for the improve_bug_report handler. No real LLM is ever called."""

from __future__ import annotations

import json

import pytest

from bug_report_improver import handler, prompts, schema

EXAMPLE_A = prompts.FEW_SHOT_EXAMPLES[0]["output"]  # vague   -> unknown
EXAMPLE_B = prompts.FEW_SHOT_EXAMPLES[1]["output"]  # detailed-> high
EXAMPLE_C = prompts.FEW_SHOT_EXAMPLES[2]["output"]  # two bugs-> low + flag


def _run(ctx, **args):
    args.setdefault("raw_text", "something is broken")
    return handler.make_handler(ctx)(args)


# --- input validation ----------------------------------------------------------
def test_empty_input_returns_error(mock_ctx):
    out = json.loads(handler.make_handler(mock_ctx())({"raw_text": ""}))
    assert "error" in out


def test_missing_raw_text_returns_error(mock_ctx):
    out = json.loads(handler.make_handler(mock_ctx())({}))
    assert "error" in out


def test_non_dict_args_returns_error(mock_ctx):
    out = json.loads(handler.make_handler(mock_ctx())("not a dict"))
    assert "error" in out


def test_oversized_input_returns_error(mock_ctx):
    big = "x" * (schema.MAX_RAW_TEXT_BYTES + 1)
    out = json.loads(handler.make_handler(mock_ctx())({"raw_text": big}))
    assert "error" in out and "KB" in out["error"]


def test_invalid_format_returns_error(mock_ctx):
    out = json.loads(_run(mock_ctx(), format="xml"))
    assert "error" in out


def test_non_string_context_returns_error(mock_ctx):
    out = json.loads(_run(mock_ctx(), context=123))
    assert "error" in out


# --- the three canonical examples ----------------------------------------------
def test_vague_input_lists_missing_evidence(mock_ctx):
    out = json.loads(_run(mock_ctx([EXAMPLE_A]), raw_text="login broken sometimes", format="json"))
    assert out["severity"] == "unknown"
    assert out["reproduction_steps"] == []
    assert len(out["missing_evidence"]) >= 4


def test_detailed_input_returns_high_severity(mock_ctx):
    out = json.loads(_run(mock_ctx([EXAMPLE_B]), format="json"))
    assert out["severity"] == "high"
    assert out["missing_evidence"] == []


def test_multi_bug_input_flags_extras(mock_ctx):
    out = json.loads(_run(mock_ctx([EXAMPLE_C]), format="json"))
    assert any(
        ("separate" in e.lower()) or ("second" in e.lower()) for e in out["missing_evidence"]
    )


# --- formatting ----------------------------------------------------------------
def test_markdown_format_renders_headings(mock_ctx):
    md = _run(mock_ctx([EXAMPLE_B]), format="markdown")
    assert md.startswith("# ")
    for heading in (
        "## Reproduction Steps",
        "## Expected Behavior",
        "## Actual Behavior",
        "## Missing Evidence",
    ):
        assert heading in md
    assert "## Severity: high" in md


def test_default_format_is_markdown(mock_ctx):
    assert _run(mock_ctx([EXAMPLE_B])).startswith("# ")


def test_json_format_returns_valid_json(mock_ctx):
    obj = json.loads(_run(mock_ctx([EXAMPLE_A]), format="json"))
    assert set(obj) == set(schema.REQUIRED_OUTPUT_FIELDS)


def test_markdown_handles_empty_steps_and_evidence(mock_ctx):
    md = _run(mock_ctx([EXAMPLE_A]), format="markdown")  # A: no steps
    assert "_None provided._" in md  # empty reproduction_steps
    md_b = _run(mock_ctx([EXAMPLE_B]), format="markdown")  # B: no missing evidence
    assert "_None — the report appears complete._" in md_b


# --- call wiring ---------------------------------------------------------------
def test_context_is_passed_to_model(mock_ctx):
    ctx = mock_ctx([EXAMPLE_B])
    _run(ctx, raw_text="boom", context="macOS 14.4")
    assert "macOS 14.4" in ctx.llm.calls[0]["input"][0]["text"]


def test_output_schema_is_enforced_by_host(mock_ctx):
    ctx = mock_ctx([EXAMPLE_B])
    _run(ctx, format="json")
    call = ctx.llm.calls[0]
    assert call["json_schema"] is schema.BUG_REPORT_OUTPUT_SCHEMA
    assert call["temperature"] == 0.0
    assert "Worked examples" in call["instructions"]


def test_handler_tolerates_kwargs_invocation(mock_ctx):
    ctx = mock_ctx([EXAMPLE_B])
    out = handler.make_handler(ctx)({"raw_text": "x", "format": "json"}, task_id="t1", extra="ignored")
    assert json.loads(out)["severity"] == "high"


# --- retry logic ---------------------------------------------------------------
def test_invalid_llm_response_retries_once(mock_ctx):
    ctx = mock_ctx([None, EXAMPLE_B])  # unparseable, then good
    out = json.loads(_run(ctx, format="json"))
    assert out["severity"] == "high"
    assert len(ctx.llm.calls) == 2
    assert handler._RETRY_SUFFIX in ctx.llm.calls[1]["instructions"]


def test_invalid_llm_response_returns_error_on_second_failure(mock_ctx):
    ctx = mock_ctx([None, None])
    out = json.loads(_run(ctx))
    assert "error" in out
    assert len(ctx.llm.calls) == 2  # exactly one retry, no more


def test_structurally_invalid_output_triggers_retry(mock_ctx):
    bad = dict(EXAMPLE_B, severity="catastrophic")  # invalid severity
    ctx = mock_ctx([bad, EXAMPLE_B])
    out = json.loads(_run(ctx, format="json"))
    assert out["severity"] == "high"
    assert len(ctx.llm.calls) == 2


# --- failure modes -------------------------------------------------------------
def test_llm_exception_returns_error(mock_ctx):
    out = json.loads(_run(mock_ctx([RuntimeError("network down")])))
    assert "error" in out and "network down" in out["error"]


def test_llm_unavailable_returns_error(no_llm_ctx):
    out = json.loads(_run(no_llm_ctx))
    assert "error" in out and "unavailable" in out["error"].lower()


# --- output coercion unit tests ------------------------------------------------
def test_schema_validates_required_fields():
    incomplete = {k: v for k, v in EXAMPLE_B.items() if k != "title"}
    with pytest.raises(ValueError):
        handler._coerce_report(incomplete)


def test_coerce_rejects_bad_severity():
    with pytest.raises(ValueError):
        handler._coerce_report(dict(EXAMPLE_B, severity="nope"))


def test_coerce_rejects_non_array_steps():
    with pytest.raises(ValueError):
        handler._coerce_report(dict(EXAMPLE_B, reproduction_steps="1. do x"))


def test_coerce_rejects_non_dict():
    with pytest.raises(ValueError):
        handler._coerce_report(["not", "a", "dict"])


def test_validate_input_rejects_non_dict_directly():
    # The handler coerces non-dict args to {}, but the guard is defensive.
    with pytest.raises(ValueError):
        handler._validate_input("not a dict")


def test_coerce_normalizes_types():
    r = handler._coerce_report(EXAMPLE_A)
    assert isinstance(r["reproduction_steps"], list)
    assert all(isinstance(s, str) for s in r["missing_evidence"])


# --- prompt / rubric consistency ----------------------------------------------
def test_few_shot_examples_conform_to_schema():
    for ex in prompts.FEW_SHOT_EXAMPLES:
        r = handler._coerce_report(ex["output"])
        assert len(r["title"]) <= 80


def test_rubric_keys_match_severity_levels():
    assert tuple(schema.SEVERITY_RUBRIC) == schema.SEVERITY_LEVELS


def test_instructions_include_rubric_and_examples():
    ins = prompts.build_instructions()
    assert ins.count("Input:") == 3
    for level in schema.SEVERITY_LEVELS:
        assert f"- {level}:" in ins


# --- registration (covers __init__.register) -----------------------------------
def test_register_wires_tool():
    import bug_report_improver as plugin

    captured: dict = {}

    class RegCtx:
        llm = None

        def register_tool(self, **kw):
            captured.update(kw)

    plugin.register(RegCtx())
    assert captured["name"] == "improve_bug_report"
    assert captured["toolset"] == "qa"
    assert captured["schema"]["name"] == "improve_bug_report"
    assert "raw_text" in captured["schema"]["parameters"]["properties"]
    assert callable(captured["handler"])
