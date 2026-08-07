"""Agent loop.

Design: the loop is pluggable. `ScriptedAgent` is deterministic slot filling
that proves the architecture (nulls drive the interview, state is shared,
surfaces update incrementally) with zero API dependencies, so the app runs
anywhere. The Claude Agent SDK slots in behind the same `respond()` interface;
nothing upstream changes.

ScriptedAgent's "NLU" is deliberately crude (keyword + number extraction).
It is scaffolding, not the product — do not extend it; replace it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import a2ui, marketplace
from .state import SessionStore, missing_slots

SLOT_QUESTIONS = {
    "mode": "Are you looking to buy a car, or rent one?",
    "use_case": "What will you mainly use it for? (e.g. family trips, city commute, weekend getaways)",
    "car_type": "Any preference on type — SUV, sedan, hatchback, something else?",
    "budget": "What's your budget? (total if buying, per day if renting)",
    "target_date": "When do you need it by? (a date, or a from–to range for rentals)",
}


@dataclass
class AgentReply:
    """One agent turn: prose + zero or more A2UI messages for the client."""

    text: str
    a2ui_messages: list[dict[str, Any]] = field(default_factory=list)


class ScriptedAgent:
    """Offline interview agent. Asks for the first missing intent slot;
    parses the user's answer into state; renders progress via A2UI."""

    def __init__(self, store: SessionStore) -> None:
        self.store = store

    def respond(self, session_id: str, user_text: str) -> AgentReply:
        state = self.store.get_or_create(session_id)
        first_render = not state["interview"]["asked"]

        # 1. Try to absorb the user's message into the pending slot.
        pending = state["interview"]["asked"][-1] if state["interview"]["asked"] else None
        if pending and user_text.strip():
            self._fill_slot(state, pending, user_text)

        # 2. Nulls drive the interview: ask the next missing slot.
        missing = missing_slots(state)
        if missing:
            slot = missing[0]
            if slot not in state["interview"]["asked"]:
                state["interview"]["asked"].append(slot)
            reply_text = SLOT_QUESTIONS[slot]
        else:
            state["interview"]["complete"] = True
            state = self.store.save(state)
            return self._research(session_id, first_render)

        state = self.store.save(state)
        msgs = a2ui.interview_progress_messages(state, first_time=first_render)
        return AgentReply(text=reply_text, a2ui_messages=msgs)

    # -- research step: search -> shortlist -> catalogue ----------------------

    def _research(self, session_id: str, first_render: bool) -> AgentReply:
        """Runs the deterministic half of the pipeline and renders results.

        NOTE: ordering here is the shortlist score, and each card's `why` is a
        readout of that score's breakdown. It is honest but it is NOT the LLM
        reasoning FR-3 ultimately calls for — that arrives with the Claude Agent
        SDK, which will re-rank these candidates and write real per-car
        rationale into research.ranked.
        """
        state = self.store.get(session_id)
        intent = state["intent"]
        query = {
            "mode": intent["mode"],
            "car_type": intent["car_type"],
            "budget_max": intent["budget"]["amount"],
            "available": intent["target_date"],
        }
        state["research"]["last_query"] = query
        state["phase"] = "researching"
        state = self.store.save(state)

        result = marketplace.search_listings(state, **query)
        if "error" in result:
            return AgentReply(text=result["error"]["message"])

        shortlist = marketplace.shortlist_candidates(state, limit=6)
        state = self.store.get(session_id)
        state["research"]["shortlist"] = shortlist["shortlist"]
        state = self.store.save(state)

        cards = [self._card(s) for s in shortlist["shortlist"]]
        msgs: list[dict[str, Any]] = []
        if first_render:
            msgs += a2ui.interview_progress_messages(state, first_time=True)
        msgs += a2ui.results_messages(cards, shortlist["considered"])

        if cards:
            verb = "to rent" if intent["mode"] == "rent" else "to buy"
            text_out = (f"Found {shortlist['considered']} {intent['car_type']} options {verb} "
                        f"within your budget — here are the {len(cards)} strongest.")
        else:
            text_out = f"Nothing matched that exactly. {result.get('hint', '')}".strip()
        return AgentReply(text=text_out, a2ui_messages=msgs)

    def _card(self, scored: dict[str, Any]) -> dict[str, Any]:
        l = marketplace.listing_by_id(scored["listing_id"])
        per = "/day" if l["price"]["period"] == "per_day" else ""
        b = scored["breakdown"]
        reasons = []
        if b["budget_fit"] >= 0.9:
            reasons.append("uses your budget well")
        elif b["budget_fit"] >= 0.6:
            reasons.append("comfortably under budget")
        if b["constraints"] == 1.0:
            reasons.append("meets every stated requirement")
        elif b["constraints"] >= 0.5:
            reasons.append("meets most of your requirements")
        if b["availability"] == 1.0:
            reasons.append("free for your whole date range")
        elif b["availability"] >= 0.5:
            reasons.append("partly available in your window")
        return {
            "listing_id": l["listing_id"],
            "title": f"{l['brand']} {l['model']} ({l['year']})",
            "price": f"₹{l['price']['amount']:,}{per}",
            "meta": f"{l['category']} · {l['fuel']} · {l['transmission']} · "
                    f"{l['seats']} seats · {l['location']}",
            "why": "Why: " + ("; ".join(reasons) if reasons else "closest available match"),
        }

    # -- crude slot parsing (scaffolding only) --------------------------------

    def _fill_slot(self, state: dict[str, Any], slot: str, answer: str) -> None:
        intent = state["intent"]
        low = answer.lower()
        if slot == "mode":
            if "rent" in low:
                intent["mode"] = "rent"
                intent["budget"]["period"] = "per_day"
            elif "buy" in low or "purchase" in low:
                intent["mode"] = "buy"
                intent["budget"]["period"] = "total"
        elif slot == "use_case":
            intent["use_case"] = answer.strip()
        elif slot == "car_type":
            for t in ("suv", "sedan", "hatchback", "muv", "coupe", "convertible", "pickup", "van", "ev", "luxury"):
                if t in low:
                    intent["car_type"] = t.upper() if t in ("suv", "muv", "ev") else t.capitalize()
                    return
            intent["car_type"] = answer.strip()
        elif slot == "budget":
            amount = _parse_amount(low)
            if amount:
                intent["budget"]["amount"] = amount
        elif slot == "target_date":
            dates = re.findall(r"\d{4}-\d{2}-\d{2}", answer)
            if len(dates) >= 2:
                intent["target_date"] = {"from": dates[0], "to": dates[1]}
            elif len(dates) == 1:
                intent["target_date"] = dates[0]
            elif answer.strip():
                intent["target_date"] = answer.strip()  # free text ok for skeleton


def _parse_amount(text: str) -> int | None:
    m = re.search(r"([\d,]+(?:\.\d+)?)\s*(lakh|lac|l\b|crore|cr\b|k\b)?", text)
    if not m or not m.group(1):
        return None
    try:
        num = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    unit = (m.group(2) or "").strip()
    if unit in ("lakh", "lac", "l"):
        num *= 100_000
    elif unit in ("crore", "cr"):
        num *= 10_000_000
    elif unit == "k":
        num *= 1_000
    return int(num) if num > 0 else None
