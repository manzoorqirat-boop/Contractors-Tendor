"""The conditions that decide a civil tender, found in its text by fixed rules, each with the sentence it came from.

Reading a tender notice with an AI assistant is fast but can be wrong, and a wrong EMD or completion period costs money. These rules are the opposite trade:
they find fewer things, but every item is quoted from the document in its own words, so a person can check it in a second, and they need no API key. They
also give a second opinion on the AI's reading: `ground` checks every quote the AI offers against the document, and flags the ones that are not really there.

What is looked for (each at most once): earnest money, bid validity, completion period, defect liability period, performance security, security deposit or
retention, liquidated damages, similar-work experience, annual turnover, mobilisation advance, and whether price variation is allowed. A clause the tender does
not state is not reported: nothing is guessed.

`changes` compares the clauses of two texts (a tender and its corrigendum) and lists what moved.
"""
from __future__ import annotations

import re
from typing import Any

MONEY = r"(?:rs\.?|₹|inr)\s*([\d][\d,]*(?:\.\d+)?)\s*(crores?|cr\.?|lakhs?|lacs?)?"
PCT = r"(\d+(?:\.\d+)?)\s*(?:%|per\s*cent|percent)"
PERIOD = r"(\d+(?:\.\d+)?)\s*(days?|weeks?|months?|years?)"
GAP = r"[^.\n;]{0,%d}?"


def _gap(n: int) -> str:
    return GAP % n


def _rupees(num: str, unit: str | None) -> str:
    value = float(num.replace(",", ""))
    u = (unit or "").lower()
    if u.startswith("cr"):
        return f"₹{value:g} crore"
    if u.startswith(("lakh", "lac")):
        return f"₹{value:g} lakh"
    return "₹" + (_indian(int(value)) if value == int(value) else f"{value:,.2f}")


def _indian(n: int) -> str:
    """4,25,000 style grouping, as the tender documents write it."""
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups + [tail])


def _period(n: str, unit: str) -> str:
    v = float(n)
    u = unit.lower().rstrip("s")
    return f"{int(v) if v == int(v) else v} {u}{'' if v == 1 else 's'}"


