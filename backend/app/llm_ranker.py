"""Claude Agent SDK integration: ranking and per-car reasoning (FR-3).

This is the LLM half of the hybrid split (plan §4). Deterministic code has
already filtered and scored the marketplace down to a shortlist; the model's
job is to *order those candidates and say why*, in the user's own terms.

Why the model does not search: letting it filter 388 listings would make the
ranking unauditable and slow. Letting it rank 6 pre-scored candidates keeps
the explanation honest — every car it discusses is one the deterministic step
already justified, and the trace shows both steps separately.

Grounding rules, enforced in code rather than trusted to the prompt:
  - the model may only rank listing_ids that were on the shortlist
  - every id must appear exactly once
  - reasoning is required and length-capped
Any violation falls back to shortlist order. A confidently wrong recommendation
is worse than a boring correct one.

The SDK is an optional dependency: `pip install -r backend/requirements-agent.txt`.
Without it (or without credentials) `rank_shortlist` returns None and the caller
keeps the deterministic ordering, so the app still runs for a judge with no key.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from . import marketplace

log = logging.getLogger(__name__)

MODEL = os.environ.get("CARMATCH_MODEL", "claude-sonnet-4-5")
MAX_REASONING_CHARS = 240

SYSTEM_PROMPT = """You are a car-buying concierge ranking a pre-filtered shortlist.

You will receive the user's stated needs and a shortlist of candidate cars that
deterministic scoring has ALREADY verified as within budget and available.

Your job is to order these candidates from best to worst fit and explain each
choice in one sentence, referring to what the user actually said they needed.

Rules:
- Rank ONLY the listing_ids given to you. Never invent a car.
- Include every listing_id exactly once.
- Reference the user's own stated needs (use case, constraints, dates, budget).
  Do not invent criteria they never mentioned.
- Be specific and honest. If a car wins on one axis but loses on another, say
  so. Do not claim a car is "perfect".
- One sentence per car, under 240 characters.

