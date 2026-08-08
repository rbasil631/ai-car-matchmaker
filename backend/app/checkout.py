"""Checkout: booking form and mock payment (contracts §5 and §6).

One car checks out at a time — `checkout.active_listing_id` is singular by
design (data-model §5), so these functions refuse to start a second checkout
while one is pending rather than quietly overwriting it.

Nothing here touches a real payment provider. The decline rule is a fixed test
behaviour (card ending 0000) so the failure path is demonstrable on demand.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from . import marketplace

DECLINE_SUFFIX = "0000"
REQUIRED_FORM_FIELDS = ("name", "phone")


def _err(code: str, message: str, hint: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "hint": hint}}


# ---- tool 5: open_booking_form ----------------------------------------------


def open_booking_form(state: dict[str, Any], listing_id: str) -> dict[str, Any]:
    """Prefill payload for the booking-form MCP App. The listing must be held."""
    held_ids = {h["listing_id"] for h in state["garage"]["held"]}
    if listing_id not in held_ids:
        return _err("not_held", f"{listing_id} is not in the garage",
                    "offer to hold it first")

    active = state["checkout"]["active_listing_id"]
    if active and active != listing_id and state["checkout"]["payment_status"] == "pending":
        return _err("checkout_in_progress",
                    f"a checkout for {active} is already in progress",
                    "finish or cancel that booking first")

    if any(c["listing_id"] == listing_id for c in state["checkout"]["completed"]):
        return _err("already_booked", f"{listing_id} is already booked",
                    "offer the confirmation details instead")

    listing = marketplace.listing_by_id(listing_id)
    mode = state["intent"]["mode"] or ("rent" if listing["for"] == "rent" else "buy")
    frm, to = _date_range(state)

    return {
        "session_id": state["session_id"],
        "listing_id": listing_id,
        "mode": mode,
        "summary": f"{listing['brand']} {listing['model']} ({listing['year']}) · "
                   f"{_price_label(listing)}",
        "location": listing["location"],
        "prefill_from": frm or "",
        "prefill_to": to or "",
    }


def submit_booking_form(state: dict[str, Any], listing_id: str,
                        form_data: dict[str, Any]) -> dict[str, Any]:
    """Validate and commit the form. Sets the active checkout and phase."""
    held_ids = {h["listing_id"] for h in state["garage"]["held"]}
    if listing_id not in held_ids:
        return _err("not_held", f"{listing_id} is not in the garage",
                    "offer to hold it first")

    missing = [f for f in REQUIRED_FORM_FIELDS if not str(form_data.get(f, "")).strip()]
    if missing:
        return _err("incomplete_form",
                    f"please fill in: {', '.join(missing)}",
                    "ask the user for the missing fields")

    state["checkout"]["form_data"] = dict(form_data)
    state["checkout"]["active_listing_id"] = listing_id
    state["checkout"]["payment_status"] = "pending"
    state["phase"] = "booking"
    return {"ok": True, "listing_id": listing_id}


# ---- tool 6: open_payment ----------------------------------------------------


def open_payment(state: dict[str, Any]) -> dict[str, Any]:
    """Prefill payload for the payment MCP App. Requires a completed form."""
    checkout = state["checkout"]
    listing_id = checkout["active_listing_id"]
    if not listing_id or not checkout["form_data"]:
        return _err("no_booking", "no booking details captured yet",
                    "open the booking form first")

    listing = marketplace.listing_by_id(listing_id)
    total, breakdown = quote(state, listing)
    return {
        "session_id": state["session_id"],
        "listing_id": listing_id,
        "amount_label": f"₹{total:,}",
        "summary": f"{listing['brand']} {listing['model']} · {breakdown}",
    }


def submit_payment(state: dict[str, Any], card_last4: str) -> dict[str, Any]:
    """Mock authorisation.

    `card_last4` is all the iframe sends — no full number, expiry or CVV ever
    reaches this process, so there is nothing sensitive to redact downstream.
    """
    checkout = state["checkout"]
    listing_id = checkout["active_listing_id"]
    if not listing_id or not checkout["form_data"]:
        return _err("no_booking", "no booking to pay for",
                    "open the booking form first")

    if str(card_last4).endswith(DECLINE_SUFFIX):
        # Contract §6: leave the booking intact so the user can retry.
        checkout["payment_status"] = "pending"
        return {"status": "declined",
                "message": "Card declined. Try a different card."}

    listing = marketplace.listing_by_id(listing_id)
    total, _ = quote(state, listing)
    confirmation_id = f"c-{uuid.uuid4().hex[:8]}"

    checkout["payment_status"] = "confirmed"
    checkout["confirmation_id"] = confirmation_id
    checkout["completed"].append({
        "listing_id": listing_id,
        "confirmation_id": confirmation_id,
        "amount": total,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    })
    checkout["active_listing_id"] = None
    checkout["form_data"] = {}

    # Back to comparing if other held cars are still unbooked; otherwise done.
    booked = {c["listing_id"] for c in checkout["completed"]}
    remaining = [h for h in state["garage"]["held"] if h["listing_id"] not in booked]
    state["phase"] = "comparing" if remaining else "done"

    return {"status": "confirmed", "confirmation_id": confirmation_id,
            "amount": total, "listing_id": listing_id}


# ---- pricing -----------------------------------------------------------------


def quote(state: dict[str, Any], listing: dict[str, Any]) -> tuple[int, str]:
    """Total payable plus a human-readable breakdown.

    Rentals multiply the day rate by the requested range, so the payment screen
    shows what the user actually owes rather than a per-day sticker price.
    """
    amount = listing["price"]["amount"]
    if listing["price"]["period"] != "per_day":
        return amount, "purchase total"

    days = _rental_days(state)
    return amount * days, f"₹{amount:,}/day × {days} day{'s' if days != 1 else ''}"


def _rental_days(state: dict[str, Any]) -> int:
    frm, to = _date_range(state)
    if not frm or not to:
        return 1
    try:
        d = (date.fromisoformat(to) - date.fromisoformat(frm)).days
    except ValueError:
        return 1
    return max(1, d)


def _date_range(state: dict[str, Any]) -> tuple[str | None, str | None]:
    target = state["intent"].get("target_date")
    if isinstance(target, dict):
        return target.get("from"), target.get("to")
    if isinstance(target, str):
        return target, None
    return None, None


def _price_label(listing: dict[str, Any]) -> str:
    per = "/day" if listing["price"]["period"] == "per_day" else ""
    return f"₹{listing['price']['amount']:,}{per}"
