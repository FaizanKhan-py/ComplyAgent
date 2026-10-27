"""The collections dashboard: how the agents and the officers are doing, from the database.

work           what waits for a person: drafts to approve (and for a team lead), blocked
               drafts, conversations handed over, follow-ups due
conversations  open, with a person, closed; and the emails each closed one needed
               ("emails per resolution")
promises       open, kept, broken; the share of settled promises that were kept
compliance     how often the Red Team passed a draft on the first round, the average number
               of rounds, sends the contact rules refused, customers who asked for no contact
activity       emails sent per day over the last 14 days, and what customers wrote about
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from ..core.db import Database

DAYS = 14


def _count(conn, sql: str, args: tuple = ()) -> int:
    return int(conn.execute(sql, args).fetchone()[0] or 0)


def summary(db: Database, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    now_text = now.isoformat(timespec="seconds")
    since = (now - timedelta(days=DAYS - 1)).date().isoformat()
    with db.connect() as conn:
        drafts = {
            row["status"]: row["n"]
            for row in conn.execute(
                "SELECT m.status, COUNT(*) AS n FROM conversation_messages m"
                " JOIN conversations c ON c.id = m.conversation_id"
                " WHERE m.direction = 'outbound' AND c.status != 'closed'"
                " AND m.status IN ('pending_approval', 'awaiting_supervisor', 'blocked',"
                " 'approved')"
                " GROUP BY m.status"
            )
        }
        conversations = {
            row["status"]: row["n"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS n FROM conversations GROUP BY status"
            )
        }
        per_closed = conn.execute(
            "SELECT COUNT(*) AS closed, COALESCE(SUM(sent), 0) AS emails FROM ("
            " SELECT c.id, (SELECT COUNT(*) FROM conversation_messages m"
            "  WHERE m.conversation_id = c.id AND m.status = 'sent') AS sent"
            " FROM conversations c WHERE c.status = 'closed') AS closed_ones"
        ).fetchone()
        promises = {
            row["status"]: row["n"]
            for row in conn.execute("SELECT status, COUNT(*) AS n FROM promises GROUP BY status")
        }
        reviews = [
            json.loads(row["review"])
            for row in conn.execute(
                "SELECT review FROM conversation_messages WHERE direction = 'outbound'"
                " AND review IS NOT NULL"
            )
        ]
        sent_by_day = {
            row["day"]: row["n"]
            for row in conn.execute(
                "SELECT SUBSTR(sent_at, 1, 10) AS day, COUNT(*) AS n FROM conversation_messages"
                " WHERE status = 'sent' AND sent_at >= ? GROUP BY SUBSTR(sent_at, 1, 10)",
                (since,),
            )
        }
        intents = {
            row["intent"]: row["n"]
            for row in conn.execute(
                "SELECT intent, COUNT(*) AS n FROM conversation_messages"
                " WHERE direction = 'inbound' AND intent IS NOT NULL GROUP BY intent"
            )
        }
        escalated = [
            dict(row)
            for row in conn.execute(
                "SELECT id, customer_id, status_reason, updated_at FROM conversations"
                " WHERE status = 'escalated' ORDER BY updated_at DESC LIMIT 5"
            )
        ]
        follow_ups_due = _count(
            conn, "SELECT COUNT(*) FROM follow_ups WHERE status = 'scheduled' AND due_at <= ?",
            (now_text,),
        )  # fmt: skip
        follow_ups_scheduled = _count(
            conn, "SELECT COUNT(*) FROM follow_ups WHERE status = 'scheduled'"
        )
        refused = _count(conn, "SELECT COUNT(*) FROM audit_log WHERE event = 'contact.refused'")
        do_not_contact = _count(conn, "SELECT COUNT(*) FROM do_not_contact")
        delivered = _count(
            conn,
            "SELECT COUNT(*) FROM conversation_messages WHERE status = 'sent' AND delivered = 1",
        )
        sent_total = _count(
            conn, "SELECT COUNT(*) FROM conversation_messages WHERE status = 'sent'"
        )
        collected = _count(conn, "SELECT COALESCE(SUM(amount), 0) FROM payments")

    rounds = [r.get("rounds") or [] for r in reviews]
    first_try = [rs for rs in rounds if rs]
    kept, broken = promises.get("kept", 0), promises.get("broken", 0)
    closed = per_closed["closed"]
    days = [(now - timedelta(days=n)).date().isoformat() for n in range(DAYS - 1, -1, -1)]
    return {
        "generated_at": now_text,
        "work": {
            "pending_approval": drafts.get("pending_approval", 0),
            "awaiting_supervisor": drafts.get("awaiting_supervisor", 0),
            "blocked": drafts.get("blocked", 0),
            "approved_unsent": drafts.get("approved", 0),
            "escalated": conversations.get("escalated", 0),
            "follow_ups_due": follow_ups_due,
        },
        "conversations": {
            "open": conversations.get("open", 0),
            "escalated": conversations.get("escalated", 0),
            "closed": closed,
            "emails_per_resolution": round(per_closed["emails"] / closed, 2) if closed else None,
            "recent_escalations": escalated,
        },
        "promises": {
            "open": promises.get("open", 0),
            "kept": kept,
            "broken": broken,
            "kept_rate": round(kept / (kept + broken), 3) if kept + broken else None,
            "collected": collected,
        },
        "compliance": {
            "drafts_reviewed": len(first_try),
            "first_round_pass_rate": (
                round(sum(1 for rs in first_try if rs[0]["passed"]) / len(first_try), 3)
                if first_try
                else None
            ),
            "average_rounds": (
                round(sum(len(rs) for rs in first_try) / len(first_try), 2) if first_try else None
            ),
            "sends_refused": refused,
            "do_not_contact": do_not_contact,
        },
        "activity": {
            "emails_sent": sent_total,
            "emails_delivered": delivered,
            "follow_ups_scheduled": follow_ups_scheduled,
            "sent_by_day": [{"day": d, "emails": sent_by_day.get(d, 0)} for d in days],
            "intents": dict(sorted(intents.items(), key=lambda kv: -kv[1])),
        },
    }
