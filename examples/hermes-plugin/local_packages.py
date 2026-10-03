"""Reviewed binary packages, verified before either installer receives them."""

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
from packaging.version import Version

_OV = "https://files.pythonhosted.org/packages/"
_CPP = "https://github.com/abetlen/llama-cpp-python/releases/download/"
# macOS uses the working Metal archive. The same release's CPU macOS archive
# fails CRC validation and must not be substituted here.
_PACKAGES = {
    ("Darwin", "arm64"): (
        "5c/f4/222b2eb392a09cf2e13648d4f2f509c6043d16385ebd48a457a453816245/openviking-0.4.22-cp310-abi3-macosx_14_0_arm64.whl",
        "ddfa3d23274cbd9afcdfaf1a1542b0a9e5e8286221a96bb7e40bd63e312d5bae",
        "v0.3.30-metal/llama_cpp_python-0.3.30-py3-none-macosx_11_0_arm64.whl",
        "4f5b385f31dbda502d7118aff0c07c9e98961fa0402c025b89ff3250db843eb3",
    ),
    ("Linux", "x86_64"): (
        "aa/02/69642eddcbced2d52dc8489a0e8f618b7ae11e6caede9b02fac0f6f54528/openviking-0.4.22-cp310-abi3-manylinux_2_31_x86_64.whl",
        "fa508fc9c84a32b3206d2f719c415bad5c50e88c1838fb19349881e087da72c8",
        "v0.3.30/llama_cpp_python-0.3.30-py3-none-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
        "591337ea31b6fea25f02da53db136041899a530eda1b2c2611f0bebe7c560a15",
    ),
    ("Linux", "aarch64"): (
        "29/ef/8bda981fae06066d2c462d051ceb6038043e776cac7b87f723fe65e34f17/openviking-0.4.22-cp310-abi3-manylinux_2_31_aarch64.whl",
        "480070405e5c64c0a7d8d1d2afba7e98a55f151a67c785e4e5d0d54320096afc",
        "v0.3.30/llama_cpp_python-0.3.30-py3-none-manylinux2014_aarch64.manylinux_2_17_aarch64.whl",
        "0720e1d122a4d1ad46607813bd34018ff46b0f50cd0a38fb93fad650b3b98ade",
    ),
    ("Windows", "x86_64"): (
        "3f/26/e0d259f1a9fb53d02b99487f232265f79965fefb9b956563612c7acf5ae5/openviking-0.4.22-cp310-abi3-win_amd64.whl",
        "cad1f3e4ea843c47178a8d5056df733b1e3f49d4e902b6f803b9e192320bb89b",
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
        ov_path, ov_hash, cpp_path, cpp_hash = packages
        requirements = [
            f"openviking[local-embed] @ {_OV}{ov_path}#sha256={ov_hash}",
            f"llama-cpp-python @ {_CPP}{cpp_path}#sha256={cpp_hash}",
        ]
    # The PM resolver otherwise chooses a LiteLLM release that excludes Python
    # 3.14. This constraint belongs only to OpenViking's private environment.
    return [*requirements, 'litellm==1.83.7; python_version >= "3.14"']


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
