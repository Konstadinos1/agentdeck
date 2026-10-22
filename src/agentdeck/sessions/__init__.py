"""Collect sessions from every supported agent into one normalised list."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentdeck.models import AgentSession, TokenUsage
from agentdeck.pricing import PriceBook
from agentdeck.sessions.claude import ClaudeFileSummary, read_claude_sessions
from agentdeck.sessions.codex import CodexFileSummary, read_codex_sessions
from agentdeck.sessions.common import FileSummaryCache
from agentdeck.sessions.hermes import read_hermes_sessions
from agentdeck.sessions.openclaw import OpenClawFileSummary, read_openclaw_sessions

logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 7
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


@dataclass
class SessionCollection:
    sessions: list[AgentSession]
    # Per-agent read failures are surfaced in the UI instead of hiding the agent.
    errors: dict[str, str] = field(default_factory=dict)


@dataclass
class AgentUsageSummary:
    agent: str
    session_count: int = 0
    today: TokenUsage = field(default_factory=TokenUsage)
    window: TokenUsage = field(default_factory=TokenUsage)
    today_cost_usd: float = 0.0
    window_cost_usd: float = 0.0
    # True when at least one session had no price, so the cost is a lower bound.
    cost_incomplete: bool = False


class SessionCollector:
    def __init__(self, home: Path | None = None, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> None:
        self.home = home or Path.home()
        self.lookback_days = lookback_days
        self._claude_cache: FileSummaryCache[ClaudeFileSummary] = FileSummaryCache()
        self._codex_cache: FileSummaryCache[CodexFileSummary] = FileSummaryCache()
        self._openclaw_cache: FileSummaryCache[OpenClawFileSummary] = FileSummaryCache()

    def _readers(self, since: datetime) -> dict[str, Callable[[], list[AgentSession]]]:
        home = self.home
        return {
            "Claude Code": lambda: read_claude_sessions(home / ".claude" / "projects", since, self._claude_cache),
            "Codex": lambda: read_codex_sessions(home / ".codex", since, self._codex_cache),
            "Hermes": lambda: read_hermes_sessions(home / ".hermes" / "state.db", since),
            "OpenClaw": lambda: read_openclaw_sessions(home / ".openclaw" / "agents", since, self._openclaw_cache),
        }

    def collect(self, now: datetime | None = None) -> SessionCollection:
        since = (now or datetime.now(timezone.utc)) - timedelta(days=self.lookback_days)
        collection = SessionCollection(sessions=[])
        for agent, reader in self._readers(since).items():
            try:
                collection.sessions.extend(reader())
            except (OSError, sqlite3.Error, ValueError) as exc:
                logger.warning("Failed reading %s sessions: %s", agent, exc)
                collection.errors[agent] = str(exc)
        collection.sessions = [s for s in collection.sessions if (s.last_active_at or _EPOCH) >= since]
        collection.sessions.sort(key=lambda s: s.last_active_at or _EPOCH, reverse=True)
        return collection


def _start_of_local_day(now: datetime) -> datetime:
    local_now = now.astimezone()
    return local_now.replace(hour=0, minute=0, second=0, microsecond=0)


def summarize_usage_by_agent(
    sessions: list[AgentSession], prices: PriceBook, now: datetime | None = None
) -> list[AgentUsageSummary]:
    """Aggregate by agent. A session counts toward 'today' if it was active today (local time)."""
    day_start = _start_of_local_day(now or datetime.now(timezone.utc))
    summaries: dict[str, AgentUsageSummary] = {}
    for session in sessions:
        summary = summaries.setdefault(session.agent, AgentUsageSummary(agent=session.agent))
        cost = prices.session_cost_usd(session)
        summary.session_count += 1
        summary.window.add(session.usage)
        summary.cost_incomplete |= cost is None and session.usage.total_tokens > 0
        summary.window_cost_usd += cost or 0.0
        if session.last_active_at and session.last_active_at >= day_start:
            summary.today.add(session.usage)
            summary.today_cost_usd += cost or 0.0
    return sorted(summaries.values(), key=lambda s: s.window.total_tokens, reverse=True)
