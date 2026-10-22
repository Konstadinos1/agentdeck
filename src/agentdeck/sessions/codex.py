"""Codex sessions: ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl + session_index.jsonl."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from agentdeck.models import AgentSession, TokenUsage
from agentdeck.sessions.common import (
    FileSummaryCache,
    JsonRecord,
    as_int,
    iter_jsonl_records,
    latest,
    parse_iso_timestamp,
    recently_modified_files,
)

AGENT_NAME = "Codex"
LINE_MARKERS = ('"session_meta"', '"token_count"', '"thread_settings_applied"', '"turn_context"')


@dataclass
class CodexFileSummary:
    session_id: str
    model: str = ""
    cwd: str = ""
    started_at: datetime | None = None
    last_at: datetime | None = None
    usage: TokenUsage = field(default_factory=TokenUsage)


def _usage_from_totals(totals: JsonRecord) -> TokenUsage:
    # OpenAI reports cached tokens as a subset of input tokens; split them so
    # cached reads can be priced at the (cheaper) cache rate.
    cached = as_int(totals.get("cached_input_tokens"))
    return TokenUsage(
        input_tokens=max(as_int(totals.get("input_tokens")) - cached, 0),
        output_tokens=as_int(totals.get("output_tokens")),
        cache_read_tokens=cached,
        cache_write_tokens=as_int(totals.get("cache_write_input_tokens")),
    )


def _apply_record(summary: CodexFileSummary, record: JsonRecord) -> None:
    payload = record.get("payload") or {}
    record_type, payload_type = record.get("type"), payload.get("type")
    summary.last_at = latest(summary.last_at, parse_iso_timestamp(record.get("timestamp")))
    if record_type == "session_meta":
        summary.session_id = payload.get("id") or summary.session_id
        summary.cwd = payload.get("cwd") or summary.cwd
        summary.started_at = parse_iso_timestamp(payload.get("timestamp")) or summary.started_at
    elif record_type == "turn_context" and payload.get("model"):
        summary.model = payload["model"]
    elif payload_type == "thread_settings_applied":
        summary.model = (payload.get("thread_settings") or {}).get("model") or summary.model
    elif payload_type == "token_count":
        totals = (payload.get("info") or {}).get("total_token_usage")
        if totals:
            summary.usage = _usage_from_totals(totals)


def parse_codex_rollout_file(path: Path) -> CodexFileSummary:
    summary = CodexFileSummary(session_id=path.stem)
    for record in iter_jsonl_records(path, LINE_MARKERS):
        _apply_record(summary, record)
    return summary


def read_codex_thread_titles(index_path: Path) -> dict[str, str]:
    """Later index entries are renames of earlier ones, so the last title wins."""
    titles: dict[str, str] = {}
    for record in iter_jsonl_records(index_path):
        if record.get("id") and record.get("thread_name"):
            titles[record["id"]] = record["thread_name"]
    return titles


def read_codex_sessions(
    codex_home: Path, since: datetime, cache: FileSummaryCache[CodexFileSummary]
) -> list[AgentSession]:
    titles = read_codex_thread_titles(codex_home / "session_index.jsonl")
    sessions: list[AgentSession] = []
    for path in recently_modified_files(codex_home / "sessions", "*.jsonl", since):
        part = cache.get(path, parse_codex_rollout_file)
        sessions.append(
            AgentSession(
                agent=AGENT_NAME,
                session_id=part.session_id,
                title=titles.get(part.session_id, ""),
                model=part.model,
                cwd=part.cwd,
                started_at=part.started_at,
                last_active_at=part.last_at,
                usage=part.usage,
            )
        )
    return sessions
