"""Hermes sessions: ~/.hermes/state.db (SQLite, `sessions` table with token + cost columns)."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from agentdeck.models import AgentSession, TokenUsage
from agentdeck.sessions.common import as_int, latest, parse_epoch_seconds

logger = logging.getLogger(__name__)

AGENT_NAME = "Hermes"

# SELECT * keeps us tolerant of Hermes schema migrations adding/removing columns.
RECENT_SESSIONS_SQL = """
SELECT * FROM sessions
WHERE COALESCE(last_activity_at, ended_at, started_at) >= ?
ORDER BY COALESCE(last_activity_at, ended_at, started_at) DESC
"""


def _column(row: sqlite3.Row, name: str) -> object:
    return row[name] if name in row.keys() else None


def _reported_cost(row: sqlite3.Row) -> float | None:
    # An invoiced (actual) cost is more trustworthy than Hermes' own estimate.
    for column in ("actual_cost_usd", "estimated_cost_usd"):
        value = _column(row, column)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _session_from_row(row: sqlite3.Row) -> AgentSession:
    started = parse_epoch_seconds(_column(row, "started_at"))
    return AgentSession(
        agent=AGENT_NAME,
        session_id=str(row["id"]),
        title=str(_column(row, "title") or _column(row, "display_name") or _column(row, "source") or ""),
        model=str(_column(row, "model") or ""),
        cwd=str(_column(row, "cwd") or ""),
        started_at=started,
        last_active_at=latest(
            started,
            parse_epoch_seconds(_column(row, "ended_at")),
            parse_epoch_seconds(_column(row, "last_activity_at")),
        ),
        usage=TokenUsage(
            input_tokens=as_int(_column(row, "input_tokens")),
            output_tokens=as_int(_column(row, "output_tokens")),
            cache_read_tokens=as_int(_column(row, "cache_read_tokens")),
            cache_write_tokens=as_int(_column(row, "cache_write_tokens")),
        ),
        reported_cost_usd=_reported_cost(row),
    )


def read_hermes_sessions(state_db: Path, since: datetime) -> list[AgentSession]:
    if not state_db.is_file():
        return []
    # Read-only URI: the Hermes gateway owns this DB and may be writing to it.
    uri = f"file:{state_db}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=2.0)
    try:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(RECENT_SESSIONS_SQL, (since.timestamp(),)).fetchall()
    finally:
        connection.close()
    return [
        _session_from_row(row)
        for row in rows
        if not ("hidden" in row.keys() and row["hidden"])
    ]
