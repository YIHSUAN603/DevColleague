from __future__ import annotations

from datetime import date, timedelta

import pytest

from devcolleague.services.summary import resolve_range


def test_resolve_ranges() -> None:
    since, until = resolve_range("yesterday")
    assert until - since == timedelta(days=1)
    assert since.hour == 0 and since.tzinfo is not None

    since, _ = resolve_range("this_week")
    assert since.weekday() == 0

    since, until = resolve_range("custom", date(2026, 11, 2), date(2026, 11, 6))
    assert since.date() == date(2026, 11, 2)
    assert until.date() == date(2026, 11, 7)  # 結束日當天包含在內

    with pytest.raises(ValueError):
        resolve_range("custom", date(2026, 11, 6), date(2026, 11, 2))
    with pytest.raises(ValueError):
        resolve_range("custom")
