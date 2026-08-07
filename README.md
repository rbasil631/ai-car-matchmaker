# AI Car Matchmaker

A multistep AI agent that takes a user from "I need a car" to a mocked booking confirmation — entirely inside a chat conversation. Built for the **Amulate Summer Hackathon 2026**.

Not a car listing website. An automotive concierge: it interviews the user, researches a marketplace, reasons across options, and completes the booking workflow conversationally.

## How it works

```
User ⇄ A2UI renderer ⇄ Agent loop ⇄ Session state
                          ⇅
   [ search | shortlist | hold | compare | booking form (MCP App) | mock payment (MCP App) ]
                          ↓
                     Confirmation
```

- **Agent loop, not a pipeline.** Search, shortlist, and compare are tools the agent calls *repeatedly* as the user revises constraints ("actually, under ₹15 lakh", "anything hybrid?").
- **Session state is shared, not a stage.** Every tool reads and writes one versioned session object — the payment app knows the budget the interview captured.
- **Two UI paradigms, one surface.** A2UI renders agent-driven dynamic UI (interview progress, catalogue, compare table, reasoning steps). MCP Apps render the two mandatory server-owned interfaces: booking form and mock payment.
- **Hybrid ranking.** Deterministic filter + score narrows ~100 listings to a shortlist of ≤10; the agent ranks and explains those. The explanations are real reasoning, not post-hoc narration.
- **Compare/hold garage.** Users hold multiple cars, compare them side by side, and take one through checkout at a time.

## Spec-driven development

This project is built spec-first (GitHub spec-kit conventions). The spec artifacts are the source of truth:

| Artifact | Purpose |
|---|---|
| [`specs/001-car-matchmaker/spec.md`](specs/001-car-matchmaker/spec.md) | What & why — scenarios, functional requirements, acceptance checks |
| [`specs/001-car-matchmaker/plan.md`](specs/001-car-matchmaker/plan.md) | How — architecture, stack, UI paradigm split, milestones |
| [`specs/001-car-matchmaker/data-model.md`](specs/001-car-matchmaker/data-model.md) | Session state schema (the agent's memory) |
| [`specs/001-car-matchmaker/contracts/tools.md`](specs/001-car-matchmaker/contracts/tools.md) | The six tool contracts |

## Hackathon requirements mapping

| Requirement | Where |
|---|---|
| Interactive in-UI interview | Agent loop, interview phase (spec FR-1) |
| Research + ranked, explained suggestions | `search` + `shortlist` tools + agent ranking (spec FR-2, FR-3) |
| **MCP Apps: form filling + mock payment (mandatory)** | `open_booking_form` and `open_payment` MCP Apps (contracts §5, §6) |
| Dynamic UI via A2UI | A2UI renderer for catalogue, progress, reasoning (plan §3) |
| Mock marketplace ≥100 listings / ≥10 categories / ≥10 brands per category | `data/listings.json` + CI validator (spec FR-6) |
| Multistep agent memory | Session state (data-model.md) |
| Agent harness | Claude Agent SDK (plan §2) |
| Docker / deployed | Docker Compose (plan §2, milestone M5) |
| Bonus: observability | Langfuse via OpenTelemetry (plan §6) |

## Status

🚧 **Spec phase complete — implementation starting.** Milestones tracked in [`plan.md §5`](specs/001-car-matchmaker/plan.md).

## Running (placeholder)

```bash
# lands with milestone M1
docker compose up
```
