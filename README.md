# hermes-bug-report-improver

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that exposes
the **`improve_bug_report`** tool. Given a poorly written or incomplete bug report,
it returns a structured version with a title, summary, reproduction steps, expected
and actual behavior, a suggested severity, a severity rationale, and a list of
missing evidence. The rewrite is delegated to the agent's own model via
`ctx.llm.complete_structured` — **no provider keys live in this plugin**.

It does not invent missing facts: anything the input does not state (OS, version,
exact error, steps, …) is listed under *missing evidence* rather than guessed.

## Requirements

- Hermes Agent **v0.13+** (uses `ctx.llm`; verified against 0.14.0).
- Python **3.11+** (matches Hermes).
- No third-party runtime dependencies — standard library only.

## Install

The plugin directory name must match the plugin name. Place it at the user-scope
plugin location:

```bash
# Option A — clone directly into the plugins directory
git clone <this-repo> ~/.hermes/plugins/hermes-bug-report-improver

# Option B — symlink an existing checkout
ln -s /path/to/hermes-bug-report-improver ~/.hermes/plugins/hermes-bug-report-improver
```

## Enable

Plugins are disabled by default. Enable it either with the CLI:

```bash
hermes plugins enable hermes-bug-report-improver
```

…or by editing `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - hermes-bug-report-improver
```

Verify it loaded with `hermes plugins list`. The tool appears in the **`qa`**
toolset.

## Usage

### As a tool

The agent calls `improve_bug_report` with:

| Parameter   | Type   | Required | Default      | Description                                                            |
|-------------|--------|----------|--------------|------------------------------------------------------------------------|
| `raw_text`  | string | yes      | —            | The original, unstructured bug report text (max 16 KB).                |
| `context`   | string | no       | `""`         | Extra environment/context info (versions, OS, browser, build number; max 4 KB). |
| `format`    | string | no       | `"markdown"` | `"markdown"` for humans, `"json"` for downstream tooling.              |

### As a slash command

```
/improve-bug <paste the raw bug report text>
```

Renders the structured report as Markdown directly in the session.

## Example

**Input** (`raw_text`):

> Checkout 'Pay now' button does nothing on Safari 17 (macOS 14.4), app v3.2.1.
> Steps: 1) add item to cart 2) go to checkout 3) click 'Pay now' — nothing happens
> and no network request fires. Console shows 'TypeError: undefined is not a
> function' at checkout.js:88. Expected: payment is processed. Happens 100% of the
> time on Safari; Chrome works fine. This blocks all purchases for Safari users.

**Output** (`format: "markdown"`):

```markdown
# Checkout 'Pay now' button is unresponsive on Safari 17

Clicking 'Pay now' on Safari 17 / macOS 14.4 (app v3.2.1) does nothing and fires no network request, blocking checkout. Chrome is unaffected.

## Reproduction Steps

1. Add an item to the cart
2. Go to checkout
3. Click 'Pay now'

## Expected Behavior

Payment is processed and the order completes.

## Actual Behavior

Nothing happens and no network request fires. Console shows: TypeError: undefined is not a function (checkout.js:88).

## Severity: high

A major workflow (payment) is fully blocked for all Safari users with no workaround; Chrome still works, so it is not a total outage.

## Missing Evidence

_None — the report appears complete._
```

With `format: "json"` the same data is returned as a JSON object with these fields:
`title`, `summary`, `reproduction_steps` (array), `expected_behavior`,
`actual_behavior`, `severity`, `severity_rationale`, `missing_evidence` (array).

## Severity rubric

The rubric is fixed in v0.1.0 and is **not** user-configurable:

- `critical` — data loss, security breach, full service outage, blocks all users.
- `high` — blocks a major workflow, affects many users, no workaround available.
- `medium` — degraded experience, workaround exists, affects a subset of users.
- `low` — cosmetic, minor, edge case.
- `unknown` — insufficient signal in the input to assign a level.

## Behavioral guarantees

- **Never invents facts.** Missing OS/version/steps/errors are listed under
  `missing_evidence`, not fabricated.
- **Prefers `unknown` over guessing** when the input gives no signal about impact.
- **Preserves verbatim** error messages, stack traces, and quoted strings. The
  `json` output keeps the exact text; the Markdown rendering normalizes whitespace
  and escapes HTML for safety (see [Security](#security)), so prefer
  `format: "json"` when exact bytes matter.
- **One bug per call.** If the input mixes multiple bugs, the first is structured
  and the rest are flagged in `missing_evidence` to be filed separately.
- **Never crashes the tool call.** All failures (invalid input, model error,
  `ctx.llm` unavailable, unparseable output after one retry) return a structured
  `{"error": "..."}` string.

## Security

The tool ingests an untrusted bug report and restructures it, so it treats the
model's output as untrusted too:

- **Prompt injection is contained, not prevented.** A malicious report can try to
  steer the model, but the output is constrained to a fixed JSON schema
  (`additionalProperties: false`) and then re-validated by the handler (severity is
  allow-listed, fields are type-checked, the title is length-capped). Injection
  cannot change the report's shape or add fields — only the free-text values.
- **Markdown output is sanitized.** Rendered fields have control characters
  removed (including ESC, preventing ANSI escape injection in a terminal),
  whitespace collapsed (preventing forged headings/lists), and `& < >` HTML-escaped
  (preventing raw-HTML/XSS in an HTML renderer).
- **Render Markdown safely downstream.** Markdown link/image syntax
  (`[…](javascript:…)`, `![](http://attacker/?leak)`) cannot be neutralized at the
  source without mangling legitimate text. Consumers that render this Markdown as
  HTML should use a sanitizing renderer (disallow raw HTML, restrict URL schemes,
  do not auto-load remote images). `format: "json"` returns exact text (structural
  escaping only) and leaves display escaping to the consumer.
- **Input is bounded.** `raw_text` is capped at 16 KB and `context` at 4 KB.
- **No secrets, no egress.** The plugin stores no API keys and makes no network
  calls of its own; it uses the agent's model via `ctx.llm`. Unexpected errors
  return a generic message and are logged rather than reflected to the caller.

## Configuration notes

- No environment variables and no external API — it uses the agent's active model
  and credentials through `ctx.llm`.
- The severity rubric and the output schema are part of the v0.1.0 contract.

## Development & tests

Tests mock `ctx.llm` and make **no network calls**:

```bash
pytest                                   # run the suite
coverage run --branch -m pytest          # with branch coverage
coverage report                          # plugin modules reported (target: >=80%)
```

If your system Python is externally managed (PEP 668) and `coverage` cannot be
installed normally, install it into a local directory and point `PYTHONPATH` at it:

```bash
python3 -m pip install --target=.covtools coverage
PYTHONPATH=.covtools python3 -m coverage run --branch -m pytest
PYTHONPATH=.covtools python3 -m coverage report
```

## Limitations (out of scope for v0.1.0)

- No integration with external trackers (Jira, etc.).
- The severity rubric is not user-configurable.
- No multi-bug splitting (one bug per call).
- English input/output only; no localization.
- No caching of LLM calls.
- No companion history/memory provider.
- No gateway-only or platform-specific behavior.

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE).
