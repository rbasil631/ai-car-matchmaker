"""Validate the mock marketplace against spec FR-6. Exits non-zero on failure.

Checks:
  1. >= 100 listings
  2. >= 10 categories
  3. >= 10 distinct brands in EVERY category
  4. every listing has >= 1 availability window, each window from <= to
  5. required fields present and well-typed; listing_ids unique
  6. both modes (sale/rent) carry >= 10 categories each, so neither a buyer
     nor a renter hits a threadbare catalogue
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

DATA = Path(__file__).resolve().parent / "listings.json"

REQUIRED = ("listing_id", "for", "brand", "model", "category", "year",
            "price", "fuel", "transmission", "seats", "location",
            "features", "availability")


def validate(listings: list[dict]) -> list[str]:
    errors: list[str] = []

    if len(listings) < 100:
        errors.append(f"FR-6: need >=100 listings, found {len(listings)}")

    by_cat: dict[str, set[str]] = defaultdict(set)
    by_mode_cat: dict[str, set[str]] = defaultdict(set)
    seen_ids: set[str] = set()

    for l in listings:
        lid = l.get("listing_id", "<missing id>")
        for field in REQUIRED:
            if field not in l:
                errors.append(f"{lid}: missing field '{field}'")
        if lid in seen_ids:
            errors.append(f"duplicate listing_id {lid}")
        seen_ids.add(lid)

        if l.get("for") not in ("sale", "rent"):
            errors.append(f"{lid}: 'for' must be sale|rent, got {l.get('for')!r}")

        price = l.get("price", {})
        if price.get("period") not in ("total", "per_day"):
            errors.append(f"{lid}: price.period must be total|per_day")
        if not isinstance(price.get("amount"), int) or price.get("amount", 0) <= 0:
            errors.append(f"{lid}: price.amount must be a positive int")
        if l.get("for") == "sale" and price.get("period") != "total":
            errors.append(f"{lid}: sale listing must price as 'total'")
        if l.get("for") == "rent" and price.get("period") != "per_day":
            errors.append(f"{lid}: rent listing must price as 'per_day'")

        windows = l.get("availability") or []
        if not windows:
            errors.append(f"{lid}: FR-6 requires >=1 availability window")
        for w in windows:
            if not w.get("from") or not w.get("to"):
                errors.append(f"{lid}: availability window missing from/to")
            elif w["from"] > w["to"]:
                errors.append(f"{lid}: availability window inverted ({w['from']} > {w['to']})")

        if l.get("category") and l.get("brand"):
            by_cat[l["category"]].add(l["brand"])
            by_mode_cat[l.get("for", "?")].add(l["category"])

    if len(by_cat) < 10:
        errors.append(f"FR-6: need >=10 categories, found {len(by_cat)}")
    for cat, brands in sorted(by_cat.items()):
        if len(brands) < 10:
            errors.append(f"FR-6: category '{cat}' has {len(brands)} brands, need >=10")

    for mode in ("sale", "rent"):
        if len(by_mode_cat[mode]) < 10:
            errors.append(
                f"mode '{mode}' covers {len(by_mode_cat[mode])} categories, need >=10"
            )

    return errors


def main() -> int:
    if not DATA.exists():
        print(f"FAIL: {DATA} not found — run python data/generate_listings.py")
        return 1
    listings = json.loads(DATA.read_text())
    errors = validate(listings)
    cats = {l["category"] for l in listings}
    brands = {l["brand"] for l in listings}
    if errors:
        print(f"FAIL — {len(errors)} problem(s):")
        for e in errors[:40]:
            print("  -", e)
        if len(errors) > 40:
            print(f"  … and {len(errors) - 40} more")
        return 1
    print(
        f"OK — {len(listings)} listings · {len(cats)} categories · {len(brands)} brands · "
        f"min brands/category {min(len({l['brand'] for l in listings if l['category'] == c}) for c in cats)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
