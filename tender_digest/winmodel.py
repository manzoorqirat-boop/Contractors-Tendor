"""How likely is a bid to be won? Learned from your own won and lost bids, shown only when there is enough history to say anything.

A contractor decides a few dozen bids a year. With so little data a clever model only memorises, so this one is deliberately simple and explainable:

  * every feature of a bid (its work type, department, size, portal, how well it fits you, how you priced against the estimate) has a win rate among your past
    bids; each rate is pulled towards your overall win rate in proportion to how few bids it rests on (a group of 2 bids says almost nothing; 20 says a lot);
  * the pulls are added on the log-odds scale and damped, because features overlap (a department and a work type often go together) and adding them at full
    strength would make the answer overconfident.

It then tests itself the only honest way: for each past bid, in date order, it predicts from the bids before it and compares with what happened (Brier score),
against simply guessing your overall win rate. The score is shown **only when it has beaten that guess on your own history**. Until you have MIN_DECIDED decided bids,
or when the model has not earned its keep, the answer is your overall win rate and the plain statement that the history cannot yet tell good bids from bad.

What it cannot know: who else bids, what the department wants this year, or anything not recorded. It also only sees bids you chose to make, so it measures your
success among those, not the whole market. It supports a decision; it must never make one.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any

MIN_DECIDED = 40         # decided (won or lost) bids before any personal score is shown
MIN_TRAIN = 20           # bids a backtest prediction needs behind it
MIN_TESTED = 15          # predictions made in the backtest before it can vouch for the model
PRIOR = 5.0              # how many "average" bids each feature value starts with: bigger = more cautious
SHIFT_CAP = 2.5          # no single feature, or the sum, can move the log-odds by more than this
BEATS_BY = 0.95          # the model must score at most this share of the baseline's Brier score to be shown


def logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def band(v: float | None) -> str:
    if not v or v <= 0:
        return ""
    return "under 1 lakh" if v < 1e5 else "1 to 10 lakh" if v < 1e6 else "10 lakh to 1 crore" if v < 1e7 else "1 to 10 crore" if v < 1e8 else "over 10 crore"


def fit_bucket(fit: int | None) -> str:
    return "" if not fit else "fit 4 to 5" if fit >= 4 else "fit 3" if fit == 3 else "fit 1 to 2"


def quote_bucket(my_value: float | None, estimate: float | None) -> str:
    if not my_value or not estimate or estimate <= 0 or my_value <= 0:
        return ""
    pct = (my_value / estimate - 1) * 100
    return "quoted over 10% below the estimate" if pct < -10 else "quoted 3 to 10% below the estimate" if pct < -3 else "quoted within 3% of the estimate" if pct <= 3 else "quoted above the estimate"


def features(row: dict[str, Any]) -> dict[str, str]:
    """What is known about a bid, as {feature: value}. A feature that is not known is left out, never guessed."""
    f = {
        "work type": (row.get("work_type") or "").strip(),
        "department": ((row.get("organisation") or "").split("||")[0].strip()).lower(),
        "size": band(row.get("value")),
        "portal": (row.get("portal") or "").strip(),
        "fit": fit_bucket(row.get("fit")),
        "price": quote_bucket(row.get("my_value"), row.get("value")),
    }
    return {k: v for k, v in f.items() if v}


def rows_from(bids: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Decided bids as training rows: {won, feats, when}."""
    out = []
    for b in bids:
        if b.get("stage") not in ("won", "lost"):
            continue
        when = None
        try:
            when = datetime.fromisoformat(str(b.get("stage_changed") or b.get("updated") or "")[:19])
        except ValueError:
            when = None
        out.append({"won": b["stage"] == "won", "feats": features(b), "when": when})
    return out


