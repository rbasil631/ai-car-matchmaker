# Plan 001 — Technical approach

## 1. Architecture

An **agent loop with tools**, not a staged pipeline. The loop plans, calls tools, and revises based on user input and tool results. There is no separate "interview agent" — interviewing is the loop's behavior while required intent slots are null.

```
User ⇄ Frontend (chat + A2UI renderer + MCP App host)
            ⇄ Agent loop (Claude Agent SDK)
                 ⇄ Session state store (read/write every turn)
                 ⇄ Tools: search · shortlist · hold · compare
                 ⇄ MCP Apps: booking_form · mock_payment
```

Key properties:
- Every tool is re-invocable; "show me cheaper ones" re-enters `search` with revised constraints.
- Session state sits **beside** the loop, not inline — it is memory, not a stage.
- Interview logic = the set of null fields in `intent`. `interview.asked[]` prevents re-asking declined slots.

## 2. Stack decisions

| Concern | Choice | Why |
|---|---|---|
| Agent harness | **Claude Agent SDK** | Required-list option; first-class MCP support, which the mandatory MCP Apps make decisive |
| Backend | Python (FastAPI) | Agent SDK + MCP Python SDK share the runtime; websocket streaming to frontend |
| MCP Apps server | MCP Python SDK server exposing `ui://` templates + tools | Per MCP Apps spec: templates as resources, tools linked via `_meta`, iframe ⇄ host over postMessage JSON-RPC |
| Frontend | Web chat client that is (a) an A2UI renderer and (b) an MCP Apps host | The two mandated UI paradigms live in one surface — see §3 |
| Marketplace | Mock dataset (`data/listings.json`), generated + validated by script | Deterministic demos; FR-6 floor enforced in CI |
| State | Single JSON session object, SQLite-backed, versioned per write | Traceable state transitions feed observability |
| Ship | Docker Compose (backend + frontend) | Brief accepts container or deployment; container is reproducible for judges |

## 3. The two-paradigm UI problem (highest risk)

The brief mandates **both** A2UI (agent-emitted declarative component trees) and **MCP Apps** (server-owned sandboxed HTML iframes) in one chat surface.

- **A2UI owns:** interview progress, search status, reasoning-step display, catalogue cards, the live compare table. The agent emits component JSON; the client renders it. The compare table is the A2UI showcase — it updates as the garage changes.
- **MCP Apps own:** booking form and mock payment only (the mandatory two). Rendered as sandboxed iframes; user actions inside the iframe round-trip to the server as tool calls.
- **Handoff:** an A2UI "Book this" action triggers the agent to call `open_booking_form`, whose result carries the MCP App UI reference; the host renders the iframe in-chat. State (selected listing, prefill data) crosses via session state, never via UI-to-UI coupling.

Risk mitigations: build a walking skeleton of *both* renderers in week one before feature work; verify exact MCP Apps `_meta` keys and host↔iframe method names against current docs at implementation time (they have churned across spec revisions); treat A2UI schema details as unverified until read from a2ui.org.

## 4. Ranking: hybrid by design

- `shortlist` (deterministic code): filter by hard constraints, score by budget fit + feature match + date availability. Fast, testable, traceable. Output ≤10 with per-candidate score breakdown.
- Agent (LLM): ranks the shortlist and writes per-car reasoning referencing the user's own stated needs. Authentic explanation, bounded cost.

This split is deliberate: pure-code ranking makes the agent's "reasoning" post-hoc narration; pure-LLM ranking is slow and unstable. Hybrid keeps the explanation honest and the demo fast.

## 5. Milestones

1. **M1 — Walking skeleton:** chat loop + session state + trivial A2UI render + one MCP App iframe rendering end-to-end.
2. **M2 — Marketplace:** dataset generator + validator, `search` + `shortlist` tools, ranked recommendations with reasoning.
3. **M3 — Garage:** hold/compare tools + live A2UI compare table.
4. **M4 — Checkout:** booking form + mock payment MCP Apps, confirmation, completed-bookings badges.
5. **M5 — Ship:** Docker, README run instructions, Langfuse tracing, deck + demo video.

## 6. Observability (bonus)

Langfuse over OpenTelemetry. Trace unit = one agent turn; spans for each tool call; session-state diffs attached as span attributes so judges can replay "code narrowed 100→10, agent ranked those 10" as two auditable steps.
