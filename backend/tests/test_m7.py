"""M7 tests: natural-language vehicle parsing and interview robustness.

These cover the six phrasings the old substring scan got wrong, plus the
interview trap that leaving a slot null could otherwise create.
"""
import pytest

from app import marketplace, vehicle_parse as vp
from app.agent import ScriptedAgent
from app.state import (MAX_SLOT_ATTEMPTS, SessionStore, missing_slots,
                       new_session_state)


# ---- category parsing --------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    # the specific bugs: a longer alias must beat the shorter one inside it
    ("compact SUV", "Compact SUV"),
    ("a small SUV", "Compact SUV"),
    ("crossover", "Compact SUV"),
    ("luxury sedan", "Luxury Sedan"),
    ("premium sedan", "Luxury Sedan"),
    ("luxury SUV", "Luxury SUV"),
    # "EV" used to produce a category string that does not exist
    ("an EV", "Electric"),
    ("electric car", "Electric"),
    ("electric vehicle", "Electric"),
    # plain cases must keep working
    ("hatchback", "Hatchback"),
    ("saloon", "Sedan"),
    ("MPV", "MUV"),
    ("people carrier", "MUV"),
    ("convertible", "Convertible"),
    ("roadster", "Convertible"),
    ("pickup truck", "Pickup"),
    ("full-size SUV", "SUV"),
])
def test_category_aliases(text, expected):
    assert vp.parse_car_type(text) == expected


def test_longer_alias_wins_over_substring():
    """The core fix: 'compact SUV' must not resolve to plain 'SUV'."""
    assert vp.parse_car_type("compact suv") != vp.parse_car_type("suv")
    assert vp.parse_car_type("luxury sedan") != vp.parse_car_type("sedan")


@pytest.mark.parametrize("text,expected", [
    ("a Creta", "SUV"),
    ("I want a Swift", "Hatchback"),
    ("something like a Nexon", "Compact SUV"),
])
def test_model_names_resolve_to_categories(text, expected):
    assert vp.parse_car_type(text) == expected


def test_every_parsed_category_exists_in_the_dataset():
    """A category the parser can produce but the data lacks returns nothing."""
    real = {l["category"] for l in marketplace.load_listings()}
    for alias, category in vp.CATEGORY_ALIASES.items():
        assert category in real, f"alias {alias!r} maps to unknown category {category!r}"


def test_unparseable_type_returns_none():
    assert vp.parse_car_type("asdfgh") is None
    assert vp.parse_car_type("") is None


def test_use_case_hints_are_a_last_resort():
    # an explicit type beats the hint in the same sentence
    assert vp.parse_car_type("a hatchback for the family") == "Hatchback"
    assert vp.parse_car_type("something for the family") == "MUV"


# ---- constraint extraction ---------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("7 seater", "seats>=7"),
    ("7-seater", "seats>=7"),
    ("seven seater", "seats>=7"),
    ("room for 7", "seats>=7"),
    ("seats 8", "seats>=8"),
])
def test_seat_requirements(text, expected):
    assert expected in vp.parse_constraints(text)


def test_transmission_and_fuel():
    assert "automatic" in vp.parse_constraints("automatic please")
    assert "manual" in vp.parse_constraints("manual gearbox")
    assert "hybrid" in vp.parse_constraints("a hybrid")
    assert "ev" in vp.parse_constraints("fully electric")


def test_features_and_brands_come_from_the_dataset():
    out = vp.parse_constraints("a Toyota with a sunroof and adas")
    assert "sunroof" in out and "adas" in out and "toyota" in out


def test_constraints_are_understood_by_the_scorer():
    """Tokens must be ones marketplace.constraint_satisfied already speaks."""
    listing = {"seats": 7, "fuel": "hybrid", "transmission": "automatic",
               "features": ["sunroof"], "brand": "Toyota", "location": "Goa"}
    for token in vp.parse_constraints("7 seater automatic hybrid with sunroof"):
        assert marketplace.constraint_satisfied(listing, token), token


def test_no_constraints_from_empty_text():
    assert vp.parse_constraints("") == []


