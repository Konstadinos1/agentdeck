"""Live process scanning, process-tree aggregation, kill and restart."""

from __future__ import annotations

import logging
import os
import subprocess
import time
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path

import psutil

from agentdeck.agents import classify_agent_cmdline
from agentdeck.models import AgentProcess, SystemStats

logger = logging.getLogger(__name__)

GRACEFUL_TERMINATE_TIMEOUT_S = 5.0
RESTART_LOG_DIR = Path.home() / ".local" / "state" / "agentdeck" / "restarts"

Classifier = Callable[[list[str]], str | None]


class ProcessActionError(RuntimeError):
    """Raised when a kill/restart request cannot be carried out."""


class AgentProcessScanner:
    """Finds agent processes and measures their whole process tree.

    The scanner is stateful on purpose: psutil computes CPU% as a delta between
    two calls on the *same* Process object, so objects are cached across scans.
    """

    def __init__(self, classifier: Classifier = classify_agent_cmdline) -> None:
        self._classifier = classifier
        self._process_cache: dict[tuple[int, float], psutil.Process] = {}
        self._own_pid = os.getpid()

    def _cached(self, proc: psutil.Process) -> psutil.Process:
        key = (proc.pid, proc.create_time())
        return self._process_cache.setdefault(key, proc)

    def _cpu_percent(self, proc: psutil.Process) -> float:
        try:
            return self._cached(proc).cpu_percent(interval=None)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return 0.0

    def _classify_all(self) -> dict[int, tuple[psutil.Process, str]]:
        matches: dict[int, tuple[psutil.Process, str]] = {}
        for proc in psutil.process_iter(["pid", "cmdline"]):
            if proc.pid == self._own_pid:
                continue
            agent = self._classifier(proc.info.get("cmdline") or [])
            if agent:
                matches[proc.pid] = (proc, agent)
        return matches

    def _is_nested(self, proc: psutil.Process, agent: str, matches: dict[int, tuple[psutil.Process, str]]) -> bool:
        """A process is nested if an ancestor belongs to the same agent (already counted)."""
        try:
            parents = proc.parents()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False
        return any(p.pid in matches and matches[p.pid][1] == agent for p in parents)

    def _build_agent_process(self, proc: psutil.Process, agent: str) -> AgentProcess | None:
        try:
            with proc.oneshot():
                children = proc.children(recursive=True)
                tree = [proc, *children]
                return AgentProcess(
                    agent=agent,
                    pid=proc.pid,
                    name=proc.name(),
                    cmdline=tuple(proc.cmdline()),
                    cwd=_safe_cwd(proc),
                    started_at=datetime.fromtimestamp(proc.create_time()).astimezone(),
                    cpu_percent=sum(self._cpu_percent(p) for p in tree),
                    rss_bytes=sum(_safe_rss(p) for p in tree),
                    child_count=len(children),
                    has_terminal=proc.terminal() is not None,
                    status=proc.status(),
                )
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            return None
        except psutil.AccessDenied:
            logger.debug("Access denied reading pid %s", proc.pid)
            return None

    def _prune_cache(self) -> None:
        live = set(psutil.pids())
        self._process_cache = {k: v for k, v in self._process_cache.items() if k[0] in live}

    def scan(self) -> list[AgentProcess]:
        matches = self._classify_all()
        results: list[AgentProcess] = []
        for proc, agent in matches.values():
            if self._is_nested(proc, agent, matches):
                continue
            built = self._build_agent_process(proc, agent)
            if built is not None:
                results.append(built)
        self._prune_cache()
        return sorted(results, key=lambda p: p.rss_bytes, reverse=True)


def _safe_rss(proc: psutil.Process) -> int:
    try:
        return proc.memory_info().rss
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return 0


def _safe_cwd(proc: psutil.Process) -> str:
    try:
        return proc.cwd()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return ""


def read_system_stats() -> SystemStats:
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    return SystemStats(
        cpu_percent=psutil.cpu_percent(interval=None),
        ram_used_bytes=memory.total - memory.available,
        ram_total_bytes=memory.total,
        swap_used_bytes=swap.used,
        swap_total_bytes=swap.total,
    )


def _signal_all(procs: Iterable[psutil.Process], force: bool) -> None:
    for proc in procs:
        try:
            if force:
                proc.kill()
            else:
                proc.terminate()
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue


def terminate_process_tree(pid: int, force: bool = False, timeout_s: float = GRACEFUL_TERMINATE_TIMEOUT_S) -> int:
    """Stop a process and all its descendants. Returns how many processes were signalled.

    Children are signalled first so MCP servers and shells do not get orphaned
    and re-parented to init, where they would silently keep consuming RAM.
    """
    try:
        root = psutil.Process(pid)
        tree = [*root.children(recursive=True), root]
    except psutil.NoSuchProcess as exc:
        raise ProcessActionError(f"Process {pid} no longer exists") from exc
    except psutil.AccessDenied as exc:
        raise ProcessActionError(f"Permission denied for process {pid}") from exc
    try:
        _signal_all(tree, force)
        _, alive = psutil.wait_procs(tree, timeout=timeout_s)
        if alive and not force:
            _signal_all(alive, force=True)
            psutil.wait_procs(alive, timeout=timeout_s)
    except psutil.AccessDenied as exc:
        raise ProcessActionError(f"Permission denied stopping process tree of {pid}") from exc
    return len(tree)


def _restart_environment(pid: int) -> dict[str, str]:
    """Reuse the original environment so API keys and PATH tweaks survive the restart."""
    try:
        return psutil.Process(pid).environ() or dict(os.environ)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return dict(os.environ)


def restart_agent_process(target: AgentProcess, log_dir: Path = RESTART_LOG_DIR) -> int:
    """Stop a daemon agent and relaunch it detached with the same argv, cwd and env.

    Interactive (TTY) agents are refused: relaunching them headless would leave
    an invisible process waiting for keyboard input.
    """
    if not target.is_daemon:
        raise ProcessActionError(
            f"{target.agent} (pid {target.pid}) is interactive; restart it from its own terminal"
        )
    if not target.cmdline:
        raise ProcessActionError(f"No command line recorded for pid {target.pid}")
    environment = _restart_environment(target.pid)
    terminate_process_tree(target.pid)
    log_dir.mkdir(parents=True, exist_ok=True)
    slug = target.agent.lower().replace(" ", "-")
    log_path = log_dir / f"{slug}-{time.strftime('%Y%m%dT%H%M%S')}.log"
    try:
        with log_path.open("ab") as log_file:
            new_proc = subprocess.Popen(  # noqa: S603 - argv comes from the process we just stopped
                list(target.cmdline),
                cwd=target.cwd or None,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    except OSError as exc:
        raise ProcessActionError(f"Relaunch failed: {exc}") from exc
    return new_proc.pid
