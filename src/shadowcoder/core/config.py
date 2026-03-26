from __future__ import annotations

from pathlib import Path

import yaml


class Config:
    def __init__(self, path: str = "~/.shadowcoder/config.yaml",
                 repo_path: str | None = None):
        resolved = Path(path).expanduser()
        if not resolved.exists():
            self._data: dict = self._default_data()
        else:
            with open(resolved) as f:
                self._data = yaml.safe_load(f) or {}

        if repo_path:
            project_conf = Path(repo_path) / ".shadowcoder" / "config.yaml"
            if project_conf.exists():
                with open(project_conf) as f:
                    overrides = yaml.safe_load(f) or {}
                self._apply_overrides(overrides)

        if resolved.exists() or repo_path:
            self._validate()

    def _apply_overrides(self, overrides: dict) -> None:
        """Merge project-level config on top of global config (section-level)."""
        for section, value in overrides.items():
            if isinstance(value, dict) and isinstance(self._data.get(section), dict):
                self._data[section].update(value)
            else:
                self._data[section] = value

    @staticmethod
    def _default_data() -> dict:
        return {
            "clouds": {},
            "models": {
                "sonnet": {"model": "sonnet"},
            },
            "agents": {
                "default": {"type": "claude_code", "model": "sonnet"},
            },
            "dispatch": {
                "design": "default",
                "develop": "default",
                "design_review": ["default"],
                "develop_review": ["default"],
            },
        }

    def _validate(self):
        """Validate cross-references between clouds, models, agents, dispatch."""
        clouds = self._data.get("clouds", {})
        models = self._data.get("models", {})
        agents = self._data.get("agents", {})
        dispatch = self._data.get("dispatch", {})

        for model_name, model_conf in models.items():
            cloud = model_conf.get("cloud")
            if cloud and cloud not in clouds:
                raise ValueError(
                    f"Model '{model_name}' references unknown cloud '{cloud}'")

        for agent_name, agent_conf in agents.items():
            model = agent_conf.get("model")
            if model and model not in models:
                raise ValueError(
                    f"Agent '{agent_name}' references unknown model '{model}'")

        for phase, value in dispatch.items():
            names = value if isinstance(value, list) else [value]
            for name in names:
                if name not in agents:
                    raise ValueError(
                        f"Dispatch '{phase}' references unknown agent '{name}'")

    def _first_agent(self) -> str:
        agents = self._data.get("agents", {})
        if not agents:
            raise ValueError("No agents defined in config")
        return next(iter(agents))

    def get_agent_for_phase(self, phase: str) -> str | list[str]:
        """Return agent name(s) for a phase.
        design/develop -> str, design_review/develop_review -> list[str]."""
        value = self._data.get("dispatch", {}).get(phase)
        if value is None:
            fallback = self._first_agent()
            return [fallback] if phase.endswith("_review") else fallback
        if phase.endswith("_review"):
            return value if isinstance(value, list) else [value]
        return value

    def get_agent_config(self, name: str) -> dict:
        """Return merged config dict: agent fields + resolved model + cloud env."""
        agent = self._data["agents"][name]
        model_name = agent.get("model")
        result = dict(agent)
        if model_name:
            model = self._data["models"][model_name]
            result["model"] = model["model"]
            cloud_name = model.get("cloud")
            if cloud_name:
                cloud = self._data["clouds"][cloud_name]
                cloud_env = dict(cloud.get("env") or {})
                cloud_env.update(result.get("env") or {})
                if cloud_env:
                    result["env"] = cloud_env
        return result

    def get_pass_threshold(self) -> str:
        """Get review pass threshold: 'no_high_or_critical' or 'no_critical'."""
        return self._data.get("review_policy", {}).get(
            "pass_threshold", "no_critical")

    def get_max_review_rounds(self) -> int:
        return self._data.get("review_policy", {}).get("max_review_rounds", 3)

    def get_max_test_retries(self) -> int:
        return self._data.get("review_policy", {}).get("max_test_retries", 3)

    def get_max_budget_usd(self) -> float | None:
        return self._data.get("review_policy", {}).get("max_budget_usd")

    def get_issue_dir(self) -> str:
        return self._data.get("issue_store", {}).get("dir", ".shadowcoder/issues")

    def get_worktree_dir(self) -> str:
        return self._data.get("worktree", {}).get("base_dir", ".shadowcoder/worktrees")

    def get_log_dir(self) -> str:
        return self._data.get("logging", {}).get("dir", "~/.shadowcoder/logs")

    def get_log_level(self) -> str:
        return self._data.get("logging", {}).get("level", "INFO")

    def get_test_command(self) -> str | None:
        return self._data.get("build", {}).get("test_command")

    def get_gate_mode(self) -> str:
        return self._data.get("gate", {}).get("mode", "standard")