Respond with ONLY a JSON array, no prose and no markdown fences:
[{"listing_id": "l-042", "rank": 1, "reasoning": "..."}]
"""


def is_enabled() -> bool:
    """Whether to attempt a model call at all.

    This is a FAST check on purpose. The SDK spawns a CLI subprocess, and
    without credentials that subprocess still takes seconds to fail — which
    would stall every single search behind a doomed call. So we decide up
    front from configuration instead of learning it the slow way.

    CARMATCH_AGENT_RANKING: "off" disables; "on" forces an attempt (useful if
    credentials come from a CLI login rather than the environment); unset means
    attempt only when ANTHROPIC_API_KEY is present.
    """
    flag = os.environ.get("CARMATCH_AGENT_RANKING", "").strip().lower()
    if flag in ("off", "0", "false"):
        return False
    if not _sdk_importable():
        return False
    if flag in ("on", "1", "true"):
        return True
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _sdk_importable() -> bool:
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        return False
    return True


def build_prompt(state: dict[str, Any], shortlist: list[dict[str, Any]]) -> str:
    """Everything the model needs, and nothing it shouldn't have.

    Deliberately excludes checkout.form_data — no personal details are ever
    sent to the model, because ranking cars does not require knowing the
    user's phone number.
    """
    intent = state["intent"]
    budget = intent["budget"]
    lines = [
        "USER'S STATED NEEDS",
        f"- Mode: {intent['mode']}",
        f"- Use case: {intent['use_case']}",
        f"- Car type: {intent['car_type']}",
        f"- Budget: ₹{budget['amount']:,} {budget['period']}"
        if budget["amount"] else "- Budget: not stated",
        f"- Needed: {_fmt_date(intent['target_date'])}",
    ]
    if intent.get("constraints"):
        lines.append(f"- Must have: {', '.join(intent['constraints'])}")

    lines += ["", "SHORTLIST (all already within budget and available)"]
    for s in shortlist:
        l = marketplace.listing_by_id(s["listing_id"])
        if l is None:
            continue
        per = "/day" if l["price"]["period"] == "per_day" else ""
        b = s["breakdown"]
        lines.append(
            f"- {s['listing_id']}: {l['brand']} {l['model']} ({l['year']}), "
            f"{l['category']}, ₹{l['price']['amount']:,}{per}, {l['fuel']}, "
            f"{l['transmission']}, {l['seats']} seats, {l['location']}, "
            f"features: {', '.join(l['features'])} "
            f"[scores — budget fit {b['budget_fit']}, requirements {b['constraints']}, "
            f"availability {b['availability']}]"
        )
    lines += ["", "Rank these and explain each. JSON array only."]
    return "\n".join(lines)


async def rank_shortlist(state: dict[str, Any], shortlist: list[dict[str, Any]],
                         transport: Any = None) -> list[dict[str, Any]] | None:
    """Ask the model to rank the shortlist. Returns None on any failure."""
    if not shortlist:
        return None
    if transport is None and not is_enabled():
        log.info("agent ranking disabled or unconfigured — keeping deterministic order")
        return None

    try:
        from claude_agent_sdk import ClaudeAgentOptions, query
    except ImportError:
        return None

    options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        model=MODEL,
        max_turns=1,          # ranking is a single judgement, not a tool loop
        allowed_tools=[],     # the shortlist is already in the prompt
    )

    kwargs = {"prompt": build_prompt(state, shortlist), "options": options}
    if transport is not None:
        kwargs["transport"] = transport

    try:
        raw = extract_text([m async for m in query(**kwargs)])
    except Exception as exc:                     # CLINotFoundError, auth, network
        log.warning("agent ranking unavailable (%s) — keeping deterministic order",
                    type(exc).__name__)
        return None

    return validate_ranking(raw, shortlist)


def extract_text(messages: list[Any]) -> str:
    """Concatenate assistant text across a reply.

    Split out from the call so it is testable against real SDK message objects
    without spawning the CLI subprocess: the parsing is our logic, the transport
    is the SDK's.
    """
    from claude_agent_sdk import AssistantMessage, TextBlock

    chunks: list[str] = []
    for message in messages:
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    chunks.append(block.text)
    return "".join(chunks)


def validate_ranking(raw: str, shortlist: list[dict[str, Any]]
                     ) -> list[dict[str, Any]] | None:
    """Parse and ground-check the model's answer.

    Rejects hallucinated ids, omissions, duplicates, and empty reasoning. On
    rejection the caller keeps the deterministic order, so a bad generation
    degrades to 'less insightful' rather than 'wrong'.
    """
    text = raw.strip()
    if text.startswith("```"):                  # tolerate fenced output
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        text = text.removeprefix("json").strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        log.warning("agent ranking was not valid JSON — keeping deterministic order")
        return None
    if not isinstance(parsed, list) or not parsed:
        return None

    allowed = {s["listing_id"] for s in shortlist}
    seen: set[str] = set()
    cleaned: list[dict[str, Any]] = []

    for entry in parsed:
        if not isinstance(entry, dict):
            return None
        lid = entry.get("listing_id")
        reasoning = str(entry.get("reasoning", "")).strip()
        if lid not in allowed:
            log.warning("agent ranked unknown listing %r — rejecting ranking", lid)
            return None
        if lid in seen:
            log.warning("agent ranked %s twice — rejecting ranking", lid)
            return None
        if not reasoning:
            return None
        seen.add(lid)
        cleaned.append({"listing_id": lid, "rank": len(cleaned) + 1,
                        "reasoning": reasoning[:MAX_REASONING_CHARS]})

    if seen != allowed:
        log.warning("agent dropped %d shortlisted cars — rejecting ranking",
                    len(allowed - seen))
        return None
    return cleaned


def _fmt_date(value: Any) -> str:
    if isinstance(value, dict):
        return f"{value.get('from')} to {value.get('to')}"
    return str(value) if value else "no date given"
