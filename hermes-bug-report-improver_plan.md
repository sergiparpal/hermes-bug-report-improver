# Implementation Plan: `hermes-bug-report-improver`

A Hermes Agent plugin that exposes the `improve_bug_report` tool. Given a poorly written or incomplete bug report, the tool returns a structured version with title, reproduction steps, expected and actual behavior, suggested severity, and a list of missing evidence. The plugin uses `ctx.llm` to delegate the rewrite to the agent's LLM.

This document is designed to be executed end-to-end by Claude Code. It is organized into a verification stage, six implementation phases, and acceptance criteria.

---

## 1. Hermes Agent requirements the plugin must satisfy

### 1.1 Plugin model constraints

- **Category**: `general`. This plugin registers a tool. It is not a memory provider, context engine, image/video gen, platform adapter, or model provider.
- **Discovery location**: user-scope plugin at `~/.hermes/plugins/hermes-bug-report-improver/`. The directory name must match the plugin name declared in `plugin.yaml`.
- **No core modifications**: per Hermes' `AGENTS.md` rule (Teknium, May 2026), plugins MUST NOT modify any core file (`run_agent.py`, `cli.py`, `gateway/run.py`, `hermes_cli/main.py`, etc.). If the framework lacks a needed capability, the path is to expand the generic plugin surface via issue + PR — never to hardcode plugin-specific logic in core.
- **Activation**: explicit opt-in via `~/.hermes/config.yaml` under `plugins.enabled`. Users will enable the plugin themselves; the plugin must not assume it is auto-loaded.
- **Hermes version**: this plugin depends on `ctx.llm`, which is documented as v0.13.0+. A minimum Hermes version must be declared.

### 1.2 Required plugin files

The plugin directory MUST contain at minimum:

- `plugin.yaml` — manifest with `name`, `version`, `category`, `description`. Optional fields (`requires_hermes_version`, `requires_env`, `author`, `license`) should be confirmed against `developer-guide/build-a-hermes-plugin` before being added.
- `__init__.py` — exports a `register(ctx: PluginContext) -> None` function. This is the entry point Hermes calls during plugin loading.

Recommended additional files (kept separate for testability and prompt iteration):

- `handler.py` — handler implementation.
- `prompts.py` — system prompt, few-shot examples, severity rubric.
- `schema.py` — JSON schema for the tool's input.
- `tests/` — pytest tests with mocked `ctx.llm`.
- `tests/conftest.py` — pytest fixtures including the `ctx.llm` mock.
- `README.md` — usage, install steps, severity rubric.
- `LICENSE` — MIT or compatible.
- `CHANGELOG.md` — version history.

### 1.3 `ctx.register_tool` contract

The plugin registers exactly one tool via `ctx.register_tool`. Verified parameters (May 2026):

- `name: str` → `"improve_bug_report"`.
- `toolset: str` → the toolset the tool belongs to. Verify the available toolset names in the running Hermes installation; if no `qa` toolset exists, use the default one shipping with Hermes.
- `schema: dict` → JSON Schema for the tool's input parameters.
- `handler: Callable` → the function invoked when the tool is called.
- `check_fn: Optional[Callable]` → optional gate; if it returns False, the tool is hidden. Not used for this plugin.
- `requires_env: Optional[list[str]]` → list of environment variables that must be set. Not applicable here (no external API).
- `description: Optional[str]` → surfaced to the LLM in the tool list.

Parameters `is_async`, `emoji`, and similar may exist in source but are not in the public examples. Verify them against `developer-guide/build-a-hermes-plugin` BEFORE relying on them.

### 1.4 `ctx.llm` usage

The plugin's core mechanism is delegating the rewrite work to `ctx.llm`, which exposes the agent's LLM to plugins. The official documentation page is `developer-guide/plugin-llm-access`.

**Critical:** verify the actual signature of `ctx.llm` from that page BEFORE writing the handler. Treat any assumption about parameter names (`model`, `system`, `messages`, `temperature`), sync vs. async behavior, or return type as unconfirmed until checked.

### 1.5 Failure behavior

- Exceptions raised inside the handler propagate to Hermes' tool-call machinery, which returns the error to the LLM as the tool result. The handler should catch known failure modes and return a structured error message instead of raising.
- The plugin must remain functional if `ctx.llm` is unavailable: log clearly and return a structured error rather than crashing on import or on the first call.

### 1.6 Testing parity

