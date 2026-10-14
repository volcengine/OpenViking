"""Helpers for reading OpenViking source files from API checkers."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _find_repository_root() -> Path | None:
    """Return the source checkout root when the checker runs from a clone."""
    checker_dir = Path(__file__).resolve().parent
    for parent in (checker_dir, *checker_dir.parents):
        if (parent / "pyproject.toml").is_file() and (parent / "openviking").is_dir():
            return parent
    return None


def resolve_source_path(relative_path: str, module_name: str) -> Path:
    """Resolve a source file from a checkout, then from an installed package."""
    repository_root = _find_repository_root()
    if repository_root is not None:
        checkout_path = repository_root / relative_path
        if checkout_path.is_file():
            return checkout_path

    module_spec = importlib.util.find_spec(module_name)
    if module_spec is not None and module_spec.origin not in (None, "built-in"):
        installed_path = Path(module_spec.origin)
        if installed_path.is_file():
            return installed_path

    raise FileNotFoundError(
        f"Could not locate source for {module_name!r} "
        f"(expected {relative_path!r} in the repository or installed package)"
    )


def read_source(relative_path: str, module_name: str) -> str:
    """Read a source module using the resolved checkout or package path."""
    source_path = resolve_source_path(relative_path, module_name)
    print(f"Reading {source_path}...")
    print("=" * 80)
    return source_path.read_text(encoding="utf-8")
