# Tool contracts

Six tools. All read/write session state; all are re-invocable. Tools 1–4 are ordinary MCP tools; 5–6 are **MCP Apps** (tools whose results render interactive UI in-chat).

---

## 1. `search_listings`

Pure filter over the mock dataset. **No ranking.**

**Input**
```jsonc
{
  "session_id": "uuid",
  "mode": "buy | rent",                 // required — selects for=sale|rent inventory
  "car_type": "string?",
  "budget_max": "number?",              // interpreted via intent.budget.period
  "brands": ["string"],                 // optional
  "fuel": ["petrol|diesel|hybrid|ev"],  // optional
  "transmission": "manual|automatic?",
  "seats_min": "number?",
  "available": { "from": "date", "to": "date" },  // optional — must overlap a listing window
  "location": "string?"
}
```

**Output:** `{ "count": n, "listings": [ListingSummary] }` (id, brand, model, category, year, price, fuel, transmission, seats, location, matched-availability window).

**State:** writes `research.last_query`; sets `phase="researching"`.

**Errors:** empty result is not an error — return `count: 0` with a nearest-miss relaxation hint (e.g. "3 more if budget +10%") so the agent can negotiate constraints.

---

## 2. `shortlist_candidates`

Deterministic score over the last search result. This is the auditable half of hybrid ranking.

**Input:** `{ "session_id": "uuid", "limit": 10 }`

**Scoring (weights in code, documented here):**
- `budget_fit` — closeness under budget (over-budget = already hard-filtered)
- `constraints` — fraction of `intent.constraints[]` satisfied
- `availability` — overlap quality with `target_date`

**Output:** `{ "shortlist": [ { listing_id, score, breakdown } ] }` — sorted, ≤ limit.

**State:** writes `research.shortlist`.

**Note:** the *agent* then ranks these and writes `research.ranked` with per-car reasoning in its own turn — that step is the LLM half and is traced separately. The agent must reference the user's stated needs, not invent criteria.

---

## 3. `hold_car` / `release_car`

**Input:** `{ "session_id", "listing_id", "note": "string?" }` — note is agent- or user-authored ("cheapest", "only hybrid").

**Output:** updated garage summary `{ "held": [...] }`.

**State:** appends to / removes from `garage.held`. Release also removes the id from `garage.compare_ids`. Holds survive constraint revisions (S2/S3).

**Errors:** holding an unknown listing_id → error; holding an already-held car → idempotent no-op, current note preserved unless a new note is passed.

---

## 4. `compare_cars`

**Input:** `{ "session_id", "listing_ids": ["l-042", "l-077"] }` — must all be held; 2–4 ids.

**Output:** normalized comparison matrix — rows = attributes (price, fuel, seats, availability vs target date, constraint hits, garage note), columns = cars. The A2UI compare table renders directly from this.

**State:** writes `garage.compare_ids`; sets `phase="comparing"`.

**Errors:** id not in garage → error naming the id (agent offers to hold it first).

---

## 5. `open_booking_form` — **MCP App (mandatory)**

**Input:** `{ "session_id", "listing_id" }` — must be held.

**Renders:** `ui://booking-form` in-chat (sandboxed iframe). Prefilled from session state: listing details, rental dates from `intent.target_date`, mode-appropriate fields (delivery/registration for buy; pickup/return for rent).

**On submit (iframe → host → tool round-trip):** validates, writes `checkout.form_data`, sets `checkout.active_listing_id`, `phase="booking"`. Returns a structured summary to the agent so it can confirm in prose.

**Errors:** listing not held → error; another checkout `pending` → error (one at a time by design).

---

## 6. `open_payment` — **MCP App (mandatory, fully mocked)**

**Input:** `{ "session_id" }` — requires `checkout.form_data` complete.

**Renders:** `ui://mock-payment` in-chat. Fake card fields, clearly labelled **MOCK — no real payment**. Test behaviors: card ending `0000` → declined (demos the failure path); anything else → success after a short simulated delay.

**On success:** `payment_status="confirmed"`, generates `confirmation_id`, appends to `checkout.completed[]`, clears `active_listing_id`, sets `phase="done"` (or back to `comparing` if garage still has held cars). Garage badges the booked car.

**On decline:** `payment_status="none"`, form stays open, agent explains and offers retry.

**Never:** touches a real payment API, stores card numbers in session state, or logs card fields to traces.

---

## Cross-cutting rules

- Every tool validates `session_id` and returns the new state `version` so the agent detects stale reads.
- Tool errors are structured `{ "error": { "code", "message", "hint" } }` — hints are agent-facing ("offer to relax budget").
- All calls traced (Langfuse span per call; state diff as attribute). Card-field redaction is enforced at the tracer.
