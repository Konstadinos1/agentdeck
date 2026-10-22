"""Pure functions turning domain objects into table rows and status text.

Kept separate from the Textual app so rendering can be unit-tested without a terminal.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from rich.markup import escape
from rich.text import Text

from agentdeck.formatting import (
    format_bytes,
    format_duration,
    format_relative_time,
    format_tokens,
    format_usd,
    usage_bar,
    usage_color,
)
from agentdeck.models import AgentProcess, AgentSession, SystemStats
from agentdeck.pricing import PriceBook
from agentdeck.sessions import AgentUsageSummary

TITLE_MAX_CHARS = 48
COMMAND_MAX_CHARS = 60
UNTITLED = "(untitled)"

PROCESS_COLUMNS = ("Agent", "PID", "CPU %", "RAM", "Children", "Uptime", "Mode", "Command")
SESSION_COLUMNS = ("Agent", "Title", "Model", "Tokens", "Input", "Output", "Cache R/W", "Cost", "Last active")
USAGE_COLUMNS = ("Agent", "Sessions", "Tokens today", "Cost today", "Tokens (window)", "Cost (window)")

SORT_KEYS = ("ram", "cpu", "agent")


# User-derived strings are wrapped in Text so DataTable never parses "[...]" in a
# command line or title as Rich markup.
def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _meter(label: str, percent: float, detail: str) -> str:
    color = usage_color(percent)
    return f"[b]{label}[/b] [{color}]{usage_bar(percent)}[/] {percent:5.1f}% {detail}"


def system_status_markup(stats: SystemStats, processes: Sequence[AgentProcess]) -> str:
    swap_percent = 100.0 * stats.swap_used_bytes / stats.swap_total_bytes if stats.swap_total_bytes else 0.0
    agent_ram = sum(p.rss_bytes for p in processes)
    agent_share = 100.0 * agent_ram / stats.ram_total_bytes if stats.ram_total_bytes else 0.0
    process_count = sum(1 + p.child_count for p in processes)
    meters = "   ".join(
        (
            _meter("CPU", stats.cpu_percent, ""),
            _meter("RAM", stats.ram_percent, f"{format_bytes(stats.ram_used_bytes)}/{format_bytes(stats.ram_total_bytes)}"),
            _meter("Swap", swap_percent, format_bytes(stats.swap_used_bytes)),
        )
    )
    agents_line = (
        f"[b]Agents[/b] {len(processes)} running · {process_count} processes · "
        f"[{usage_color(agent_share)}]{format_bytes(agent_ram)} RAM ({agent_share:.0f}% of total)[/] · "
        f"CPU {sum(p.cpu_percent for p in processes):.0f}%"
    )
    return f"{meters}\n{agents_line}"


def sort_processes(processes: Sequence[AgentProcess], sort_key: str) -> list[AgentProcess]:
    if sort_key == "cpu":
        return sorted(processes, key=lambda p: p.cpu_percent, reverse=True)
    if sort_key == "agent":
        return sorted(processes, key=lambda p: (p.agent, -p.rss_bytes))
    return sorted(processes, key=lambda p: p.rss_bytes, reverse=True)


def process_row(process: AgentProcess, now: datetime | None = None) -> tuple[object, ...]:
    reference = now or datetime.now(timezone.utc)
    return (
        Text(process.agent, style="bold"),
        str(process.pid),
        Text(f"{process.cpu_percent:.1f}", justify="right"),
        Text(format_bytes(process.rss_bytes), justify="right"),
        Text(str(process.child_count), justify="right"),
        format_duration((reference - process.started_at).total_seconds()),
        "daemon" if process.is_daemon else "tty",
        Text(_truncate(" ".join(process.cmdline), COMMAND_MAX_CHARS)),
    )


def process_detail_markup(process: AgentProcess | None) -> str:
    if process is None:
        return "[dim]No agent process selected.[/dim]"
    mode = "daemon (restartable with [b]r[/b])" if process.is_daemon else "interactive TTY (kill only)"
    return (
        f"[b]{escape(process.agent)}[/b] · pid {process.pid} · {escape(process.name)} · "
        f"{escape(process.status)} · {mode}\n"
        f"[dim]cwd[/dim] {escape(process.cwd or '?')}\n"
        f"[dim]cmd[/dim] {escape(' '.join(process.cmdline))}"
    )


def session_row(session: AgentSession, prices: PriceBook, now: datetime | None = None) -> tuple[object, ...]:
    usage = session.usage
    cache = f"{format_tokens(usage.cache_read_tokens)}/{format_tokens(usage.cache_write_tokens)}"
    return (
        Text(session.agent, style="bold"),
        Text(_truncate(session.title or UNTITLED, TITLE_MAX_CHARS)),
        Text(session.model or "?"),
        Text(format_tokens(usage.total_tokens), justify="right"),
        Text(format_tokens(usage.input_tokens), justify="right"),
        Text(format_tokens(usage.output_tokens), justify="right"),
        Text(cache, justify="right"),
        Text(format_usd(prices.session_cost_usd(session)), justify="right"),
        format_relative_time(session.last_active_at, now),
    )


def usage_row(summary: AgentUsageSummary) -> tuple[object, ...]:
    return (
        Text(summary.agent, style="bold"),
        Text(str(summary.session_count), justify="right"),
        Text(format_tokens(summary.today.total_tokens), justify="right"),
        Text(format_usd(summary.today_cost_usd, summary.cost_incomplete), justify="right"),
        Text(format_tokens(summary.window.total_tokens), justify="right"),
        Text(format_usd(summary.window_cost_usd, summary.cost_incomplete), justify="right"),
    )
