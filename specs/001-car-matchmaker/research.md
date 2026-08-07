# Research 001 — Protocol verification notes

Facts below were verified against primary sources on 2026-08-08, not recalled
from memory. This closes the "unverified" risk flagged in plan.md §3.

## A2UI — verified against google/A2UI @ main, specification/v0_9_1

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
  ChoicePicker, Slider, DateTimeInput.
- `Text` uses **`variant`** (h1–h5 | caption | body). `Button` requires
  **`child`** (component id, typically a Text) + **`action`** — there is no
  `label` property. Schemas use `unevaluatedProperties: false`, so stray keys
  are hard failures.
- Data binding: `Dynamic*` types accept a literal, a JSON Pointer `{"path": ...}`,
  or a function call.
- Client→server event shape: `{version, action: {name, surfaceId,
  sourceComponentId, timestamp, context}}`.
- Transport-agnostic; WebSocket is an explicitly sanctioned binding. Ordering +
  framing + metadata are the transport contract.
- Spec schemas are vendored in `research/a2ui-schemas/`; our emitted envelopes
  are validated against them in CI (`test_envelopes_validate_against_real_spec`).

## MCP Apps — verified against modelcontextprotocol/ext-apps @ main, specification/2026-01-26

- UI templates are **pre-declared MCP resources** under the `ui://` scheme,
  mimeType **`text/html;profile=mcp-app`**.
- Tool→UI linkage: **`_meta.ui.resourceUri`** on the tool descriptor. The flat
  `_meta["ui/resourceUri"]` form is **deprecated** and will be removed before GA
  — do not emit it.
- `_meta.ui.visibility: ["model", "app"]` controls who may call a tool (agent
  vs the app's own iframe).
- Host renders the resource in a **sandboxed iframe**; iframe⇄host communicate
  via **MCP JSON-RPC over postMessage** (e.g. the iframe issues `tools/call`).
- CSP configuration lives on the **resource** `_meta`, not the tool.
- MCP Apps is an optional extension negotiated via extension capabilities.

## Implications already applied in code

- `backend/app/a2ui.py` emits spec-valid v0.9.1 envelopes (proven by tests).
- `backend/app/mcp_app.py` uses the non-deprecated `_meta.ui.resourceUri` and
  correct mimeType; iframe speaks JSON-RPC `tools/call` to the host.
- `frontend/index.html` implements both paradigms in one surface: an A2UI
  renderer (basic-catalog subset) and an MCP Apps host (sandboxed iframe +
  JSON-RPC relay) — the plan §3 walking-skeleton mitigation, done.