Hermes uses `scripts/run_tests.sh` as the canonical CI script (per `AGENTS.md`). For a user-scope plugin, equivalent tests should be runnable via plain `pytest` from inside the plugin directory. Tests MUST NOT require a live LLM — `ctx.llm` MUST be mocked.

---

## 2. Plugin specification

### 2.1 Tool contract

**Name:** `improve_bug_report`

**Description (shown to the LLM):**
> "Takes a poorly written or incomplete bug report and returns a structured version with title, reproduction steps, expected and actual behavior, suggested severity, and a list of missing evidence. Does not invent missing facts."

**Input schema:**

```json
{
  "type": "object",
  "properties": {
    "raw_text": {
      "type": "string",
      "description": "The original, unstructured bug report text."
    },
    "context": {
      "type": "string",
      "description": "Optional additional environment or context info (versions, OS, browser, build number, etc.).",
      "default": ""
    },
    "format": {
      "type": "string",
      "enum": ["markdown", "json"],
      "default": "markdown",
      "description": "Output format. 'markdown' for human consumption, 'json' for downstream tooling."
    }
  },
  "required": ["raw_text"]
}
```

**Output structure** (canonical, regardless of `format`):

- `title` — string, ≤ 80 chars.
- `summary` — string, 1–2 sentences.
- `reproduction_steps` — array of strings. If absent in the input, returns `[]` and adds an entry to `missing_evidence`.
- `expected_behavior` — string. Same rule.
- `actual_behavior` — string. Same rule.
- `severity` — enum: `critical`, `high`, `medium`, `low`, `unknown`.
- `severity_rationale` — string, 1–2 sentences explaining the severity choice.
- `missing_evidence` — array of strings naming evidence not present in the input that would help triage.

When `format == "markdown"`, the same fields render as a structured Markdown document with headings; when `format == "json"`, returned as a JSON string.

### 2.2 Behavioral rules

1. **Never invent missing facts.** If the raw text does not state OS, version, environment, exact error message, or steps — list each as a missing evidence item rather than fabricating.
2. **`severity = "unknown"` is allowed and preferred over guessing.** When the raw text gives no signal about impact, return `unknown` with rationale instead of inventing a severity.
3. **Preserve user's wording where possible.** Reorganize and clarify, but do not rewrite verbatim error messages, stack traces, or quoted strings.
4. **Single bug per call.** If the raw text mixes multiple bugs, return the first one as structured output and add a `missing_evidence` item noting that additional bugs were detected in the input and should be filed separately.

### 2.3 Severity rubric (fixed in v0.1.0)

- `critical` — data loss, security breach, full service outage, blocks all users.
- `high` — blocks a major workflow, affects many users, no workaround available.
- `medium` — degraded experience, workaround exists, affects a subset of users.
- `low` — cosmetic, minor, edge case.
- `unknown` — insufficient signal in the input to assign a level.

The rubric is hardcoded in v0.1.0 and is not user-configurable.

---

## 3. Implementation phases

Each phase has a definition of done. Complete and verify the current phase before moving to the next.

### Phase 0 — Verification (≈ 30 min)

**Goal:** resolve unknowns before writing any code.

Tasks:
- [ ] Open `https://hermes-agent.nousresearch.com/docs/developer-guide/plugin-llm-access`. Record the exact signature of `ctx.llm` (sync/async, parameter names, return shape) in `NOTES.md` at the project root.
- [ ] Open `https://hermes-agent.nousresearch.com/docs/developer-guide/build-a-hermes-plugin`. Confirm the `plugin.yaml` schema, the `register(ctx)` contract, and which optional fields exist (`requires_hermes_version`, `requires_env`, `author`, `license`).
- [ ] Skim `https://github.com/NousResearch/hermes-example-plugins` for any plugin that registers a tool. Use its structure as the starting template.
- [ ] Confirm the target Hermes version with the user (likely v0.13.0+).
- [ ] Record findings in `NOTES.md` with a section per source URL and a final "Assumptions confirmed/changed" section.

**Definition of done:** `NOTES.md` exists with concrete answers to the four points above. No production code written yet.

### Phase 1 — Skeleton (≈ 30–45 min)

**Goal:** a loadable, no-op plugin.

