from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, RichLog, Input

from shadowcoder.core.bus import Message, MessageBus, MessageType


class ShadowCoderApp(App):
    CSS_PATH = None
    TITLE = "ShadowCoder"

    def __init__(self, bus: MessageBus, **kwargs):
        super().__init__(**kwargs)
        self.bus = bus

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield RichLog(id="output", wrap=True, highlight=True)
        yield Input(
            placeholder="命令: create <title> | list | info #id | design #id | develop #id | test #id | resume #id | approve #id | cancel #id"
        )
        yield Footer()

    async def on_mount(self):
        self.bus.subscribe(MessageType.EVT_ISSUE_CREATED, self._on_issue_created)
        self.bus.subscribe(MessageType.EVT_AGENT_OUTPUT, self._on_agent_output)
        self.bus.subscribe(MessageType.EVT_STATUS_CHANGED, self._on_status_changed)
        self.bus.subscribe(MessageType.EVT_REVIEW_RESULT, self._on_review_result)
        self.bus.subscribe(MessageType.EVT_TASK_COMPLETED, self._on_task_completed)
        self.bus.subscribe(MessageType.EVT_TASK_FAILED, self._on_task_failed)
        self.bus.subscribe(MessageType.EVT_ERROR, self._on_error)

    async def on_input_submitted(self, event: Input.Submitted):
        cmd = event.value.strip()
        event.input.clear()
        if not cmd:
            return
        log = self.query_one("#output", RichLog)
        log.write(f"[bold]> {cmd}[/bold]")
        msg = self._parse_command(cmd)
        if msg:
            await self.bus.publish(msg)

    def _parse_command(self, cmd: str) -> Message | None:
        log = self.query_one("#output", RichLog)
        parts = cmd.split()
        match parts:
            case ["create", *title_parts] if title_parts:
                # Support: create "title" --from path/to/requirements.md
                title_words = []
                description = None
                i = 0
                while i < len(title_parts):
                    if title_parts[i] == "--from" and i + 1 < len(title_parts):
                        description = title_parts[i + 1]
                        i += 2
                    else:
                        title_words.append(title_parts[i])
                        i += 1
                payload = {"title": " ".join(title_words)}
                if description:
                    payload["description"] = description
                return Message(MessageType.CMD_CREATE_ISSUE, payload)
            case ["list"]:
                return Message(MessageType.CMD_LIST, {})
            case ["info", ref]:
                return Message(MessageType.CMD_INFO, {"issue_id": int(ref.lstrip("#"))})
            case ["design", ref]:
                return Message(MessageType.CMD_DESIGN, {"issue_id": int(ref.lstrip("#"))})
            case ["develop", ref]:
                return Message(MessageType.CMD_DEVELOP, {"issue_id": int(ref.lstrip("#"))})
            case ["test", ref]:
                return Message(MessageType.CMD_TEST, {"issue_id": int(ref.lstrip("#"))})
            case ["resume", ref]:
                return Message(MessageType.CMD_RESUME, {"issue_id": int(ref.lstrip("#"))})
            case ["approve", ref]:
                return Message(MessageType.CMD_APPROVE, {"issue_id": int(ref.lstrip("#"))})
            case ["cancel", ref]:
                return Message(MessageType.CMD_CANCEL, {"issue_id": int(ref.lstrip("#"))})
            case ["cleanup", ref]:
                return Message(MessageType.CMD_CLEANUP, {"issue_id": int(ref.lstrip("#"))})
            case _:
                log.write(f"[red]未知命令: {cmd}[/red]")
                return None

    async def _on_issue_created(self, msg: Message):
        log = self.query_one("#output", RichLog)
        log.write(f"[green]Issue #{msg.payload['issue_id']} created: {msg.payload['title']}[/green]")

    async def _on_agent_output(self, msg: Message):
        self.query_one("#output", RichLog).write(msg.payload["chunk"])

    async def _on_status_changed(self, msg: Message):
        log = self.query_one("#output", RichLog)
        extra = f" (round {msg.payload['round']})" if "round" in msg.payload else ""
        log.write(f"[blue]Issue #{msg.payload['issue_id']} → {msg.payload['status']}{extra}[/blue]")

    async def _on_review_result(self, msg: Message):
        log = self.query_one("#output", RichLog)
        passed = "[green]PASSED[/green]" if msg.payload["passed"] else "[red]NOT PASSED[/red]"
        log.write(f"Review by {msg.payload['reviewer']}: {passed} ({msg.payload['comments']} comments)")

    async def _on_task_completed(self, msg: Message):
        log = self.query_one("#output", RichLog)
        log.write(f"[green]Task {msg.payload['task_id']} completed for issue #{msg.payload['issue_id']}[/green]")

    async def _on_task_failed(self, msg: Message):
        log = self.query_one("#output", RichLog)
        reason = msg.payload.get("reason", "unknown")
        log.write(f"[red]Task failed for issue #{msg.payload['issue_id']}: {reason}[/red]")

    async def _on_error(self, msg: Message):
        log = self.query_one("#output", RichLog)
        log.write(f"[red]Error: {msg.payload['message']}[/red]")


def main():
    import shadowcoder.agents  # trigger agent registration

    from shadowcoder.core.config import Config
    from shadowcoder.core.engine import Engine
    from shadowcoder.core.issue_store import IssueStore
    from shadowcoder.core.task_manager import TaskManager
    from shadowcoder.core.worktree import WorktreeManager
    from shadowcoder.agents.registry import AgentRegistry

    import os

    config = Config()
    repo_path = os.getcwd()

    bus = MessageBus()
    wt_manager = WorktreeManager(config.get_worktree_dir())
    task_manager = TaskManager(wt_manager)
    issue_store = IssueStore(repo_path, config)
    agent_registry = AgentRegistry(config)
    engine = Engine(bus, issue_store, task_manager, agent_registry, config, repo_path)

    app = ShadowCoderApp(bus)
    app.run()
