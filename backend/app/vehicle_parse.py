"""Natural-language parsing for vehicle type and requirements.

Replaces a substring scan that got six of ten realistic phrasings wrong. Three
fixes, each addressing a distinct failure:

1. **Longest match wins.** "compact SUV" used to hit the "suv" alias first and
   search full-size SUVs; "luxury sedan" became plain "Sedan". Aliases are now
   tried longest-first, so the more specific category always claims the phrase.

2. **Vocabulary comes from the dataset, not a hand-list.** "EV" mapped to a
   category string ("EV") that does not exist — the real one is "Electric" — so
   it returned nothing. Canonical categories and model names are derived from
   the listings at runtime, which means a category can never be reachable in
   code but absent from the data.

3. **Model and brand names resolve.** "a Creta" is a category statement to a
   human. It is now looked up and resolved to that model's category, with the
   brand recorded as a constraint.

Constraint extraction is the other half. `intent.constraints` was scored by the
shortlist all along but nothing ever populated it from conversation, so "7
seater automatic" influenced nothing. It does now.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from . import marketplace

# Phrases that mean a category. Written lowercase; matched longest-first so
# "compact suv" is tested before "suv".
CATEGORY_ALIASES: dict[str, str] = {
    "compact suv": "Compact SUV",
    "sub-4m suv": "Compact SUV",
    "sub 4m suv": "Compact SUV",
    "small suv": "Compact SUV",
    "crossover": "Compact SUV",
    "luxury sedan": "Luxury Sedan",
    "premium sedan": "Luxury Sedan",
    "executive sedan": "Luxury Sedan",
    "luxury suv": "Luxury SUV",
    "premium suv": "Luxury SUV",
    "full size suv": "SUV",
    "full-size suv": "SUV",
    "electric": "Electric",
    "electric car": "Electric",
    "electric vehicle": "Electric",
    "ev": "Electric",
    "e-car": "Electric",
    "battery car": "Electric",
    "hatchback": "Hatchback",
    "hatch": "Hatchback",
    "sedan": "Sedan",
    "saloon": "Sedan",
    "suv": "SUV",
    "muv": "MUV",
    "mpv": "MUV",
    "people carrier": "MUV",
    "people mover": "MUV",
    "minivan": "Van",
    "van": "Van",
    "tempo traveller": "Van",
    "coupe": "Coupe",
    "sports car": "Coupe",
    "convertible": "Convertible",
    "cabriolet": "Convertible",
    "cabrio": "Convertible",
    "roadster": "Convertible",
    "pickup": "Pickup",
    "pick-up": "Pickup",
    "pick up": "Pickup",
    "ute": "Pickup",
}

# Loose use-case hints. Only consulted when nothing more specific matched, so a
# stated type always beats an inferred one.
USE_CASE_HINTS: dict[str, str] = {
    "family": "MUV",
    "road trip": "SUV",
    "city commute": "Hatchback",
    "city driving": "Hatchback",
    "office commute": "Hatchback",
    "long drive": "SUV",
    "off road": "SUV",
    "cargo": "Van",
    "goods": "Van",
    "luggage": "MUV",
}

_WORD_NUMBERS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12,
}


@lru_cache(maxsize=1)
def _dataset_vocab() -> tuple[dict[str, str], dict[str, str], set[str], set[str]]:
    """Build lookup tables from the listings themselves.

    Returns (model -> category, brand -> canonical brand, categories, features).
    Deriving these means a rename in the generator can never leave the parser
    pointing at a category that no longer exists.
    """
    models: dict[str, str] = {}
    brands: dict[str, str] = {}
    categories: set[str] = set()
    features: set[str] = set()
    for l in marketplace.load_listings():
        categories.add(l["category"])
        brands[l["brand"].lower()] = l["brand"]
        # a model name can appear in more than one category; first wins, and
        # ambiguity is rare enough that guessing beats refusing
        models.setdefault(l["model"].lower(), l["category"])
        for f in l["features"]:
            features.add(f.lower())

    # Also accept models the generator knows about but didn't happen to draw
    # into a listing. "I want a Swift" should still resolve to Hatchback even
    # when no Swift is in stock — the category is the useful answer, and search
    # will offer the alternatives.
    for model, category in _taxonomy_models().items():
        if category in categories:
            models.setdefault(model, category)
    return models, brands, categories, features


@lru_cache(maxsize=1)
def _taxonomy_models() -> dict[str, str]:
    """model -> category for every model the generator can produce."""
    import importlib.util

    script = marketplace.DATA_PATH.parent / "generate_listings.py"
    spec = importlib.util.spec_from_file_location("generate_listings", script)
    if spec is None or spec.loader is None:      # pragma: no cover - defensive
        return {}
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    out: dict[str, str] = {}
    for category, brands in gen.TAXONOMY.items():
        for model_list in brands.values():
            for model in model_list:
                out.setdefault(model.lower(), category)
    return out


def canonical_categories() -> set[str]:
    return _dataset_vocab()[2]


def _normalise(text: str) -> str:
    """Lowercase and flatten punctuation so '7-seater' and '7 seater' agree."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def parse_car_type(text: str) -> str | None:
    """Resolve free text to a category that exists in the dataset."""
    if not text or not text.strip():
        return None
    norm = _normalise(text)
    categories = canonical_categories()

    # 1. exact category name, e.g. the user typed "Compact SUV"
    for cat in categories:
        if _normalise(cat) == norm:
            return cat

    # 2. aliases, longest first — this is the fix for "compact SUV"
    for alias in sorted(CATEGORY_ALIASES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", norm):
            cat = CATEGORY_ALIASES[alias]
            if cat in categories:
                return cat

    # 3. a category name appearing inside a longer sentence
    for cat in sorted(categories, key=len, reverse=True):
        if re.search(rf"\b{re.escape(_normalise(cat))}\b", norm):
            return cat

    # 4. model names — "a Creta" is a category statement to a human
    models, _, _, _ = _dataset_vocab()
    for model in sorted(models, key=len, reverse=True):
        if len(model) < 3:
            continue
        if re.search(rf"\b{re.escape(_normalise(model))}\b", norm):
            return models[model]

    # 5. use-case hints, last so an explicit type always wins
    for hint, cat in USE_CASE_HINTS.items():
        if hint in norm and cat in categories:
            return cat

    return None


def parse_constraints(text: str) -> list[str]:
    """Pull requirement tokens out of anything the user says.

    Returns tokens `marketplace.constraint_satisfied` already understands, so
    the shortlist scores them without further translation.
    """
    if not text:
        return []
    norm = _normalise(text)
    found: list[str] = []

    def add(token: str) -> None:
        if token not in found:
            found.append(token)

    # seats — "7 seater", "seven-seater", "seats 7", "room for 7",
    # and the way people actually say it: "for 7 people", "6 of us"
    _SEAT_NOUNS = r"(?:seater|seats?|people|persons?|passengers?|of us)"
    m = re.search(rf"\b(\d{{1,2}})\s*{_SEAT_NOUNS}\b", norm)
    if not m:
        m = re.search(r"\b(?:seats|seat|room for|fits|space for)\s*(\d{1,2})\b", norm)
    if m:
        seats = int(m.group(1))
        if 2 <= seats <= 12:            # ignore stray numbers like years
            add(f"seats>={seats}")
    else:
        for word, n in _WORD_NUMBERS.items():
            if re.search(rf"\b{word}\s*{_SEAT_NOUNS}\b", norm):
                add(f"seats>={n}")
                break

    # transmission
    if re.search(r"\b(automatic|auto|amt|dct|cvt)\b", norm):
        add("automatic")
    elif re.search(r"\b(manual|stick shift|stick)\b", norm):
        add("manual")

    # fuel
    for token, canon in (("hybrid", "hybrid"), ("diesel", "diesel"),
                         ("petrol", "petrol"), ("gasoline", "petrol")):
        if re.search(rf"\b{token}\b", norm):
            add(canon)
    if re.search(r"\b(electric|ev|battery)\b", norm):
        add("ev")

    # features and brands, straight from the dataset vocabulary
    _, brands, _, features = _dataset_vocab()
    for feature in features:
        if re.search(rf"\b{re.escape(_normalise(feature))}\b", norm):
            add(feature)
    for brand in brands:
        if len(brand) < 3:
            continue
        if re.search(rf"\b{re.escape(_normalise(brand))}\b", norm):
            add(brand)

    return found


def parse_mode(text: str) -> str | None:
    """Buy vs rent. Checked first because it reinterprets every later number."""
    norm = _normalise(text)
    if re.search(r"\b(rent|rental|renting|hire|hiring|lease|leasing|borrow)\b", norm):
        return "rent"
    if re.search(r"\b(buy|buying|purchase|purchasing|own|owning|bought)\b", norm):
        return "buy"
    return None
