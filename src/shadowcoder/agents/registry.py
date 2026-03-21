from __future__ import annotations

from shadowcoder.agents.base import BaseAgent
from shadowcoder.core.config import Config


class AgentRegistry:
    _agent_classes: dict[str, type[BaseAgent]] = {}

    def __init__(self, config: Config):
        self.config = config
        self._instances: dict[str, BaseAgent] = {}

    @classmethod
    def register(cls, type_name: str, agent_class: type[BaseAgent]) -> None:
        cls._agent_classes[type_name] = agent_class

    def get(self, name: str) -> BaseAgent:
        if name == "default":
            name = self.config.get_default_agent()
        if name not in self._instances:
            agent_conf = self.config.get_agent_config(name)
            agent_type = agent_conf["type"]
            if agent_type not in self._agent_classes:
                raise KeyError(f"Unknown agent type: {agent_type}")
            cls = self._agent_classes[agent_type]
            self._instances[name] = cls(agent_conf)
        return self._instances[name]
