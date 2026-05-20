# Changelog

All notable changes to this project are documented here. This project adheres to
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-05-20

Initial release.

### Added

- **`improve_bug_report` tool** (toolset `qa`): turns a raw or incomplete bug
  report into a structured one — `title`, `summary`, `reproduction_steps`,
  `expected_behavior`, `actual_behavior`, `severity`, `severity_rationale`, and
  `missing_evidence`.
- **Two output formats** via the `format` parameter: Markdown (default) for humans
  and JSON for downstream tooling. Optional `context` parameter for environment
  details.
- **Model delegation** through `ctx.llm.complete_structured` (output schema
  enforced by the host; no provider keys in the plugin), with one automatic retry
  on missing or invalid output before returning a structured error.
- **Fixed severity rubric** — `critical` / `high` / `medium` / `low` / `unknown`.
- **Behavioral guarantees**: never invents missing facts (lists them as missing
  evidence), prefers `unknown` over guessing, preserves verbatim error messages
  and stack traces, and handles one bug per call (additional bugs are flagged in
  `missing_evidence`). The tool handler always returns a string and never raises.
- **Optional `/improve-bug` slash command** for direct invocation in a session
  (registration is guarded so it never blocks the core tool from loading).
- **Test suite** with a mocked `ctx.llm` (no network); 100% branch coverage on the
  handler.
