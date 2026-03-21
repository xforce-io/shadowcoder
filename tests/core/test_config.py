import pytest
from shadowcoder.core.config import Config


def test_load_config(tmp_config):
    config = Config(str(tmp_config))
    assert config.get_default_agent() == "claude-code"


def test_get_agent_config(tmp_config):
    config = Config(str(tmp_config))
    ac = config.get_agent_config("claude-code")
    assert ac["type"] == "claude_code"


def test_get_agent_config_missing(tmp_config):
    config = Config(str(tmp_config))
    with pytest.raises(KeyError):
        config.get_agent_config("nonexistent")


def test_get_available_agents(tmp_config):
    config = Config(str(tmp_config))
    agents = config.get_available_agents()
    assert "claude-code" in agents
    assert "codex" in agents


def test_get_reviewers(tmp_config):
    config = Config(str(tmp_config))
    assert config.get_reviewers("design") == ["claude-code"]
    assert config.get_reviewers("develop") == ["claude-code"]


def test_get_max_review_rounds(tmp_config):
    config = Config(str(tmp_config))
    assert config.get_max_review_rounds() == 3


def test_get_max_review_rounds_default(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("agents:\n  default: x\n  available:\n    x:\n      type: x\n")
    config = Config(str(config_path))
    assert config.get_max_review_rounds() == 3


def test_get_issue_dir(tmp_config):
    config = Config(str(tmp_config))
    assert config.get_issue_dir() == ".shadowcoder/issues"


def test_get_worktree_dir(tmp_config):
    config = Config(str(tmp_config))
    assert config.get_worktree_dir() == ".shadowcoder/worktrees"


def test_get_log_dir(tmp_config):
    config = Config(str(tmp_config))
    assert config.get_log_dir() == "/tmp/shadowcoder-test/logs"


def test_get_log_level(tmp_config):
    config = Config(str(tmp_config))
    assert config.get_log_level() == "INFO"


def test_missing_config_file():
    with pytest.raises(FileNotFoundError):
        Config("/nonexistent/config.yaml")
