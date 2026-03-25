"""
Run shadowcoder against a real repo with the real Claude agent.
Usage: python scripts/run_real.py <repo_path> <command> [args]

Examples:
  python scripts/run_real.py ~/lab/coder-playground create "SQL Database Engine" --from requirements.md
  python scripts/run_real.py ~/lab/coder-playground design 1
  python scripts/run_real.py ~/lab/coder-playground develop 1
  python scripts/run_real.py ~/lab/coder-playground run "SQL Database Engine" --from requirements.md
  python scripts/run_real.py ~/lab/coder-playground run 1
  python scripts/run_real.py ~/lab/coder-playground info 1
  python scripts/run_real.py ~/lab/coder-playground list
  python scripts/run_real.py ~/lab/coder-playground cleanup 1
  python scripts/run_real.py ~/lab/coder-playground cleanup 1 --delete-branch
"""
import asyncio
import subprocess
import sys
from pathlib import Path

import shadowcoder.agents  # trigger registration

from shadowcoder.core.bus import Message, MessageBus, MessageType
from shadowcoder.core.config import Config
from shadowcoder.core.engine import Engine
from shadowcoder.core.issue_store import IssueStore
from shadowcoder.core.task_manager import TaskManager
from shadowcoder.core.worktree import WorktreeManager
from shadowcoder.agents.registry import AgentRegistry


def _validate_repo_path(repo_path: str) -> None:
    """Ensure repo_path is a git root to prevent .shadowcoder state split."""
    try:
        toplevel = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=repo_path, stderr=subprocess.DEVNULL,
        ).decode().strip()
        if Path(toplevel).resolve() != Path(repo_path).resolve():
            print(f"ERROR: repo_path must be a git root directory.\n"
                  f"  Given:    {repo_path}\n"
                  f"  Git root: {toplevel}\n"
                  f"This prevents .shadowcoder state from splitting across directories.")
            sys.exit(1)
    except subprocess.CalledProcessError:
        print(f"ERROR: {repo_path} is not a git repository.")
        sys.exit(1)


async def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    repo_path = str(Path(sys.argv[1]).resolve())
    _validate_repo_path(repo_path)
    command = sys.argv[2]
    args = sys.argv[3:]

    config = Config()
    bus = MessageBus()
    wt_manager = WorktreeManager(config.get_worktree_dir())
    task_manager = TaskManager(wt_manager)
    issue_store = IssueStore(repo_path, config)
    registry = AgentRegistry(config)
    engine = Engine(bus, issue_store, task_manager, registry, config, repo_path)

    # Subscribe to all events for logging
    async def log_event(msg):
        print(f"[EVENT] {msg.type.value}: {msg.payload}")

    for mt in MessageType:
        if mt.value.startswith("evt."):
            bus.subscribe(mt, log_event)

    # Dispatch command
    if command == "create":
        title_parts = []
        description = None
        i = 0
        while i < len(args):
            if args[i] == "--from" and i + 1 < len(args):
                source = args[i + 1]
                if source.startswith(("http://", "https://")):
                    description = source
                else:
                    desc_path = Path(repo_path) / source
                    if not desc_path.exists():
                        desc_path = Path(source)
                    description = str(desc_path)
                i += 2
            else:
                title_parts.append(args[i])
                i += 1
        payload = {"title": " ".join(title_parts)}
        if description:
            payload["description"] = description
        await bus.publish(Message(MessageType.CMD_CREATE_ISSUE, payload))

    elif command == "design":
        await bus.publish(Message(MessageType.CMD_DESIGN, {"issue_id": int(args[0])}))

    elif command == "develop":
        await bus.publish(Message(MessageType.CMD_DEVELOP, {"issue_id": int(args[0])}))

    elif command == "run":
        if args and args[0].isdigit():
            payload = {"issue_id": int(args[0])}
        else:
            title_parts = []
            description = None
            i = 0
            while i < len(args):
                if args[i] == "--from" and i + 1 < len(args):
                    source = args[i + 1]
                    if source.startswith(("http://", "https://")):
                        description = source
                    else:
                        desc_path = Path(repo_path) / source
                        if not desc_path.exists():
                            desc_path = Path(source)
                        description = str(desc_path)
                    i += 2
                else:
                    title_parts.append(args[i])
                    i += 1
            payload = {"title": " ".join(title_parts)}
            if description:
                payload["description"] = description
        await bus.publish(Message(MessageType.CMD_RUN, payload))

    elif command == "info":
        await bus.publish(Message(MessageType.CMD_INFO, {"issue_id": int(args[0])}))

    elif command == "list":
        await bus.publish(Message(MessageType.CMD_LIST, {}))

    elif command == "approve":
        await bus.publish(Message(MessageType.CMD_APPROVE, {"issue_id": int(args[0])}))

    elif command == "resume":
        await bus.publish(Message(MessageType.CMD_RESUME, {"issue_id": int(args[0])}))

    elif command == "cancel":
        await bus.publish(Message(MessageType.CMD_CANCEL, {"issue_id": int(args[0])}))

    elif command == "iterate":
        issue_id = int(args[0])
        requirements_parts = []
        from_source = None
        i = 1
        while i < len(args):
            if args[i] == "--from" and i + 1 < len(args):
                from_source = args[i + 1]
                i += 2
            else:
                requirements_parts.append(args[i])
                i += 1
        requirements = ""
        if from_source and from_source.startswith(("http://", "https://")):
            requirements = Engine._fetch_url_content(from_source)
        elif from_source:
            from_path = Path(repo_path) / from_source
            if not from_path.is_file():
                from_path = Path(from_source)
            if from_path.is_file():
                requirements = from_path.read_text(encoding="utf-8")
        elif requirements_parts:
            requirements = " ".join(requirements_parts)
        await bus.publish(Message(MessageType.CMD_ITERATE, {
            "issue_id": issue_id,
            "requirements": requirements,
        }))

    elif command == "cleanup":
        delete_branch = "--delete-branch" in args
        await bus.publish(Message(MessageType.CMD_CLEANUP, {
            "issue_id": int(args[0]),
            "delete_branch": delete_branch,
        }))

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)

    # Print final issue state
    if command in ("create", "design", "develop", "run", "approve", "resume", "iterate"):
        try:
            issues = issue_store.list_all()
            if issues:
                latest = issues[-1] if command == "create" else issue_store.get(int(args[0]))
                print(f"\n=== Issue #{latest.id}: {latest.title} ===")
                print(f"Status: {latest.status.value}")
                print(f"Sections: {list(latest.sections.keys())}")
                log = issue_store.get_log(latest.id)
                if log:
                    print(f"\n--- 航海日志 ---")
                    print(log)
        except Exception as e:
            print(f"Could not read issue: {e}")


if __name__ == "__main__":
    asyncio.run(main())
