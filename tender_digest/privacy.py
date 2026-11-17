"""What the app keeps about a person: a copy they (or an administrator) can download, and erasure.

Erasing does not delete the work: tenders, bids, comments and the activity log stay, so the company's records are whole. What goes is
everything that says who the person was. Their name, username, email, password, two-step secret and sign-ins are removed, and their name on
comments, checklist items, bids, approvals and the activity log is replaced by one neutral handle ("erased-7").
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any


class PrivacyError(ValueError):
    pass


def _row(r: Any, drop: tuple[str, ...] = ()) -> dict[str, Any]:
    return {k: r[k] for k in r.keys() if k not in drop}  # noqa: SIM118 - a sqlite3.Row iterates its values, not its column names


def export_person(people: Any, bids: Any, user_id: int) -> dict[str, Any]:
    """Everything stored about one account, without the password hash or the two-step secret."""
    with people.lock:
        u = people.db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not u:
            raise PrivacyError("No such person")
        name = u["username"]
        audit = [_row(r, ("id",)) for r in people.db.execute("SELECT * FROM audit WHERE username = ? ORDER BY id DESC LIMIT 5000", (name,))]
        comments = [_row(r, ("id",)) for r in bids.db.execute("SELECT bid_id, at, text FROM bid_comments WHERE username = ? ORDER BY id", (name,))]
        items = [_row(r) for r in bids.db.execute("SELECT i.bid_id, i.text, i.done FROM bid_items i WHERE LOWER(i.owner) = ?", (name.lower(),))]
        owned = [_row(r) for r in bids.db.execute("SELECT id AS bid_id, tender_id, title, stage FROM bids WHERE LOWER(owner) IN (?, ?)", (name.lower(), (u["name"] or "").lower()))]
        signoffs = []
        for b in bids.db.execute("SELECT id, signoff FROM bids WHERE signoff IS NOT NULL"):
            try:
                s = json.loads(b["signoff"])
            except ValueError:
                continue
            if name in (s.get("by"), s.get("requested_by")):
                signoffs.append({"bid_id": b["id"], **s})
        sessions = people.db.execute("SELECT COUNT(*) FROM sessions WHERE user_id = ?", (user_id,)).fetchone()[0]
    return {"exported": datetime.now().isoformat(timespec="seconds"), "account": _row(u, ("pw_hash", "totp_secret", "totp_last")) | {"two_step_on": bool(u["totp_on"])},
            "activity": audit, "comments": comments, "checklist_items_assigned": items, "bids_owned": owned, "approvals": signoffs, "signed_in_devices": sessions}


def erase_person(people: Any, bids: Any, store: Any, user_id: int, now: datetime) -> dict[str, Any]:
    """Removes who a person was from the account and everywhere their name is written. Returns what was changed."""
    handle = f"erased-{user_id}"
    with people.lock:
        u = people.db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not u:
            raise PrivacyError("No such person")
        if u["username"] == handle:
            raise PrivacyError("That person's data was already erased")
        name, display = u["username"], (u["name"] or "")
        counts: dict[str, int] = {}
        counts["activity"] = people.db.execute("UPDATE audit SET username = ? WHERE username = ?", (handle, name)).rowcount
        people.db.execute("DELETE FROM password_resets WHERE user_id = ?", (user_id,))
        people.db.execute("UPDATE users SET username = ?, name = 'Erased person', email = '', pw_hash = '!', active = 0, must_change = 0, totp_secret = NULL, totp_on = 0, totp_last = 0, last_login = NULL "
                          "WHERE id = ?", (handle, user_id))
        people.db.commit()
    store.sessions_end_for(user_id)
    with bids.lock:
        counts["comments"] = bids.db.execute("UPDATE bid_comments SET username = ? WHERE username = ?", (handle, name)).rowcount
        counts["checklist_items"] = bids.db.execute("UPDATE bid_items SET owner = NULL WHERE LOWER(owner) = ?", (name.lower(),)).rowcount
        names = [x for x in {name.lower(), display.lower()} if x]
        counts["bids_owned"] = bids.db.execute(f"UPDATE bids SET owner = NULL WHERE LOWER(owner) IN ({','.join('?' * len(names))})", names).rowcount
        n = 0
        for b in bids.db.execute("SELECT id, signoff FROM bids WHERE signoff IS NOT NULL").fetchall():
            try:
                s = json.loads(b["signoff"])
            except ValueError:
                continue
            changed = False
            for k in ("by", "requested_by"):
                if s.get(k) == name:
                    s[k], changed = handle, True
            if changed:
                bids.db.execute("UPDATE bids SET signoff = ? WHERE id = ?", (json.dumps(s), b["id"]))
                n += 1
        counts["approvals"] = n
        # a person's name written in free text (a note "asked by meera") is left alone: it is the company's own record, and the app cannot tell a name from a word
        bids.db.commit()
    return {"handle": handle, "changed": counts, "when": now.isoformat(timespec="seconds"), "note": "Names typed by hand into notes are not changed."}
