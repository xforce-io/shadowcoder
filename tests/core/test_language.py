import pytest
from pathlib import Path
from shadowcoder.core.language import detect_language, PROFILES, LanguageProfile


def test_detect_python(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]")
    profile = detect_language(str(tmp_path))
    assert profile is not None
    assert profile.name == "python"
    assert "pytest" in profile.test_command


def test_detect_rust(tmp_path):
    (tmp_path / "Cargo.toml").write_text("[package]")
    profile = detect_language(str(tmp_path))
    assert profile.name == "rust"


def test_detect_go(tmp_path):
    (tmp_path / "go.mod").write_text("module test")
    profile = detect_language(str(tmp_path))
    assert profile.name == "go"


def test_detect_node(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    profile = detect_language(str(tmp_path))
    assert profile.name == "node"


def test_detect_make(tmp_path):
    (tmp_path / "Makefile").write_text("test:")
    profile = detect_language(str(tmp_path))
    assert profile.name == "make"


def test_detect_none(tmp_path):
    assert detect_language(str(tmp_path)) is None


def test_detect_priority_rust_over_make(tmp_path):
    """Rust Cargo.toml takes priority over Makefile."""
    (tmp_path / "Cargo.toml").write_text("[package]")
    (tmp_path / "Makefile").write_text("test:")
    profile = detect_language(str(tmp_path))
    assert profile.name == "rust"


def test_detect_setup_py(tmp_path):
    (tmp_path / "setup.py").write_text("")
    profile = detect_language(str(tmp_path))
    assert profile.name == "python"


def test_individual_test_cmd_format():
    """individual_test_cmd can be formatted with {name}."""
    for p in PROFILES:
        formatted = p.individual_test_cmd.format(name="test_foo")
        assert "test_foo" in formatted


def test_all_profiles_have_required_fields():
    for p in PROFILES:
        assert p.name
        assert p.marker_files
        assert p.test_command
        assert p.individual_test_cmd
