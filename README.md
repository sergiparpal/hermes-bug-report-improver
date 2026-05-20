# hermes-bug-report-improver
A Hermes Agent plugin that exposes the `improve_bug_report` tool. Given a poorly written or incomplete bug report, the tool returns a structured version with title, reproduction steps, expected and actual behavior, suggested severity, and a list of missing evidence. The plugin uses `ctx.llm` to delegate the rewrite to the agent's LLM.
