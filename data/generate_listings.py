"""Generate the mock marketplace (spec FR-6).

Floor enforced by construction, not by luck: >=10 categories, >=10 brands in
EVERY category, >=100 listings, and every listing has >=1 availability window.
The taxonomy below is 12 categories x >=10 brands, so the brand-per-category
requirement is satisfied before a single listing is drawn.

Deterministic: fixed seed, so the committed data/listings.json is reproducible
and demos are stable. Run `python data/generate_listings.py` to regenerate.

Note: BMW Group marques (BMW, Mini, Rolls-Royce) are deliberately absent — the
brief puts BMW APIs out of scope, and keeping the badge out of the mock data
avoids any ambiguity.
"""
from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

SEED = 20260808
OUT = Path(__file__).resolve().parent / "listings.json"
BASE_DATE = date(2026, 8, 1)

# category -> brand -> [models].  Every category has >= 10 brands.
TAXONOMY: dict[str, dict[str, list[str]]] = {
    "Hatchback": {
        "Maruti Suzuki": ["Swift", "Baleno", "Ignis"], "Hyundai": ["i20", "Grand i10"],
        "Tata": ["Altroz", "Tiago"], "Honda": ["Jazz"], "Toyota": ["Glanza"],
        "Renault": ["Kwid"], "Nissan": ["Micra"], "Volkswagen": ["Polo"],
        "Skoda": ["Fabia"], "Citroen": ["C3"],
    },
    "Sedan": {
        "Maruti Suzuki": ["Ciaz", "Dzire"], "Hyundai": ["Verna", "Aura"],
        "Honda": ["City", "Amaze"], "Toyota": ["Camry"], "Skoda": ["Slavia", "Octavia"],
        "Volkswagen": ["Virtus"], "Tata": ["Tigor"], "Kia": ["K3"],
        "Nissan": ["Sunny"], "MG": ["MG5"],
    },
    "Compact SUV": {
        "Maruti Suzuki": ["Brezza", "Fronx"], "Hyundai": ["Venue", "Exter"],
        "Tata": ["Nexon", "Punch"], "Mahindra": ["XUV300", "Bolero Neo"],
        "Kia": ["Sonet"], "Toyota": ["Urban Cruiser"], "Honda": ["Elevate"],
        "MG": ["Astor"], "Nissan": ["Magnite"], "Renault": ["Kiger"],
        "Skoda": ["Kylaq"], "Volkswagen": ["Taigun"],
    },
    "SUV": {
        "Mahindra": ["Scorpio N", "XUV700", "Thar"], "Tata": ["Harrier", "Safari"],
        "Hyundai": ["Creta", "Alcazar"], "Kia": ["Seltos", "Carens"],
        "Toyota": ["Fortuner", "Hyryder"], "MG": ["Hector"], "Jeep": ["Compass"],
        "Skoda": ["Kushaq"], "Volkswagen": ["Tiguan"], "Honda": ["CR-V"],
    },
    "MUV": {
        "Maruti Suzuki": ["Ertiga", "XL6"], "Toyota": ["Innova Crysta", "Rumion"],
        "Kia": ["Carnival"], "Renault": ["Triber"], "Mahindra": ["Marazzo"],
        "Tata": ["Winger"], "Hyundai": ["Stargazer"], "Honda": ["Mobilio"],
        "MG": ["G10"], "Citroen": ["C3 Aircross"],
    },
    "Luxury Sedan": {
        "Mercedes-Benz": ["C-Class", "E-Class"], "Audi": ["A4", "A6"],
        "Volvo": ["S90"], "Lexus": ["ES"], "Jaguar": ["XF"], "Porsche": ["Panamera"],
        "Genesis": ["G80"], "Maserati": ["Ghibli"], "Cadillac": ["CT5"],
        "Tesla": ["Model S"],
    },
    "Luxury SUV": {
        "Mercedes-Benz": ["GLC", "GLE"], "Audi": ["Q5", "Q7"], "Volvo": ["XC60", "XC90"],
        "Lexus": ["NX"], "Jaguar": ["F-Pace"], "Land Rover": ["Defender", "Discovery"],
        "Porsche": ["Cayenne"], "Maserati": ["Levante"], "Bentley": ["Bentayga"],
        "Genesis": ["GV80"],
    },
    "Electric": {
        "Tata": ["Nexon EV", "Punch EV"], "MG": ["ZS EV", "Comet"],
        "Hyundai": ["Ioniq 5"], "Kia": ["EV6"], "BYD": ["Atto 3"],
        "Mahindra": ["XUV400"], "Citroen": ["eC3"], "Volvo": ["EX40"],
        "Mercedes-Benz": ["EQB"], "Audi": ["Q8 e-tron"],
    },
    "Coupe": {
        "Porsche": ["911", "Cayman"], "Audi": ["TT"], "Mercedes-Benz": ["CLE"],
        "Lexus": ["RC"], "Jaguar": ["F-Type"], "Ford": ["Mustang"],
        "Chevrolet": ["Camaro"], "Nissan": ["Z"], "Toyota": ["GR Supra"],
        "Genesis": ["G70 Shooting Brake"],
    },
    "Convertible": {
        "Mercedes-Benz": ["SL"], "Audi": ["A5 Cabriolet"], "Porsche": ["Boxster"],
        "Jaguar": ["F-Type Convertible"], "Mazda": ["MX-5"], "Ford": ["Mustang Convertible"],
        "Chevrolet": ["Corvette Convertible"], "Lexus": ["LC Convertible"],
        "Maserati": ["GranCabrio"], "Bentley": ["Continental GTC"],
    },
    "Pickup": {
        "Toyota": ["Hilux"], "Isuzu": ["V-Cross", "D-Max"], "Ford": ["Ranger"],
        "Chevrolet": ["Colorado"], "Nissan": ["Navara"], "Mitsubishi": ["Triton"],
        "Ram": ["1500"], "GMC": ["Canyon"], "Mahindra": ["Bolero Pik-Up"],
        "Tata": ["Yodha"],
    },
    "Van": {
        "Maruti Suzuki": ["Eeco"], "Toyota": ["HiAce"], "Force": ["Traveller"],
        "Mercedes-Benz": ["Sprinter"], "Ford": ["Transit"], "Renault": ["Trafic"],
        "Volkswagen": ["Transporter"], "Hyundai": ["Staria"], "Tata": ["Magic"],
        "Nissan": ["NV200"],
    },
}

