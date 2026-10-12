"""Resolve the server at setup; retain verified native embedding packages."""

from __future__ import annotations

import hashlib
import os
import platform
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urldefrag, urlparse

import httpx
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.tags import sys_tags
from packaging.utils import InvalidWheelFilename, parse_wheel_filename
from packaging.version import InvalidVersion, Version

_CPP = "https://github.com/abetlen/llama-cpp-python/releases/download/"
# macOS uses the working Metal archive. The same release's CPU macOS archive
# fails CRC validation and must not be substituted here.
_PACKAGES = {
    ("Darwin", "arm64"): (
        "v0.3.30-metal/llama_cpp_python-0.3.30-py3-none-macosx_11_0_arm64.whl",
        "4f5b385f31dbda502d7118aff0c07c9e98961fa0402c025b89ff3250db843eb3",
    ),
    ("Linux", "x86_64"): (
        "v0.3.30/llama_cpp_python-0.3.30-py3-none-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
        "591337ea31b6fea25f02da53db136041899a530eda1b2c2611f0bebe7c560a15",
    ),
    ("Linux", "aarch64"): (
        "v0.3.30/llama_cpp_python-0.3.30-py3-none-manylinux2014_aarch64.manylinux_2_17_aarch64.whl",
        "0720e1d122a4d1ad46607813bd34018ff46b0f50cd0a38fb93fad650b3b98ade",
    ),
    ("Windows", "x86_64"): (
        "v0.3.36/llama_cpp_python-0.3.36-py3-none-win_amd64.whl",
        "ae5e88a2cf464e5a2dde7c8898c2d5c71cc24d03cac6396bf7509456bde0b9fb",
    ),
}


def install_requirements(*, allow_source_build=False):
    from .quick_local import OPENVIKING_REQUIREMENT, SourceBuildRequired

    system = platform.system()
    machine = platform.machine().lower()
    machine = {"amd64": "x86_64", "arm64": "arm64" if system == "Darwin" else "aarch64"}.get(
        machine, machine
    )
    packages = _PACKAGES.get((system, machine))
    compatible = sys.maxsize > 2**32
    if system == "Darwin":
        compatible = compatible and Version(platform.mac_ver()[0] or "0") >= Version("14")
    elif system == "Linux":
        libc, version = platform.libc_ver()
        compatible = compatible and libc == "glibc" and Version(version or "0") >= Version("2.31")
    if not packages or not compatible:
        if not allow_source_build:
            raise SourceBuildRequired(
                "No reviewed Quick Local binary is available for this platform. "
                "Use a separate OpenViking server, or explicitly allow a source build "
                "in setup. A build needs native development tools and can take several minutes."
            )
        requirements = [OPENVIKING_REQUIREMENT, "llama-cpp-python==0.3.30"]
    else:
        cpp_path, cpp_hash = packages
        requirements = [
            OPENVIKING_REQUIREMENT,
            f"llama-cpp-python @ {_CPP}{cpp_path}#sha256={cpp_hash}",
        ]
    # The PM resolver otherwise chooses a LiteLLM release that excludes Python
    # 3.14. This constraint belongs only to OpenViking's private environment.
    return [*requirements, 'litellm==1.83.7; python_version >= "3.14"']


def resolve_server_requirement(*, allow_source_build=False):
    """Select a stable PyPI release only during explicit Quick Local setup."""
    from .quick_local import QuickLocalSetupError

    try:
        response = httpx.get("https://pypi.org/pypi/openviking/json", timeout=30)
        response.raise_for_status()
        return _select_server_requirement(
            response.json()["releases"],
            python_version=platform.python_version(),
            tags=list(sys_tags()),
            allow_source_build=allow_source_build,
        )
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise QuickLocalSetupError(
            "Could not check OpenViking releases on PyPI. Check network access and retry setup."
        ) from exc


def _select_server_requirement(releases, *, python_version, tags, allow_source_build=False):
    from .quick_local import OPENVIKING_REQUIREMENT, QuickLocalSetupError

    specifier = Requirement(OPENVIKING_REQUIREMENT).specifier
    ranked_tags = {tag: rank for rank, tag in enumerate(tags)}
    versions = []
    for raw_version, files in releases.items():
        try:
            version = Version(raw_version)
        except InvalidVersion:
            continue
        if version in specifier and not version.is_prerelease and not version.is_devrelease:
            versions.append((version, files))
    for version, files in sorted(versions, key=lambda item: item[0], reverse=True):
        wheels = []
        source_available = False
        for file in files:
            if file.get("yanked") or python_version not in SpecifierSet(
                file.get("requires_python") or ""
            ):
                continue
            if file.get("packagetype") == "sdist":
                source_available = True
                continue
            try:
                name, wheel_version, _build, wheel_tags = parse_wheel_filename(file["filename"])
            except (InvalidWheelFilename, KeyError):
                continue
            matches = wheel_tags.intersection(ranked_tags)
            if name == "openviking" and wheel_version == version and matches:
                wheels.append((min(ranked_tags[tag] for tag in matches), file))
        if wheels:
            file = min(wheels, key=lambda item: item[0])[1]
            url = file["url"]
            if (
                urlparse(url).scheme != "https"
                or urlparse(url).hostname != "files.pythonhosted.org"
            ):
                raise QuickLocalSetupError("OpenViking's release download must come from PyPI.")
            digest = file["digests"]["sha256"]
            return f"openviking[local-embed] @ {url}#sha256={digest}"
        if allow_source_build and source_available:
            return f"openviking[local-embed]=={version}"
    raise QuickLocalSetupError(
        "No compatible OpenViking release is available for this Python and platform. "
        "Use a separate server or explicitly allow a source build in setup."
    )


def verified_requirements(requirements: list[str], cache: Path) -> list[str]:
    """Give installers local wheels whose bytes match the reviewed pins.

    PM's project resolver does not enforce hashes in URL fragments. Checking
    before installation also protects the pre-PM path and cache reuse.
    """
    from .quick_local import QuickLocalSetupError, _prepare_private_directory

    verified = []
    for value in requirements:
        requirement = Requirement(value)
        if not requirement.url:
            verified.append(value)
            continue
        url, fragment = urldefrag(requirement.url)
        digest = parse_qs(fragment).get("sha256", [""])[0]
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise QuickLocalSetupError("Quick Local's native package needs a reviewed SHA-256 pin.")
        _prepare_private_directory(cache)
        directory = cache / digest
        _prepare_private_directory(directory)
        wheel = directory / Path(urlparse(url).path).name
        if not wheel.name.endswith(".whl") or wheel.is_symlink():
            raise QuickLocalSetupError("Quick Local's native package must be a private wheel file.")
        if not wheel.is_file() or _sha256(wheel) != digest:
            fd, temporary = tempfile.mkstemp(dir=directory, suffix=".download")
            try:
                checksum = hashlib.sha256()
                with os.fdopen(fd, "wb") as output:
                    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as response:
                        response.raise_for_status()
                        for chunk in response.iter_bytes(1024 * 1024):
                            checksum.update(chunk)
                            output.write(chunk)
                if checksum.hexdigest() != digest:
                    raise QuickLocalSetupError(
                        "Quick Local's native package failed SHA-256 verification. Installation was cancelled."
                    )
                os.replace(temporary, wheel)
            finally:
                Path(temporary).unlink(missing_ok=True)
        extras = f"[{','.join(sorted(requirement.extras))}]" if requirement.extras else ""
        marker = f" ; {requirement.marker}" if requirement.marker else ""
        verified.append(f"{requirement.name}{extras} @ {wheel.absolute().as_uri()}{marker}")
    return verified


def _sha256(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()
