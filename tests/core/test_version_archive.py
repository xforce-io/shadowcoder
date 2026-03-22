"""Tests for version archive (save_version)."""
import pytest
from shadowcoder.core.issue_store import IssueStore
from shadowcoder.core.config import Config


@pytest.fixture
def store(tmp_repo, tmp_config):
    config = Config(str(tmp_config))
    return IssueStore(str(tmp_repo), config)


def test_save_version_creates_file(store):
    store.create("Test")
    filename = store.save_version(1, "design", 1, "Design v1 content")
    assert filename == "design_r1.md"
    # File should exist
    vdir = store._versions_dir(1)
    assert (vdir / "design_r1.md").exists()
    assert (vdir / "design_r1.md").read_text() == "Design v1 content"


def test_save_version_multiple_rounds(store):
    store.create("Test")
    store.save_version(1, "design", 1, "v1")
    store.save_version(1, "design", 2, "v2")
    store.save_version(1, "design", 3, "v3")
    vdir = store._versions_dir(1)
    assert (vdir / "design_r1.md").read_text() == "v1"
    assert (vdir / "design_r2.md").read_text() == "v2"
    assert (vdir / "design_r3.md").read_text() == "v3"


def test_save_version_different_actions(store):
    store.create("Test")
    store.save_version(1, "design", 1, "design content")
    store.save_version(1, "develop", 1, "develop content")
    vdir = store._versions_dir(1)
    assert (vdir / "design_r1.md").exists()
    assert (vdir / "develop_r1.md").exists()


def test_save_version_does_not_affect_issue(store):
    store.create("Test")
    store.save_version(1, "design", 1, "archived content")
    issue = store.get(1)
    assert "设计" not in issue.sections  # main file unaffected


def test_versions_dir_not_counted_as_issue(store):
    store.create("A")
    store.save_version(1, "design", 1, "content")
    store.create("B")
    # list_all should return 2 issues, not confused by versions dir
    issues = store.list_all()
    assert len(issues) == 2
