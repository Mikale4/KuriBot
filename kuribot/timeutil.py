"""Eastern-time daily reset windows and countdown text."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from typing import Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class EasternFallbackTz(tzinfo):
    """US Eastern fallback with DST when system tzdata is unavailable."""

    _STD = timedelta(hours=-5)
    _DST = timedelta(hours=-4)

    @staticmethod
    def _first_sunday_on_or_after(dt: datetime) -> datetime:
        days_to_go = (6 - dt.weekday()) % 7
        if days_to_go:
            dt += timedelta(days=days_to_go)
        return dt

    def _dst_start(self, year: int) -> datetime:
        # Second Sunday in March at 2:00 local standard time.
        return self._first_sunday_on_or_after(datetime(year, 3, 8, 2, 0, 0))

    def _dst_end(self, year: int) -> datetime:
        # First Sunday in November at 2:00 local daylight time.
        return self._first_sunday_on_or_after(datetime(year, 11, 1, 2, 0, 0))

    def dst(self, dt: Optional[datetime]) -> timedelta:
        if dt is None:
            return timedelta(0)
        naive = dt.replace(tzinfo=None)
        start = self._dst_start(naive.year)
        end = self._dst_end(naive.year)
        return timedelta(hours=1) if start <= naive < end else timedelta(0)

    def utcoffset(self, dt: Optional[datetime]) -> timedelta:
        return self._STD + self.dst(dt)

    def tzname(self, dt: Optional[datetime]) -> str:
        return "EDT" if self.dst(dt) else "EST"


try:
    EASTERN_TZ = ZoneInfo("America/New_York")
except ZoneInfoNotFoundError:
    EASTERN_TZ = EasternFallbackTz()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def current_reset_window_bounds(now_utc: Optional[datetime] = None) -> Tuple[datetime, datetime]:
    now_utc = now_utc or utc_now()
    now_et = now_utc.astimezone(EASTERN_TZ)
    start_et = now_et.replace(hour=0, minute=0, second=0, microsecond=0)
    if now_et.hour >= 12:
        start_et = start_et.replace(hour=12)
    end_et = start_et + timedelta(hours=12)
    return start_et.astimezone(timezone.utc), end_et.astimezone(timezone.utc)


def next_reset_time(now_utc: Optional[datetime] = None) -> datetime:
    _, end_utc = current_reset_window_bounds(now_utc)
    return end_utc


def time_until_next_reset(now_utc: Optional[datetime] = None) -> timedelta:
    now_utc = now_utc or utc_now()
    return max(timedelta(0), next_reset_time(now_utc) - now_utc)


def is_current_reset_window(timestamp: Optional[str], now_utc: Optional[datetime] = None) -> bool:
    if not timestamp:
        return False
    try:
        stamped_at = datetime.fromisoformat(timestamp)
    except Exception:
        return False
    if stamped_at.tzinfo is None:
        stamped_at = stamped_at.replace(tzinfo=timezone.utc)
    window_start_utc, window_end_utc = current_reset_window_bounds(now_utc)
    return window_start_utc <= stamped_at < window_end_utc


def is_same_eastern_calendar_day(timestamp: Optional[str], now_utc: Optional[datetime] = None) -> bool:
    if not timestamp:
        return False
    try:
        stamped_at = datetime.fromisoformat(timestamp)
    except Exception:
        return False
    if stamped_at.tzinfo is None:
        stamped_at = stamped_at.replace(tzinfo=timezone.utc)
    now_utc = now_utc or utc_now()
    return stamped_at.astimezone(EASTERN_TZ).date() == now_utc.astimezone(EASTERN_TZ).date()


def next_eastern_midnight(now_utc: Optional[datetime] = None) -> datetime:
    now_utc = now_utc or utc_now()
    now_et = now_utc.astimezone(EASTERN_TZ)
    next_midnight_et = (now_et + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return next_midnight_et.astimezone(timezone.utc)


def eastern_midnight_countdown_text(now_utc: Optional[datetime] = None) -> str:
    now_utc = now_utc or utc_now()
    remaining = max(timedelta(0), next_eastern_midnight(now_utc) - now_utc)
    total_seconds = int(remaining.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h {minutes}m"
    if minutes > 0:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def human_timedelta(td: timedelta) -> str:
    total = max(0, int(td.total_seconds()))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def reset_countdown_text(now_utc: Optional[datetime] = None) -> str:
    return human_timedelta(time_until_next_reset(now_utc))


def reset_schedule_text(now_utc: Optional[datetime] = None) -> str:
    return f"Refresh in {reset_countdown_text(now_utc)} • 12:00 AM / 12:00 PM ET"


def current_et_date_string(now_utc: Optional[datetime] = None) -> str:
    now_utc = now_utc or utc_now()
    return now_utc.astimezone(EASTERN_TZ).date().isoformat()
