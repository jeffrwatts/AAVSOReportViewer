"""Minimal Julian Date -> Gregorian calendar date conversion (no astropy dependency)."""

from __future__ import annotations

import datetime


def jd_to_date(jd: float) -> datetime.date:
    """Convert a Julian Date to a UTC calendar date (standard Fliegel & Van Flandern algorithm)."""
    jd_int = int(jd + 0.5)
    a = jd_int + 32044
    b = (4 * a + 3) // 146097
    c = a - (146097 * b) // 4
    d = (4 * c + 3) // 1461
    e = c - (1461 * d) // 4
    m = (5 * e + 2) // 153
    day = e - (153 * m + 2) // 5 + 1
    month = m + 3 - 12 * (m // 10)
    year = 100 * b + d - 4800 + m // 10
    return datetime.date(year, month, day)


def jd_to_datetime(jd: float) -> datetime.datetime:
    """Convert a Julian Date to a timezone-aware UTC datetime (date and time-of-day).

    JD increments from noon UTC, so jd + 0.5 shifts the epoch to midnight -- the same
    shift jd_to_date uses for the calendar day; the fractional part left over here is
    the time-of-day since that midnight.
    """
    jd_shifted = jd + 0.5
    frac_day = jd_shifted - int(jd_shifted)
    midnight = datetime.datetime.combine(jd_to_date(jd), datetime.time(), tzinfo=datetime.timezone.utc)
    return midnight + datetime.timedelta(seconds=round(frac_day * 86400))