Tasks:
- [ ] Create the plugin directory (during development, this can be a project folder symlinked to `~/.hermes/plugins/hermes-bug-report-improver/`).
- [ ] Write `plugin.yaml` with `name`, `version: 0.1.0`, `category: general`, `description`. Add `requires_hermes_version` only if confirmed in Phase 0.
- [ ] Write `__init__.py` with a `register(ctx)` that calls `ctx.register_tool` pointing to a stub handler returning the string `"NOT IMPLEMENTED"`.
- [ ] Enable the plugin in `~/.hermes/config.yaml` under `plugins.enabled`.
- [ ] Launch Hermes and confirm the tool appears in the available tool list.
- [ ] Invoke the tool from a session and confirm it returns `"NOT IMPLEMENTED"`.

**Definition of done:** the plugin loads without warnings, the tool is visible to the agent, and calling it returns the stub string.

### Phase 2 — Schema and contract (≈ 45–60 min)

**Goal:** input and output contract are fixed and observable.

Tasks:
- [ ] In `schema.py`, define the JSON schema from §2.1.
- [ ] In `handler.py`, define the output data structure as a `TypedDict` or `dataclass` matching §2.1.
- [ ] Update the stub handler to: validate the input parameters, return a hardcoded sample output that conforms to the contract, and respect the `format` parameter (return Markdown when `format == "markdown"`, JSON string when `format == "json"`).
- [ ] Confirm from a Hermes session that the LLM sees the tool with the correct description and parameter set, and that both formats produce valid output.

**Definition of done:** the tool accepts both `markdown` and `json` formats, returns valid hardcoded output, and rejects malformed input with a clear structured error.

### Phase 3 — Prompt engineering (≈ 1–1.5 h)

**Goal:** the system prompt plus few-shot examples plus severity rubric reliably produce output matching the contract.

Tasks:
- [ ] In `prompts.py`, write the system prompt (≤ 400 words) covering: role, output JSON schema, behavioral rules (§2.2), severity rubric (§2.3), explicit "do not invent" directive.
- [ ] Add three few-shot examples in the same file:
  - **Example A** — one-line vague report ("login broken sometimes"). Expected output: many `missing_evidence` entries, severity `unknown`.
  - **Example B** — detailed report with steps, expected, actual, environment. Expected severity: clearly `high`, no `missing_evidence` or a very short list.
  - **Example C** — report mixing two unrelated bugs. Expected output: structured first bug, second bug flagged in `missing_evidence`.
- [ ] Decide and document: the prompt instructs the LLM to ALWAYS output JSON. The handler renders Markdown afterwards when needed. (Recommended for easier validation.)
- [ ] Dry-run the prompt by manually pasting it plus each example input into the same LLM Hermes uses; verify the output matches the schema in §2.1.

**Definition of done:** `prompts.py` is complete and the dry run produces schema-compliant JSON for all three examples.

### Phase 4 — Handler implementation (≈ 1–1.5 h)

**Goal:** end-to-end handler from input to validated output.

Tasks:
- [ ] In `handler.py`, implement `improve_bug_report(raw_text, context, format)` with the following behavior:
  - Validate input: non-empty `raw_text`, length ≤ 16 KB, `format` is one of the allowed values.
  - Build the LLM messages using `prompts.py`.
  - Call `ctx.llm` using the signature confirmed in Phase 0.
  - Parse the LLM's JSON response.
  - If parsing fails, retry ONCE with a follow-up message: "Your previous response was not valid JSON. Please reply with valid JSON only, no markdown fencing, matching the schema." If the retry fails, return a structured error.
  - Validate the parsed JSON: all required fields present, `severity` is one of the allowed values, `reproduction_steps` and `missing_evidence` are arrays.
  - If `format == "markdown"`, render via a Markdown template; otherwise return the JSON as a string.
- [ ] Wire `register(ctx)` to use the real handler instead of the stub.

**Definition of done:** invoking the tool from a Hermes session with a real LLM produces structured, valid output for the three Phase 3 examples and at least two additional ad-hoc inputs from the user.

### Phase 5 — Tests (≈ 45–60 min)

**Goal:** deterministic test suite that does not call a real LLM.

