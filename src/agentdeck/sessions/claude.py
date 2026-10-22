"""Claude Code sessions: ~/.claude/projects/<project>/<session>.jsonl (+ subagents/)."""

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

AGENT_NAME = "Claude Code"
LINE_MARKERS = ('"usage"', '"aiTitle"')


@dataclass
class ClaudeFileSummary:
    session_id: str
    title: str = ""
    model: str = ""
    cwd: str = ""
    first_at: datetime | None = None
    last_at: datetime | None = None
    usage: TokenUsage = field(default_factory=TokenUsage)


def _usage_from_message(message: JsonRecord) -> TokenUsage:
    usage = message.get("usage") or {}
    return TokenUsage(
        input_tokens=as_int(usage.get("input_tokens")),
        output_tokens=as_int(usage.get("output_tokens")),
        cache_read_tokens=as_int(usage.get("cache_read_input_tokens")),
        cache_write_tokens=as_int(usage.get("cache_creation_input_tokens")),
    )


def _apply_assistant_record(summary: ClaudeFileSummary, record: JsonRecord, seen_requests: set[str]) -> None:
    # Claude Code writes one line per content block, each repeating the same
    # usage; counting every line would multiply token totals.
    message = record.get("message") or {}
    request_key = record.get("requestId") or message.get("id") or record.get("uuid")
    timestamp = parse_iso_timestamp(record.get("timestamp"))
    summary.first_at = earliest(summary.first_at, timestamp)
    summary.last_at = latest(summary.last_at, timestamp)
    summary.cwd = record.get("cwd") or summary.cwd
    if message.get("model") and message["model"] != "<synthetic>":
        summary.model = message["model"]
    if request_key in seen_requests:
        return
    seen_requests.add(request_key)
    summary.usage.add(_usage_from_message(message))


def parse_claude_session_file(path: Path) -> ClaudeFileSummary:
    summary = ClaudeFileSummary(session_id=path.stem)
    seen_requests: set[str] = set()
    for record in iter_jsonl_records(path, LINE_MARKERS):
        if record.get("sessionId"):
            summary.session_id = record["sessionId"]
        if record.get("type") == "ai-title" and record.get("aiTitle"):
            summary.title = record["aiTitle"]
        elif record.get("type") == "assistant":
            _apply_assistant_record(summary, record, seen_requests)
    return summary


def _merge_into(sessions: dict[str, AgentSession], part: ClaudeFileSummary) -> None:
    existing = sessions.get(part.session_id)
    if existing is None:
        sessions[part.session_id] = AgentSession(
            agent=AGENT_NAME,
            session_id=part.session_id,
            title=part.title,
            model=part.model,
            cwd=part.cwd,
            started_at=part.first_at,
            last_active_at=part.last_at,
            usage=TokenUsage(**vars(part.usage)),
        )
        return
    existing.title = existing.title or part.title
    existing.model = existing.model or part.model
    existing.cwd = existing.cwd or part.cwd
    existing.started_at = earliest(existing.started_at, part.first_at)
    existing.last_active_at = latest(existing.last_active_at, part.last_at)
    existing.usage.add(part.usage)


def read_claude_sessions(
    projects_dir: Path, since: datetime, cache: FileSummaryCache[ClaudeFileSummary]
) -> list[AgentSession]:
    """Subagent transcripts carry the parent sessionId, so they fold into the parent."""
    sessions: dict[str, AgentSession] = {}
    for path in recently_modified_files(projects_dir, "*.jsonl", since):
        part = cache.get(path, parse_claude_session_file)
        if part.last_at is not None:
            _merge_into(sessions, part)
    return list(sessions.values())
