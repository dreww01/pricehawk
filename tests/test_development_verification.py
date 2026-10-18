"""Verification tests for development tooling, linter, and type-checker configuration."""

import tomllib
from pathlib import Path


def test_development_docs_declare_linter_and_type_checker() -> None:
    """DEVELOPMENT.md must document configured ruff and mypy verification commands."""
    dev_doc = Path("docs/DEVELOPMENT.md").read_text(encoding="utf-8")
    assert (
        "does not declare a dedicated formatter, linter, or static type checker dependency"
        not in dev_doc
    ), "DEVELOPMENT.md still claims no dedicated linter or type-checker is declared"

    assert "uv run ruff check" in dev_doc, "DEVELOPMENT.md must document ruff check command"
    assert "uv run mypy" in dev_doc, "DEVELOPMENT.md must document mypy verification command"


def test_pyproject_configures_ruff_and_mypy() -> None:
    """pyproject.toml must declare and configure ruff and mypy."""
    pyproject_path = Path("pyproject.toml")
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

    dev_deps = data.get("dependency-groups", {}).get("dev", [])
    has_ruff = any("ruff" in dep for dep in dev_deps)
    has_mypy = any("mypy" in dep for dep in dev_deps)
    assert has_ruff, "ruff must be declared in dev dependencies"
    assert has_mypy, "mypy must be declared in dev dependencies"

    tools = data.get("tool", {})
    assert "ruff" in tools, "tool.ruff must be configured in pyproject.toml"
    assert "mypy" in tools, "tool.mypy must be configured in pyproject.toml"
