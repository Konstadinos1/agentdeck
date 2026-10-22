"""Session readers are tested against synthetic files that mirror each agent's real format."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agentdeck.models import AgentSession, TokenUsage
from agentdeck.pricing import ModelPrice, PriceBook
from agentdeck.sessions import SessionCollector, summarize_usage_by_agent
from agentdeck.sessions.claude import parse_claude_session_file
from agentdeck.sessions.codex import parse_codex_rollout_file, read_codex_thread_titles
from agentdeck.sessions.common import FileSummaryCache, iter_jsonl_records, parse_iso_timestamp
from agentdeck.sessions.hermes import read_hermes_sessions
from agentdeck.sessions.openclaw import parse_openclaw_session_file

NOW = datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _write_jsonl(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def _claude_assistant(request_id: str, at: datetime, session_id: str = "sess-1", out: int = 10) -> dict:
    return {
        "type": "assistant",
        "sessionId": session_id,
        "requestId": request_id,
        "timestamp": _iso(at),
        "cwd": "/home/costa/proj",
        "message": {
            "id": f"msg-{request_id}",
            "model": "claude-opus-5",
            "usage": {
                "input_tokens": 5,
                "output_tokens": out,
                "cache_read_input_tokens": 100,
                "cache_creation_input_tokens": 50,
            },
        },
    }


# ---- common ----


def test_iter_jsonl_skips_corrupt_and_non_object_lines(tmp_path: Path) -> None:
    path = tmp_path / "x.jsonl"
    path.write_text('{"a": 1}\nnot json\n[1, 2]\n{"b": 2}\n', encoding="utf-8")
    assert list(iter_jsonl_records(path)) == [{"a": 1}, {"b": 2}]


def test_iter_jsonl_on_missing_file_yields_nothing(tmp_path: Path) -> None:
    assert list(iter_jsonl_records(tmp_path / "missing.jsonl")) == []


def test_parse_iso_timestamp_handles_zulu_naive_and_garbage() -> None:
    assert parse_iso_timestamp("2026-10-20T02:47:06.343Z") == datetime(2026, 10, 20, 2, 47, 6, 343000, timezone.utc)
    assert parse_iso_timestamp("2026-10-20T02:47:06").tzinfo == timezone.utc
    assert parse_iso_timestamp("yesterday") is None
    assert parse_iso_timestamp(None) is None


def test_file_summary_cache_reparses_only_when_file_changes(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("a", encoding="utf-8")
    calls: list[Path] = []
    cache: FileSummaryCache[int] = FileSummaryCache()

    def parse(p: Path) -> int:
        calls.append(p)
        return len(p.read_text(encoding="utf-8"))

    assert cache.get(path, parse) == 1
    assert cache.get(path, parse) == 1
    path.write_text("abc", encoding="utf-8")
    assert cache.get(path, parse) == 3
    assert len(calls) == 2


# ---- Claude Code ----


def test_claude_usage_is_deduplicated_per_request(tmp_path: Path) -> None:
    t0 = NOW - timedelta(hours=1)
    records = [
        {"type": "ai-title", "aiTitle": "Fix payroll QPP", "sessionId": "sess-1"},
        _claude_assistant("req-1", t0),
        _claude_assistant("req-1", t0 + timedelta(seconds=1)),  # second content block, same usage
        _claude_assistant("req-2", t0 + timedelta(minutes=5), out=20),
    ]
    summary = parse_claude_session_file(_write_jsonl(tmp_path / "sess-1.jsonl", records))
    assert summary.title == "Fix payroll QPP"
    assert summary.model == "claude-opus-5"
    assert summary.cwd == "/home/costa/proj"
    assert summary.usage == TokenUsage(input_tokens=10, output_tokens=30, cache_read_tokens=200, cache_write_tokens=100)
    assert summary.first_at == parse_iso_timestamp(_iso(t0))
    assert summary.last_at == parse_iso_timestamp(_iso(t0 + timedelta(minutes=5)))


def test_claude_synthetic_model_does_not_override_real_model(tmp_path: Path) -> None:
    synthetic = _claude_assistant("req-2", NOW)
    synthetic["message"]["model"] = "<synthetic>"
    path = _write_jsonl(tmp_path / "s.jsonl", [_claude_assistant("req-1", NOW), synthetic])
    assert parse_claude_session_file(path).model == "claude-opus-5"


def test_claude_subagent_transcripts_fold_into_parent_session(tmp_path: Path) -> None:
    project = tmp_path / ".claude" / "projects" / "-home-costa"
    _write_jsonl(project / "sess-1.jsonl", [_claude_assistant("req-1", NOW - timedelta(minutes=2))])
    _write_jsonl(project / "sess-1" / "subagents" / "agent-a.jsonl", [_claude_assistant("req-sub", NOW)])
    sessions = [s for s in SessionCollector(home=tmp_path).collect().sessions if s.agent == "Claude Code"]
    assert len(sessions) == 1
    assert sessions[0].usage.output_tokens == 20


# ---- Codex ----


def _codex_records(at: datetime) -> list[dict]:
    return [
        {"timestamp": _iso(at), "type": "session_meta", "payload": {"id": "thr-1", "cwd": "/home/costa", "timestamp": _iso(at)}},
        {"timestamp": _iso(at), "type": "turn_context", "payload": {"model": "gpt-6"}},
        {"timestamp": _iso(at), "type": "event_msg", "payload": {"type": "token_count", "info": None}},
        {
            "timestamp": _iso(at + timedelta(minutes=1)),
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {"total_token_usage": {"input_tokens": 1000, "cached_input_tokens": 400, "output_tokens": 50}},
            },
        },
        {
            "timestamp": _iso(at + timedelta(minutes=2)),
            "type": "event_msg",
            "payload": {"type": "thread_settings_applied", "thread_settings": {"model": "gpt-6-sol"}},
        },
    ]


def test_codex_rollout_splits_cached_input_and_tracks_latest_model(tmp_path: Path) -> None:
    summary = parse_codex_rollout_file(_write_jsonl(tmp_path / "rollout.jsonl", _codex_records(NOW)))
    assert summary.session_id == "thr-1"
    assert summary.model == "gpt-6-sol"
    assert summary.usage == TokenUsage(input_tokens=600, output_tokens=50, cache_read_tokens=400)
    assert summary.last_at == parse_iso_timestamp(_iso(NOW + timedelta(minutes=2)))


def test_codex_thread_titles_last_rename_wins(tmp_path: Path) -> None:
    index = _write_jsonl(
        tmp_path / "session_index.jsonl",
        [{"id": "thr-1", "thread_name": "draft name"}, {"id": "thr-1", "thread_name": "Final name"}],
    )
    assert read_codex_thread_titles(index) == {"thr-1": "Final name"}


# ---- Hermes ----


def _create_hermes_db(path: Path, rows: list[tuple]) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, model TEXT, title TEXT, started_at REAL,"
        " ended_at REAL, last_activity_at REAL, input_tokens INT, output_tokens INT, cache_read_tokens INT,"
        " cache_write_tokens INT, estimated_cost_usd REAL, actual_cost_usd REAL, hidden INT)"
    )
    connection.executemany("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    connection.commit()
    connection.close()


def test_hermes_reads_recent_visible_sessions_with_reported_cost(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    recent, old = NOW.timestamp() - 60, (NOW - timedelta(days=30)).timestamp()
    _create_hermes_db(
        db,
        [
            ("cron_1", "cron", "glm-5.2", "perimeter scan", recent, recent, recent, 100, 20, 300, 0, 0.0035, None, 0),
            ("cli_1", "cli", "glm-5.2", None, recent, None, None, 1, 1, 0, 0, 0.5, 0.42, 0),
            ("hidden", "cli", "m", "x", recent, None, None, 1, 1, 0, 0, None, None, 1),
            ("ancient", "cli", "m", "y", old, old, old, 1, 1, 0, 0, None, None, 0),
        ],
    )
    sessions = {s.session_id: s for s in read_hermes_sessions(db, NOW - timedelta(days=7))}
    assert set(sessions) == {"cron_1", "cli_1"}
    assert sessions["cron_1"].reported_cost_usd == pytest.approx(0.0035)
    assert sessions["cron_1"].usage.cache_read_tokens == 300
    assert sessions["cli_1"].reported_cost_usd == pytest.approx(0.42)  # actual beats estimate
    assert sessions["cli_1"].title == "cli"  # falls back to source


def test_hermes_missing_db_returns_no_sessions(tmp_path: Path) -> None:
    assert read_hermes_sessions(tmp_path / "absent.db", NOW) == []


# ---- OpenClaw ----


def test_openclaw_sums_usage_and_reported_cost(tmp_path: Path) -> None:
    records = [
        {"type": "session", "id": "oc-1", "timestamp": _iso(NOW), "cwd": "/home/costa/.openclaw/workspace"},
        {"type": "model_change", "timestamp": _iso(NOW), "modelId": "glm-5.1"},
        {"type": "message", "timestamp": _iso(NOW), "message": {"role": "user", "content": "Are   you still there"}},
        {
            "type": "message",
            "timestamp": _iso(NOW),
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Yep"}],
                "usage": {"input": 20, "output": 9, "cacheRead": 4, "cacheWrite": 0, "cost": {"total": 0.025}},
            },
        },
        {"type": "message", "timestamp": _iso(NOW), "message": {"role": "assistant", "usage": {"prompt_tokens": 0}}},
    ]
    summary = parse_openclaw_session_file(_write_jsonl(tmp_path / "oc-1-topic-1.jsonl", records))
    assert summary.session_id == "oc-1"
    assert summary.title == "Are you still there"
    assert summary.model == "glm-5.1"
    assert summary.usage == TokenUsage(input_tokens=20, output_tokens=9, cache_read_tokens=4)
    assert summary.cost_usd == pytest.approx(0.025)


# ---- collector + summary ----


def _populate_home(home: Path) -> None:
    _write_jsonl(home / ".claude" / "projects" / "p" / "c1.jsonl", [_claude_assistant("r1", NOW, session_id="c1")])
    _write_jsonl(home / ".codex" / "sessions" / "2026" / "10" / "20" / "rollout-1.jsonl", _codex_records(NOW))
    _write_jsonl(home / ".codex" / "session_index.jsonl", [{"id": "thr-1", "thread_name": "Codex thread"}])
    _write_jsonl(
        home / ".openclaw" / "agents" / "main" / "sessions" / "oc-1.jsonl",
        [{"type": "session", "id": "oc-1", "timestamp": _iso(NOW), "cwd": "/tmp"}],
    )
    stale = _write_jsonl(home / ".openclaw" / "agents" / "main" / "sessions" / "old.jsonl", [{"type": "session", "id": "old"}])
    old_time = (NOW - timedelta(days=30)).timestamp()
    os.utime(stale, (old_time, old_time))


def test_collector_merges_agents_skips_stale_files_and_sorts_newest_first(tmp_path: Path) -> None:
    _populate_home(tmp_path)
    collection = SessionCollector(home=tmp_path, lookback_days=7).collect()
    assert collection.errors == {}
    assert {s.agent for s in collection.sessions} == {"Claude Code", "Codex", "OpenClaw"}
    assert collection.sessions[0].agent == "Codex"  # its last record is 2 minutes in the future
    assert next(s for s in collection.sessions if s.agent == "Codex").title == "Codex thread"


def test_collector_reports_corrupt_hermes_db_without_dropping_other_agents(tmp_path: Path) -> None:
    _populate_home(tmp_path)
    (tmp_path / ".hermes").mkdir()
    (tmp_path / ".hermes" / "state.db").write_bytes(b"this is not sqlite")
    collection = SessionCollector(home=tmp_path).collect()
    assert "Hermes" in collection.errors
    assert {s.agent for s in collection.sessions} == {"Claude Code", "Codex", "OpenClaw"}


def _summary_session(agent: str, model: str, tokens: int, active: datetime, reported: float | None = None) -> AgentSession:
    return AgentSession(agent, f"{agent}-{tokens}", "", model, "", active, active, TokenUsage(input_tokens=tokens), reported)


def test_usage_summary_splits_today_from_window_and_flags_unpriced_models() -> None:
    sessions = [
        _summary_session("Codex", "gpt-6", 1_000_000, NOW),
        _summary_session("Codex", "gpt-6", 2_000_000, NOW - timedelta(days=3)),
        _summary_session("Codex", "unknown-model", 10, NOW),
        _summary_session("Hermes", "glm", 5, NOW, reported=0.5),
    ]
    prices = PriceBook({"gpt-6": ModelPrice(input=1.0, output=0.0)})
    summaries = {s.agent: s for s in summarize_usage_by_agent(sessions, prices, now=NOW)}
    codex = summaries["Codex"]
    assert codex.session_count == 3
    assert codex.today.total_tokens == 1_000_010
    assert codex.window.total_tokens == 3_000_010
    assert codex.today_cost_usd == pytest.approx(1.0)
    assert codex.window_cost_usd == pytest.approx(3.0)
    assert codex.cost_incomplete is True
    assert summaries["Hermes"].cost_incomplete is False
    assert summaries["Hermes"].window_cost_usd == pytest.approx(0.5)
