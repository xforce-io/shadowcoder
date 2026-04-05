import asyncio
from pathlib import Path

import pytest

from shadowcoder.dashboard.watcher import FileWatcher


@pytest.mark.asyncio
async def test_watcher_detects_log_change(tmp_path):
    issues_dir = tmp_path / ".shadowcoder" / "issues" / "0001"
    issues_dir.mkdir(parents=True)
    log_path = issues_dir / "issue.log"
    log_path.write_text("[2026-04-01 10:00:00] init\n")

    events: list[dict] = []

    async def on_change(event: dict):
        events.append(event)

    watcher = FileWatcher(str(tmp_path), on_change)
    watcher.start()

    try:
        await asyncio.sleep(0.5)
        with open(log_path, "a") as f:
            f.write("[2026-04-01 10:05:00] new entry\n")

        for _ in range(20):
            if events:
                break
            await asyncio.sleep(0.2)

        assert len(events) >= 1
        assert events[0]["issue_id"] == 1
        assert events[0]["file_type"] in ("log", "issue", "feedback")
    finally:
        watcher.stop()


@pytest.mark.asyncio
async def test_watcher_ignores_unrelated_files(tmp_path):
    issues_dir = tmp_path / ".shadowcoder" / "issues" / "0001"
    issues_dir.mkdir(parents=True)

    events: list[dict] = []

    async def on_change(event: dict):
        events.append(event)

    watcher = FileWatcher(str(tmp_path), on_change)
    watcher.start()

    try:
        await asyncio.sleep(0.5)
        (issues_dir / "random.txt").write_text("hello")
        await asyncio.sleep(1.0)
        assert len(events) == 0
    finally:
        watcher.stop()
