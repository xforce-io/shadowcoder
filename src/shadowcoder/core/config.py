from __future__ import annotations

from pathlib import Path

import yaml


class Config:
    def __init__(self, path: str = "~/.shadowcoder/config.yaml"):
        resolved = Path(path).expanduser()
        if not resolved.exists():
            raise FileNotFoundError(f"Config file not found: {resolved}")
        with open(resolved) as f:
            self._data: dict = yaml.safe_load(f) or {}

    def get_default_agent(self) -> str:
        return self._data["agents"]["default"]

    def get_agent_config(self, name: str) -> dict:
        return self._data["agents"]["available"][name]

    def get_available_agents(self) -> list[str]:
        return list(self._data["agents"]["available"].keys())

    def get_reviewers(self, stage: str) -> list[str]:
        return self._data.get("reviewers", {}).get(stage, [])

    def get_max_review_rounds(self) -> int:
        return self._data.get("review_policy", {}).get("max_review_rounds", 3)

    def get_max_test_retries(self) -> int:
        return self._data.get("review_policy", {}).get("max_test_retries", 3)

    def get_issue_dir(self) -> str:
        return self._data.get("issue_store", {}).get("dir", ".shadowcoder/issues")

    def get_worktree_dir(self) -> str:
        return self._data.get("worktree", {}).get("base_dir", ".shadowcoder/worktrees")

    def get_log_dir(self) -> str:
        return self._data.get("logging", {}).get("dir", "~/.shadowcoder/logs")

    def get_log_level(self) -> str:
        return self._data.get("logging", {}).get("level", "INFO")
