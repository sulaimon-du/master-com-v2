from datetime import UTC, date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# The product runs in Tajikistan: "today" for a client is the date there, not the UTC date.
LOCAL_TZ_NAME = "Asia/Dushanbe"
try:
    LOCAL_TZ = ZoneInfo(LOCAL_TZ_NAME)
except ZoneInfoNotFoundError:  # a slim image without tzdata: Tajikistan is UTC+5, no DST
    LOCAL_TZ = timezone(timedelta(hours=5), LOCAL_TZ_NAME)


def utcnow() -> datetime:
    return datetime.now(UTC)


def local_today() -> date:
    """The current calendar date in Tajikistan (Asia/Dushanbe)."""
    return utcnow().astimezone(LOCAL_TZ).date()
