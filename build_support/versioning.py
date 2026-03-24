from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

SCM_TAG_REGEX = r"^(?:v)?(?:[a-zA-Z0-9_]+@)?(?P<version>[0-9]+(?:\.[0-9]+)*)$"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _get_scm_version(project_root: Path) -> str:
    from setuptools_scm import get_version

    return get_version(
        root=str(project_root),
        relative_to=__file__,
        local_scheme="no-local-version",
        tag_regex=SCM_TAG_REGEX,
    )


def resolve_openviking_version(
    env: Mapping[str, str] | None = None, project_root: Path | None = None
) -> str:
    """Resolve the version shared by the Python package and bundled ov binary."""
    env = env or os.environ
    project_root = project_root or PROJECT_ROOT

    for key in ("OPENVIKING_VERSION", "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_OPENVIKING"):
        value = env.get(key, "").strip()
        if value:
            return value

    return _get_scm_version(project_root)
