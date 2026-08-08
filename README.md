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
- **Session state is shared, not a stage.** Every tool reads and writes one versioned session object — which is how a ₹2,800/day rental becomes a ₹14,000 five-day total on a payment screen that never asked for dates.
- **Two UI paradigms, one surface.** A2UI renders agent-driven dynamic UI (interview progress, catalogue, garage, live compare table). MCP Apps render the two mandatory server-owned interfaces: booking form and mock payment.
- **Hybrid ranking.** Deterministic filter + score narrows the marketplace to a shortlist; the model ranks those and writes per-car reasoning. The split is what makes the explanation auditable.
- **Degraded, never wrong.** No model? Deterministic scores still rank and explain. Bad generation? Grounding checks reject it. Card declined? The booking survives for a retry.

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

**Optional — model-written reasoning** (otherwise the cards explain via deterministic scores):

```bash
pip install -r backend/requirements-agent.txt   # ~286 MB; the wheel bundles the Claude Code CLI
export ANTHROPIC_API_KEY=sk-ant-...
```

**Optional — hosted tracing** (the local trace endpoint works without it):

```bash
pip install -r backend/requirements-observability.txt
export LANGFUSE_PUBLIC_KEY=... LANGFUSE_SECRET_KEY=...
```

The marketplace dataset generates itself on first run:

```bash
python data/generate_listings.py     # deterministic, seeded
python data/validate_listings.py     # asserts the FR-6 floor
```

Tests (includes validating every emitted A2UI envelope against Google's real v0.9.1 spec schemas):

```bash
bash research/fetch_schemas.sh
cd backend && python -m pytest tests/     # 88 tests
```

## Observability

Every tool call is traced. `GET /trace/{session_id}` returns the spans plus a digest — no account required:

```json
{ "narrowing": "code narrowed 15→6, model ranked those 6",
  "span_count": 3, "errors": [] }
```

The deterministic step and the model step are separate spans on purpose: that split is the auditability claim. Personal fields are redacted at the tracer, and card data never reaches the server at all.

## Deliverables

| Artifact | Where |
|---|---|
| Slide deck | `docs/ai-car-matchmaker.pptx` |
| Demo video script | [`docs/demo-script.md`](docs/demo-script.md) |
| Public repo | this repository |

## Spec-driven development

Built spec-first (GitHub spec-kit conventions). The spec artifacts are the source of truth:

| Artifact | Purpose |
|---|---|
| [`specs/001-car-matchmaker/spec.md`](specs/001-car-matchmaker/spec.md) | What & why — scenarios, functional requirements, acceptance checks |
| [`specs/001-car-matchmaker/plan.md`](specs/001-car-matchmaker/plan.md) | How — architecture, stack, UI paradigm split, milestones |
| [`specs/001-car-matchmaker/data-model.md`](specs/001-car-matchmaker/data-model.md) | Session state schema (the agent's memory) |
| [`specs/001-car-matchmaker/contracts/tools.md`](specs/001-car-matchmaker/contracts/tools.md) | The six tool contracts |
| [`specs/001-car-matchmaker/research.md`](specs/001-car-matchmaker/research.md) | Protocol facts verified from primary sources (A2UI, MCP Apps, Claude Agent SDK) |

Reading the specs rather than recalling them caught three bugs that would have shipped silently: `Text.usageHint` → `variant`, `Button.label` → `child`, and `updateDataModel.contents` → `value`.

## Hackathon requirements mapping

| Requirement | Where |
|---|---|
| Interactive in-UI interview | Agent loop, interview phase (FR-1) — **live** |
| Research + ranked, explained suggestions | `search_listings` + `shortlist_candidates` + model ranking (FR-2, FR-3) — **live** |
| **MCP Apps: form filling + mock payment (mandatory)** | `open_booking_form` + `open_payment`, both rendered in-chat with full state transitions — **live** |
| Dynamic UI via A2UI | A2UI v0.9.1 — interview, catalogue, garage, compare table; every envelope schema-validated in tests — **live** |
| Hold, annotate, compare side by side | `hold_car` / `release_car` / `compare_cars` (FR-4) — **live** |
| Mock marketplace ≥100 / ≥10 categories / ≥10 brands per category | `data/generate_listings.py` + validator — **live (388 / 12 / ≥10)** |
| Multistep agent memory | Versioned session state (data-model.md) — **live** |
| No real payments | Fully mocked and labelled; the iframe posts only the card's last four digits — **live** |
| Agent harness | **Claude Agent SDK** — ranking + per-car reasoning (`backend/app/llm_ranker.py`) |
| Docker / deployed | `docker compose up` — **live** |
| Bonus: observability | Local trace endpoint + optional Langfuse export — **live** |

## Notes

- `data/listings.json` is git-ignored: the generator is seeded and deterministic, so the dataset is reproduced byte-for-byte rather than committed twice. A test asserts generator output matches the materialised file.
- BMW Group marques are deliberately absent from the mock data — the brief puts BMW APIs out of scope, and keeping the badge out avoids ambiguity.
- CI: `ci/workflow.yml` holds the pipeline. Move it to `.github/workflows/ci.yml` locally — creating workflow files needs a token scope this project's GitHub connection lacks.
