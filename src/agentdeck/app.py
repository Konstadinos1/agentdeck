"""Textual TUI: live agent processes, sessions and token/cost usage."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Label, Static, TabbedContent, TabPane

from agentdeck import __version__
from agentdeck.models import AgentProcess, SystemStats
from agentdeck.pricing import DEFAULT_PRICING_PATH, PriceBook, load_price_book
from agentdeck.processes import (
    AgentProcessScanner,
    ProcessActionError,
    read_system_stats,
    restart_agent_process,
    terminate_process_tree,
)
from agentdeck.sessions import DEFAULT_LOOKBACK_DAYS, SessionCollection, SessionCollector, summarize_usage_by_agent
from agentdeck.views import (
    PROCESS_COLUMNS,
    SESSION_COLUMNS,
    SORT_KEYS,
    USAGE_COLUMNS,
    process_detail_markup,
    process_row,
    session_row,
    sort_processes,
    system_status_markup,
    usage_row,
)

DEFAULT_PROCESS_REFRESH_S = 2.0
DEFAULT_SESSION_REFRESH_S = 60.0
LOG_PATH = Path.home() / ".local" / "state" / "agentdeck" / "agentdeck.log"
ALL_AGENTS = "all"

logger = logging.getLogger(__name__)


class ConfirmScreen(ModalScreen[bool]):
    """Yes/no gate in front of every destructive action."""

    BINDINGS = [Binding("y", "answer(True)", "Yes"), Binding("n,escape", "answer(False)", "No")]

    def __init__(self, question: str) -> None:
        super().__init__()
        self._question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Label(self._question, id="confirm-question")
            with Horizontal(id="confirm-buttons"):
                yield Button("Yes (y)", variant="error", id="yes")
                yield Button("No (n)", variant="primary", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_answer(self, answer: bool) -> None:
        self.dismiss(answer)


class AgentDeckApp(App[None]):
    TITLE = "agentdeck"
    SUB_TITLE = "AI agent control center"
    CSS_PATH = "agentdeck.tcss"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("k", "kill_selected(False)", "Kill"),
        Binding("K", "kill_selected(True)", "Force kill"),
        Binding("r", "restart_selected", "Restart"),
        Binding("f", "cycle_filter", "Filter agent"),
        Binding("s", "cycle_sort", "Sort"),
        Binding("u", "refresh_sessions", "Reload sessions"),
        Binding("1", "show_tab('processes')", "Processes", show=False),
        Binding("2", "show_tab('sessions')", "Sessions", show=False),
        Binding("3", "show_tab('usage')", "Usage", show=False),
    ]

    def __init__(
        self,
        scanner: AgentProcessScanner | None = None,
        collector: SessionCollector | None = None,
        prices: PriceBook | None = None,
        process_refresh_s: float = DEFAULT_PROCESS_REFRESH_S,
        session_refresh_s: float = DEFAULT_SESSION_REFRESH_S,
        stats_reader: Callable[[], SystemStats] = read_system_stats,
    ) -> None:
        super().__init__()
        self.scanner = scanner or AgentProcessScanner()
        self.collector = collector or SessionCollector()
        self.prices = prices if prices is not None else load_price_book()
        self.process_refresh_s = process_refresh_s
        self.session_refresh_s = session_refresh_s
        self._read_stats = stats_reader
        self.processes: list[AgentProcess] = []
        self.sessions = SessionCollection(sessions=[])
        self.agent_filter = ALL_AGENTS
        self.sort_key = SORT_KEYS[0]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Scanning…", id="system-bar")
        with TabbedContent(initial="processes"):
            with TabPane("Processes [1]", id="processes"):
                yield DataTable(id="process-table", cursor_type="row", zebra_stripes=True)
                yield Static(process_detail_markup(None), id="process-detail")
            with TabPane("Sessions [2]", id="sessions"):
                yield DataTable(id="session-table", cursor_type="row", zebra_stripes=True)
            with TabPane("Usage [3]", id="usage"):
                yield DataTable(id="usage-table", cursor_type="row", zebra_stripes=True)
                yield Static("", id="usage-note")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#process-table", DataTable).add_columns(*PROCESS_COLUMNS)
        self.query_one("#session-table", DataTable).add_columns(*SESSION_COLUMNS)
        self.query_one("#usage-table", DataTable).add_columns(*USAGE_COLUMNS)
        self.refresh_processes()
        self.refresh_sessions()
        self.set_interval(self.process_refresh_s, self.refresh_processes)
        self.set_interval(self.session_refresh_s, self.refresh_sessions)

    # ---- data refresh (background threads so the UI never blocks on I/O) ----

    @work(thread=True, exclusive=True, group="processes")
    def refresh_processes(self) -> None:
        try:
            stats = self._read_stats()
            processes = self.scanner.scan()
        except Exception as exc:  # noqa: BLE001 - a scan failure must not kill the TUI
            logger.exception("Process scan failed")
            self.call_from_thread(self.notify, f"Process scan failed: {exc}", severity="error")
            return
        self.call_from_thread(self._apply_processes, stats, processes)

    @work(thread=True, exclusive=True, group="sessions")
    def refresh_sessions(self) -> None:
        try:
            collection = self.collector.collect()
        except Exception as exc:  # noqa: BLE001 - keep showing the last good data
            logger.exception("Session collection failed")
            self.call_from_thread(self.notify, f"Session scan failed: {exc}", severity="error")
            return
        self.call_from_thread(self._apply_sessions, collection)

    def _apply_processes(self, stats: SystemStats, processes: list[AgentProcess]) -> None:
        self.processes = processes
        self.query_one("#system-bar", Static).update(system_status_markup(stats, processes))
        self._render_process_table()

    def _apply_sessions(self, collection: SessionCollection) -> None:
        self.sessions = collection
        self._render_session_table()
        self._render_usage_table()
        for agent, error in collection.errors.items():
            self.notify(f"{agent}: {error}", title="Session read error", severity="warning")

    # ---- rendering ----

    def _visible_processes(self) -> list[AgentProcess]:
        matching = [p for p in self.processes if self.agent_filter in (ALL_AGENTS, p.agent)]
        return sort_processes(matching, self.sort_key)

    def _render_process_table(self) -> None:
        table = self.query_one("#process-table", DataTable)
        selected_pid = self._selected_pid()
        table.clear()
        for process in self._visible_processes():
            table.add_row(*process_row(process), key=str(process.pid))
        if selected_pid is not None and str(selected_pid) in table.rows:
            table.move_cursor(row=table.get_row_index(str(selected_pid)))
        self._render_process_detail()
        filter_label = "all agents" if self.agent_filter == ALL_AGENTS else self.agent_filter
        self.sub_title = f"{filter_label} · sort: {self.sort_key} · v{__version__}"

    def _render_process_detail(self) -> None:
        self.query_one("#process-detail", Static).update(process_detail_markup(self._selected_process()))

    def _render_session_table(self) -> None:
        table = self.query_one("#session-table", DataTable)
        table.clear()
        for index, session in enumerate(self.sessions.sessions):
            if self.agent_filter in (ALL_AGENTS, session.agent):
                table.add_row(*session_row(session, self.prices), key=f"{session.agent}:{session.session_id}:{index}")

    def _render_usage_table(self) -> None:
        table = self.query_one("#usage-table", DataTable)
        table.clear()
        for summary in summarize_usage_by_agent(self.sessions.sessions, self.prices):
            table.add_row(*usage_row(summary), key=summary.agent)
        note = (
            f"Window: last {self.collector.lookback_days} days. Hermes/OpenClaw costs are self-reported; "
            f"Claude Code/Codex costs need prices in {DEFAULT_PRICING_PATH} (≥ = some models unpriced)."
        )
        self.query_one("#usage-note", Static).update(f"[dim]{note}[/dim]")

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "process-table":
            self._render_process_detail()

    # ---- selection helpers ----

    def _selected_pid(self) -> int | None:
        table = self.query_one("#process-table", DataTable)
        if table.row_count == 0:
            return None
        try:
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        except Exception:  # noqa: BLE001 - cursor can be stale during a re-render
            return None
        return int(row_key.value) if row_key.value else None

    def _selected_process(self) -> AgentProcess | None:
        pid = self._selected_pid()
        return next((p for p in self.processes if p.pid == pid), None)

    # ---- actions ----

    def action_show_tab(self, tab_id: str) -> None:
        self.query_one(TabbedContent).active = tab_id

    def action_cycle_sort(self) -> None:
        self.sort_key = SORT_KEYS[(SORT_KEYS.index(self.sort_key) + 1) % len(SORT_KEYS)]
        self._render_process_table()

    def action_cycle_filter(self) -> None:
        present = sorted({p.agent for p in self.processes} | {s.agent for s in self.sessions.sessions})
        options = [ALL_AGENTS, *present]
        current = options.index(self.agent_filter) if self.agent_filter in options else 0
        self.agent_filter = options[(current + 1) % len(options)]
        self._render_process_table()
        self._render_session_table()

    def action_refresh_sessions(self) -> None:
        self.notify("Reloading sessions…")
        self.refresh_sessions()

    def _require_selection(self) -> AgentProcess | None:
        process = self._selected_process()
        if process is None:
            self.notify("Select an agent process first (Processes tab).", severity="warning")
        return process

    def action_kill_selected(self, force: bool) -> None:
        process = self._require_selection()
        if process is None:
            return
        verb = "Force-kill" if force else "Stop"
        question = f"{verb} {process.agent} (pid {process.pid}) and its {process.child_count} child processes?"

        def on_answer(confirmed: bool | None) -> None:
            if confirmed:
                self._kill_worker(process, force)

        self.push_screen(ConfirmScreen(question), on_answer)

    def action_restart_selected(self) -> None:
        process = self._require_selection()
        if process is None:
            return
        if not process.is_daemon:
            self.notify(f"{process.agent} is interactive; restart it in its own terminal.", severity="warning")
            return

        def on_answer(confirmed: bool | None) -> None:
            if confirmed:
                self._restart_worker(process)

        self.push_screen(ConfirmScreen(f"Restart {process.agent} (pid {process.pid})?"), on_answer)

    @work(thread=True, group="actions")
    def _kill_worker(self, process: AgentProcess, force: bool) -> None:
        try:
            count = terminate_process_tree(process.pid, force=force)
        except ProcessActionError as exc:
            self.call_from_thread(self.notify, str(exc), severity="error")
            return
        self.call_from_thread(self.notify, f"Stopped {process.agent}: {count} processes")
        self.call_from_thread(self.refresh_processes)

    @work(thread=True, group="actions")
    def _restart_worker(self, process: AgentProcess) -> None:
        try:
            new_pid = restart_agent_process(process)
        except ProcessActionError as exc:
            self.call_from_thread(self.notify, str(exc), severity="error")
            return
        self.call_from_thread(self.notify, f"Restarted {process.agent} as pid {new_pid}")
        self.call_from_thread(self.refresh_processes)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="agentdeck", description=AgentDeckApp.SUB_TITLE)
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS, help="session lookback window in days")
    parser.add_argument("--interval", type=float, default=DEFAULT_PROCESS_REFRESH_S, help="process refresh seconds")
    parser.add_argument("--pricing", type=Path, default=DEFAULT_PRICING_PATH, help="pricing TOML file")
    parser.add_argument("--version", action="version", version=f"agentdeck {__version__}")
    args = parser.parse_args(argv)
    if args.days < 1 or args.interval <= 0:
        parser.error("--days must be >= 1 and --interval must be > 0")
    return args


def _configure_logging() -> None:
    # Log to a file: anything written to stderr would corrupt the TUI.
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    _configure_logging()
    AgentDeckApp(
        collector=SessionCollector(lookback_days=args.days),
        prices=load_price_book(args.pricing),
        process_refresh_s=args.interval,
    ).run()
