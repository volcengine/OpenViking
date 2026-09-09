"""Configure the version shared by release build consumers."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Collection
from pathlib import Path

from build_support.versioning import resolve_openviking_version

RELEASE_TAG_PATTERN = re.compile(r"^v([0-9]+\.[0-9]+\.[0-9]+)$")


def build_environment(
    input_version: str, scm_version: str, head_tags: Collection[str]
) -> dict[str, str]:
    """Return the environment shared by Python and Rust build consumers."""
    match = RELEASE_TAG_PATTERN.fullmatch(input_version) if input_version else None
    if input_version and match is None:
        raise ValueError(f"Invalid release tag {input_version!r}; expected vX.Y.Z")
    if input_version and input_version not in head_tags:
        raise ValueError(f"Release tag {input_version!r} does not point at HEAD")

    version = match.group(1) if match else scm_version
    entries = {"OPENVIKING_VERSION": version}
    if match:
        entries["SETUPTOOLS_SCM_PRETEND_VERSION_FOR_OPENVIKING"] = version
    return entries


def main() -> None:
    input_version = os.environ.get("INPUT_VERSION", "").strip()
    head_tags = (
        subprocess.run(
            ["git", "tag", "--points-at", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        if input_version
        else ()
    )
    entries = build_environment(
        input_version,
        "" if input_version else resolve_openviking_version(),
        head_tags,
    )

    github_env = Path(os.environ["GITHUB_ENV"])
    with github_env.open("a", encoding="utf-8") as env_file:
        for key, value in entries.items():
            env_file.write(f"{key}={value}\n")
    print(f"Configured OpenViking version: {entries['OPENVIKING_VERSION']}")


if __name__ == "__main__":
    main()