# category -> (sale price range in INR, typical seats, plausible fuels)
PROFILE = {
    "Hatchback":     ((450_000, 950_000), [5], ["petrol", "diesel", "hybrid"]),
    "Sedan":         ((800_000, 1_900_000), [5], ["petrol", "diesel", "hybrid"]),
    "Compact SUV":   ((800_000, 1_600_000), [5], ["petrol", "diesel", "hybrid"]),
    "SUV":           ((1_200_000, 3_200_000), [5, 7], ["petrol", "diesel", "hybrid"]),
    "MUV":           ((900_000, 2_200_000), [6, 7, 8], ["petrol", "diesel", "hybrid"]),
    "Luxury Sedan":  ((4_500_000, 12_000_000), [5], ["petrol", "diesel", "hybrid", "ev"]),
    "Luxury SUV":    ((6_000_000, 20_000_000), [5, 7], ["petrol", "diesel", "hybrid", "ev"]),
    "Electric":      ((1_200_000, 6_000_000), [5, 7], ["ev"]),
    "Coupe":         ((5_500_000, 20_000_000), [2, 4], ["petrol", "hybrid"]),
    "Convertible":   ((6_000_000, 25_000_000), [2, 4], ["petrol", "hybrid"]),
    "Pickup":        ((1_000_000, 4_000_000), [5], ["diesel", "petrol"]),
    "Van":           ((600_000, 2_500_000), [7, 9, 12], ["diesel", "petrol", "ev"]),
}

