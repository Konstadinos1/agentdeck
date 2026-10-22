"""Helpers shared by the per-agent session readers."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")
JsonRecord = dict[str, Any]


def parse_iso_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_epoch_seconds(value: object) -> datetime | None:
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    return datetime.fromtimestamp(float(value), tz=timezone.utc)


def as_int(value: object) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


def iter_jsonl_records(path: Path, line_markers: tuple[str, ...] = ()) -> Iterator[JsonRecord]:
    """Yield JSON objects from a JSONL file, skipping corrupt or irrelevant lines.

    `line_markers` is a cheap substring pre-filter: session logs are often tens
    of MB and json-decoding only the lines we need keeps refreshes fast.
    """
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line_markers and not any(marker in line for marker in line_markers):
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    yield record
    except OSError as exc:
        logger.warning("Cannot read %s: %s", path, exc)


def recently_modified_files(root: Path, pattern: str, since: datetime) -> list[Path]:
    if not root.is_dir():
        return []
    cutoff = since.timestamp()
    recent: list[Path] = []
    for path in root.rglob(pattern):
        try:
            if path.is_file() and path.stat().st_mtime >= cutoff:
                recent.append(path)
        except OSError:
            continue
    return recent


def latest(*candidates: datetime | None) -> datetime | None:
    present = [c for c in candidates if c is not None]
    return max(present) if present else None


def earliest(*candidates: datetime | None) -> datetime | None:
    present = [c for c in candidates if c is not None]
    return min(present) if present else None


class FileSummaryCache(Generic[T]):
    """Re-parse a file only when its size or mtime changed since the last refresh."""

    def __init__(self) -> None:
        self._entries: dict[Path, tuple[int, int, T]] = {}

    def get(self, path: Path, parse: Callable[[Path], T]) -> T:
        stat = path.stat()
        cached = self._entries.get(path)
        if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
            return cached[2]
        summary = parse(path)
        self._entries[path] = (stat.st_mtime_ns, stat.st_size, summary)
        return summary
