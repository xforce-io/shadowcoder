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
  python scripts/run_real.py ~/lab/coder-playground init
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


def _seed_user_roles() -> list[str]:
    """Copy default role instructions to ~/.shadowcoder/roles/ if missing."""
    import shutil
    seed_dir = Path(__file__).resolve().parent.parent / "data" / "roles"
    user_roles = Path("~/.shadowcoder/roles").expanduser()
    created = []
    if not seed_dir.is_dir():
        return created
    for role_dir in sorted(seed_dir.iterdir()):
        if not role_dir.is_dir():
            continue
        dest = user_roles / role_dir.name
        if not dest.exists():
            shutil.copytree(role_dir, dest)
            created.append(f"~/.shadowcoder/roles/{role_dir.name}/")
    return created


def _init_project(repo_path: str) -> None:
    """Scaffold .shadowcoder directory structure for a new project."""
    sc = Path(repo_path) / ".shadowcoder"
    created = []

    for d in ["issues", "worktrees", "roles"]:
        p = sc / d
        if not p.exists():
            p.mkdir(parents=True)
            created.append(str(p.relative_to(repo_path)))

    config_file = sc / "config.yaml"
    if not config_file.exists():
        config_file.write_text("""\
# Project-level shadowcoder config.
# Overrides ~/.shadowcoder/config.yaml — only write what differs.
# See: https://github.com/anthropics/shadowcoder#multi-model-support

# Example: use codex for develop, claude for review
# dispatch:
#   design: codex
#   develop: codex
#   design_review: [opus]
#   develop_review: [opus]
""")
        created.append(str(config_file.relative_to(repo_path)))

    gitignore = sc / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("worktrees/\n")
        created.append(str(gitignore.relative_to(repo_path)))

    # Ensure .shadowcoder/ is in repo root .gitignore
    root_gitignore = Path(repo_path) / ".gitignore"
    entry = ".shadowcoder/\n"
    if root_gitignore.exists():
        existing = root_gitignore.read_text()
        if ".shadowcoder/" not in existing:
            with root_gitignore.open("a") as f:
                if not existing.endswith("\n"):
                    f.write("\n")
                f.write(entry)
            created.append(".gitignore (appended .shadowcoder/)")
    else:
        root_gitignore.write_text(entry)
        created.append(".gitignore")

    # Seed user-level default roles if missing
    seeded = _seed_user_roles()

    if created or seeded:
        print(f"Initialized .shadowcoder in {repo_path}")
        for f in created:
            print(f"  created {f}")
        for f in seeded:
            print(f"  seeded  {f}")
    else:
        print(f".shadowcoder already initialized in {repo_path}")


async def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    repo_path = str(Path(sys.argv[1]).resolve())
    _validate_repo_path(repo_path)
    command = sys.argv[2]
    args = sys.argv[3:]

    if command == "init":
        _init_project(repo_path)
        return

    config = Config(repo_path=repo_path)
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
        elif not args:
            payload = {"resume_last": True}
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
                if command == "create":
                    latest = issues[-1]
                elif command == "run" and (not args or not args[0].isdigit()):
                    last_id = issue_store.get_last()
                    latest = issue_store.get(last_id) if last_id else issues[-1]
                else:
                    latest = issue_store.get(int(args[0]))
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
