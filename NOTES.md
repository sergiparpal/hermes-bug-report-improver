# Phase 0 — Verification Notes

**Date:** 2026-05-20
**Verified against:** `NousResearch/hermes-agent` @ `main` (pyproject `version = "0.14.0"`, `requires-python = ">=3.11"`).
**Local toolchain:** Python 3.12.3, pytest 7.4.4.

The plan (`hermes-bug-report-improver_plan.md`) explicitly defers to whatever the
official docs/source actually state (§4.3). Several plan assumptions turned out to
be wrong; the **as-built** facts below are authoritative and the code follows them.

---

## Source 1 — `ctx.llm` API
Docs: `developer-guide/plugin-llm-access`
Source: `hermes-example-plugins/plugin-llm-example/__init__.py` (real, complete plugin).

Two sync methods (`+` async `acomplete` / `acomplete_structured` with identical params):

```python
result = ctx.llm.complete(
    messages=[{"role": "system"|"user", "content": "..."}],
    provider=None, model=None, temperature=None, max_tokens=None,
    timeout=None, agent_id=None, profile=None, purpose="audit-string",
)   # -> PluginLlmCompleteResult

result = ctx.llm.complete_structured(
    instructions="...",                       # what to extract
    input=[{"type": "text", "text": "..."},   # or {"type":"image","data"/"url",...}
    json_schema={...},                        # the OUTPUT schema to enforce
    json_mode=False, schema_name=None, system_prompt=None,
    provider=None, model=None, temperature=None, max_tokens=None,
    timeout=None, agent_id=None, profile=None, purpose=None,
)   # -> PluginLlmStructuredResult
```

Return objects:
- `PluginLlmCompleteResult`: `text:str`, `provider:str`, `model:str`, `agent_id:str`, `usage:PluginLlmUsage`, `audit:dict`.
- `PluginLlmStructuredResult` (extends above): `parsed: Optional[Any]` (validated object when JSON), `content_type:str` (`"json"`|`"text"`).
- `PluginLlmUsage`: `input_tokens`, `output_tokens`, `total_tokens`, `cache_read_tokens`, `cache_write_tokens`, `cost_usd`.

**Real usage pattern (from `plugin-llm-example`):**
```python
result = ctx.llm.complete_structured(
    instructions=_INSTRUCTIONS, input=inputs, json_schema=_RECEIPT_SCHEMA,
    schema_name="receipt.record", purpose="...", temperature=0.0, max_tokens=512,
)
if result.parsed is not None:
    ... json.dumps(result.parsed) ...
else:
    ... result.text ...   # graceful fallback, no exception raised to host
```

**Decision:** the handler uses `complete_structured(json_schema=BUG_REPORT_OUTPUT_SCHEMA)`.
The framework enforces the schema and returns `result.parsed`, so the plan's
"prompt for JSON → parse → retry once" loop (§4 Phase 4) is largely handled by the
host. We still keep a one-retry fallback for the case where `parsed is None`.

---

