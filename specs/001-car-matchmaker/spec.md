# Spec 001 — AI Car Matchmaker

**Status:** Draft · **Date:** 2026-08-08 · **Hackathon:** Amulate Summer 2026

## Problem

Finding the right car to buy or rent means translating a fuzzy human need ("something for family road trips, around ₹20 lakh, by October") into structured search across marketplaces, weighing trade-offs, and completing a booking. This project builds an AI concierge that does all of it inside one conversation.

**This is not a listing website.** The deliverable is an agent that understands intent, reasons across multiple steps, uses tools, renders dynamic interfaces, and completes the booking workflow conversationally.

## User scenarios

### S1 — Happy path (rent)
Riya needs a car for a 5-day Goa trip next month. The agent interviews her (rent, SUV-ish, ≤₹3,500/day, dates), searches the marketplace, presents a ranked shortlist with per-car reasoning, she holds three, compares them, picks one, fills the booking form in-chat, pays via the mock payment interface, and receives a confirmation — without leaving the conversation.

### S2 — Revision mid-flow
Arjun sees recommendations and says "actually, under ₹15 lakh and only hybrids." The agent updates constraints in session state and re-runs search + shortlist. No restart, no re-interview of already-answered slots.

### S3 — Compare and defer
A user holds four cars while browsing, compares a subset side by side, asks "which would you pick and why?", and the agent reasons across the held set using its notes before the user proceeds to checkout with one.

### S4 — Ambiguous intent
User starts with "I need wheels." The agent's first job is the buy-vs-rent fork, because budget interpretation depends on it (₹50,000 total vs per month are different searches).

## Functional requirements

- **FR-1 Interview.** The agent conversationally captures: mode (buy/rent), use case, car type/category, budget (+period), target date (or rental range). It asks only for unfilled slots, never re-asks answered or declined ones, and renders interview progress via A2UI.
- **FR-2 Research.** The agent searches the marketplace via a `search` tool over the mock dataset. Search is re-invocable at any time with revised constraints.
- **FR-3 Ranked, explained suggestions.** A deterministic `shortlist` step narrows candidates (budget fit, constraint match, date availability) to ≤10; the agent then ranks those and attaches a genuine per-car rationale. Both steps are separately traceable.
- **FR-4 Hold & compare.** Users can hold multiple cars in a garage, annotate them, and compare any held subset in a live A2UI comparison view. Holds survive constraint revisions.
- **FR-5 Booking & payment (mandatory MCP Apps).** The booking form and the mock payment/checkout are MCP Apps rendered inside the chat. Payment is fully mocked — no real transactions, no external payment APIs. One car checks out at a time; completed bookings are recorded and badged in the garage.
- **FR-6 Mock marketplace.** ≥100 listings, ≥10 categories, ≥10 brands per category. Every listing carries availability windows so target dates are meaningful, plus price (with period semantics for rentals), fuel, transmission, seats, location, and features.
- **FR-7 Memory.** One session state object (see data-model.md) persists across interview → research → compare → booking. Every tool reads and writes it.
- **FR-8 Dynamic UI.** Catalogue, interview state, search status, and reasoning steps render via A2UI-driven dynamic UI — not static HTML.

## Non-goals

- Real payments or payment-provider integration.
- Real marketplace/dealership APIs (mock dataset chosen for determinism and demo reliability).
- Multi-user accounts or auth.
- BMW Group APIs (explicitly out of scope per brief).

## Acceptance checks

1. From a cold start, a user reaches a booking confirmation entirely in-chat (S1).
2. Changing budget after recommendations produces a revised shortlist without re-interviewing (S2).
3. The compare view updates live as cars are added/removed from comparison (S3).
4. Killing and resuming the session preserves intent, garage, and checkout state (FR-7).
5. Dataset validation script confirms FR-6 floor (100/10/10) and availability coverage.
6. Both MCP Apps render inside the conversation and drive state transitions on submit (FR-5).
