"""Fixed contact rules: a send is refused when it breaks the contact policy (CP-01).

    hours       only between the policy's hours in the customer's time zone (8:00 a.m. to
                9:00 p.m.), and never on a public holiday (PUBLIC_HOLIDAYS)
    frequency   no more than N contact attempts in any 7-day period, all channels together:
                sent emails and the calls an officer logs (ledger contacts)
    quiet       after a live conversation, none for 48 hours, unless the customer has written
                since (asked for contact)

The numbers are read from the indexed contact policy, so changing the policy changes the rule;
the settings give the values to use when the policy does not state one. The model and the Red
Team still review the wording; these rules do not depend on either of them.

The customer data has no time zones: CUSTOMER_UTC_OFFSET gives one for every customer
(+08:00, Taiwan, where the dataset comes from).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from ..core.config import Settings
from ..core.db import Database

POLICY_QUERY = (
    "Permitted contact hours, how many contact attempts are allowed in a period, and how long "
    "to wait after a live conversation with the customer"
)
NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
           "eight": 8, "nine": 9, "ten": 10}  # fmt: skip


def now() -> datetime:
    """The current time. Tests replace this to send at a fixed time."""
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Rules:
    start_hour: float
    end_hour: float
    max_contacts: int
    period_days: int
    quiet_hours: int
    holidays: frozenset[date]
    offset: timezone
    source: str  # where the numbers came from

    def describe(self) -> str:
        return (
            f"Contact only between {_clock(self.start_hour)} and {_clock(self.end_hour)} customer "
            f"time, at most {self.max_contacts} attempts in any {self.period_days} days, and none "
            f"for {self.quiet_hours} hours after a live conversation ({self.source})."
        )


def _clock(hour: float) -> str:
    return f"{int(hour):02d}:{round((hour % 1) * 60):02d}"


def _hour(number: str, minutes: str | None, half: str) -> float:
    hour = int(number) % 12 + (12 if half.lower().startswith("p") else 0)
    return hour + int(minutes or 0) / 60


def _number(word: str) -> int | None:
    return int(word) if word.isdigit() else NUMBERS.get(word.lower())


def parse(text: str) -> dict:
    """The rules a policy text states. Missing ones are left out."""
    found: dict = {}
    hours = re.search(
        r"between\s+(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?\s+and\s+(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?",
        text,
        re.IGNORECASE,
    )
    if hours:
        found["start_hour"] = _hour(hours[1], hours[2], hours[3])
        found["end_hour"] = _hour(hours[4], hours[5], hours[6])
    frequency = re.search(
        r"(?:more than|over)\s+(\w+)\s+contact attempts?\s+in\s+any\s+(\w+)[- ]day",
        text,
        re.IGNORECASE,
    )
    if frequency and _number(frequency[1]) and _number(frequency[2]):
        found["max_contacts"] = _number(frequency[1])
        found["period_days"] = _number(frequency[2])
    quiet = re.search(
        r"live conversation[^.]*?at least\s+(\w+)\s+hours", text, re.IGNORECASE | re.DOTALL
    )
    if quiet and _number(quiet[1]):
        found["quiet_hours"] = _number(quiet[1])
    return found


def _offset(text: str) -> timezone:
    match = re.fullmatch(r"([+-])(\d{2}):(\d{2})", text.strip())
    if not match:
        raise ValueError("CUSTOMER_UTC_OFFSET must look like +08:00")
    minutes = int(match[2]) * 60 + int(match[3])
    return timezone(timedelta(minutes=minutes if match[1] == "+" else -minutes))


def load(settings: Settings, kb) -> Rules:
    """The rules: the indexed contact policy first, the settings for anything it does not say."""
    stated: dict = {}
    sources: list[str] = []
    try:
        for chunk in kb.search(POLICY_QUERY, 5):
            found = {k: v for k, v in parse(chunk["text"]).items() if k not in stated}
            if found:
                stated |= found
                sources.append(chunk["filename"])
    except Exception:  # embeddings down: the settings still give safe rules
        pass
    defaults = {
        "start_hour": settings.contact_start_hour,
        "end_hour": settings.contact_end_hour,
        "max_contacts": settings.max_contacts_per_period,
        "period_days": settings.contact_period_days,
        "quiet_hours": settings.quiet_hours_after_call,
    }
    holidays = frozenset(date.fromisoformat(d) for d in settings.public_holidays)
    source = ", ".join(dict.fromkeys(sources)) or "settings"
    if sources and len(stated) < len(defaults):
        source += " and settings"
    return Rules(
        **(defaults | stated),
        holidays=holidays,
        offset=_offset(settings.customer_utc_offset),
        source=source,
    )


def load_default() -> Rules:
    """The rules from the environment's settings alone (no policy search)."""
    return load(Settings.from_env(), None)


def local_time(rules: Rules, moment: datetime | None = None) -> datetime:
    return (moment or now()).astimezone(rules.offset)


def problems(
    rules: Rules, db: Database, customer_id: int, moment: datetime | None = None
) -> list[str]:
    """Why a contact attempt may not be made now; empty when it may."""
    at = moment or now()
    local = local_time(rules, at)
    found = []
    if local.date() in rules.holidays:
        found.append(f"{local.date().isoformat()} is a public holiday: no contact is allowed")
    hour = local.hour + local.minute / 60
    if not rules.start_hour <= hour < rules.end_hour:
        found.append(
            f"it is {local:%H:%M} for the customer; contact is only allowed between "
            f"{_clock(rules.start_hour)} and {_clock(rules.end_hour)}"
        )
    since = (at - timedelta(days=rules.period_days)).isoformat(timespec="seconds")
    with db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE customer_id = ? AND made_at > ?",
            (customer_id, since),
        ).fetchone()[0]
        reached = conn.execute(
            "SELECT made_at FROM contacts WHERE customer_id = ? AND outcome = 'reached'"
            " ORDER BY made_at DESC LIMIT 1",
            (customer_id,),
        ).fetchone()
        wrote_since = None
        if reached:
            wrote_since = conn.execute(
                "SELECT 1 FROM conversation_messages m JOIN conversations c"
                " ON c.id = m.conversation_id WHERE c.customer_id = ?"
                " AND m.direction = 'inbound' AND m.created_at >= ? LIMIT 1",
                (customer_id, reached["made_at"]),
            ).fetchone()
    if count >= rules.max_contacts:
        found.append(
            f"the customer was already contacted {count} times in the last {rules.period_days} "
            f"days; the limit is {rules.max_contacts}"
        )
    if reached and not wrote_since:
        until = datetime.fromisoformat(reached["made_at"]) + timedelta(hours=rules.quiet_hours)
        if at < until:
            found.append(
                f"an officer spoke to the customer on {reached['made_at'][:16].replace('T', ' ')} "
                f"UTC; no contact for {rules.quiet_hours} hours after a live conversation, "
                "unless the customer asks"
            )
    return found


def next_window(rules: Rules, moment: datetime | None = None) -> datetime:
    """The next time inside the contact hours, on a day that is not a holiday, in UTC."""
    local = local_time(rules, moment)
    hour = local.hour + local.minute / 60
    open_now = rules.start_hour <= hour < rules.end_hour and local.date() not in rules.holidays
    if open_now:
        return local.astimezone(timezone.utc)
    day = local.date() if hour < rules.start_hour else local.date() + timedelta(days=1)
    while day in rules.holidays:
        day += timedelta(days=1)
    start = datetime.combine(day, time(int(rules.start_hour), round(rules.start_hour % 1 * 60)))
    return start.replace(tzinfo=rules.offset).astimezone(timezone.utc)
