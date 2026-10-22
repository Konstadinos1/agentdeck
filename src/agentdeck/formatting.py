"""Human-friendly rendering of bytes, token counts, money and time."""

from __future__ import annotations

from datetime import datetime, timezone

BYTES_PER_KIB = 1024
SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600
SECONDS_PER_DAY = 86_400
BAR_WIDTH = 20
# Tuned for a ~7 GiB WSL VM, where >85% RAM means swapping is imminent.
WARNING_USAGE_PERCENT = 65.0
CRITICAL_USAGE_PERCENT = 85.0
MISSING_VALUE = "—"
# Quebec convention for displayed dates (ISO stays internal).
DISPLAY_DATETIME_FORMAT = "%d/%m/%Y %H:%M"


def format_bytes(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < BYTES_PER_KIB:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= BYTES_PER_KIB
    return f"{value:.1f} TiB"


def format_tokens(count: int) -> str:
    if count >= 1_000_000_000:
        return f"{count / 1_000_000_000:.2f}B"
    if count >= 1_000_000:
        return f"{count / 1_000_000:.2f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


def format_usd(amount: float | None, incomplete: bool = False) -> str:
    if amount is None:
        return MISSING_VALUE
    rendered = f"${amount:,.2f}" if amount >= 0.01 or amount == 0 else f"${amount:.4f}"
    return f"≥{rendered}" if incomplete else rendered


def format_duration(seconds: float) -> str:
    seconds = max(int(seconds), 0)
    if seconds < SECONDS_PER_MINUTE:
        return f"{seconds}s"
    if seconds < SECONDS_PER_HOUR:
        return f"{seconds // SECONDS_PER_MINUTE}m"
    if seconds < SECONDS_PER_DAY:
        hours, remainder = divmod(seconds, SECONDS_PER_HOUR)
        return f"{hours}h{remainder // SECONDS_PER_MINUTE:02d}m"
    days, remainder = divmod(seconds, SECONDS_PER_DAY)
    return f"{days}d{remainder // SECONDS_PER_HOUR}h"


def format_relative_time(moment: datetime | None, now: datetime | None = None) -> str:
    if moment is None:
        return MISSING_VALUE
    reference = now or datetime.now(timezone.utc)
    return f"{format_duration((reference - moment).total_seconds())} ago"


def format_display_datetime(moment: datetime | None) -> str:
    return moment.astimezone().strftime(DISPLAY_DATETIME_FORMAT) if moment else MISSING_VALUE


def usage_bar(percent: float, width: int = BAR_WIDTH) -> str:
    clamped = min(max(percent, 0.0), 100.0)
    filled = round(width * clamped / 100.0)
    return "█" * filled + "░" * (width - filled)


def usage_color(percent: float) -> str:
    if percent >= CRITICAL_USAGE_PERCENT:
        return "red"
    if percent >= WARNING_USAGE_PERCENT:
        return "yellow"
    return "green"
