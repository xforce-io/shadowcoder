"""Watchdog-based file monitoring for .shadowcoder/issues/."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Awaitable, Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

_ISSUE_DIR_RE = re.compile(r"/(\d{4})/")

_WATCHED_FILES = {
    "issue.md": "issue",
    "issue.log": "log",
    "feedback.json": "feedback",
    "metrics_history.json": "metrics",
}

OnChangeCallback = Callable[[dict], Awaitable[None]]


class _Handler(FileSystemEventHandler):
    def __init__(self, callback: OnChangeCallback, loop: asyncio.AbstractEventLoop) -> None:
        self._callback = callback
        self._loop = loop

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._handle(event.src_path)

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._handle(event.src_path)

    def _handle(self, path_str: str) -> None:
        path = Path(path_str)
        file_type = _WATCHED_FILES.get(path.name)
        if file_type is None:
            return
        m = _ISSUE_DIR_RE.search(path_str)
        if m is None:
            return
        issue_id = int(m.group(1))
        event = {"issue_id": issue_id, "file_type": file_type, "path": path_str}
        asyncio.run_coroutine_threadsafe(self._callback(event), self._loop)


class FileWatcher:
    def __init__(self, repo_path: str, on_change: OnChangeCallback) -> None:
        self._watch_path = str(Path(repo_path) / ".shadowcoder" / "issues")
        self._on_change = on_change
        self._observer = Observer()

    def start(self) -> None:
        loop = asyncio.get_event_loop()
        handler = _Handler(self._on_change, loop)
        Path(self._watch_path).mkdir(parents=True, exist_ok=True)
        self._observer.schedule(handler, self._watch_path, recursive=True)
        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join(timeout=5)
