from datetime import datetime, timedelta, timezone

import pytest

from agentdeck.formatting import (
    MISSING_VALUE,
    format_bytes,
    format_display_datetime,
    format_duration,
    format_relative_time,
    format_tokens,
    format_usd,
    usage_bar,
    usage_color,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, "0 B"), (1023, "1023 B"), (1024, "1.0 KiB"), (5 * 1024**3, "5.0 GiB"), (2 * 1024**4, "2.0 TiB")],
)
def test_format_bytes_boundaries(value: int, expected: str) -> None:
    assert format_bytes(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, "0"), (999, "999"), (1_000, "1.0k"), (2_500_000, "2.50M"), (3_000_000_000, "3.00B")],
)
def test_format_tokens_boundaries(value: int, expected: str) -> None:
    assert format_tokens(value) == expected


def test_format_usd_handles_missing_tiny_and_incomplete_amounts() -> None:
    assert format_usd(None) == MISSING_VALUE
    assert format_usd(0) == "$0.00"
    assert format_usd(0.0035) == "$0.0035"
    assert format_usd(1234.5) == "$1,234.50"
    assert format_usd(2.0, incomplete=True) == "≥$2.00"


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(-5, "0s"), (59, "59s"), (60, "1m"), (3_661, "1h01m"), (90_000, "1d1h")],
)
def test_format_duration(seconds: float, expected: str) -> None:
    assert format_duration(seconds) == expected


def test_format_relative_time() -> None:
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    assert format_relative_time(now - timedelta(minutes=5), now) == "5m ago"
    assert format_relative_time(None, now) == MISSING_VALUE


def test_display_datetime_uses_quebec_day_month_year_order() -> None:
    moment = datetime(2026, 3, 7, 12, 0, tzinfo=timezone.utc)
    rendered = format_display_datetime(moment)
    assert rendered.startswith(moment.astimezone().strftime("%d/%m/%Y"))
    assert format_display_datetime(None) == MISSING_VALUE


def test_usage_bar_clamps_out_of_range_percentages() -> None:
    assert usage_bar(-10, width=4) == "░░░░"
    assert usage_bar(50, width=4) == "██░░"
    assert usage_bar(150, width=4) == "████"


def test_usage_color_thresholds() -> None:
    assert usage_color(10) == "green"
    assert usage_color(65) == "yellow"
    assert usage_color(85) == "red"
