"""What is worth a look first: a "Recommended" order that learns from what you star, bid on, hide and drop.

Every open tender gets a priority from 0 to 100, added up from parts a person can read:

    urgency   up to 25   closing in a few days to two weeks is ideal; under a day is too late to prepare; far away can wait
    fit       up to 25   the AI's fit score (1 to 5), when the AI assistant has labelled the tender
    size      up to 15   inside the size range you set (Vault > Company); 8 when no range is set or the value is not stated
    learned   -20 to +25 how much this tender looks like ones you starred or bid on, against ones you hid or dropped
    starred   10         you starred it

The learned part is a plain count, not a black box. Each thing a tender has (its department, category, type of work, place, portal, and the
main words of its title) is scored by how often you chose or avoided tenders with it: (liked - avoided) / (liked + avoided + 3). The 3
keeps one stray click from deciding anything. Nothing is learned until there are at least MIN_SIGNALS choices, and it can be switched off in
Settings. The reasons it used are kept as a short note shown with each tender, so the order is never a mystery.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime
from typing import Any

MIN_SIGNALS = 5          # choices needed before anything is learned
SMOOTHING = 3.0          # evidence a feature needs before it counts for much
KIND_WEIGHT = {"dept": 1.0, "category": 0.5, "work_type": 0.6, "location": 0.3, "portal": 0.2, "word": 0.5}
KIND_LABEL = {"dept": "department", "category": "category", "work_type": "type of work", "location": "place", "portal": "portal", "word": "wording"}
STOP = {
    "work", "works", "tender", "tenders", "with", "from", "that", "this", "under", "upto", "supply", "providing", "including", "various", "other", "amount",
    "construction", "repair", "repairs", "improvement", "maintenance", "near", "road", "roads", "side", "item", "items", "year", "years", "phase", "package",
}
# the words above are kept out of the learned wording: they are in almost every title, so they say nothing about what you like


def _words(title: str) -> list[str]:
    seen: list[str] = []
    for w in re.findall(r"[a-z]{4,}", (title or "").lower()):
        if w not in STOP and w not in seen:
            seen.append(w)
    return seen[:8]


def features(row: dict[str, Any]) -> list[tuple[str, str]]:
    """What a tender has that can be liked or avoided."""
    org = str(row.get("organisation") or "")
    dept = org.split("||")[0].strip()
    out: list[tuple[str, str]] = []
    for kind, value in (("dept", dept), ("category", row.get("category")), ("work_type", row.get("work_type")), ("location", row.get("location")), ("portal", row.get("portal"))):
        v = str(value or "").strip()
        if v:
            out.append((kind, v.lower() if kind != "dept" else v))
    out += [("word", w) for w in _words(str(row.get("title") or row.get("description") or ""))]
    return out


class Model:
    def __init__(self) -> None:
        self.liked: Counter[tuple[str, str]] = Counter()
        self.avoided: Counter[tuple[str, str]] = Counter()
        self.signals = {"positive": 0, "negative": 0}

    def add(self, row: dict[str, Any], sign: int, weight: float = 1.0) -> None:
        target = self.liked if sign > 0 else self.avoided
        for f in features(row):
            target[f] += weight
        self.signals["positive" if sign > 0 else "negative"] += 1

    def add_department(self, dept: str, sign: int, weight: float = 3.0) -> None:
        """A whole department hidden (or one you keep bidding on) is a strong, explicit choice."""
        (self.liked if sign > 0 else self.avoided)[("dept", dept)] += weight
        self.signals["positive" if sign > 0 else "negative"] += 1

    @property
    def ready(self) -> bool:
        return self.signals["positive"] + self.signals["negative"] >= MIN_SIGNALS

    def strength(self, feature: tuple[str, str]) -> float:
        """-1 (always avoided) to +1 (always chosen), pulled towards 0 when there is little evidence."""
        up, down = self.liked.get(feature, 0.0), self.avoided.get(feature, 0.0)
        return (up - down) / (up + down + SMOOTHING) if (up or down) else 0.0

    def score(self, row: dict[str, Any]) -> tuple[float, list[tuple[str, str, float]]]:
        """(-1..+1, the reasons as (kind, value, contribution)) for one tender."""
        if not self.ready:
            return 0.0, []
        parts: list[tuple[str, str, float]] = []
        words: list[tuple[str, str, float]] = []
        for kind, value in features(row):
            s = self.strength((kind, value))
            if not s:
                continue
            (words if kind == "word" else parts).append((kind, value, KIND_WEIGHT[kind] * s))
        words.sort(key=lambda p: -abs(p[2]))
        parts += words[:3]
        total = sum(p[2] for p in parts)
        parts.sort(key=lambda p: -abs(p[2]))
        return math.tanh(total / 1.5), parts

    def top(self, n: int = 6) -> dict[str, list[dict[str, Any]]]:
        """The strongest likes and dislikes, for showing what has been learned."""
        feats = set(self.liked) | set(self.avoided)
        ranked = sorted(((f, self.strength(f)) for f in feats), key=lambda x: -abs(x[1]))
        make = lambda f, s: {"kind": KIND_LABEL[f[0]], "value": f[1], "strength": round(s, 2), "chosen": round(self.liked.get(f, 0)), "avoided": round(self.avoided.get(f, 0))}  # noqa: E731
        return {"liked": [make(f, s) for f, s in ranked if s > 0.05 and f[0] in ("dept", "word", "category", "work_type")][:n],
                "avoided": [make(f, s) for f, s in ranked if s < -0.05 and f[0] in ("dept", "word", "category", "work_type")][:n]}


def _hours_left(closing: str | None, now: datetime) -> float | None:
    if not closing:
        return None
    try:
        return (datetime.fromisoformat(closing) - now).total_seconds() / 3600
    except ValueError:
        return None


def priority(row: dict[str, Any], now: datetime, model: Model | None, size_min: float | None, size_max: float | None, starred: bool) -> tuple[float, str]:
    """(0..100, a short note saying why) for one open tender."""
    notes: list[str] = []
    h = _hours_left(row.get("closing"), now)
    if h is None:
        urgency = 8
    elif h < 24:
        urgency = 5
        notes.append("closes within a day, little time to prepare")
    elif h < 72:
        urgency = 15
        notes.append("closes tomorrow" if h < 48 else "closes in 2 days")
    elif h <= 24 * 14:
        urgency = 25
        notes.append(f"closes in {int(h // 24)} days: time to prepare")
    elif h <= 24 * 30:
        urgency = 18
    else:
        urgency = 10
    fit = row.get("fit")
    fit_pts = 12 if not fit else 5 * int(fit)
    if fit and int(fit) >= 4:
        notes.append(f"good fit ({int(fit)}/5)")
    elif fit and int(fit) <= 2:
        notes.append(f"weak fit ({int(fit)}/5)")
    value = row.get("value")
    if size_min is None and size_max is None:
        size = 8
    elif value is None:
        size = 8
    elif (size_min is None or value >= size_min) and (size_max is None or value <= size_max):
        size = 15
        notes.append("fits your size")
    else:
        size = 0
        notes.append("outside your size range")
    learned = 0.0
    if model is not None and model.ready:
        x, why = model.score(row)
        learned = 25 * x if x > 0 else 20 * x
        for kind, value_, contribution in why[:2]:
            if abs(contribution) >= 0.12:
                label = f"“{value_}”" if kind == "word" else value_
                notes.append(f"you often {'choose' if contribution > 0 else 'hide'} {KIND_LABEL[kind]} {label}" if kind != "dept"
                             else f"you often {'bid on or star' if contribution > 0 else 'hide'} {value_}")
    boost = 10 if starred else 0
    if starred:
        notes.append("starred")
    total = max(0.0, min(100.0, urgency + fit_pts + size + learned + boost))
    return round(total, 1), "; ".join(notes[:4])
