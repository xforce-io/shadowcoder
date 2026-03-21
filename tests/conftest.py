import pytest
import tempfile
import os
from pathlib import Path


@pytest.fixture
def tmp_repo(tmp_path):
    """Create a temporary git repo for testing."""
    import subprocess
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"],
                   cwd=str(tmp_path), check=True, capture_output=True)
    return tmp_path


@pytest.fixture
def tmp_config(tmp_path):
    """Create a temporary config file with default values."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("""\
agents:
  default: claude-code
  available:
    claude-code:
      type: claude_code
    codex:
      type: codex

reviewers:
  design: [claude-code]
  develop: [claude-code]

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
""")
    return config_path