## Source 2 — Plugin contract / `register_tool`
Docs: `guides/build-a-hermes-plugin`  (NB: the plan's URL `developer-guide/build-a-hermes-plugin` is **404**; correct path is `guides/...`.)
Source: `hermes-agent/plugins/spotify/__init__.py` + `plugins/spotify/tools.py` (real `register_tool`).

```python
def register(ctx) -> None:
    ctx.register_tool(
        name="...",                 # str
        toolset="...",              # str — custom namespace; created if it doesn't exist
        schema=SOME_SCHEMA,         # dict with TOP-LEVEL name/description/parameters
        handler=some_handler,       # Callable[[dict], str]
        check_fn=None,              # optional gate; hide tool if returns False
        # emoji="..."               # optional, exists in source but not required
    )
```

**Tool schema shape (verbatim from spotify):**
```python
SPOTIFY_SEARCH_SCHEMA = {
    "name": "spotify_search",
    "description": "Search the Spotify catalog ...",
    "parameters": {"type": "object", "properties": {...}, "required": ["query"]},
}
```
→ The `schema` is a full tool schema (`name`/`description`/`parameters`), **not** just
the raw parameters object the plan's §2.1 shows. Code wraps the §2.1 schema accordingly.

**Tool handler signature (verbatim from spotify):**
```python
def _handle_spotify_search(args: dict, **kw) -> str:
    query = str(args.get("query") or "").strip()
    if not query:
        return tool_error("query is required")
```
→ Handler receives a **single `args` dict** (+ `**kw`) and **returns a string; never
raises**. NOT `improve_bug_report(raw_text, context, format)` as the plan's Phase 4
states. Our handler is `improve_bug_report_handler(args, **kwargs) -> str` and reads
`args["raw_text"]`, `args.get("context","")`, `args.get("format","markdown")`.

Other `ctx` methods (from build guide): `register_hook`, `register_command(name, handler, description="")`,
`register_cli_command`, `register_skill`, `dispatch_tool`.

---

## Source 3 — Plugin loading mechanism
Source: `hermes-agent/hermes_cli/plugins.py` (the actual loader).

```python
spec = importlib.util.spec_from_file_location(
    module_name, init_file, submodule_search_locations=[str(plugin_dir)],
)
# module_name = f"{_NS_PARENT}.{slug}"; slug = key.replace("/","__").replace("-","_")
```
- Plugin dir is loaded **as a package** (submodule search locations set) under name
  `hermes_plugins.hermes_bug_report_improver`. → **Relative imports work**
  (`from .handler import ...`, `from . import schema, prompts`). No `sys.path` hacks.
- Manifest fields the loader actually reads: `name, version, description, author,
  requires_env, provides_tools, provides_hooks, kind` (kind whitelist-validated).
  It does **NOT** read `requires_hermes_version` or `license` (ignored, harmless).

---

## Source 4 — Discovery & enablement
Docs: `user-guide/features/plugins`.

Discovery order (later overrides earlier): bundled `<repo>/plugins/` → user
`~/.hermes/plugins/` → project `.hermes/plugins/` (needs `HERMES_ENABLE_PROJECT_PLUGINS=true`)
→ pip entry points. Disabled by default; enable explicitly:

```yaml
plugins:
  enabled:
    - hermes-bug-report-improver
  disabled:        # optional deny-list, always wins
    - some-plugin
```
CLI: `hermes plugins enable <name>` / `hermes plugins disable <name>` / `hermes plugins` (interactive).

---

## Source 5 — Toolsets
Docs: `reference/toolsets-reference`.

Built-in toolsets: browser, clarify, code_execution, cronjob, debugging, delegation,
discord, discord_admin, feishu_doc, feishu_drive, file, homeassistant, computer_use,
image_gen, video_gen, kanban, memory, messaging, moa, safe, search, session_search,
skills, spotify, terminal, todo, tts, vision, video, web, x_search, yuanbao.

- **No `qa` toolset exists.** But plugins register their own toolset just by naming it
  in `register_tool` (confirmed: spotify uses `toolset="spotify"`).
- **Decision:** use a dedicated `toolset="qa"` — matches the plan's intent and is
  created on registration.

---

## Assumptions: confirmed vs changed (vs the plan)

| Plan said | Reality | Action |
|---|---|---|
| `ctx.llm(...)` called directly, signature TBD | `ctx.llm.complete(...)` / `ctx.llm.complete_structured(...)` | Use `complete_structured` |
| `register_tool(... description=, requires_env=, ...)` | `register_tool(name, toolset, schema, handler, check_fn, override, [emoji])` — no `description`/`requires_env` args | description lives **inside** `schema`; `requires_env` is a manifest field |
| input schema passed as bare `{type,properties,required}` | schema must be `{name, description, parameters:{...}}` | Wrap §2.1 schema |
| handler `improve_bug_report(raw_text, context, format)` | `handler(args: dict, **kw) -> str`, returns string, never raises | Single-dict handler |
| manifest field `category: general` | No `category` field; optional `kind` is whitelist-validated (general = omit kind) | **Omit** kind/category |
| ctx.llm is "v0.13.0+"; declare `requires_hermes_version` | Field exists but loader **ignores** it; current version 0.14.0 | Declare `"0.13.0"` as documentation only; real safety = handler try/except |
| doc URL `developer-guide/build-a-hermes-plugin` | 404 — real path `guides/build-a-hermes-plugin` | n/a |
| LICENSE: MIT recommended | Repo already ships **GPL-3.0** (deliberate user choice) | Keep GPL-3.0; do not clobber |
| Phase 4: parse JSON + retry once manually | `complete_structured` returns validated `result.parsed` | Keep a 1-retry fallback for `parsed is None` |

## Decisions locked for v0.1.0
- `toolset = "qa"` (created on registration).
- Output format: `format="json"` → return the structured object as a JSON string;
  `format="markdown"` → return a rendered Markdown string; **all error paths** return
  `json.dumps({"error": ...})`. (Handler always returns a string, never raises.)
- `requires_hermes_version: "0.13.0"` in manifest as documentation (loader ignores it).
- License stays GPL-3.0-or-later.
- Pure stdlib only (json, dataclasses, etc.). pytest is a dev/test dependency only.

## Cannot be verified in this environment (require the user's Hermes install)
These live steps from the plan need a running Hermes v0.13+ with model credentials and
are **left for the user** — they are not marked done:
- Phase 1: launch Hermes, confirm the tool appears, confirm stub returns its string.
- Phase 3: dry-run the prompt against the live model.
- Phase 4: invoke the tool end-to-end against a real LLM.
Everything else (all code + the mocked-LLM test suite) is built and runs locally.
