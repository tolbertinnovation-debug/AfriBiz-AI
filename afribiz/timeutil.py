"""Time helpers. Stored timestamps are UTC ISO-8601; "today" is in the business's own timezone."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

TIMEZONE_BY_COUNTRY = {
    "KE": "Africa/Nairobi", "TZ": "Africa/Dar_es_Salaam", "UG": "Africa/Kampala", "RW": "Africa/Kigali",
    "ET": "Africa/Addis_Ababa", "SO": "Africa/Mogadishu", "SS": "Africa/Juba",
    "NG": "Africa/Lagos", "GH": "Africa/Accra", "SN": "Africa/Dakar", "CI": "Africa/Abidjan",
    "ML": "Africa/Bamako", "BF": "Africa/Ouagadougou", "TG": "Africa/Lome", "BJ": "Africa/Porto-Novo",
    "NE": "Africa/Niamey", "CM": "Africa/Douala", "GA": "Africa/Libreville", "CG": "Africa/Brazzaville",
    "CD": "Africa/Kinshasa", "SL": "Africa/Freetown", "LR": "Africa/Monrovia", "GM": "Africa/Banjul",
    "ZA": "Africa/Johannesburg", "ZM": "Africa/Lusaka", "MW": "Africa/Blantyre", "MZ": "Africa/Maputo",
    "BW": "Africa/Gaborone", "NA": "Africa/Windhoek", "ZW": "Africa/Harare",
    "EG": "Africa/Cairo", "MA": "Africa/Casablanca",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def tz(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "Africa/Nairobi")
    except ZoneInfoNotFoundError:  # pragma: no cover - only without tzdata
        return ZoneInfo("UTC")


def local_today(tz_name: str | None, now: datetime | None = None) -> date:
    return (now or utcnow()).astimezone(tz(tz_name)).date()


def period_bounds(period: str, tz_name: str | None, now: datetime | None = None) -> tuple[str, str, str]:
    """Return (start_iso_utc, end_iso_utc, label) for today / yesterday / week / month / all."""
    zone = tz(tz_name)
    local_now = (now or utcnow()).astimezone(zone)
    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    period = (period or "today").lower()
    if period == "yesterday":
        start, end, label = today_start - timedelta(days=1), today_start, "yesterday"
    elif period == "week":
        start = today_start - timedelta(days=today_start.weekday())
        end, label = today_start + timedelta(days=1), "this week"
    elif period == "month":
        start = today_start.replace(day=1)
        end, label = today_start + timedelta(days=1), "this month"
    elif period == "last30":
        start, end, label = today_start - timedelta(days=29), today_start + timedelta(days=1), "the last 30 days"
    elif period == "all":
        start, end, label = datetime(2000, 1, 1, tzinfo=zone), today_start + timedelta(days=1), "all time"
    else:
        start, end, label = today_start, today_start + timedelta(days=1), "today"
    return iso(start), iso(end), label