# ---- mode --------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("rent please", "rent"), ("I'd like to hire one", "rent"),
    ("leasing", "rent"), ("buy", "buy"), ("I want to purchase", "buy"),
    ("not sure yet", None),
])
def test_mode_parsing(text, expected):
    assert vp.parse_mode(text) == expected


# ---- interview integration ---------------------------------------------------

def _interview(store, sid, answers):
    agent = ScriptedAgent(store)
    reply = None
    for a in answers:
        reply = agent.respond(sid, a)
    return agent, reply


def test_interview_captures_category_and_constraints(tmp_path):
    store = SessionStore(tmp_path / "p.sqlite")
    _interview(store, "p1", ["", "rent", "goa trip with friends",
                             "a 7-seater automatic compact SUV",
                             "4000 per day", "2026-09-10 to 2026-09-15"])
    intent = store.get("p1")["intent"]
    assert intent["car_type"] == "Compact SUV"
    assert "seats>=7" in intent["constraints"]
    assert "automatic" in intent["constraints"]


def test_constraints_are_collected_from_any_answer(tmp_path):
    """A requirement stated during the use-case question still counts."""
    store = SessionStore(tmp_path / "p.sqlite")
    _interview(store, "p2", ["", "rent", "family trip, we need 7 seats",
                             "SUV", "5000 per day", "2026-09-10 to 2026-09-15"])
    assert "seats>=7" in store.get("p2")["intent"]["constraints"]


def test_constraints_reach_the_shortlist_breakdown(tmp_path):
    store = SessionStore(tmp_path / "p.sqlite")
    _interview(store, "p3", ["", "rent", "goa trip", "an automatic SUV",
                             "5000 per day", "2026-09-10 to 2026-09-15"])
    state = store.get("p3")
    assert state["intent"]["constraints"], "interview must populate constraints"
    # the scorer now has something to score, so the dimension is meaningful
    scores = {c["breakdown"]["constraints"] for c in state["research"]["shortlist"]}
    assert scores, "shortlist should exist"
    assert any(v == 1.0 for v in scores), "some car should satisfy the constraint"


# ---- the interview must not trap the user ------------------------------------

def test_unparseable_answers_do_not_loop_forever(tmp_path):
    store = SessionStore(tmp_path / "p.sqlite")
    agent, reply = _interview(store, "p4", ["", "rent", "goa trip",
                                            "asdfgh", "qwerty",
                                            "4000 per day",
                                            "2026-09-10 to 2026-09-15"])
    state = store.get("p4")
    assert state["interview"]["complete"] is True
    assert "car_type" in state["interview"]["declined"]
    assert state["intent"]["car_type"] is None       # honestly recorded as unset
    assert state["research"]["shortlist"], "search still runs without a category"


def test_retry_prompt_differs_from_the_first_ask(tmp_path):
    store = SessionStore(tmp_path / "p.sqlite")
    agent = ScriptedAgent(store)
    for a in ["", "rent", "goa trip"]:
        first = agent.respond("p5", a)
    retry = agent.respond("p5", "asdfgh")
    assert retry.text != first.text
    assert "hatchback" in retry.text.lower()          # names the valid options


def test_declined_slot_stops_being_requested():
    state = new_session_state()
    state["intent"]["mode"] = "rent"
    state["intent"]["use_case"] = "trip"
    state["intent"]["budget"]["amount"] = 3000
    state["intent"]["target_date"] = "2026-09-10"
    assert missing_slots(state) == ["car_type"]
    state["interview"]["declined"].append("car_type")
    assert missing_slots(state) == []


def test_attempts_are_capped_at_the_documented_limit(tmp_path):
    store = SessionStore(tmp_path / "p.sqlite")
    agent = ScriptedAgent(store)
    for a in ["", "rent", "goa trip", "asdfgh"]:
        agent.respond("p6", a)
    assert store.get("p6")["interview"]["attempts"]["car_type"] == MAX_SLOT_ATTEMPTS


def test_null_category_is_not_rendered_to_the_user(tmp_path):
    store = SessionStore(tmp_path / "p.sqlite")
    _, reply = _interview(store, "p7", ["", "rent", "goa trip", "asdfgh",
                                        "qwerty", "4000 per day",
                                        "2026-09-10 to 2026-09-15"])
    assert "None" not in reply.text
