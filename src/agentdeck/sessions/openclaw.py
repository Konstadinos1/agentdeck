"""OpenClaw sessions: ~/.openclaw/agents/<agent>/sessions/*.jsonl (usage + cost per message)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from agentdeck.models import AgentSession, TokenUsage
from agentdeck.sessions.common import (
    FileSummaryCache,
    JsonRecord,
    as_int,
    earliest,
    iter_jsonl_records,
    latest,
    parse_iso_timestamp,
    recently_modified_files,
)

AGENT_NAME = "OpenClaw"
TITLE_MAX_CHARS = 80
LINE_MARKERS = ('"session"', '"model_change"', '"usage"', '"user"')


@dataclass
class OpenClawFileSummary:
    session_id: str
    title: str = ""
    model: str = ""
    cwd: str = ""
    first_at: datetime | None = None
    last_at: datetime | None = None
    usage: TokenUsage = field(default_factory=TokenUsage)
    cost_usd: float = 0.0


def _message_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


def _apply_message(summary: OpenClawFileSummary, message: JsonRecord) -> None:
    if message.get("role") == "user" and not summary.title:
        summary.title = " ".join(_message_text(message.get("content")).split())[:TITLE_MAX_CHARS]
    if message.get("model"):
        summary.model = message["model"]
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return
    summary.usage.add(
        TokenUsage(
            input_tokens=as_int(usage.get("input")),
            output_tokens=as_int(usage.get("output")),
            cache_read_tokens=as_int(usage.get("cacheRead")),
            cache_write_tokens=as_int(usage.get("cacheWrite")),
        )
    )
    cost = usage.get("cost")
    if isinstance(cost, dict) and isinstance(cost.get("total"), (int, float)):
        summary.cost_usd += float(cost["total"])


def parse_openclaw_session_file(path: Path) -> OpenClawFileSummary:
    summary = OpenClawFileSummary(session_id=path.stem)
    for record in iter_jsonl_records(path, LINE_MARKERS):
        timestamp = parse_iso_timestamp(record.get("timestamp"))
        summary.first_at = earliest(summary.first_at, timestamp)
        summary.last_at = latest(summary.last_at, timestamp)
        record_type = record.get("type")
        if record_type == "session":
            summary.session_id = record.get("id") or summary.session_id
            summary.cwd = record.get("cwd") or summary.cwd
        elif record_type == "model_change" and record.get("modelId"):
            summary.model = record["modelId"]
        elif record_type == "message" and isinstance(record.get("message"), dict):
            _apply_message(summary, record["message"])
    return summary


def read_openclaw_sessions(
    agents_dir: Path, since: datetime, cache: FileSummaryCache[OpenClawFileSummary]
) -> list[AgentSession]:
    sessions: list[AgentSession] = []
    for path in recently_modified_files(agents_dir, "*.jsonl", since):
        if path.parent.name != "sessions":
            continue
        part = cache.get(path, parse_openclaw_session_file)
        sessions.append(
            AgentSession(
                agent=AGENT_NAME,
                session_id=part.session_id,
                title=part.title,
                model=part.model,
                cwd=part.cwd,
                started_at=part.first_at,
                last_active_at=part.last_at,
                usage=part.usage,
                reported_cost_usd=part.cost_usd,
            )
        )
    return sessions
