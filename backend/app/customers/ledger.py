"""The collections ledger: what happens after the 2005 dataset ends.

    payments   money the customer paid, recorded by an officer. They lower the amount the
               emails ask for, and keep a promise to pay
    promises   "I'll pay on Friday": recorded by the Communication Agent from the customer's
               email (or by an officer). Kept when the payments recorded by the due date cover
               it, broken when the date passes without them
    contacts   every contact attempt, all channels: sent emails and the calls an officer
               logs. The contact rules count them (at most N in any 7 days)

The agents read the ledger as facts (decision.case_facts) and the Follow-up Agent checks the
promises. Like do_not_contact, the tables have no foreign keys, so a re-import keeps them.
"""

from __future__ import annotations

from datetime import date

from ..core.db import Database, now_iso

PROMISE_HORIZON_DAYS = 60  # a promise further away than this is not recorded


class Ledger:
    def __init__(self, db: Database) -> None:
        self.db = db

    # payments

    def record_payment(self, customer_id: int, amount: int, paid_on: date, by: str,
                       note: str | None = None) -> dict:  # fmt: skip
        with self.db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO payments (customer_id, amount, paid_on, recorded_by, note, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (customer_id, amount, paid_on.isoformat(), by, note, now_iso()),
            )
            row = conn.execute("SELECT * FROM payments WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)

    def payments(self, customer_id: int) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM payments WHERE customer_id = ? ORDER BY paid_on DESC, id DESC",
                (customer_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def paid_total(
        self, customer_id: int, since: str | None = None, until: str | None = None
    ) -> int:
        sql, args = (
            "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE customer_id = ?",
            [customer_id],
        )
        if since:
            sql += " AND paid_on >= ?"
            args.append(since)
        if until:
            sql += " AND paid_on <= ?"
            args.append(until)
        with self.db.connect() as conn:
            return int(conn.execute(sql, args).fetchone()[0])

    # promises

    def record_promise(self, customer_id: int, due_on: date, by: str, amount: int | None = None,
                       conversation_id: int | None = None) -> dict:  # fmt: skip
        """A new promise replaces the customer's open one (the newest word counts)."""
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE promises SET status = 'cancelled', resolved_at = ?, outcome = ?"
                " WHERE customer_id = ? AND status = 'open'",
                (now_iso(), "Replaced by a newer promise.", customer_id),
            )
            cur = conn.execute(
                "INSERT INTO promises (customer_id, conversation_id, amount, due_on, status,"
                " recorded_by, created_at) VALUES (?, ?, ?, ?, 'open', ?, ?)",
                (customer_id, conversation_id, amount, due_on.isoformat(), by, now_iso()),
            )
            row = conn.execute("SELECT * FROM promises WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)

    def promises(self, customer_id: int) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM promises WHERE customer_id = ? ORDER BY id DESC", (customer_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def open_promise(self, customer_id: int) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM promises WHERE customer_id = ? AND status = 'open'"
                " ORDER BY id DESC LIMIT 1",
                (customer_id,),
            ).fetchone()
        return dict(row) if row else None

    def broken_count(self, customer_id: int, conversation_id: int | None = None) -> int:
        sql, args = (
            "SELECT COUNT(*) FROM promises WHERE customer_id = ? AND status = 'broken'",
            [customer_id],
        )
        if conversation_id is not None:
            sql += " AND conversation_id = ?"
            args.append(conversation_id)
        with self.db.connect() as conn:
            return int(conn.execute(sql, args).fetchone()[0])

    def check_promise(self, customer_id: int, today: date) -> dict | None:
        """Settles the open promise if it can be: kept when the payments since it was made
        cover it, broken when its date has passed without them. Returns the promise when it
        was settled, None when it is still open (or there is none)."""
        promise = self.open_promise(customer_id)
        if promise is None:
            return None
        made_on = promise["created_at"][:10]
        paid = self.paid_total(customer_id, since=made_on, until=promise["due_on"])
        needed = promise["amount"] or 1
        if paid >= needed:
            status, outcome = "kept", f"Kept: NT${paid:,} paid by {promise['due_on']}."
        elif today > date.fromisoformat(promise["due_on"]):
            status = "broken"
            outcome = f"Broken: NT${paid:,} paid by {promise['due_on']}" + (
                f", of NT${promise['amount']:,} promised." if promise["amount"] else "."
            )
        else:
            return None
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE promises SET status = ?, resolved_at = ?, outcome = ? WHERE id = ?",
                (status, now_iso(), outcome, promise["id"]),
            )
        return {**promise, "status": status, "outcome": outcome}

    def cancel_promises(self, customer_id: int, reason: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE promises SET status = 'cancelled', resolved_at = ?, outcome = ?"
                " WHERE customer_id = ? AND status = 'open'",
                (now_iso(), reason, customer_id),
            )

    # contacts

    def record_contact(
        self,
        customer_id: int,
        channel: str,
        outcome: str,
        by: str,
        conversation_id: int | None = None,
        note: str | None = None,
    ) -> dict:
        with self.db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO contacts (customer_id, conversation_id, channel, outcome, made_by,"
                " note, made_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (customer_id, conversation_id, channel, outcome, by, note, now_iso()),
            )
            row = conn.execute("SELECT * FROM contacts WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)

    def contacts(self, customer_id: int, limit: int = 50) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM contacts WHERE customer_id = ? ORDER BY id DESC LIMIT ?",
                (customer_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def summary(self, customer_id: int) -> dict:
        return {
            "payments": self.payments(customer_id),
            "promises": self.promises(customer_id),
            "contacts": self.contacts(customer_id),
            "paid_total": self.paid_total(customer_id),
        }
