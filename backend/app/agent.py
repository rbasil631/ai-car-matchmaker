"""Agent loop for M1 (walking skeleton).

Design: the loop is pluggable. M1 ships `ScriptedAgent` — deterministic slot
filling that proves the architecture (nulls drive the interview, state is
shared, surfaces update incrementally) with zero API dependencies, so the
skeleton runs anywhere. M2 swaps in the Claude Agent SDK behind the same
`respond()` interface; nothing upstream changes.

ScriptedAgent's "NLU" is deliberately crude (keyword + number extraction).
It is scaffolding, not the product — do not extend it; replace it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import a2ui
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
            state["phase"] = "researching"
            reply_text = (
                "Great — I have everything I need. "
                "(M2 will search the marketplace here; the skeleton stops at a complete interview.)"
            )

        state = self.store.save(state)
        msgs = a2ui.interview_progress_messages(state, first_time=first_render)
        return AgentReply(text=reply_text, a2ui_messages=msgs)

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
