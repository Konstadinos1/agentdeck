"""Plain data containers shared by the scanners and the UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class AgentProcess:
    """A root agent process plus the aggregated cost of its whole process tree.

    Aggregating the tree matters because agents spawn MCP servers, shells and
    language servers that often use more RAM than the agent itself.
    """

    agent: str
    pid: int
    name: str
    cmdline: tuple[str, ...]
    cwd: str
    started_at: datetime
    cpu_percent: float
    rss_bytes: int
    child_count: int
    has_terminal: bool
    status: str

    @property
    def is_daemon(self) -> bool:
        """Daemons (no controlling TTY) are the only processes safe to relaunch headless."""
        return not self.has_terminal


@dataclass(frozen=True)
class SystemStats:
    cpu_percent: float
    ram_used_bytes: int
    ram_total_bytes: int
    swap_used_bytes: int
    swap_total_bytes: int

    @property
    def ram_percent(self) -> float:
        if self.ram_total_bytes == 0:
            return 0.0
        return 100.0 * self.ram_used_bytes / self.ram_total_bytes


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )

    def add(self, other: TokenUsage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens


@dataclass
class AgentSession:
    """One conversation/thread of an agent, normalised across agent formats."""

    agent: str
    session_id: str
    title: str
    model: str
    cwd: str
    started_at: datetime | None
    last_active_at: datetime | None
    usage: TokenUsage = field(default_factory=TokenUsage)
    # Cost reported by the agent itself (Hermes, OpenClaw) beats our estimate.
    reported_cost_usd: float | None = None