Tasks:
- [ ] Create `tests/conftest.py` with a `mock_ctx` fixture exposing a fake `ctx.llm` whose response is configurable per test.
- [ ] Create `tests/test_handler.py` with at minimum these tests:
  - `test_empty_input_returns_error`
  - `test_vague_input_lists_missing_evidence` (mock LLM returns Example A's expected JSON)
  - `test_detailed_input_returns_high_severity` (Example B)
  - `test_multi_bug_input_flags_extras` (Example C)
  - `test_markdown_format_renders_headings`
  - `test_invalid_llm_response_retries_once`
  - `test_invalid_llm_response_returns_error_on_second_failure`
  - `test_schema_validates_required_fields`
  - `test_oversized_input_returns_error`
- [ ] Add `pytest.ini` or a `pyproject.toml` section so that `pytest` from the plugin root just works without extra flags.

**Definition of done:** `pytest` from the plugin root passes; no network calls are made; branch coverage of `handler.py` is ≥ 80%.

### Phase 6 — Docs and optional slash command (≈ 30 min)

**Goal:** a user can install, enable, and use the plugin from the README alone.

Tasks:
- [ ] Write `README.md` with: install steps, enabling in `config.yaml`, an example showing input → Markdown output, the severity rubric, configuration notes, and a "limitations" section listing the out-of-scope items from §6.
- [ ] **Optional**: register a slash command `/improve-bug` via `ctx.register_command` for direct invocation inside sessions. Confirm the signature from the Phase 0 docs notes.
- [ ] Write `CHANGELOG.md` with a `v0.1.0` entry listing the included features.
- [ ] Write `LICENSE` (MIT recommended).

**Definition of done:** README is self-sufficient — a fresh user can install and use the plugin without asking questions.

---

## 4. Instructions for Claude Code

### 4.1 How to invoke Claude Code on this plan

Start Claude Code in the working directory where the plugin source will live. Recommended first prompt:

> "Read `IMPLEMENTATION_PLAN.md` end to end. Then execute Phase 0 only. Stop after Phase 0 is complete and present `NOTES.md` for review before continuing."

After Phase 0 review, in subsequent sessions:

> "Continue with Phase N from `IMPLEMENTATION_PLAN.md`. Stop at the end of the phase and summarize what was done."

For Phase 3 (prompt engineering) and Phase 4 (handler), prefix the prompt with `ultrathink` since both involve trade-offs that benefit from more reasoning.

### 4.2 Things Claude Code MUST NOT do

- MUST NOT modify any file inside the Hermes Agent repo. The plugin is fully standalone under its own directory.
- MUST NOT add any dependency outside the Python stdlib. If a verified `ctx.llm` signature returns something requiring an external parser, pause and ask the user before adding a dependency.
- MUST NOT skip Phase 0. Every later phase depends on the verified `ctx.llm` signature and `plugin.yaml` schema.
- MUST NOT assume parameters like `is_async`, `emoji`, or `requires_hermes_version` exist. Verify each in the official docs before using them.
- MUST NOT run tests against a live LLM. All tests must use the `mock_ctx` fixture.
- MUST NOT introduce a memory provider, context engine, or any other plugin category. This plugin is `general` only.
- MUST NOT change the severity rubric or the output schema without explicit user approval — both are part of the v0.1.0 contract.

### 4.3 Things Claude Code SHOULD do

- Commit at the end of each phase with a message like `phase N: <summary>`.
- Run `pytest` from the plugin root after every code change in Phases 4 and 5.
- Update `NOTES.md` whenever a Hermes docs assumption turns out to be different from what this plan says — the plan defers to whatever the official docs actually state.
- Stop and ask the user before deviating from the schema in §2.1, the rubric in §2.3, or any "MUST NOT" item above.

---

## 5. Acceptance criteria for v0.1.0

The plugin is ready for release when all of the following hold:

1. `pytest` passes with ≥ 80% branch coverage on `handler.py`.
2. The plugin loads in a Hermes v0.13.0+ installation without warnings.
3. The `improve_bug_report` tool appears in the agent's tool list with the correct description.
4. Invoking the tool with the three Phase 3 example inputs returns schema-conformant output.
5. The severity rubric in `README.md` matches §2.3 verbatim.
6. No file outside `~/.hermes/plugins/hermes-bug-report-improver/` was modified.
7. `NOTES.md` documents every Hermes-doc verification done in Phase 0.
8. `CHANGELOG.md` has a `v0.1.0` entry.

---

## 6. Out of scope for v0.1.0

The following are explicit non-goals for the first release. They are candidates for future versions:

- Integration with `hermes-jira-incidents` or any external tracker.
- A user-configurable severity rubric.
- Multi-bug splitting (the tool handles one bug per call).
- Localization / non-English input or output.
- Caching of LLM calls.
- A companion `hermes-bug-report-history` memory provider.
- A gateway-only mode or platform-specific behavior.