def _quote(text: str, start: int, end: int, limit: int = 260) -> str:
    """The sentence (or line) around a match, tidied, at most `limit` characters and always containing the match."""
    # a full stop inside "Rs.", "No." or "0.5" does not end a sentence: blank those out (same length) before looking for the ends
    plain = re.sub(r"\b(?:rs|no|nos|sr|st|dr|approx|max|min|cl|sl)\.", lambda m: m.group(0)[:-1] + " ", text, flags=re.I)
    plain = re.sub(r"(?<=\d)\.(?=\d)", " ", plain)
    left = max(plain.rfind(c, 0, start) for c in ".\n;") + 1
    right_candidates = [i for i in (plain.find(c, end) for c in ".\n;") if i != -1]
    right = (min(right_candidates) + 1) if right_candidates else len(text)
    if right - left > limit:                                           # a very long sentence: keep the stretch around the match
        left = max(left, start - limit // 3)
        right = min(right, left + limit)
    return re.sub(r"\s+", " ", text[left:right]).strip(" -:\t")


def _first(pattern: str, text: str) -> re.Match[str] | None:
    return re.search(pattern, text, re.I)


def _each(kind: str, label: str, pattern: str, text: str, fmt: Any, watch: str = "") -> dict[str, Any] | None:
    m = _first(pattern, text)
    if not m:
        return None
    value = fmt(m)
    if not value:
        return None
    return {"kind": kind, "label": label, "value": value, "quote": _quote(text, m.start(), m.end()), "watch": watch}


def _emd(text: str) -> dict[str, Any] | None:
    lead = r"(?:earnest\s+money(?:\s+deposit)?|e\.?\s?m\.?\s?d\.?|bid\s+security)"
    m = _first(lead + _gap(90) + MONEY, text)
    if m:
        return {"kind": "emd", "label": "Earnest money (EMD)", "value": _rupees(m.group(1), m.group(2)), "quote": _quote(text, m.start(), m.end()), "watch": ""}
    m = _first(lead + _gap(90) + PCT, text)
    if m:
        return {"kind": "emd", "label": "Earnest money (EMD)", "value": f"{m.group(1)}% of the estimate", "quote": _quote(text, m.start(), m.end()), "watch": ""}
    return None


def _similar(text: str) -> dict[str, Any] | None:
    m = _first(r"similar\s+(?:nature\s+of\s+)?works?" + r"[^.;]{0,260}?" + PCT + r"[^.;]{0,60}?(?:estimated|tender(?:ed)?|advertised|contract)", text)
    if m:
        return {"kind": "similar_work", "label": "Similar-work experience", "value": f"a similar work worth {m.group(1)}% of the estimate",
                "quote": _quote(text, m.start(), m.end()), "watch": ""}
    m = _first(r"similar\s+(?:nature\s+of\s+)?works?[^.;]{0,200}?" + MONEY, text)
    if m:
        return {"kind": "similar_work", "label": "Similar-work experience", "value": f"a similar work worth {_rupees(m.group(1), m.group(2))}", "quote": _quote(text, m.start(), m.end()), "watch": ""}
    return None


def _damages(text: str) -> dict[str, Any] | None:
    m = _first(r"(?:liquidated\s+damages?|compensation\s+for\s+delay|penalty\s+for\s+delay)" + _gap(120) + PCT + r"[^.\n;]{0,40}?(?:per|each|every|a)\s+(week|day|month)", text)
    if not m:
        return None
    value = f"{m.group(1)}% per {m.group(2).lower()}"
    cap = _first(r"(?:maximum|max\.?|not\s+(?:to\s+)?exceed(?:ing)?|up\s*to|limited\s+to|subject\s+to\s+a\s+(?:maximum|ceiling))" + _gap(40) + PCT, text[m.start():m.end() + 260])
    if cap:
        value += f", up to {cap.group(1)}%"
    return {"kind": "liquidated_damages", "label": "Liquidated damages for delay", "value": value, "quote": _quote(text, m.start(), m.start() + (cap.end() if cap else m.end() - m.start())), "watch": ""}


def _price_variation(text: str) -> dict[str, Any] | None:
    no = _first(r"(?:no\s+(?:price\s+(?:variation|adjustment|escalation)|escalation)|not\s+(?:be\s+)?entitled\s+to\s+(?:any\s+)?(?:price\s+)?(?:variation|escalation|adjustment)|"
                r"price\s+(?:variation|adjustment|escalation)\s+(?:clause\s+)?(?:is|shall\s+be)\s+not\s+(?:applicable|admissible|allowed)|fixed\s+price\s+contract)", text)
    if no:
        return {"kind": "price_variation", "label": "Price variation", "value": "not allowed: the price is fixed for the whole contract", "quote": _quote(text, no.start(), no.end()),
                "watch": "No price escalation: material and labour rises are yours. Price this in."}
    yes = _first(r"price\s+(?:variation|adjustment|escalation)", text)
    if yes:
        return {"kind": "price_variation", "label": "Price variation", "value": "a price variation clause is mentioned (check how it works)", "quote": _quote(text, yes.start(), yes.end()), "watch": ""}
    return None


def find(text: str) -> list[dict[str, Any]]:
    """Every clause the rules can find in `text`, in a fixed order: [{kind, label, value, quote, watch}]. `watch` is a short warning, or empty."""
    text = re.sub(r"[ \t\r\f\v]+", " ", text or "")
    found = [
        _emd(text),
        _each("bid_validity", "Bid validity", r"bid\s+(?:validity|shall\s+(?:remain|be)\s+valid)" + _gap(70) + PERIOD, text, lambda m: _period(m.group(1), m.group(2))),
        _each("completion", "Time for completion", r"(?:(?:time|period)\s+(?:of|for|allowed\s+for)\s+(?:completion|execution)|completion\s+period|period\s+of\s+(?:work|contract)|contract\s+period)"
              + _gap(60) + PERIOD, text, lambda m: _period(m.group(1), m.group(2))),
        _each("defect_liability", "Defect liability period", r"(?:defects?\s+liability(?:\s+period)?|\bDLP\b|maintenance\s+period|warranty\s+period)" + _gap(60) + PERIOD, text,
              lambda m: _period(m.group(1), m.group(2))),
        _each("performance_security", "Performance security", r"performance\s+(?:security|guarantee|bank\s+guarantee)" + _gap(90) + PCT, text, lambda m: f"{m.group(1)}% of the contract value"),
        _each("retention", "Security deposit / retention", r"(?:security\s+deposit|retention(?:\s+money)?)" + _gap(70) + PCT, text, lambda m: f"{m.group(1)}%"),
        _damages(text),
        _similar(text),
        _each("turnover", "Annual turnover required", r"(?:average\s+)?annual\s+(?:financial\s+)?turn\s?over" + _gap(140) + MONEY, text, lambda m: _rupees(m.group(1), m.group(2))),
        _each("mobilisation_advance", "Mobilisation advance", r"mobili[sz]ation\s+advance" + _gap(80) + PCT, text, lambda m: f"{m.group(1)}%"),
        _price_variation(text),
    ]
    return [c for c in found if c]


# ---- checking the AI's quotes against the document ------------------------------------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def quote_in(text: str, quote: str) -> bool:
    """Is `quote` really in `text`? Case, spacing and punctuation do not matter; the words and their order do. An ellipsis (...) in the quote splits it into parts that
    must each be present in order, so a shortened quote still counts but an invented one does not."""
    parts = [p for p in (_norm(x) for x in re.split(r"\.{2,}|…", quote or "")) if len(p) >= 12]
    if not parts:
        return False
    hay, pos = _norm(text), 0
    for p in parts:
        i = hay.find(p, pos)
        if i == -1:
            return False
        pos = i + len(p)
    return True


def ground(evidence: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    """Marks each {item, quote} as verified (found in the text), unverified (not found) or None (there is no text to check against)."""
    have_text = len((text or "").strip()) >= 200
    return [{**e, "verified": quote_in(text, e.get("quote", "")) if have_text else None} for e in evidence]


# ---- what a corrigendum changed ---------------------------------------------------------------------------------------------

def changes(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Clauses stated in both with a different value, and clauses the second text adds: [{kind, label, old, new, quote}]."""
    old = {c["kind"]: c for c in before}
    out = []
    for c in after:
        was = old.get(c["kind"])
        if was and was["value"] != c["value"]:
            out.append({"kind": c["kind"], "label": c["label"], "old": was["value"], "new": c["value"], "quote": c["quote"]})
        elif not was:
            out.append({"kind": c["kind"], "label": c["label"], "old": "", "new": c["value"], "quote": c["quote"]})
    return out
