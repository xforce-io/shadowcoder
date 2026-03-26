from shadowcoder.agents.claude_code import ClaudeCodeAgent
from shadowcoder.agents.codex import CodexAgent
from shadowcoder.agents.registry import AgentRegistry

AgentRegistry.register("claude_code", ClaudeCodeAgent)
AgentRegistry.register("codex", CodexAgent)
