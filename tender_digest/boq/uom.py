"""Unit-of-measure normalisation (Layer 2, UOM & Conversion Master).

Tenders write the same unit many ways (Cum, Cu.m, M3). Matching only compares items with the same canonical unit,
so this file decides what "the same" means. Conversion factors are kept for reporting and for checking imports.
"""
from __future__ import annotations

import re

# canonical unit -> (dimension, factor to that dimension's base unit)
UNITS: dict[str, tuple[str, float]] = {
    "CUM": ("volume", 1.0), "CFT": ("volume", 0.0283168),
    "SQM": ("area", 1.0), "SFT": ("area", 0.09290304),      # 100 SFT = 9.29 SQM
    "RMT": ("length", 1.0), "RFT": ("length", 0.3048),
    "MT": ("mass", 1.0), "KG": ("mass", 0.001), "QTL": ("mass", 0.1),  # 1 MT = 1,000 KG
    "NOS": ("count", 1.0), "LS": ("lump sum", 1.0), "LTR": ("liquid", 1.0),
    "BAG": ("bag", 1.0), "SET": ("set", 1.0),   # cement is bought by the bag; fittings and kits by the set
}

# spellings, lower case with everything except letters and digits removed
_ALIASES: dict[str, str] = {}
for canon, spellings in {
    "CUM": "cum cmt m3 cubicmeter cubicmetre cubicmeters cubicmetres cubicm",
    "SQM": "sqm sqmtr sqmeter sqmetre squaremeter squaremetre squaremeters squaremetres m2 sqmt",
    "RMT": "rmt rm rmtr rmeter runningmeter runningmetre runningmeters runningmetres mtr m meter metre meters metres",
    "MT": "mt mton tonne tonnes ton tons metricton metrictons metrictonne",
    "KG": "kg kgs kilogram kilograms",
    "QTL": "qtl quintal quintals",
    "NOS": "nos no number numbers each ea unit units pcs pc piece pieces",
    "LS": "ls lumpsum lump",
    "LTR": "ltr ltrs lit litre litres liter liters",
    "SFT": "sft sqft sqfeet squarefeet sqft",
    "CFT": "cft cuft cubicfeet cubicfoot",
    "RFT": "rft rfeet runningfeet runningfoot",
    "BAG": "bag bags",
    "SET": "set sets",
}.items():
    for s in spellings.split():
        _ALIASES[s] = canon


def normalise_uom(raw: str | None) -> str | None:
    """'Cu.m' -> 'CUM', 'Sq. m' -> 'SQM', 'Tonne' -> 'MT'. None when the unit is blank or not recognised."""
    key = re.sub(r"[^a-z0-9]", "", (raw or "").lower())
    return _ALIASES.get(key) if key else None


def dimension(canon: str | None) -> str | None:
    return UNITS[canon][0] if canon in UNITS else None


def convert(qty: float, src: str | None, dst: str | None) -> float | None:
    """Convert a quantity between canonical units of the same dimension; None when they cannot be converted."""
    if src not in UNITS or dst not in UNITS or UNITS[src][0] != UNITS[dst][0]:
        return None
    return qty * UNITS[src][1] / UNITS[dst][1]


def all_aliases() -> dict[str, str]:
    """Every known spelling (squashed) with its canonical unit, for seeding the UOM master table."""
    return dict(_ALIASES)
