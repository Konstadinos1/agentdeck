from datetime import datetime, timedelta, timezone

from rich.text import Text

from agentdeck.models import AgentProcess, AgentSession, SystemStats, TokenUsage
from agentdeck.pricing import PriceBook
from agentdeck.views import (
    PROCESS_COLUMNS,
    SESSION_COLUMNS,
    UNTITLED,
    process_detail_markup,
    process_row,
    session_row,
    sort_processes,
    system_status_markup,
)

GIB = 1024**3
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def _proc(agent: str, pid: int, cpu: float, rss: int, tty: bool = False, cmd: tuple[str, ...] = ("node", "x")) -> AgentProcess:
    return AgentProcess(agent, pid, "node", cmd, "/home/costa", NOW - timedelta(hours=2), cpu, rss, 3, tty, "sleeping")


def test_system_status_shows_agent_share_of_ram() -> None:
    stats = SystemStats(cpu_percent=10, ram_used_bytes=5 * GIB, ram_total_bytes=8 * GIB, swap_used_bytes=0, swap_total_bytes=0)
    markup = system_status_markup(stats, [_proc("Codex", 1, 5, 2 * GIB)])
    assert "1 running · 4 processes" in markup
    assert "2.0 GiB RAM (25% of total)" in markup


def test_sort_processes_by_each_key() -> None:
    procs = [_proc("B", 1, cpu=50, rss=1), _proc("A", 2, cpu=1, rss=9), _proc("A", 3, cpu=9, rss=20)]
    assert [p.pid for p in sort_processes(procs, "ram")] == [3, 2, 1]
    assert [p.pid for p in sort_processes(procs, "cpu")] == [1, 3, 2]
    assert [p.pid for p in sort_processes(procs, "agent")] == [3, 2, 1]


def test_process_row_matches_columns_and_keeps_brackets_literal() -> None:
    row = process_row(_proc("Hermes", 7, 1.5, GIB, cmd=("python", "[bold]evil[/bold]")), now=NOW)
    assert len(row) == len(PROCESS_COLUMNS)
    assert row[5] == "2h00m"
    assert row[6] == "daemon"
    assert isinstance(row[7], Text) and "[bold]" in row[7].plain


def test_process_detail_escapes_markup_and_explains_mode() -> None:
    detail = process_detail_markup(_proc("Claude Code", 9, 0, 0, tty=True, cmd=("claude", "[x]")))
    assert "interactive TTY" in detail
    assert "\\[x]" in detail
    assert "No agent process" in process_detail_markup(None)


def test_session_row_handles_untitled_unpriced_session() -> None:
    session = AgentSession("Codex", "id", "", "", "", None, NOW - timedelta(minutes=3), TokenUsage(input_tokens=1500))
    row = session_row(session, PriceBook(), now=NOW)
    assert len(row) == len(SESSION_COLUMNS)
    assert row[1].plain == UNTITLED
    assert row[7].plain == "—"
    assert row[8] == "3m ago"
