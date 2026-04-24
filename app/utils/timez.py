from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from flask import has_request_context
from flask_login import current_user

from ..constants import APP_TIMEZONE


UTC_TZ = timezone.utc
LOCAL_TZ = ZoneInfo(APP_TIMEZONE)


def _get_user_tz(user=None) -> ZoneInfo:
    tz_name = None

    if user is not None:
        tz_name = getattr(user, 'timezone', None)

    if not tz_name and has_request_context():
        try:
            if getattr(current_user, 'is_authenticated', False):
                tz_name = getattr(current_user, 'timezone', None)
        except Exception:
            pass

    tz_name = (tz_name or APP_TIMEZONE).strip()

    try:
        return ZoneInfo(tz_name)
    except Exception:
        return LOCAL_TZ


def local_now(user=None) -> datetime:
    return datetime.now(_get_user_tz(user))


def local_today(user=None) -> date:
    return local_now(user).date()


def _utcnow() -> datetime:
    return datetime.now(UTC_TZ).replace(tzinfo=None)


def _parse_date(value, fallback: date | None = None) -> date | None:
    """Parse a date from form/query JSON input.

    Routes may pass a fallback date when a missing or invalid value should
    safely resolve to today or the currently selected calendar date. Calls
    without a fallback still return None, which keeps helper functions such
    as _aware_local_datetime strict for validation.
    """
    if value is None or value == '':
        return fallback

    if isinstance(value, date) and not isinstance(value, datetime):
        return value

    if isinstance(value, datetime):
        return value.date()

    text = str(value).strip()
    if not text:
        return fallback

    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        return fallback


def _parse_time(value) -> time | None:
    if value is None or value == '':
        return None

    if isinstance(value, time):
        return value.replace(tzinfo=None)

    text = str(value).strip()
    if not text:
        return None

    for fmt in ('%H:%M', '%H:%M:%S'):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue

    try:
        return time.fromisoformat(text)
    except ValueError:
        return None


def _aware_local_datetime(value_date, value_time=None, user=None) -> datetime | None:
    parsed_date = _parse_date(value_date)
    if parsed_date is None:
        return None

    parsed_time = _parse_time(value_time) or time(0, 0)
    return datetime.combine(parsed_date, parsed_time, tzinfo=_get_user_tz(user))


def _local_duration_hours(start_dt, end_dt) -> float:
    if start_dt is None or end_dt is None:
        return 0.0

    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=LOCAL_TZ)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=LOCAL_TZ)

    seconds = (end_dt - start_dt).total_seconds()
    return max(0.0, seconds / 3600.0)


__all__ = [
    'LOCAL_TZ',
    'UTC_TZ',
    'local_now',
    'local_today',
    '_parse_date',
    '_parse_time',
    '_aware_local_datetime',
    '_local_duration_hours',
    '_utcnow',
]