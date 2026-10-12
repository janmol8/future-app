"""Time helpers. Rule: all market logic in Asia/Kolkata, all storage in UTC."""
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def ist_now() -> datetime:
    return datetime.now(IST)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_utc(dt: datetime) -> datetime:
    """Normalize an aware datetime to UTC. Naive datetimes are assumed IST."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(timezone.utc)


def to_ist(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def iso_utc(dt: datetime | None = None) -> str:
    """ISO-8601 string in UTC with offset — the canonical storage format."""
    dt = dt or utc_now()
    return to_utc(dt).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def ist_date_str(dt: datetime | None = None) -> str:
    return to_ist(dt or ist_now()).strftime("%Y-%m-%d")
