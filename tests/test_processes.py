"""Process tests spawn real throwaway processes tagged with a marker script name."""

from __future__ import annotations

import dataclasses
import subprocess
import sys
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import psutil
import pytest

from agentdeck.models import AgentProcess
from agentdeck.processes import (
    AgentProcessScanner,
    ProcessActionError,
    read_system_stats,
    restart_agent_process,
    terminate_process_tree,
)

MARKER = "agentdeck_fake_agent"
FAKE_AGENT = "Fake Agent"
SPAWN_SETTLE_TIMEOUT_S = 5.0
FAKE_AGENT_SCRIPT = """
import subprocess, sys, time
if len(sys.argv) == 1:
    subprocess.Popen([sys.executable, __file__, "child"])
time.sleep(120)
"""


def _fake_classifier(cmdline: list[str]) -> str | None:
    return FAKE_AGENT if any(MARKER in arg for arg in cmdline[:2]) else None


@pytest.fixture
def fake_agent_script(tmp_path: Path) -> Path:
    script = tmp_path / f"{MARKER}.py"
    script.write_text(FAKE_AGENT_SCRIPT, encoding="utf-8")
    return script


def _wait_for(predicate, timeout_s: float = SPAWN_SETTLE_TIMEOUT_S) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


@pytest.fixture
def fake_agent_tree(fake_agent_script: Path) -> Iterator[subprocess.Popen[bytes]]:
    proc = subprocess.Popen([sys.executable, str(fake_agent_script)], start_new_session=True)
    _wait_for(lambda: len(psutil.Process(proc.pid).children()) == 1)
    yield proc
    for leftover in [*psutil.Process(proc.pid).children(recursive=True), psutil.Process(proc.pid)] if psutil.pid_exists(proc.pid) else []:
        leftover.kill()
    proc.wait(timeout=SPAWN_SETTLE_TIMEOUT_S)


def test_scanner_reports_one_root_per_agent_tree_with_aggregated_memory(fake_agent_tree: subprocess.Popen[bytes]) -> None:
    scanner = AgentProcessScanner(classifier=_fake_classifier)
    found = [p for p in scanner.scan() if p.agent == FAKE_AGENT]
    assert len(found) == 1, "nested child of the same agent must not be listed separately"
    root = found[0]
    assert root.pid == fake_agent_tree.pid
    assert root.child_count == 1
    child_rss = psutil.Process(fake_agent_tree.pid).children()[0].memory_info().rss
    assert root.rss_bytes > child_rss
    assert root.started_at.tzinfo is not None


def test_scanner_ignores_its_own_process() -> None:
    scanner = AgentProcessScanner(classifier=lambda cmd: "Self")
    assert all(p.pid != psutil.Process().pid for p in scanner.scan())


def test_terminate_process_tree_stops_children_too(fake_agent_tree: subprocess.Popen[bytes]) -> None:
    child_pid = psutil.Process(fake_agent_tree.pid).children()[0].pid
    assert terminate_process_tree(fake_agent_tree.pid, timeout_s=2.0) == 2
    fake_agent_tree.wait(timeout=SPAWN_SETTLE_TIMEOUT_S)
    _wait_for(lambda: not psutil.pid_exists(child_pid) or psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE)


def test_terminate_missing_process_raises_action_error() -> None:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    with pytest.raises(ProcessActionError, match="no longer exists"):
        terminate_process_tree(proc.pid)


def _agent_process(pid: int, cmdline: tuple[str, ...], has_terminal: bool) -> AgentProcess:
    return AgentProcess(
        agent=FAKE_AGENT, pid=pid, name="python", cmdline=cmdline, cwd="", started_at=datetime.now().astimezone(),
        cpu_percent=0.0, rss_bytes=0, child_count=0, has_terminal=has_terminal, status="sleeping",
    )


def test_restart_refuses_interactive_processes() -> None:
    with pytest.raises(ProcessActionError, match="interactive"):
        restart_agent_process(_agent_process(pid=1, cmdline=("x",), has_terminal=True))


def test_restart_refuses_empty_command_line() -> None:
    with pytest.raises(ProcessActionError, match="No command line"):
        restart_agent_process(_agent_process(pid=1, cmdline=(), has_terminal=False))


def test_restart_daemon_relaunches_same_command(fake_agent_script: Path, tmp_path: Path) -> None:
    original = subprocess.Popen([sys.executable, str(fake_agent_script), "child"], start_new_session=True)
    scanner = AgentProcessScanner(classifier=_fake_classifier)
    _wait_for(lambda: any(p.pid == original.pid for p in scanner.scan()))
    target = next(p for p in scanner.scan() if p.pid == original.pid)
    new_pid = restart_agent_process(dataclasses.replace(target, has_terminal=False), log_dir=tmp_path / "logs")
    try:
        original.wait(timeout=SPAWN_SETTLE_TIMEOUT_S)
        relaunched = psutil.Process(new_pid)
        assert relaunched.cmdline() == list(target.cmdline)
        assert len(list((tmp_path / "logs").glob("fake-agent-*.log"))) == 1
    finally:
        psutil.Process(new_pid).kill()


def test_system_stats_are_sane() -> None:
    stats = read_system_stats()
    assert stats.ram_total_bytes > 0
    assert 0 <= stats.ram_percent <= 100
