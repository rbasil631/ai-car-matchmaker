# Research 001 — Protocol verification notes

Facts below were verified against primary sources, not recalled from memory.
This closes the "unverified" risk flagged in plan.md §3.

## A2UI — verified against google/A2UI @ main, specification/v0_9_1 (2026-08-08)

- Current production protocol: **v0.9.1** (v1.0 is RC, v0.8 legacy). Every
  envelope carries `"version": "v0.9.1"`.
- Four server→client message types: `createSurface` (requires `surfaceId` AND
  `catalogId`), `updateComponents`, `updateDataModel`, `deleteSurface`.
- `updateDataModel` payload key is **`value`** (replaces at `path`; omitting
  `value` deletes the key). Not `contents`.
- Components are a **flat list** with `id` references — children referred to by
  ID, never inline. Discriminator property: `component`.
- Basic catalog (18 components): Text, Image, Icon, Video, AudioPlayer, Row,
  Column, List, Card, Tabs, Modal, Divider, Button, TextField, CheckBox,
  ChoicePicker, Slider, DateTimeInput. **No Table** — grids are built by
  nesting Columns inside Rows, per the spec's own guidance.
- `Text` uses **`variant`** (h1–h5 | caption | body). `Button` requires
  **`child`** (component id, typically a Text) + **`action`** — there is no
  `label` property. `weight` (from `CatalogComponentCommon`) is valid only on a
  direct descendant of a Row or Column. Schemas use
  `unevaluatedProperties: false`, so stray keys are hard failures.
- Data binding: `Dynamic*` types accept a literal, a JSON Pointer
  `{"path": ...}`, or a function call.
- Client→server event shape: `{version, action: {name, surfaceId,
  sourceComponentId, timestamp, context}}`.
- Transport-agnostic; WebSocket is an explicitly sanctioned binding.
- Spec schemas are fetched by `research/fetch_schemas.sh`; every envelope this
  app emits is validated against them in the test suite.

## MCP Apps — verified against modelcontextprotocol/ext-apps @ main, specification/2026-01-26

- UI templates are **pre-declared MCP resources** under the `ui://` scheme,
  mimeType **`text/html;profile=mcp-app`**. Because templates are pre-declared
  rather than generated per call, one template must handle every variant it
  will be asked to render — hence our booking form branching on buy vs rent
  from its prefill payload.
- Tool→UI linkage: **`_meta.ui.resourceUri`** on the tool descriptor. The flat
  `_meta["ui/resourceUri"]` form is **deprecated** and will be removed before
  GA — do not emit it.
- `_meta.ui.visibility: ["model", "app"]` controls who may call a tool.
- Host renders the resource in a **sandboxed iframe**; iframe⇄host communicate
  via **MCP JSON-RPC over postMessage**.
- CSP configuration lives on the **resource** `_meta`, not the tool.
- MCP Apps is an optional extension negotiated via extension capabilities.

## Claude Agent SDK — verified against installed `claude-agent-sdk` 0.2.134 (2026-08-08)

Checked by installing the package and reading its actual signatures, because
agent-SDK APIs churn and a wrong guess here would be invisible until runtime.

- **Package name is `claude-agent-sdk`**, import `claude_agent_sdk`.
- Two entry points:
  - `query(*, prompt, options=None, transport=None)` → `AsyncIterator[Message]`.
    Stateless, one-shot; documented for batch/scripted use.
  - `ClaudeSDKClient` for stateful, interactive conversations with interrupts.
- `ClaudeAgentOptions` is a dataclass; fields we care about: `system_prompt`,
  `mcp_servers`, `allowed_tools`, `model`, `max_turns`, `permission_mode`,
  `max_budget_usd`, `cwd`, `env`, `setting_sources`.
- **In-process tools** are the important capability for this project:
  - `@tool(name, description, input_schema)` decorates an async function into
    an `SdkMcpTool`. `input_schema` accepts a dict of types, a TypedDict, or
    full JSON Schema.
  - `create_sdk_mcp_server(name, version="1.0.0", tools=[...])` returns an
    `McpSdkServerConfig` that runs **inside our own process** — no IPC, and
    tools get direct access to application state. That is exactly what our
    session-state-sharing tool contracts need.
- Message/content types for parsing replies: `AssistantMessage`, `UserMessage`,
  `SystemMessage`, `ResultMessage`, and blocks `TextBlock`, `ThinkingBlock`,
  `ToolUseBlock`, `ToolResultBlock`.
- **Transport is a subprocess running the Claude Code CLI.** The wheel is
  platform-specific and **bundles the binary** at
  `claude_agent_sdk/_bundled/claude` (~286 MB installed), falling back to
  `shutil.which("claude")`. Consequences:
  - No separate Node/npm install is required — but the Docker image grows
    substantially, so the agent extra is kept optional (see below).
  - `CLINotFoundError` is the failure mode when neither bundled nor PATH CLI
    is present; it must be caught and degraded from, not allowed to crash a
    demo.
- `query(transport=...)` accepts a custom `Transport`
  (`connect`/`write`/`read_messages`/`close`/`end_input`/`is_ready`), so the
  integration is **testable without network access or an API key** by injecting
  a scripted transport.

### Implications applied

- The four marketplace/garage tools are exposed to the agent as in-process SDK
  MCP tools rather than a hand-rolled dispatch loop.
- `ScriptedAgent` is retained as an offline fallback so the app still runs for
  a judge with no API key, and so `CLINotFoundError` / missing-key conditions
  degrade instead of failing.
- The SDK is an optional dependency (`requirements-agent.txt`), keeping the
  base image small and the test suite runnable without a 286 MB install.
