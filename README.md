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
- **Session state is shared, not a stage.** Every tool reads and writes one versioned session object — the payment app knows the dates the interview captured, which is how a ₹2,800/day rental becomes a ₹14,000 five-day total.
- **Two UI paradigms, one surface.** A2UI renders agent-driven dynamic UI (interview progress, catalogue, garage, live compare table). MCP Apps render the two mandatory server-owned interfaces: booking form and mock payment.
- **Hybrid ranking.** Deterministic filter + score narrows the marketplace to a shortlist; the agent ranks and explains those. The explanations are real reasoning, not post-hoc narration.
- **Compare/hold garage.** Users hold multiple cars, compare them side by side, and take one through checkout at a time. The comparison is a *view* over the held set — narrowing what you look at never discards what you saved.

## The full journey (scenario S1)

```
"I need a car"  →  interview (5 slots)  →  15 matches, 6 ranked with reasons
                →  hold  →  compare side by side  →  Book this
                →  booking form (MCP App, prefilled)
                →  mock payment (MCP App) → declined → retry → confirmed
                →  ₹14,000 · confirmation c-df3a0a58 · badged in the garage
```

## Running

```bash
docker compose up
# → http://localhost:8000
```

or locally:

```bash
pip install -r backend/requirements.txt
cd backend && python -m uvicorn app.main:app --reload
```

The marketplace dataset generates itself on first run. To materialise or inspect it directly:

```bash
python data/generate_listings.py     # deterministic, seeded
python data/validate_listings.py     # asserts the FR-6 floor
```

Tests (includes validating every emitted A2UI envelope against Google's real v0.9.1 spec schemas):

```bash
bash research/fetch_schemas.sh       # pulls A2UI spec schemas (pinned source)
cd backend && python -m pytest tests/
```

## Status

✅ **M1 — walking skeleton.** Chat loop, versioned session state with optimistic concurrency, interview driven by unfilled intent slots, live A2UI progress surface, MCP App rendering in a sandboxed iframe with a JSON-RPC `tools/call` round trip.

✅ **M2 — marketplace.** Seeded generator producing 388 listings across 12 categories with ≥10 brands in every category, a validator enforcing the FR-6 floor, `search_listings` (pure filter, returns a relaxation hint instead of dead-ending on zero results) and `shortlist_candidates` (deterministic scoring with a per-candidate breakdown).

✅ **M3 — garage & compare.** `hold_car` / `release_car` / `compare_cars`. Holds carry a note derived from the shortlist and survive constraint revisions. The comparison table is built from nested Row/Column with `weight` — the basic catalog has no Table component — and updates live as cars enter and leave. Releasing a car cascades out of the comparison so no stale column lingers.

✅ **M4 — checkout.** Both mandatory MCP Apps, wired end to end. The booking form is mode-aware (pickup/return for rentals, delivery/registration for purchases) and prefilled from session state. Mock payment quotes the real total, demonstrates the declined path on a card ending `0000`, keeps the booking intact for retry, then issues a confirmation id and badges the car in the garage. One car checks out at a time; a second attempt while one is pending is refused.

🚧 **Next.** The Claude Agent SDK swap, so ranking and per-car rationale come from the model rather than a score readout (the deterministic shortlist stays — that split is the point). Then M5: Langfuse tracing, slide deck, demo video.

Milestones: [`plan.md §5`](specs/001-car-matchmaker/plan.md).

## Spec-driven development

This project is built spec-first (GitHub spec-kit conventions). The spec artifacts are the source of truth:

| Artifact | Purpose |
|---|---|
| [`specs/001-car-matchmaker/spec.md`](specs/001-car-matchmaker/spec.md) | What & why — scenarios, functional requirements, acceptance checks |
| [`specs/001-car-matchmaker/plan.md`](specs/001-car-matchmaker/plan.md) | How — architecture, stack, UI paradigm split, milestones |
| [`specs/001-car-matchmaker/data-model.md`](specs/001-car-matchmaker/data-model.md) | Session state schema (the agent's memory) |
| [`specs/001-car-matchmaker/contracts/tools.md`](specs/001-car-matchmaker/contracts/tools.md) | The six tool contracts |
| [`specs/001-car-matchmaker/research.md`](specs/001-car-matchmaker/research.md) | Protocol facts verified from primary sources (A2UI v0.9.1, MCP Apps) |

## Hackathon requirements mapping

| Requirement | Where |
|---|---|
| Interactive in-UI interview | Agent loop, interview phase (FR-1) — **live** |
| Research + ranked, explained suggestions | `search_listings` + `shortlist_candidates` + agent ranking (FR-2, FR-3) — **deterministic half live** |
| **MCP Apps: form filling + mock payment (mandatory)** | `open_booking_form` + `open_payment`, both rendered in-chat with full state transitions (contracts §5, §6) — **live** |
| Dynamic UI via A2UI | A2UI v0.9.1 renderer — interview, catalogue, garage, compare table; every envelope spec-schema-validated in tests — **live** |
| Hold, annotate, compare side by side | `hold_car` / `release_car` / `compare_cars` (FR-4) — **live** |
| Mock marketplace ≥100 listings / ≥10 categories / ≥10 brands per category | `data/generate_listings.py` + `data/validate_listings.py` — **live (388 / 12 / ≥10)** |
| Multistep agent memory | Versioned session state (data-model.md) — **live** |
| No real payments | Payment is fully mocked and labelled; the iframe posts only the card's last four digits, so no card data reaches the server, session state, or traces — **live** |
| Agent harness | Claude Agent SDK (plan §2) |
| Docker / deployed | `docker compose up` — **live** |
| Bonus: observability | Langfuse via OpenTelemetry (plan §6) |

## Notes

- `data/listings.json` is git-ignored: the generator is seeded and deterministic, so the dataset is reproduced byte-for-byte rather than committed twice. A test asserts generator output matches the materialised file.
- BMW Group marques are deliberately absent from the mock data — the brief puts BMW APIs out of scope, and keeping the badge out avoids ambiguity.
- CI workflow (`.github/workflows/ci.yml`) is not committed: creating workflow files needs a token scope this project's GitHub connection lacks. The workflow would run `research/fetch_schemas.sh` then `pytest`.