LOCATIONS = ["Delhi", "Gurugram", "Noida", "Mumbai", "Pune", "Bengaluru",
             "Hyderabad", "Chennai", "Kolkata", "Jaipur", "Ahmedabad", "Goa"]

FEATURES = ["sunroof", "adas", "360-camera", "ventilated-seats", "wireless-charging",
            "connected-tech", "hill-assist", "roof-rails", "ambient-lighting",
            "premium-audio", "cruise-control", "parking-sensors"]


def _rent_per_day(sale_price: int) -> int:
    """Rental day-rate heuristic: ~0.12% of capital value, rounded to 50."""
    raw = max(900, int(sale_price * 0.0012))
    return int(round(raw / 50.0) * 50)


def _availability(rng: random.Random, for_: str) -> list[dict[str, str]]:
    """1-3 windows. Sale listings get one long window; rentals get gappy ones
    so that target-date overlap is a real filter, not a formality."""
    windows = []
    if for_ == "sale":
        start = BASE_DATE + timedelta(days=rng.randint(0, 20))
        windows.append({"from": start.isoformat(),
                        "to": (start + timedelta(days=rng.randint(120, 240))).isoformat()})
    else:
        cursor = BASE_DATE + timedelta(days=rng.randint(0, 14))
        for _ in range(rng.randint(1, 3)):
            length = rng.randint(10, 45)
            windows.append({"from": cursor.isoformat(),
                            "to": (cursor + timedelta(days=length)).isoformat()})
            cursor += timedelta(days=length + rng.randint(5, 25))
    return windows


# Mainstream categories get more inventory per brand. This is realism AND
# usability: a compound query like "7-seat automatic SUV under 20 lakh" must
# return something, and with one listing per brand-category pair it often
# returned nothing. Niche categories stay thin, which is true to life.
DEPTH = {
    "Hatchback": 2, "Sedan": 2, "Compact SUV": 2, "SUV": 3, "MUV": 2, "Electric": 2,
    "Luxury Sedan": 1, "Luxury SUV": 1, "Coupe": 1, "Convertible": 1,
    "Pickup": 1, "Van": 1,
}


def generate() -> list[dict]:
    rng = random.Random(SEED)
    listings: list[dict] = []
    n = 0
    for category, brands in TAXONOMY.items():
        (lo, hi), seat_opts, fuels = PROFILE[category]
        depth = DEPTH[category]
        for brand, models in brands.items():
            # >=1 sale and >=1 rent per brand-category pair: both modes inherit
            # the full taxonomy, so a renter sees >=10 categories too.
            for for_ in ("sale",) * depth + ("rent",) * depth:
                model = rng.choice(models)
                sale_price = int(rng.uniform(lo, hi) / 10_000) * 10_000
                n += 1
                listings.append({
                    "listing_id": f"l-{n:03d}",
                    "for": for_,
                    "brand": brand,
                    "model": model,
                    "category": category,
                    "year": rng.randint(2021, 2026),
                    "price": (
                        {"amount": sale_price, "period": "total", "currency": "INR"}
                        if for_ == "sale"
                        else {"amount": _rent_per_day(sale_price), "period": "per_day", "currency": "INR"}
                    ),
                    "fuel": rng.choice(fuels),
                    "transmission": rng.choice(["manual", "automatic", "automatic"]),
                    "seats": rng.choice(seat_opts),
                    "location": rng.choice(LOCATIONS),
                    "features": sorted(rng.sample(FEATURES, rng.randint(2, 5))),
                    "availability": _availability(rng, for_),
                })
    return listings


if __name__ == "__main__":
    data = generate()
    OUT.write_text(json.dumps(data, separators=(",", ":")))
    cats = {l["category"] for l in data}
    print(f"wrote {len(data)} listings, {len(cats)} categories -> {OUT}")
