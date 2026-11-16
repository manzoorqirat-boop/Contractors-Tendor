"""Search that understands how tender titles are written, with no AI and no network.

  * Every word must be found, in any of the title, description, department, place, category or Tender ID, so "bridge nashik" finds a bridge in Nashik.
  * Words match from the start of a word ("road" finds "roads" and "roadwork"), and plurals and endings are ignored.
  * Words that mean the same in civil works are matched together: a search for "drain" also finds nala, sewer and storm water.
  * "quoted words" must appear together, and -word leaves out anything containing it.
  * Amounts in the search become value limits: "bridge above 2 crore", "road under 50 lakh".
  * When nothing matches, a close spelling found in the collected tenders is offered ("drainge" -> "drainage").
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache

# Groups of words used for the same kind of work. A search for any one finds all of them.
GROUPS: list[list[str]] = [
    ["road", "roads", "rd", "highway", "carriageway", "pavement", "asphalt", "bituminous", "resurfacing", "tarring"],
    ["drain", "drainage", "nala", "nalla", "nullah", "gutter", "sewer", "sewerage", "storm water", "stormwater"],
    ["bridge", "flyover", "culvert", "rob", "rub", "causeway", "viaduct", "cd work", "minor bridge", "major bridge"],
    ["building", "bldg", "structure"],
    ["water supply", "pipeline", "pipe line", "wss", "jal", "reservoir", "esr", "wtp", "tank"],
    ["repair", "repairs", "renovation", "restoration", "maintenance", "r&m", "upgradation", "strengthening", "rehabilitation"],
    ["concrete", "cc", "rcc", "pcc", "cement concrete"],
    ["paver", "pavers", "paver block", "interlocking"],
    ["compound wall", "boundary wall", "fencing"],
    ["earthwork", "excavation", "embankment", "filling"],
    ["canal", "irrigation", "check dam", "weir", "bandhara", "minor irrigation"],
    ["housing", "quarters", "residential", "flats", "staff quarters"],
    ["hospital", "phc", "chc", "dispensary", "health centre", "health center"],
    ["school", "classroom", "class room", "anganwadi", "college"],
]
STOP = {"a", "an", "the", "of", "for", "in", "at", "and", "to", "on", "with", "or", "work", "works", "tender", "tenders", "from", "by"}
UNITS = {"crore": 1e7, "crores": 1e7, "cr": 1e7, "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5, "l": 1e5, "thousand": 1e3, "k": 1e3}
_AMOUNT = r"(?:rs\.?|₹|inr)?\s*(\d[\d,]*(?:\.\d+)?)\s*(crores?|cr|lakhs?|lacs?|l|thousand|k)?\b"
_OVER = re.compile(r"\b(?:above|over|more than|greater than|at least|minimum|min)\s*" + _AMOUNT, re.I)
_UNDER = re.compile(r"\b(?:under|below|less than|up to|upto|at most|maximum|max)\s*" + _AMOUNT, re.I)
_TOKEN = re.compile(r'"[^"]+"|-[\w&./]+|[\w&./]+')
MAX_TERMS = 8

_GROUP_OF: dict[str, list[str]] = {}
for _g in GROUPS:
    for _w in _g:
        _GROUP_OF[_w] = _g


def stem(word: str) -> str:
    for end in ("ing", "es", "s", "ed"):
        if word.endswith(end) and len(word) - len(end) >= 4:
            return word[: -len(end)]
    return word


@dataclass
class Parsed:
    terms: list[list[str]] = field(default_factory=list)   # each term: the words that satisfy it (it and its synonyms)
    phrases: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    min_value: float | None = None
    max_value: float | None = None

    @property
    def has_text(self) -> bool:
        return bool(self.terms or self.phrases or self.excluded)

    def spec(self) -> str:
        return json.dumps({"t": self.terms, "p": self.phrases, "x": self.excluded}, separators=(",", ":"), sort_keys=True)

    def describe(self) -> str:
        """The search in words, so a person sees how it was understood."""
        bits = []
        for t in self.terms:
            bits.append(t[0] + (f" (or {', '.join(t[1:4])}{'…' if len(t) > 4 else ''})" if len(t) > 1 else ""))
        bits += [f"“{p}”" for p in self.phrases]
        out = "Searching for " + " and ".join(bits) if bits else "Searching"
        if self.excluded:
            out += ", leaving out " + ", ".join(self.excluded)
        def inr(v: float) -> str:
            return f"₹{v / 1e7:g} crore" if v >= 1e7 else f"₹{v / 1e5:g} lakh" if v >= 1e5 else f"₹{v:,.0f}"
        if self.min_value is not None:
            out += f", value over {inr(self.min_value)}"
        if self.max_value is not None:
            out += f", value under {inr(self.max_value)}"
        return out


def _amount(num: str, unit: str | None) -> float:
    return float(num.replace(",", "")) * UNITS.get((unit or "").lower(), 1.0)


def parse(q: str) -> Parsed:
    q = (q or "")[:200]
    out = Parsed()
    m = _OVER.search(q)
    if m:
        out.min_value, q = _amount(m.group(1), m.group(2)), q[:m.start()] + " " + q[m.end():]
    m = _UNDER.search(q)
    if m:
        out.max_value, q = _amount(m.group(1), m.group(2)), q[:m.start()] + " " + q[m.end():]
    for tok in _TOKEN.findall(q.lower()):
        if not re.search(r"[a-z0-9]", tok):
            continue  # punctuation alone is not a word
        if tok.startswith('"'):
            phrase = " ".join(tok.strip('"').split())
            if phrase:
                out.phrases.append(phrase)
        elif tok.startswith("-") and len(tok) > 1:
            out.excluded.append(tok[1:])
        elif tok not in STOP and len(out.terms) < MAX_TERMS:
            base = stem(tok)
            group = _GROUP_OF.get(tok) or _GROUP_OF.get(base)
            words = list(dict.fromkeys([tok, base, *(group or [])]))
            out.terms.append(words)
    return out


def _word_re(word: str) -> re.Pattern[str]:
    # starts at the beginning of a word; a short word (rd, cc, rcc) must be the whole word so "rd" does not match "standard"
    tail = r"(?![a-z0-9])" if len(word) <= 3 else ""
    return re.compile(r"(?<![a-z0-9])" + re.escape(word) + tail)


@lru_cache(maxsize=256)
def _compile(spec: str) -> tuple[list[list[re.Pattern[str]]], list[str], list[re.Pattern[str]]]:
    d = json.loads(spec)
    return [[_word_re(w) for w in t] for t in d["t"]], d["p"], [_word_re(w) for w in d["x"]]


def match(text: str | None, spec: str) -> int:
    """The function the database calls for each row: 1 when the text satisfies the search."""
    terms, phrases, excluded = _compile(spec)
    t = (text or "").lower()
    if any(p not in t for p in phrases):
        return 0
    if any(x.search(t) for x in excluded):
        return 0
    return 1 if all(any(r.search(t) for r in term) for term in terms) else 0


def suggest(q: str, vocabulary: dict[str, int]) -> str | None:
    """The search with its misspelt words replaced by the closest word in the collected tenders, or None when nothing would change."""
    parsed = parse(q)
    fixed, changed = [], False
    for tok in re.findall(r"[\w&./]+", (q or "").lower()):
        if tok in STOP or len(tok) < 4 or tok in vocabulary or stem(tok) in vocabulary or any(w.startswith(tok) for w in vocabulary if w[:1] == tok[:1]):
            fixed.append(tok)
            continue
        best = difflib.get_close_matches(tok, [w for w in vocabulary if abs(len(w) - len(tok)) <= 2], n=1, cutoff=0.75)
        if best:
            fixed.append(best[0])
            changed = True
        else:
            fixed.append(tok)
    return " ".join(fixed) if changed and parsed.has_text else None


def vocabulary_from(texts: list[str], limit: int = 30000) -> dict[str, int]:
    counts: dict[str, int] = {}
    for t in texts:
        for w in set(re.findall(r"[a-z][a-z&]{3,}", (t or "").lower())):
            counts[w] = counts.get(w, 0) + 1
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:limit]
    return dict(top)
