"""M2 tests: marketplace dataset, search, shortlist, and the research step.

The dataset test is the FR-6 gate — it runs the same validator CI runs, so a
regenerated dataset that breaks the floor fails here too.
"""
import importlib.util
import json
from pathlib import Path

import pytest

from app import marketplace
from app.agent import ScriptedAgent
from app.state import SessionStore, new_session_state

ROOT = Path(__file__).resolve().parents[2]


def _load_validator():
    spec = importlib.util.spec_from_file_location(
        "validate_listings", ROOT / "data" / "validate_listings.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- dataset (FR-6) ----------------------------------------------------------

def test_dataset_meets_fr6_floor():
    # load_listings() generates the dataset if absent, so this passes on a
    # clean checkout and does not depend on test ordering.
    listings = marketplace.load_listings()
    errors = _load_validator().validate(listings)
    assert errors == [], f"FR-6 violations: {errors[:5]}"


def test_dataset_is_deterministic():
    spec = importlib.util.spec_from_file_location(
        "generate_listings", ROOT / "data" / "generate_listings.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    assert gen.generate() == marketplace.load_listings(), \
        "generator output drifted from the materialised dataset"


# ---- search ------------------------------------------------------------------

def _state(**intent):
    s = new_session_state()
    s["intent"].update(intent)
    return s


def test_search_splits_sale_and_rent_inventory():
    buy = marketplace.search_listings(_state(mode="buy"), mode="buy")
    rent = marketplace.search_listings(_state(mode="rent"), mode="rent")
    assert buy["count"] > 0 and rent["count"] > 0
    assert all(l["price"]["period"] == "total" for l in buy["listings"])
    assert all(l["price"]["period"] == "per_day" for l in rent["listings"])


def test_search_filters_apply():
    r = marketplace.search_listings(
        _state(mode="buy"), mode="buy", car_type="SUV", budget_max=2_000_000,
        transmission="automatic", seats_min=7)
    assert r["count"] > 0
    for l in r["listings"]:
        assert l["category"] == "SUV"
        assert l["price"]["amount"] <= 2_000_000
        assert l["transmission"] == "automatic"
        assert l["seats"] >= 7


def test_search_requires_mode():
    r = marketplace.search_listings(new_session_state())
    assert r["error"]["code"] == "missing_mode"


def test_empty_result_returns_hint_not_error():
    r = marketplace.search_listings(_state(mode="buy"), mode="buy",
                                    car_type="SUV", budget_max=50_000)
    assert r["count"] == 0
    assert "error" not in r
    assert r["hint"]


def test_availability_filter_excludes_non_overlapping_windows():
    rng = {"from": "2027-06-01", "to": "2027-06-10"}   # beyond generated windows
    r = marketplace.search_listings(_state(mode="rent"), mode="rent", available=rng)
    assert r["count"] == 0


# ---- constraints & scoring ---------------------------------------------------

def test_constraint_evaluator():
    l = {"seats": 7, "fuel": "hybrid", "transmission": "automatic",
         "features": ["sunroof"], "brand": "Tata", "location": "Delhi"}
    assert marketplace.constraint_satisfied(l, "seats>=7")
    assert not marketplace.constraint_satisfied(l, "seats>7")
    assert marketplace.constraint_satisfied(l, "hybrid")
    assert marketplace.constraint_satisfied(l, "automatic")
    assert marketplace.constraint_satisfied(l, "sunroof")
    assert marketplace.constraint_satisfied(l, "delhi")
    assert not marketplace.constraint_satisfied(l, "moon-roof")   # unknown != pass


def test_shortlist_respects_limit_and_is_sorted():
    s = _state(mode="buy", car_type="Compact SUV")
    s["intent"]["budget"] = {"amount": 1_600_000, "period": "total", "currency": "INR"}
    s["intent"]["constraints"] = ["automatic"]
    s["research"]["last_query"] = {"mode": "buy", "car_type": "Compact SUV",
                                   "budget_max": 1_600_000}
    out = marketplace.shortlist_candidates(s, limit=5)
    assert len(out["shortlist"]) == 5
    assert out["considered"] >= 5
    scores = [c["score"] for c in out["shortlist"]]
    assert scores == sorted(scores, reverse=True)
    for c in out["shortlist"]:
        assert set(c["breakdown"]) == {"budget_fit", "constraints", "availability"}


def test_shortlist_is_deterministic():
    s = _state(mode="rent", car_type="SUV")
    s["research"]["last_query"] = {"mode": "rent", "car_type": "SUV"}
    a = marketplace.shortlist_candidates(s, limit=6)
    b = marketplace.shortlist_candidates(s, limit=6)
    assert a == b


def test_over_budget_scores_zero_budget_fit():
    listing = {"price": {"amount": 200}, "seats": 5}
    assert marketplace._budget_score(listing, 100) == 0.0


# ---- research step end-to-end -------------------------------------------------

def test_interview_completion_produces_catalogue(tmp_path):
    store = SessionStore(tmp_path / "m2.sqlite")
    agent = ScriptedAgent(store)
    sid = "m2-1"
    for answer in ["", "rent", "goa trip", "an SUV", "4000 per day",
                   "2026-09-10 to 2026-09-15"]:
        reply = agent.respond(sid, answer)

    state = store.get(sid)
    assert state["phase"] == "researching"
    assert state["research"]["shortlist"], "shortlist must be committed to state"
    assert state["research"]["last_query"]["car_type"] == "SUV"

    kinds = [next(k for k in m if k != "version") for m in reply.a2ui_messages]
    assert kinds[-3:] == ["deleteSurface", "createSurface", "updateComponents"]
    comps = reply.a2ui_messages[-1]["updateComponents"]["components"]
    whys = [c["text"] for c in comps if c["id"].endswith("-why")]
    assert whys and all(w.startswith("Why:") for w in whys)


def test_no_match_path_explains_instead_of_dead_ending(tmp_path):
    store = SessionStore(tmp_path / "m2b.sqlite")
    agent = ScriptedAgent(store)
    sid = "m2-2"
    for answer in ["", "buy", "commute", "Convertible", "300000",
                   "2026-09-10"]:
        reply = agent.respond(sid, answer)
    assert store.get(sid)["research"]["shortlist"] == []
    assert "match" in reply.text.lower() or "budget" in reply.text.lower()
