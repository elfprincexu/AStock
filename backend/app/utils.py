"""Shared utility helpers."""

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

# A valid A-share industry name must contain at least one CJK character.
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def is_valid_industry(value: str | None) -> bool:
    """Return True if *value* looks like a real A-share industry name.

    Invalid examples: None, '', '--', '-', 'N/A', pure ASCII symbols.
    Valid examples:   '酿酒行业', '半导体', '银行'.
    """
    if not value or not value.strip():
        return False
    return bool(_CJK_RE.search(value))


def latest_expected_trading_date() -> date:
    """Return the most recent date on which A-share kline data should exist.

    Rules (Asia/Shanghai time):
    - Weekend → last Friday
    - Weekday before 16:00 → previous trading day
    - Weekday 16:00 or later → today (market closed, data available)

    This does NOT account for Chinese public holidays; those are rare enough
    that a redundant fetch on those days is acceptable.
    """
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    today = now.date()
    wd = today.weekday()  # 0=Mon … 6=Sun

    if wd == 5:  # Saturday
        return today - timedelta(days=1)
    if wd == 6:  # Sunday
        return today - timedelta(days=2)

    # Weekday
    if now.hour < 16:
        # Data for today not yet available; expect yesterday's
        if wd == 0:  # Monday → last Friday
            return today - timedelta(days=3)
        return today - timedelta(days=1)

    return today
