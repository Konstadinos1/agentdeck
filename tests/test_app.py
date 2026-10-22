"""Headless TUI tests driven through Textual's Pilot with fake data sources."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from textual.widgets import DataTable, TabbedContent

from agentdeck.app import ALL_AGENTS, AgentDeckApp, ConfirmScreen, _parse_args
from agentdeck.models import AgentProcess, AgentSession, SystemStats, TokenUsage
from agentdeck.pricing import PriceBook
from agentdeck.sessions import SessionCollection

GIB = 1024**3
NOW = datetime.now(timezone.utc)


def _proc(agent: str, pid: int, tty: bool) -> AgentProcess:
    return AgentProcess(agent, pid, "node", ("node", agent), "/tmp", NOW - timedelta(minutes=1), 1.0, GIB, 2, tty, "sleeping")


class FakeScanner:
    def __init__(self, processes: list[AgentProcess]) -> None:
        self.processes = processes

    def scan(self) -> list[AgentProcess]:
        return list(self.processes)


class FakeCollector:
    lookback_days = 7

    def collect(self) -> SessionCollection:
        session = AgentSession("Codex", "t1", "Audit repos", "gpt-6", "/home/costa", NOW, NOW, TokenUsage(input_tokens=42))
        return SessionCollection(sessions=[session], errors={"Hermes": "database is locked"})


def _app(processes: list[AgentProcess]) -> AgentDeckApp:
    stats = SystemStats(cpu_percent=5, ram_used_bytes=4 * GIB, ram_total_bytes=8 * GIB, swap_used_bytes=0, swap_total_bytes=GIB)
    return AgentDeckApp(
        scanner=FakeScanner(processes),  # type: ignore[arg-type]
        collector=FakeCollector(),  # type: ignore[arg-type]
        prices=PriceBook(),
        process_refresh_s=60,
        session_refresh_s=60,
        stats_reader=lambda: stats,
    )


async def _settle(app: AgentDeckApp, pilot) -> None:
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_tables_are_populated_from_scanner_and_collector() -> None:
    app = _app([_proc("Codex", 101, tty=False), _proc("Hermes", 202, tty=False)])
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        assert app.query_one("#process-table", DataTable).row_count == 2
        assert app.query_one("#session-table", DataTable).row_count == 1
        assert app.query_one("#usage-table", DataTable).row_count == 1


async def test_filter_cycles_through_present_agents() -> None:
    app = _app([_proc("Codex", 101, tty=False), _proc("Hermes", 202, tty=False)])
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        await pilot.press("f")
        assert app.agent_filter == "Codex"
        assert app.query_one("#process-table", DataTable).row_count == 1
        await pilot.press("f", "f")
        assert app.agent_filter == ALL_AGENTS


async def test_number_keys_switch_tabs() -> None:
    app = _app([])
    async with app.run_test() as pilot:
        await pilot.press("3")
        assert app.query_one(TabbedContent).active == "usage"
        await pilot.press("2")
        assert app.query_one(TabbedContent).active == "sessions"


async def test_kill_asks_for_confirmation_and_cancel_keeps_process(monkeypatch: pytest.MonkeyPatch) -> None:
    killed: list[int] = []
    monkeypatch.setattr("agentdeck.app.terminate_process_tree", lambda pid, force=False: killed.append(pid) or 1)
    app = _app([_proc("Codex", 101, tty=False)])
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        await pilot.press("k")
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("n")
        await _settle(app, pilot)
        assert killed == []


async def test_confirmed_kill_terminates_selected_process(monkeypatch: pytest.MonkeyPatch) -> None:
    killed: list[tuple[int, bool]] = []
    monkeypatch.setattr("agentdeck.app.terminate_process_tree", lambda pid, force=False: killed.append((pid, force)) or 3)
    app = _app([_proc("Codex", 101, tty=False)])
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        await pilot.press("K", "y")
        await _settle(app, pilot)
        assert killed == [(101, True)]


async def test_restart_of_interactive_process_is_refused_without_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    restarted: list[int] = []
    monkeypatch.setattr("agentdeck.app.restart_agent_process", lambda p: restarted.append(p.pid) or 999)
    app = _app([_proc("Claude Code", 101, tty=True)])
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        await pilot.press("r")
        assert not isinstance(app.screen, ConfirmScreen)
        assert restarted == []


async def test_confirmed_restart_of_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    restarted: list[int] = []
    monkeypatch.setattr("agentdeck.app.restart_agent_process", lambda p: restarted.append(p.pid) or 999)
    app = _app([_proc("Hermes", 202, tty=False)])
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        await pilot.press("r", "y")
        await _settle(app, pilot)
        assert restarted == [202]


def test_cli_arguments_are_validated() -> None:
    assert _parse_args(["--days", "3"]).days == 3
    with pytest.raises(SystemExit):
        _parse_args(["--days", "0"])
    with pytest.raises(SystemExit):
        _parse_args(["--interval", "-1"])
