import pytest
from shadowcoder.core.config import Config


NEW_CONFIG = """\
clouds:
  local:
    env: {}
  volcengine:
    env:
      ANTHROPIC_BASE_URL: https://example.com
      ANTHROPIC_AUTH_TOKEN: test-key

models:
  sonnet:
    cloud: local
    model: sonnet
  deepseek:
    cloud: volcengine
    model: deepseek-v3-2-251201

agents:
  fast-coder:
    type: claude_code
    model: deepseek
    permission_mode: auto
  quality-reviewer:
    type: claude_code
    model: sonnet

dispatch:
  design: fast-coder
  develop: fast-coder
  design_review: [quality-reviewer]
  develop_review: [quality-reviewer]

review_policy:
  pass_threshold: no_high_or_critical
  max_review_rounds: 3

logging:
  dir: /tmp/shadowcoder-test/logs
  level: INFO

issue_store:
  dir: .shadowcoder/issues

worktree:
  base_dir: .shadowcoder/worktrees
"""


@pytest.fixture
def new_config(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(NEW_CONFIG)
    return Config(str(p))


def test_get_agent_for_phase_design(new_config):
    assert new_config.get_agent_for_phase("design") == "fast-coder"


def test_get_agent_for_phase_develop(new_config):
    assert new_config.get_agent_for_phase("develop") == "fast-coder"


def test_get_agent_for_phase_review_returns_list(new_config):
    assert new_config.get_agent_for_phase("design_review") == ["quality-reviewer"]
    assert new_config.get_agent_for_phase("develop_review") == ["quality-reviewer"]


def test_get_agent_for_phase_review_string_becomes_list(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(NEW_CONFIG.replace(
        "design_review: [quality-reviewer]",
        "design_review: quality-reviewer"))
    config = Config(str(p))
    assert config.get_agent_for_phase("design_review") == ["quality-reviewer"]


def test_get_agent_for_phase_fallback(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("clouds:\n  local:\n    env: {}\nmodels:\n  sonnet:\n    cloud: local\n    model: sonnet\nagents:\n  only-agent:\n    type: claude_code\n    model: sonnet\n")
    config = Config(str(p))
    assert config.get_agent_for_phase("design") == "only-agent"
    assert config.get_agent_for_phase("design_review") == ["only-agent"]


def test_get_agent_config_merges_cloud_env(new_config):
    ac = new_config.get_agent_config("fast-coder")
    assert ac["type"] == "claude_code"
    assert ac["model"] == "deepseek-v3-2-251201"
    assert ac["env"]["ANTHROPIC_BASE_URL"] == "https://example.com"


def test_get_agent_config_no_cloud_env(new_config):
    ac = new_config.get_agent_config("quality-reviewer")
    assert ac["model"] == "sonnet"
    assert "env" not in ac or ac.get("env", {}) == {}


def test_validation_bad_model_ref(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("clouds:\n  local:\n    env: {}\nmodels:\n  sonnet:\n    cloud: local\n    model: sonnet\nagents:\n  bad:\n    type: claude_code\n    model: nonexistent\n")
    with pytest.raises(ValueError, match="unknown model"):
        Config(str(p))


def test_validation_bad_cloud_ref(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("clouds: {}\nmodels:\n  sonnet:\n    cloud: nonexistent\n    model: sonnet\nagents:\n  a:\n    type: claude_code\n    model: sonnet\n")
    with pytest.raises(ValueError, match="unknown cloud"):
        Config(str(p))


def test_validation_bad_dispatch_ref(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("clouds:\n  local:\n    env: {}\nmodels:\n  sonnet:\n    cloud: local\n    model: sonnet\nagents:\n  a:\n    type: claude_code\n    model: sonnet\ndispatch:\n  design: nonexistent\n")
    with pytest.raises(ValueError, match="unknown agent"):
        Config(str(p))


def test_max_review_rounds(new_config):
    assert new_config.get_max_review_rounds() == 3


def test_max_review_rounds_default(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("clouds: {}\nmodels: {}\nagents:\n  a:\n    type: x\n")
    config = Config(str(p))
    assert config.get_max_review_rounds() == 3


def test_issue_dir(new_config):
    assert new_config.get_issue_dir() == ".shadowcoder/issues"


def test_worktree_dir(new_config):
    assert new_config.get_worktree_dir() == ".shadowcoder/worktrees"


def test_log_dir(new_config):
    assert new_config.get_log_dir() == "/tmp/shadowcoder-test/logs"


def test_log_level(new_config):
    assert new_config.get_log_level() == "INFO"


def test_missing_config_uses_defaults():
    """No config file → built-in defaults, no error."""
    config = Config("/nonexistent/config.yaml")
    assert config.get_agent_for_phase("design") == "default"
    assert config.get_agent_for_phase("design_review") == ["default"]
    ac = config.get_agent_config("default")
    assert ac["type"] == "claude_code"
    assert ac["model"] == "sonnet"
    assert "env" not in ac or ac.get("env") is None


def test_missing_config_getter_defaults():
    """All getters return sensible defaults with no config file."""
    config = Config("/nonexistent/config.yaml")
    assert config.get_max_review_rounds() == 3
    assert config.get_max_test_retries() == 3
    assert config.get_max_budget_usd() is None
    assert config.get_issue_dir() == ".shadowcoder/issues"
    assert config.get_worktree_dir() == ".shadowcoder/worktrees"
    assert config.get_pass_threshold() == "no_critical"
    assert config.get_gate_mode() == "standard"
    assert config.get_test_command() is None


def test_max_budget_not_set(new_config):
    assert new_config.get_max_budget_usd() is None


def test_max_budget_set(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("clouds: {}\nmodels: {}\nagents:\n  a:\n    type: x\nreview_policy:\n  max_budget_usd: 2.50\n")
    config = Config(str(p))
    assert config.get_max_budget_usd() == 2.50


# --- Project-level config override tests ---


def test_project_config_overrides_dispatch(tmp_path):
    """Project .shadowcoder/config.yaml overrides dispatch section."""
    global_conf = tmp_path / "global.yaml"
    global_conf.write_text(NEW_CONFIG)

    repo = tmp_path / "repo"
    sc = repo / ".shadowcoder"
    sc.mkdir(parents=True)
    (sc / "config.yaml").write_text("dispatch:\n  design: quality-reviewer\n")

    config = Config(str(global_conf), repo_path=str(repo))
    assert config.get_agent_for_phase("design") == "quality-reviewer"
    # develop still from global
    assert config.get_agent_for_phase("develop") == "fast-coder"


def test_project_config_adds_agents(tmp_path):
    """Project config can add new agents that merge with global agents."""
    global_conf = tmp_path / "global.yaml"
    global_conf.write_text(NEW_CONFIG)

    repo = tmp_path / "repo"
    sc = repo / ".shadowcoder"
    sc.mkdir(parents=True)
    (sc / "config.yaml").write_text(
        "agents:\n  codex-coder:\n    type: codex\n    model: sonnet\n"
        "dispatch:\n  develop: codex-coder\n")

    config = Config(str(global_conf), repo_path=str(repo))
    assert config.get_agent_for_phase("develop") == "codex-coder"
    # global agents still exist
    ac = config.get_agent_config("fast-coder")
    assert ac["type"] == "claude_code"


def test_project_config_no_file(tmp_path):
    """No project config file → same as global."""
    global_conf = tmp_path / "global.yaml"
    global_conf.write_text(NEW_CONFIG)

    repo = tmp_path / "repo"
    repo.mkdir()

    config = Config(str(global_conf), repo_path=str(repo))
    assert config.get_agent_for_phase("design") == "fast-coder"


def test_project_config_overrides_review_policy(tmp_path):
    """Project config can override review_policy."""
    global_conf = tmp_path / "global.yaml"
    global_conf.write_text(NEW_CONFIG)

    repo = tmp_path / "repo"
    sc = repo / ".shadowcoder"
    sc.mkdir(parents=True)
    (sc / "config.yaml").write_text("review_policy:\n  max_review_rounds: 5\n")

    config = Config(str(global_conf), repo_path=str(repo))
    assert config.get_max_review_rounds() == 5
    # pass_threshold still from global
    assert config.get_pass_threshold() == "no_high_or_critical"