def fit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    wins = sum(1 for r in rows if r["won"])
    base = (wins + 1) / (n + 2)                                    # a gentle prior so 0 of 3 is not "never" and 3 of 3 is not "always"
    table: dict[tuple[str, str], list[int]] = {}
    for r in rows:
        for k, v in r["feats"].items():
            cell = table.setdefault((k, v), [0, 0])
            cell[0] += 1 if r["won"] else 0
            cell[1] += 1
    return {"n": n, "wins": wins, "base": base, "table": table}


def predict(model: dict[str, Any], feats: dict[str, str]) -> dict[str, Any]:
    base = model["base"]
    known = [(k, v) for k, v in feats.items() if (k, v) in model["table"]]
    damp = 1 / math.sqrt(len(known)) if known else 1.0
    total, parts = 0.0, []
    for k, v in known:
        w, n = model["table"][(k, v)]
        smoothed = (w + PRIOR * base) / (n + PRIOR)
        shift = max(-SHIFT_CAP, min(SHIFT_CAP, logit(smoothed) - logit(base))) * damp
        total += shift
        parts.append({"feature": k, "value": v, "wins": w, "of": n, "shift": round(shift, 2)})
    total = max(-SHIFT_CAP, min(SHIFT_CAP, total))
    p = sigmoid(logit(base) + total)
    parts.sort(key=lambda x: -abs(x["shift"]))
    return {"p": p, "base": base, "parts": parts}


def backtest(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Each bid, in date order, predicted from the bids before it. Compared with guessing your overall win rate so far."""
    dated = sorted((r for r in rows if r["when"]), key=lambda r: r["when"])
    if len(dated) < MIN_TRAIN + MIN_TESTED:
        return {"tested": 0, "reliable": False, "have": len(dated), "needed": MIN_TRAIN + MIN_TESTED}
    se_model = se_base = 0.0
    tested = 0
    bands: dict[str, list[float]] = {"low": [0, 0, 0.0], "mid": [0, 0, 0.0], "high": [0, 0, 0.0]}   # [n, wins, sum of predicted]
    for i in range(MIN_TRAIN, len(dated)):
        model = fit(dated[:i])
        p = predict(model, dated[i]["feats"])["p"]
        y = 1.0 if dated[i]["won"] else 0.0
        se_model += (p - y) ** 2
        se_base += (model["base"] - y) ** 2
        tested += 1
        cell = bands["low" if p < 0.25 else "high" if p >= 0.5 else "mid"]
        cell[0] += 1
        cell[1] += y
        cell[2] += p
    brier_model, brier_base = se_model / tested, se_base / tested
    return {"tested": tested, "brier_model": round(brier_model, 3), "brier_baseline": round(brier_base, 3), "reliable": tested >= MIN_TESTED and brier_model <= brier_base * BEATS_BY,
            "calibration": [{"band": k, "n": int(c[0]), "predicted_pct": round(100 * c[2] / c[0]), "actual_pct": round(100 * c[1] / c[0])} for k, c in bands.items() if c[0]]}


def assess(bids: list[dict[str, Any]], candidate: dict[str, Any] | None = None) -> dict[str, Any]:
    """The whole answer for the page: what is known, whether a score is earned, and (with a candidate bid) its score and why."""
    rows = rows_from(bids)
    n = len(rows)
    wins = sum(1 for r in rows if r["won"])
    out: dict[str, Any] = {"decided": n, "won": wins, "need": MIN_DECIDED, "state": "too_few", "overall_pct": round(100 * wins / n) if n else None, "score_pct": None, "parts": [], "test": None}
    if n < MIN_DECIDED:
        return out
    test = backtest(rows)
    out["test"] = test
    out["state"] = "ready" if test["reliable"] else "no_edge"
    if candidate is not None and test["reliable"]:
        pr = predict(fit(rows), features(candidate))
        out["score_pct"] = round(100 * pr["p"])
        out["parts"] = [p for p in pr["parts"] if abs(p["shift"]) >= 0.15][:4]      # only what really moved the answer is worth explaining
    return out
