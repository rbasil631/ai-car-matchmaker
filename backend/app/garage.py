"""Garage tools: `hold_car`, `release_car`, `compare_cars`.

Contracts: specs/001-car-matchmaker/contracts/tools.md §3 and §4.

The garage is the durable set; comparison is a *view* over it (data-model §4).
That separation is why these functions never let a compare operation mutate
`held` — narrowing what you're looking at should not throw away what you saved.

Each function mutates the passed state dict and returns a result payload; the
caller commits the write. One writer per turn keeps version bumps traceable.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import marketplace

MAX_COMPARE = 4
MIN_COMPARE = 2


def _err(code: str, message: str, hint: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "hint": hint}}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---- tool 3: hold_car / release_car -----------------------------------------


def hold_car(state: dict[str, Any], listing_id: str,
             note: str | None = None) -> dict[str, Any]:
    """Idempotent. Re-holding a car keeps its existing note unless a new one is
    passed — a second click should never silently erase why you saved it."""
    listing = marketplace.listing_by_id(listing_id)
    if listing is None:
        return _err("unknown_listing", f"no listing {listing_id}",
                    "offer to search again")

    held = state["garage"]["held"]
    existing = next((h for h in held if h["listing_id"] == listing_id), None)
    if existing:
        if note:
            existing["note"] = note
        return {"held": held, "already_held": True, "listing_id": listing_id}

    held.append({
        "listing_id": listing_id,
        "held_at": _now(),
        "note": note or _auto_note(state, listing_id),
    })
    return {"held": held, "already_held": False, "listing_id": listing_id}


def release_car(state: dict[str, Any], listing_id: str) -> dict[str, Any]:
    garage = state["garage"]
    before = len(garage["held"])
    garage["held"] = [h for h in garage["held"] if h["listing_id"] != listing_id]
    if len(garage["held"]) == before:
        return _err("not_held", f"{listing_id} is not in the garage",
                    "list what is held before releasing")
    # releasing also drops it from the comparison — comparing an absent car is
    # the kind of stale state that produces confusing UI
    garage["compare_ids"] = [i for i in garage["compare_ids"] if i != listing_id]
    return {"held": garage["held"], "released": listing_id}


def _auto_note(state: dict[str, Any], listing_id: str) -> str:
    """Derive why this car is worth saving, from the shortlist that produced it.

    Only claims a superlative when it is true across the whole shortlist, so
    the note stays checkable rather than decorative.
    """
    shortlist = state["research"].get("shortlist") or []
    ids = [s["listing_id"] for s in shortlist]
    if listing_id not in ids:
        return ""
    listings = {i: marketplace.listing_by_id(i) for i in ids}
    me = listings[listing_id]
    scores = {s["listing_id"]: s for s in shortlist}

    if len(ids) > 1 and me["price"]["amount"] == min(
            l["price"]["amount"] for l in listings.values()):
        return "cheapest on the shortlist"
    if scores[listing_id]["breakdown"]["constraints"] == 1.0 and sum(
            1 for s in shortlist if s["breakdown"]["constraints"] == 1.0) == 1:
        return "only one meeting every requirement"
    if scores[listing_id]["breakdown"]["availability"] == 1.0 and sum(
            1 for s in shortlist if s["breakdown"]["availability"] == 1.0) == 1:
        return "only one free for the full date range"
    if ids and listing_id == max(scores, key=lambda i: scores[i]["score"]):
        return "top-ranked overall"
    return "shortlisted"


# ---- tool 4: compare_cars ----------------------------------------------------

def compare_cars(state: dict[str, Any], listing_ids: list[str]) -> dict[str, Any]:
    """Build a normalized comparison matrix. All ids must already be held."""
    if not (MIN_COMPARE <= len(listing_ids) <= MAX_COMPARE):
        return _err("bad_compare_size",
                    f"compare takes {MIN_COMPARE}-{MAX_COMPARE} cars, got {len(listing_ids)}",
                    "ask which ones to compare")

    held_ids = {h["listing_id"] for h in state["garage"]["held"]}
    for lid in listing_ids:
        if lid not in held_ids:
            return _err("not_held", f"{lid} is not in the garage",
                        f"offer to hold {lid} first")

    notes = {h["listing_id"]: h.get("note", "") for h in state["garage"]["held"]}
    cars = [marketplace.listing_by_id(i) for i in listing_ids]
    intent = state["intent"]
    target = marketplace._norm_range(intent.get("target_date"))
    constraints = intent.get("constraints") or []

    def price(l):
        per = "/day" if l["price"]["period"] == "per_day" else ""
        return f"₹{l['price']['amount']:,}{per}"

    rows = [
        {"label": "Price", "values": [price(l) for l in cars]},
        {"label": "Year", "values": [str(l["year"]) for l in cars]},
        {"label": "Category", "values": [l["category"] for l in cars]},
        {"label": "Fuel", "values": [l["fuel"] for l in cars]},
        {"label": "Transmission", "values": [l["transmission"] for l in cars]},
        {"label": "Seats", "values": [str(l["seats"]) for l in cars]},
        {"label": "Location", "values": [l["location"] for l in cars]},
        {"label": "Availability", "values": [_availability_cell(l, target) for l in cars]},
    ]
    if constraints:
        rows.append({"label": "Your requirements",
                     "values": [_constraints_cell(l, constraints) for l in cars]})
    rows.append({"label": "Your note",
                 "values": [notes.get(l["listing_id"], "") or "—" for l in cars]})

    state["garage"]["compare_ids"] = list(listing_ids)
    state["phase"] = "comparing"

    return {
        "columns": [{"listing_id": l["listing_id"],
                     "title": f"{l['brand']} {l['model']}"} for l in cars],
        "rows": rows,
    }


def _availability_cell(listing: dict, target: tuple[str, str] | None) -> str:
    if target is None:
        w = listing["availability"][0]
        return f"{w['from']} → {w['to']}"
    frm, to = target
    for w in listing["availability"]:
        if w["from"] <= frm and w["to"] >= to:
            return "covers your dates"
    for w in listing["availability"]:
        if marketplace._windows_overlap(w, frm, to):
            return "partial overlap"
    return "not available then"


def _constraints_cell(listing: dict, constraints: list[str]) -> str:
    marks = ["✓" if marketplace.constraint_satisfied(listing, c) else "✗"
             for c in constraints]
    return " · ".join(f"{m} {c}" for m, c in zip(marks, constraints))
