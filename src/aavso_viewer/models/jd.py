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
