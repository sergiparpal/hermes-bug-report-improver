# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-tool [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin: `improve_bug_report` (in the `qa` toolset) rewrites a raw bug report into a structured one. The rewrite is delegated to the agent's own model via `ctx.llm.complete_structured` — the plugin holds no provider keys, makes no network calls, and has no runtime dependencies (stdlib only, Python 3.11+).

## Commands

```bash
python3 -m pytest                      # full suite (71 tests; -q is preset in pyproject.toml)
python3 -m pytest tests/test_handler.py::test_detailed_input_returns_high_severity  # one test
python3 -m pytest -k severity          # tests matching an expression
```

Coverage: the system Python is externally managed (PEP 668), so `coverage` is installed into a local dir and reached via `PYTHONPATH` (already set up in `.covtools/`):

```bash
PYTHONPATH=.covtools python3 -m coverage run --branch -m pytest
PYTHONPATH=.covtools python3 -m coverage report      # target ≥80%
```

Tests mock `ctx.llm` (see `tests/conftest.py`) and never hit a real model.

## Architecture

One module per request stage, all inside the `hermes_bug_report_improver/` package. The package's `register(ctx)` (in its `__init__.py`) registers the tool plus the optional `/improve-bug` command at load time. A request then runs:

`handler._improve` → `validation.validate_input` → `engine.build_report` (the model call) → `domain.ImprovedBugReport` → `rendering.format_output`

- **`schema.py`** — single source of truth for constants: the tool input schema, the LLM output schema (`BUG_REPORT_OUTPUT_SCHEMA`, `additionalProperties: false`), the severity rubric, and the size/length caps. `SEVERITY_RUBRIC` drives both the prompt and output validation; the schema's `required` list drives `REQUIRED_OUTPUT_FIELDS`. Change severity levels or output fields **here only**.
- **`host.py`** — `Protocol`s for the slice of the Hermes host the plugin touches (`ctx.llm.complete_structured`, `register_tool`, `register_command`). This is the one typed boundary to Hermes; the `complete_structured` signature is verified against hermes-agent `main`.
- **`engine.py`** — the only module that knows the LLM contract (generation settings, schema name, retry). Makes one structured call; on an unusable result, retries **once** with a stricter directive and a larger token budget. A `ValueError` from `complete_structured` (host-side schema rejection) is retryable; other exceptions propagate.
- **`prompts.py`** — system prompt + 3 few-shot examples. `build_instructions()` is `lru_cache`d. The example *outputs* double as test fixtures (`EXAMPLE_A/B/C` in the tests), so editing them can break tests.

## Conventions & non-obvious constraints

- **The handler must never raise.** Tool handlers return a string; failures return a JSON error envelope `{"error": "..."}`. Internal exception text is logged, never reflected to the caller (a test asserts this). Both entry points (the tool and `/improve-bug`) share `_improve`, which returns an `_Outcome` (report **or** error) so each formats at its own edge rather than re-parsing the other's output.

- **Two separate validation boundaries — don't conflate them.** `validation.validate_input` guards the untrusted *tool arguments* (presence, type, byte size, format enum). `domain.ImprovedBugReport.from_parsed` validates the untrusted *model output* (required fields, allow-listed severity, array types).

- **`rendering._sanitize_for_md` is the security chokepoint.** Model text is attacker-influenced, so before embedding any field in Markdown it collapses whitespace, strips Unicode control/format chars (ANSI + bidi), HTML-escapes `& < >`, and backslash-escapes a *leading* block-level marker so a field cannot forge a heading/list/code-fence/table at its block boundary (whitespace collapse alone only stops markers introduced by an embedded newline). The **`json` output is intentionally exempt** — it returns byte-faithful text and leaves display escaping to the consumer. Preserve that asymmetry when editing rendering.

- **The title invariant** (single line, ≤ `MAX_TITLE_CHARS`) is enforced in `ImprovedBugReport.__post_init__`, so every construction path — and therefore both output formats — inherits it.

- **Package layout + the root shim.** The implementation modules live in the `hermes_bug_report_improver/` package and import each other relatively (`from . import schema`), never absolutely. Two names remain, now cleanly split: the plugin **directory** is `hermes-bug-report-improver` (hyphenated, so not importable as-is), while the **import package** (and pip distribution's underscore form) is `hermes_bug_report_improver`. Hermes loads a plugin by executing the directory's root `__init__.py`, which is a thin compatibility shim: it puts its own directory on `sys.path` and re-exports `register` from the package (`from hermes_bug_report_improver import register`), so the plugin loads whether pip-installed or dropped uninstalled into `~/.hermes/plugins/`. Tests and tooling import the package directly — `pyproject.toml` sets `pythonpath = ["."]` so pytest finds it without an install, and `test_root_shim_loads_the_way_hermes_does` exercises the shim in a subprocess the way Hermes does. Keep intra-package imports relative; never hard-code the directory or package name.
