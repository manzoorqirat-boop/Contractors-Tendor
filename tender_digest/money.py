"""Money tied up in bids: tender fees, earnest money (EMD), performance security and bank guarantees.

For a contractor the cash that is locked in live bids, and the refund that never gets chased, is a daily worry. Each payment is one row on a bid:
what it is, how it was paid, when, when it should come back, and what became of it. From those rows the app works out the cash locked now, the
guarantee limit in use, and a short list of things that need a person today (a refund overdue, a guarantee about to expire, an EMD not yet paid
for a bid that is closing). Nothing here talks to a bank: a person records what happened.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from .bids import _DATE, BidBook, BidError, _when

# kind -> (label, comes back to you?)
KINDS: dict[str, tuple[str, bool]] = {
    "emd": ("Earnest money (EMD)", True),
    "tender_fee": ("Tender document fee", False),
    "processing_fee": ("Processing or portal fee", False),
    "performance_security": ("Performance security", True),
    "security_deposit": ("Security deposit or retention", True),
    "other": ("Other payment", False),
}
INSTRUMENTS = {"online": "Online (net banking, UPI, NEFT)", "dd": "Demand draft", "bg": "Bank guarantee", "fdr": "Fixed deposit (FDR)", "other": "Other"}
# a bank guarantee uses a bank limit; everything else is cash that is out of your account
CASH_INSTRUMENTS = ("online", "dd", "fdr", "other")
STATUSES = {"planned": "Planned, not paid yet", "paid": "Paid", "refunded": "Refunded", "forfeited": "Forfeited", "adjusted": "Adjusted against another payment"}
SETTLED = ("refunded", "forfeited", "adjusted")
OPEN_STAGES = ("interested", "preparing", "submitted", "opened")
EXPIRY_WARNING_DAYS = 30
MAX_AMOUNT = 1e13

_FIELDS: dict[str, str | int] = {"kind": "kind", "amount": "amount", "instrument": "instrument", "status": "status", "reference": 60, "bank": 60, "paid_on": "date", "valid_until": "date",
           "refund_due": "date", "settled_on": "date", "note": 300}


def refundable(kind: str) -> bool:
    return KINDS.get(kind, ("", False))[1]


def clean(raw: Any, today: date, partial: bool = False) -> dict[str, Any]:
    """Validates a payment as typed. Raises BidError with a message fit to show. With partial, only the fields given are checked."""
    if not isinstance(raw, dict):
        raise BidError("Nothing to save")
    out: dict[str, Any] = {}
    for k, v in raw.items():
        rule = _FIELDS.get(k)
        if rule is None:
            raise BidError(f"'{k}' cannot be set")
        if rule == "kind":
            if v not in KINDS:
                raise BidError("Choose what the payment is for")
            out[k] = v
        elif rule == "instrument":
            if v not in INSTRUMENTS:
                raise BidError("Choose how it was paid")
            out[k] = v
        elif rule == "status":
            if v not in STATUSES:
                raise BidError("Unknown status")
            out[k] = v
        elif rule == "amount":
            try:
                n = float(v)
            except (TypeError, ValueError):
                raise BidError("The amount must be a number") from None
            if isinstance(v, bool) or not 0 < n < MAX_AMOUNT:
                raise BidError("The amount must be more than zero")
            out[k] = round(n, 2)
        elif rule == "date":
            v = str(v).strip() if v is not None else ""
            if v and (not _DATE.match(v) or _when(v) is None):
                raise BidError("Dates look like 2026-11-20, and must be real dates")
            out[k] = v[:10] or None
        else:
            out[k] = str(v if v is not None else "").strip()[: int(rule)]
    if not partial:
        for need, message in (("kind", "Choose what the payment is for"), ("amount", "Enter the amount")):
            if need not in out:
                raise BidError(message)
        out.setdefault("instrument", "online")
        out.setdefault("status", "planned")
    return out


def _fill(row: dict[str, Any], today: date) -> dict[str, Any]:
    """What follows from the status: a paid payment has a paid date, a settled one a settled date, and a payment that does not come back has no refund date."""
    status = row.get("status")
    if status in ("paid", *SETTLED) and not row.get("paid_on"):
        row["paid_on"] = today.isoformat()
    if status in SETTLED and not row.get("settled_on"):
        row["settled_on"] = today.isoformat()
    if status not in SETTLED:
        row["settled_on"] = None
    if not refundable(row.get("kind", "")):
        row["refund_due"] = None
    paid, settled = row.get("paid_on"), row.get("settled_on")
    if paid and settled and settled < paid:
        raise BidError("It cannot be settled before it was paid")
    due = row.get("refund_due")
    if paid and due and due < paid:
        raise BidError("The refund cannot be due before the payment")
    if row.get("instrument") not in ("bg", "fdr", "dd"):
        row["valid_until"] = None                    # only a guarantee, a deposit receipt or a draft runs out
    return row


def locked(row: dict[str, Any]) -> bool:
    """Money out that should come back and has not yet."""
    return row["status"] == "paid" and refundable(row["kind"])


class MoneyBook:
    def __init__(self, bids: BidBook):
        self.bids = bids
        self.db, self.lock = bids.db, bids.lock

    # ---- one bid -------------------------------------------------------------------------------------------------

    def for_bid(self, bid_id: int) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM bid_money WHERE bid_id = ? ORDER BY id", (bid_id,)).fetchall()
        return [dict(r) for r in rows]

    def get(self, bid_id: int, money_id: int) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT * FROM bid_money WHERE id = ? AND bid_id = ?", (money_id, bid_id)).fetchone()
        return dict(r) if r else None

    def add(self, bid_id: int, raw: Any, now: datetime) -> dict:
        row = _fill({**clean(raw, now.date())}, now.date())
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO bid_money (bid_id, kind, amount, instrument, status, reference, bank, paid_on, valid_until, refund_due, settled_on, note, created, updated) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (bid_id, row["kind"], row["amount"], row["instrument"], row["status"], row.get("reference", ""), row.get("bank", ""), row.get("paid_on"), row.get("valid_until"),
                 row.get("refund_due"), row.get("settled_on"), row.get("note", ""), now.isoformat(timespec="seconds"), now.isoformat(timespec="seconds")))
            self.db.commit()
            money_id = int(cur.lastrowid or 0)
        return self.get(bid_id, money_id) or {}

    def update(self, bid_id: int, money_id: int, raw: Any, now: datetime) -> dict:
        current = self.get(bid_id, money_id)
        if not current:
            raise BidError("No such payment")
        merged = _fill({**current, **clean(raw, now.date(), partial=True)}, now.date())
        with self.lock:
            self.db.execute(
                "UPDATE bid_money SET kind=?, amount=?, instrument=?, status=?, reference=?, bank=?, paid_on=?, valid_until=?, refund_due=?, settled_on=?, note=?, updated=? WHERE id=? AND bid_id=?",
                (merged["kind"], merged["amount"], merged["instrument"], merged["status"], merged.get("reference", ""), merged.get("bank", ""), merged.get("paid_on"), merged.get("valid_until"),
                 merged.get("refund_due"), merged.get("settled_on"), merged.get("note", ""), now.isoformat(timespec="seconds"), money_id, bid_id))
            self.db.commit()
        return self.get(bid_id, money_id) or {}

    def delete(self, bid_id: int, money_id: int) -> bool:
        with self.lock:
            cur = self.db.execute("DELETE FROM bid_money WHERE id = ? AND bid_id = ?", (money_id, bid_id))
            self.db.commit()
            return cur.rowcount > 0

    # ---- totals --------------------------------------------------------------------------------------------------

    @staticmethod
    def totals(rows: list[dict]) -> dict[str, float]:
        cash = sum(r["amount"] for r in rows if locked(r) and r["instrument"] in CASH_INSTRUMENTS)
        guarantees = sum(r["amount"] for r in rows if locked(r) and r["instrument"] == "bg")
        return {
            "cash_locked": cash, "guarantees": guarantees,
            "fees_paid": sum(r["amount"] for r in rows if r["status"] == "paid" and not refundable(r["kind"])),
            "refunded": sum(r["amount"] for r in rows if r["status"] == "refunded"),
            "forfeited": sum(r["amount"] for r in rows if r["status"] == "forfeited"),
            "planned": sum(r["amount"] for r in rows if r["status"] == "planned"),
        }

    # ---- what needs a person ----------------------------------------------------------------------------------------

    @staticmethod
    def _flags(r: dict, bid: dict, today: date) -> list[dict[str, Any]]:
        """Things to do about one payment. severity: high (act today), medium (this week), low (when convenient)."""
        out: list[dict[str, Any]] = []
        label = KINDS[r["kind"]][0]

        def flag(sev: str, code: str, text: str) -> None:
            out.append({"severity": sev, "code": code, "text": text})

        if r["status"] == "planned":
            when = (bid.get("emd_due") or "")[:10] if r["kind"] == "emd" and bid.get("emd_due") else (bid.get("closing") or "")[:10]
            try:
                days = (date.fromisoformat(when) - today).days if when else None
            except ValueError:
                days = None
            if bid.get("stage") in OPEN_STAGES and days is not None and days <= 3:
                what = "overdue" if days < 0 else "due today" if days == 0 else f"due in {days} day{'s' if days != 1 else ''}"
                flag("high" if days <= 1 else "medium", "unpaid", f"{label} is not paid yet and the {'EMD' if r['kind'] == 'emd' and bid.get('emd_due') else 'bid'} is {what}")
        if locked(r):
            if r.get("refund_due"):
                late = (today - date.fromisoformat(r["refund_due"])).days
                if late > 0:
                    flag("high", "refund_overdue", f"{label} should have come back {late} day{'s' if late != 1 else ''} ago: ask for the refund")
                elif late >= -7:
                    flag("low", "refund_soon", f"{label} is due back {'today' if late == 0 else 'in ' + str(-late) + ' day' + ('s' if late != -1 else '')}")
            elif bid.get("stage") in ("lost", "dropped"):
                flag("medium", "refund_ask", f"The bid was {bid['stage']} and the {label.lower()} is still held: ask for it back and note the date it is due")
            elif bid.get("stage") == "won" and r["kind"] == "emd":
                flag("low", "emd_won", "The bid was won and the EMD is still held: ask for it back or adjust it against the performance security")
            if r.get("valid_until") and r["instrument"] in ("bg", "fdr", "dd"):
                left = (date.fromisoformat(r["valid_until"]) - today).days
                name = INSTRUMENTS[r["instrument"]].split(" (")[0].lower()
                if left < 0:
                    flag("high", "expired", f"The {name} for the {label.lower()} ran out {-left} day{'s' if left != -1 else ''} ago")
                elif left <= EXPIRY_WARNING_DAYS:
                    flag("high" if left <= 7 else "medium", "expiring", f"The {name} for the {label.lower()} runs out in {left} day{'s' if left != 1 else ''}: renew it or collect it")
        return out

    def _all(self) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT m.*, b.tender_id, b.title, b.description, b.organisation, b.stage, b.closing, b.emd_due, b.my_value FROM bid_money m JOIN bids b ON b.id = m.bid_id "
                "ORDER BY m.id").fetchall()
        return [dict(r) for r in rows]

    def attention(self, now: datetime) -> list[dict[str, Any]]:
        """Everything needing action across all bids, most urgent first."""
        today = now.date()
        rank = {"high": 0, "medium": 1, "low": 2}
        out = []
        for r in self._all():
            bid = {k: r[k] for k in ("stage", "closing", "emd_due")}
            for f in self._flags(r, bid, today):
                out.append({**f, "bid_id": r["bid_id"], "money_id": r["id"], "tender_id": r["tender_id"], "title": (r["description"] or r["title"] or "")[:140], "kind": r["kind"],
                            "amount": r["amount"], "instrument": r["instrument"]})
        out.sort(key=lambda a: (rank[a["severity"]], a["bid_id"]))
        return out

    def bid_view(self, bid_id: int, now: datetime) -> dict[str, Any]:
        bid = self.bids.get(bid_id) or {}
        rows = self.for_bid(bid_id)
        today = now.date()
        view = []
        for r in rows:
            view.append({**r, "kind_label": KINDS[r["kind"]][0], "refundable": refundable(r["kind"]), "locked": locked(r), "flags": self._flags(r, bid, today)})
        suggestion = None
        asked = bid.get("emd")
        if asked and asked > 0 and not any(r["kind"] == "emd" for r in rows) and bid.get("stage") in OPEN_STAGES:
            suggestion = {"kind": "emd", "amount": asked, "text": "The tender asks for earnest money. Record how you pay it so it is not forgotten."}
        return {"items": view, "totals": self.totals(rows), "suggestion": suggestion}

    def summary(self, now: datetime) -> dict[str, Any]:
        rows = self._all()
        today = now.date()
        by_bid: dict[int, dict[str, Any]] = {}
        for r in rows:
            if locked(r):
                b = by_bid.setdefault(r["bid_id"], {"bid_id": r["bid_id"], "tender_id": r["tender_id"], "title": (r["description"] or r["title"] or "")[:140], "stage": r["stage"], "cash": 0.0, "guarantee": 0.0})
                b["guarantee" if r["instrument"] == "bg" else "cash"] += r["amount"]
        horizon = (today + timedelta(days=30)).isoformat()
        coming = sum(r["amount"] for r in rows if locked(r) and r.get("refund_due") and today.isoformat() <= r["refund_due"] <= horizon)
        late = sum(r["amount"] for r in rows if locked(r) and r.get("refund_due") and r["refund_due"] < today.isoformat())
        return {"totals": self.totals(rows), "by_bid": sorted(by_bid.values(), key=lambda b: -(b["cash"] + b["guarantee"])), "refunds_due_30_days": coming, "refunds_overdue": late,
                "attention": self.attention(now), "count": len(rows)}

    def digest_lines(self, now: datetime, limit: int = 8) -> list[dict[str, Any]]:
        """The urgent items for the daily email (medium and high only)."""
        return [a for a in self.attention(now) if a["severity"] in ("high", "medium")][:limit]

    def export_rows(self) -> list[list[Any]]:
        head = ["Tender ID", "Work", "Department", "Bid stage", "Payment", "Amount", "How paid", "Reference", "Bank", "Status", "Paid on", "Valid until", "Refund due", "Settled on", "Note"]
        rows = [head]
        for r in self._all():
            rows.append([r["tender_id"], (r["description"] or r["title"] or "")[:200], (r["organisation"] or "").split("||")[0], r["stage"], KINDS[r["kind"]][0], r["amount"],
                         INSTRUMENTS[r["instrument"]], r["reference"], r["bank"], STATUSES[r["status"]], r["paid_on"], r["valid_until"], r["refund_due"], r["settled_on"], r["note"]])
        return rows
