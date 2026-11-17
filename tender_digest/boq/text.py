"""Text handling for matching: normalising item descriptions, pulling out technical specs, and a small TF-IDF index.

The index stands in for pgvector at the sizes this app runs at (thousands of items, in memory). It sits behind the
`SemanticIndex.search` method, so a vector database or an embedding model can replace it without touching the matcher.
"""
from __future__ import annotations

import math
import re
from collections import Counter

_SYNONYMS = [
    (r"\breinforced\s+cement\s+concrete\b", "rcc"), (r"\bplain\s+cement\s+concrete\b", "pcc"), (r"\bp\.?\s?c\.?\s?c\.?\b", "pcc"),
    (r"\br\.?\s?c\.?\s?c\.?\b", "rcc"), (r"\bw\.?\s?m\.?\s?m\.?\b", "wmm"), (r"\bwet\s+mix\s+macadam\b", "wmm"),
    (r"\bg\.?\s?s\.?\s?b\.?\b", "gsb"), (r"\bgranular\s+sub[\s-]?base\b", "gsb"), (r"\bdense\s+bituminous\s+macadam\b", "dbm"),
    (r"\bbituminous\s+concrete\b", "bc"), (r"\bthermo\s?plastic\b", "thermoplastic"), (r"\btmt\s+bars?\b", "tmt steel"),
    (r"\breinforcement\s+steel\b", "tmt steel"), (r"\bcement\s+mortar\b", "cm"), (r"\bdia(meter)?\b", "dia"),
]
# Scope words such as supply, laying and fixing are deliberately NOT stop words: "supply only" and "supply and laying" differ.
_STOP = set("a an and of the in with for to at on by from as is be per all any etc including inclusive complete incl "  # noqa: SIM905 - read as a sentence, a word list is easier to check and extend
            "work works item items done carried out".split())
_GRADE = re.compile(r"\bM[\s-]?(\d{2})\b")          # concrete grade, upper-case M only so 'm' (metre) is untouched
_FE = re.compile(r"\bFe[\s-]?(\d{3})\s?(D|S)?\b", re.I)


def normalise(text: str) -> str:
    """Lower-cased, punctuation-free, synonyms folded: 'R.C.C. M-30 in sub-structure' -> 'rcc m30 sub structure'."""
    t = text or ""
    t = _GRADE.sub(lambda m: f" m{m.group(1)} ", t)
    t = _FE.sub(lambda m: f" fe{m.group(1)}{(m.group(2) or '').lower()} ", t)
    t = t.lower()
    for rx, rep in _SYNONYMS:
        t = re.sub(rx, f" {rep} ", t)
    words = re.findall(r"[a-z]+\d*[a-z]*|\d+(?:\.\d+)?", t)
    return " ".join(w for w in words if w not in _STOP)


def alias_key(text: str) -> str:
    """Stable key for the learned-alias memory: the normalised words, in order."""
    return normalise(text)


def specs(text: str) -> dict[str, set[str]]:
    """Technical specs written in the text. Two items conflict when they state the same kind of spec with different values."""
    raw = text or ""
    low = raw.lower()
    out: dict[str, set[str]] = {}
    g = {f"M{m}" for m in _GRADE.findall(raw)}
    if g:
        out["concrete grade"] = g
    f = {f"FE{a}{(b or '').upper()}" for a, b in _FE.findall(raw)}
    if f:
        out["steel grade"] = f
    p = {m.upper() for m in re.findall(r"\bnp\s?-?(\d)\b", low)}
    if p:
        out["pipe class"] = {f"NP{x}" for x in p}
    b = {f"VG{m}" for m in re.findall(r"\bvg[\s-]?(\d{2})\b", low)}
    if b:
        out["bitumen grade"] = b
    sizes = {m for m in re.findall(r"(\d+(?:\.\d+)?)\s?mm\b", low)}
    if sizes:
        out["size mm"] = {str(float(s)).rstrip("0").rstrip(".") for s in sizes}
    return out


def spec_conflicts(a: str, b: str) -> list[str]:
    """Human-readable reasons the two descriptions cannot be the same item ('concrete grade: M30 vs M25')."""
    sa, sb = specs(a), specs(b)
    out = []
    for kind in sa.keys() & sb.keys():
        if not (sa[kind] & sb[kind]):
            out.append(f"{kind}: {', '.join(sorted(sa[kind]))} vs {', '.join(sorted(sb[kind]))}")
    return sorted(out)


def _grams(norm: str) -> Counter:
    words = norm.split()
    c: Counter = Counter(words)
    for x, y in zip(words, words[1:], strict=False):
        c[f"{x}_{y}"] += 0.5  # type: ignore[assignment]  # a pair of words counts half, so the totals are fractions
    return c


class SemanticIndex:
    """TF-IDF over normalised descriptions with word and word-pair features. `search` returns (position, cosine score)."""

    def __init__(self, texts: list[str]):
        self.docs = [_grams(normalise(t)) for t in texts]
        n = len(self.docs)
        df: Counter = Counter()
        for d in self.docs:
            df.update(d.keys())
        self.idf = {t: math.log((n + 1) / (c + 1)) + 1.0 for t, c in df.items()}
        self.vecs = [self._vec(d) for d in self.docs]

    def _vec(self, grams: Counter) -> dict[str, float]:
        # words count 1, word pairs 0.5; repeated words get a damped weight
        v = {t: ((1 + math.log(c)) if c >= 1 else c) * self.idf.get(t, 1.0) for t, c in grams.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / norm for t, x in v.items()}

    def search(self, text: str, k: int = 5, allowed: set[int] | None = None) -> list[tuple[int, float]]:
        q = self._vec(_grams(normalise(text)))
        scored = []
        for i, v in enumerate(self.vecs):
            if allowed is not None and i not in allowed:
                continue
            s = sum(w * v.get(t, 0.0) for t, w in q.items())
            if s > 0:
                scored.append((i, round(min(s, 1.0), 4)))
        scored.sort(key=lambda x: -x[1])
        return scored[:k]
