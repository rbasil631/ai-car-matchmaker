# Demo video script

**Target: 3 minutes.** Judges watch a lot of these; the first 20 seconds decide
whether they watch the rest. Lead with the finished booking, then show how it
got there.

## Before you record

```bash
# fresh session so the garage is empty on camera
rm -f backend/carmatch.sqlite
export ANTHROPIC_API_KEY=sk-ant-...      # so reasoning is model-written
docker compose up
```

- Open `http://localhost:8000` in a clean browser window (no bookmarks bar).
- Clear localStorage or use a private window — an old `carmatch-session` will
  resume a half-finished conversation on camera.
- Have a second tab ready on `http://localhost:8000/trace/<session-id>`.
- Zoom the browser to ~125%. Chat text is small on video compression.

## Shot list

### 0:00–0:20 — The claim

Start on the **finished confirmation** — the end state, before you show the
journey.

> "This is a car booked end to end inside a chat conversation. No listing site,
> no forms in another tab. Here's how the agent got there."

Then reload to an empty session.

### 0:20–0:55 — The interview

Type, letting each reply land:

```
rent
goa trip with friends
SUV
4000 per day
2026-09-10 to 2026-09-15
```

> "It asks only for what it doesn't know. Those chips are A2UI — the agent emits
> component JSON, the client renders it. Notice budget is 'per day' because it
> already knows this is a rental. Buy-or-rent changes what every later number
> means."

### 0:55–1:25 — Research and reasoning

Results appear.

> "Deterministic code filtered 388 listings down to six, scoring budget fit,
> requirements and date availability. Then the model ranked those six and wrote
> the reasoning."

**Point at one "Why:" line and read it aloud.** This is the moment that
distinguishes the project from a search box — make sure the line references the
Goa trip or the seven seats.

> "The split is deliberate. Code narrowing is fast and auditable; model ranking
> is where judgement belongs. Neither one pretending to be the other."

### 1:25–1:55 — Garage and live compare

Hold three cars. Toggle two into comparison, then a third.

> "Holding is durable. Comparing is a view over what's held — so narrowing what
> I'm looking at never throws away what I saved."

**Release one car while the table is on screen.** It disappears from the garage
and the comparison together.

> "That table is A2UI too — nested rows and columns, updating live. The basic
> catalog has no table component, so it's built the way the spec prescribes."

### 1:55–2:35 — Checkout, including failure

Click **Book this**.

> "Now a different UI paradigm. This form is an MCP App — a sandboxed iframe the
> server owns, rendered inside the chat. It's prefilled with dates from the
> interview."

Fill name and phone, continue. Payment app appears.

> "₹2,800 a day times five days. The payment app knows dates it never asked
> for, because every tool reads one shared session state."

**Type a card ending 0000 first.** Let the decline land.

> "Failure path, on purpose. The booking survives so you can retry — nothing is
> lost."

Retry with `4242 4242 4242 4242`. Confirmation appears; the garage badges the
car.

### 2:35–3:00 — The receipt

Switch to the trace tab.

> "Every step is traced. 'Code narrowed 15 to 6, model ranked those 6' — two
> separate spans, so you can audit which half made which decision. No card data
> anywhere: the payment iframe only ever sends the last four digits."

Close on the repo.

> "Spec-driven throughout — scenarios, tool contracts and verified protocol
> notes are all in the repo, written before the code. Eighty-eight tests,
> including every A2UI message validated against Google's published schemas."

## Things to avoid

- **Don't narrate the code.** Judges can read the repo; the video is for the
  behaviour they can't get from a diff.
- **Don't skip the decline.** A demo where nothing fails looks staged.
- **Don't apologise for the mock marketplace.** It's a deliberate choice for
  deterministic demos and it's stated in the spec.
- **Don't rush the "Why:" line.** It is the single most differentiating thing
  on screen.

## If the model call fails on the day

The cards fall back to score-derived explanations and everything else still
works. Say so plainly — "the ranking degrades to the deterministic scores when
the model is unreachable" — it demonstrates the fallback rather than hiding it.
