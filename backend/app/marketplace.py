"""Marketplace tools: `search_listings` and `shortlist_candidates`.

Contracts: specs/001-car-matchmaker/contracts/tools.md §1 and §2.

These are the DETERMINISTIC half of the hybrid ranking (plan §4). Search is a
pure filter with no ranking; shortlist scores and truncates with a per-candidate
breakdown. The LLM half — ordering the shortlist and writing per-car reasoning —
happens in the agent turn afterwards and is traced separately. Keeping the two
apart is what makes the agent's explanations auditable rather than post-hoc.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "listings.json"

# shortlist scoring weights — documented here and in the tool contract
W_BUDGET, W_CONSTRAINTS, W_AVAILABILITY = 0.45, 0.35, 0.20

SUMMARY_FIELDS = ("listing_id", "brand", "model", "category", "year", "price",
                  "fuel", "transmission", "seats", "location", "features")


@lru_cache(maxsize=1)
def load_listings(path: str | None = None) -> list[dict[str, Any]]:
    return json.loads(Path(path or DATA_PATH).read_text())


# ---- availability -----------------------------------------------------------


def _windows_overlap(window: dict[str, str], frm: str, to: str) -> bool:
    return window["from"] <= to and window["to"] >= frm


def _norm_range(target: Any) -> tuple[str, str] | None:
    """intent.target_date is a bare ISO date, a {from,to} range, or free text."""
    if isinstance(target, dict) and target.get("from") and target.get("to"):
        return target["from"], target["to"]
    if isinstance(target, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", target):
        return target, target
    return None


def _availability_score(listing: dict, rng: tuple[str, str] | None) -> float:
    """1.0 when a single window covers the whole requested range, 0.5 when it
    only partly overlaps, 0.0 when nothing overlaps. No target date = neutral."""
    if rng is None:
        return 0.7
    frm, to = rng
    best = 0.0
    for w in listing["availability"]:
        if w["from"] <= frm and w["to"] >= to:
            return 1.0
        if _windows_overlap(w, frm, to):
            best = max(best, 0.5)
    return best


# ---- constraints ------------------------------------------------------------

_FUELS = {"petrol", "diesel", "hybrid", "ev", "electric"}
_TRANSMISSIONS = {"manual", "automatic"}


def constraint_satisfied(listing: dict, constraint: str) -> bool:
    """Understands: fuel names, transmission names, `seats>=N`, feature tags,
    brand names, and city names. Unknown constraints count as unsatisfied
    rather than silently passing — a wrong 'match' is worse than a miss."""
    c = constraint.strip().lower()
    if not c:
        return False
    m = re.fullmatch(r"seats\s*(>=|>|=|<=|<)\s*(\d+)", c)
    if m:
        op, n = m.group(1), int(m.group(2))
        seats = listing["seats"]
        return {">=": seats >= n, ">": seats > n, "=": seats == n,
                "<=": seats <= n, "<": seats < n}[op]
    if c in _FUELS:
        want = "ev" if c == "electric" else c
        return listing["fuel"] == want
    if c in _TRANSMISSIONS:
        return listing["transmission"] == c
    if c in {f.lower() for f in listing["features"]}:
        return True
    if c == listing["brand"].lower() or c == listing["location"].lower():
        return True
    return False


def _constraint_score(listing: dict, constraints: list[str]) -> float:
    if not constraints:
        return 1.0
    hits = sum(1 for c in constraints if constraint_satisfied(listing, c))
    return hits / len(constraints)


def _budget_score(listing: dict, budget_max: int | None) -> float:
    """Rewards using the budget well. Spending 60-100% of it scores highest;
    suspiciously cheap scores lower (usually a worse car, not a bargain)."""
    if not budget_max:
        return 0.7
    ratio = listing["price"]["amount"] / budget_max
    if ratio > 1.0:
        return 0.0
    if ratio >= 0.6:
        return 1.0
    return 0.55 + (ratio / 0.6) * 0.45


# ---- tool 1: search_listings -------------------------------------------------


def search_listings(state: dict[str, Any], **query: Any) -> dict[str, Any]:
    """Pure filter. Empty result is NOT an error — it returns a relaxation hint
    so the agent can negotiate constraints instead of dead-ending."""
    mode = query.get("mode") or state["intent"]["mode"]
    if mode not in ("buy", "rent"):
        return {"error": {"code": "missing_mode",
                          "message": "mode must be buy|rent",
                          "hint": "ask the user whether they're buying or renting"}}
    want_for = "sale" if mode == "buy" else "rent"

    listings = [l for l in load_listings() if l["for"] == want_for]
    filters = {k: v for k, v in query.items() if v not in (None, [], "")}
    matched = [l for l in listings if _matches(l, filters)]

    result: dict[str, Any] = {
        "count": len(matched),
        "listings": [{k: l[k] for k in SUMMARY_FIELDS} for l in matched],
    }
    if not matched:
        result["hint"] = _relaxation_hint(listings, filters)
    return result


def _matches(l: dict, f: dict) -> bool:
    if (ct := f.get("car_type")) and l["category"].lower() != str(ct).lower():
        return False
    if (bmax := f.get("budget_max")) and l["price"]["amount"] > bmax:
        return False
    if (brands := f.get("brands")) and l["brand"].lower() not in {b.lower() for b in brands}:
        return False
    if (fuels := f.get("fuel")) and l["fuel"] not in {("ev" if x == "electric" else x) for x in fuels}:
        return False
    if (tx := f.get("transmission")) and l["transmission"] != tx:
        return False
    if (smin := f.get("seats_min")) and l["seats"] < smin:
        return False
    if (loc := f.get("location")) and l["location"].lower() != str(loc).lower():
        return False
    if avail := f.get("available"):
        rng = _norm_range(avail)
        if rng and not any(_windows_overlap(w, *rng) for w in l["availability"]):
            return False
    return True


def _relaxation_hint(pool: list[dict], filters: dict) -> str:
    """Find the single filter whose removal opens the most inventory."""
    best_field, best_count = None, 0
    for field in filters:
        relaxed = {k: v for k, v in filters.items() if k != field}
        n = sum(1 for l in pool if _matches(l, relaxed))
        if n > best_count:
            best_field, best_count = field, n
    if best_field == "budget_max" and filters.get("budget_max"):
        bumped = int(filters["budget_max"] * 1.1)
        n = sum(1 for l in pool if _matches(l, {**filters, "budget_max": bumped}))
        if n:
            return f"{n} more listings if the budget rises ~10% (to ₹{bumped:,})"
    if best_field:
        return f"{best_count} listings match if '{best_field}' is relaxed — offer that trade"
    return "nothing matches even with one filter relaxed — suggest a broader search"


# ---- tool 2: shortlist_candidates --------------------------------------------


def shortlist_candidates(state: dict[str, Any], limit: int = 10) -> dict[str, Any]:
    """Score the last search result. Writes nothing itself — the caller commits
    the returned shortlist into session state (single writer, easier to trace)."""
    query = state["research"].get("last_query") or {}
    search = search_listings(state, **query)
    if "error" in search:
        return search

    intent = state["intent"]
    budget_max = query.get("budget_max") or intent["budget"]["amount"]
    rng = _norm_range(intent.get("target_date"))
    constraints = intent.get("constraints") or []
    by_id = {l["listing_id"]: l for l in load_listings()}

    scored = []
    for summary in search["listings"]:
        l = by_id[summary["listing_id"]]
        breakdown = {
            "budget_fit": round(_budget_score(l, budget_max), 3),
            "constraints": round(_constraint_score(l, constraints), 3),
            "availability": round(_availability_score(l, rng), 3),
        }
        score = (breakdown["budget_fit"] * W_BUDGET
                 + breakdown["constraints"] * W_CONSTRAINTS
                 + breakdown["availability"] * W_AVAILABILITY)
        scored.append({"listing_id": l["listing_id"], "score": round(score, 4),
                       "breakdown": breakdown})

    # deterministic tie-break by listing_id so demos are reproducible
    scored.sort(key=lambda s: (-s["score"], s["listing_id"]))
    return {"shortlist": scored[:limit], "considered": len(scored)}


def listing_by_id(listing_id: str) -> dict[str, Any] | None:
    return next((l for l in load_listings() if l["listing_id"] == listing_id), None)
