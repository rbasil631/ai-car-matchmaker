# Data model — Session state

One object per session. Versioned on every write (monotonic `version`, `updated_at`) so state transitions are traceable in observability.

```jsonc
{
  "session_id": "uuid",
  "version": 42,
  "phase": "interview | researching | comparing | booking | done",
  "updated_at": "ISO-8601",

  "intent": {
    "mode": "buy | rent | null",          // null until captured — nulls drive the interview
    "use_case": "string | null",           // "family road trips", "city commute"
    "car_type": "string | null",           // category from the dataset taxonomy
    "budget": {
      "amount": "number | null",
      "period": "total | per_day | per_month",  // 'total' for buy; interprets amount
      "currency": "INR"
    },
    "target_date": "ISO date | { from, to } | null",  // range for rentals
    "constraints": ["hybrid", "seats>=7", "automatic"]  // grows as user adds
  },

  "interview": {
    "asked": ["mode", "budget"],   // slots asked — never re-ask declined slots
    "complete": false               // true when required slots are non-null
  },

  "research": {
    "last_query": { },              // filter args of most recent search
    "shortlist": [                   // written by deterministic shortlist tool
      { "listing_id": "l-042", "score": 0.87, "breakdown": { "budget_fit": 0.9, "constraints": 1.0, "availability": 0.7 } }
    ],
    "ranked": [                      // written by the agent
      { "listing_id": "l-042", "rank": 1, "reasoning": "string — references user's stated needs" }
    ]
  },

  "garage": {
    "held": [                        // durable compare/hold set
      { "listing_id": "l-042", "held_at": "ISO-8601", "note": "only hybrid in budget" }
    ],
    "compare_ids": ["l-042", "l-077"]  // view over held set — comparing ≠ holding
  },

  "checkout": {
    "active_listing_id": "l-042 | null",  // singular by design: one car at a time
    "form_data": { },                      // written by booking_form MCP App
    "payment_status": "none | pending | confirmed",
    "confirmation_id": "string | null",
    "completed": [                          // past confirmed bookings
      { "listing_id": "l-013", "confirmation_id": "c-981", "completed_at": "ISO-8601" }
    ]
  }
}
```

## Design decisions

1. **Nulls drive the interview.** There is no separate interview state machine — the agent asks about whatever required `intent` field is still null. `interview.asked[]` exists only to avoid re-asking a slot the user declined.
2. **`budget.period` is load-bearing.** ₹50,000 total (buy) vs ₹50,000/month (rent) are different searches. Buy-vs-rent changes how every downstream number is interpreted, so period lives inside budget.
3. **`shortlist` vs `ranked` are separate.** Deterministic code writes the former with score breakdowns; the agent writes the latter with reasoning. Two auditable steps in the trace.
4. **`garage.held` vs `compare_ids`.** Comparing is a *view* over the held set. The held set is stable while the comparison shifts; compare tweaks never mutate the garage.
5. **`checkout.active_listing_id` is singular.** One car goes through the booking form and payment at a time — keeps the MCP App flow unambiguous. `completed[]` supports booking more than one car over a session (badged in the garage).

## Marketplace listing shape (`data/listings.json`)

```jsonc
{
  "listing_id": "l-042",
  "for": "sale | rent",
  "brand": "string",
  "model": "string",
  "category": "string",            // ≥10 categories across dataset
  "year": 2024,
  "price": { "amount": 1450000, "period": "total | per_day", "currency": "INR" },
  "fuel": "petrol | diesel | hybrid | ev",
  "transmission": "manual | automatic",
  "seats": 5,
  "location": "string",
  "features": ["sunroof", "adas"],
  "availability": [ { "from": "ISO date", "to": "ISO date" } ]   // makes target_date meaningful
}
```

Dataset floor (validated in CI): ≥100 listings, ≥10 categories, ≥10 brands per category, every listing has ≥1 availability window.
