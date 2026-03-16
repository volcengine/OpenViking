import importlib
import json
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pybind11
from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext

# Root directory of setup.py
SETUP_DIR = Path(__file__).resolve().parent
if str(SETUP_DIR) not in sys.path:
    sys.path.insert(0, str(SETUP_DIR))

# Import build configuration helper
get_host_engine_build_config = importlib.import_module(
    "build_support.x86_profiles"
).get_host_engine_build_config

# Locate required build tools (fallback to default names)
CMAKE_PATH = shutil.which("cmake") or "cmake"
C_COMPILER_PATH = shutil.which("gcc") or "gcc"
CXX_COMPILER_PATH = shutil.which("g++") or "g++"

# Directory containing engine source code
ENGINE_SOURCE_DIR = "src/"

# Host build configuration
ENGINE_BUILD_CONFIG = get_host_engine_build_config(platform.machine())


class OpenVikingBuildExt(build_ext):
    """
    Custom build extension for OpenViking.

    Responsibilities:
    - Build AGFS server artifacts
    - Build ov CLI binary
    - Build Python native extensions via CMake
    - Copy required binaries into package structure
    """

    def run(self):
        """Main build entry point."""
        self.build_agfs_artifacts()
        self.build_ov_cli_artifact()
        self.cmake_executable = CMAKE_PATH

        for ext in self.extensions:
            self.build_extension(ext)

    def _copy_artifact(self, src, dst):
        """Copy build artifact and preserve executable permissions."""
        print(f"Copying artifact from {src} to {dst}")

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))

        if sys.platform != "win32":
            os.chmod(str(dst), 0o755)

    def _copy_artifacts_to_build_lib(self, target_binary=None, target_lib=None):
        """Copy artifacts into build_lib for wheel packaging."""
        if self.build_lib:

            build_pkg_dir = Path(self.build_lib) / "openviking"

            if target_binary and target_binary.exists():
                self._copy_artifact(
                    target_binary,
                    build_pkg_dir / "bin" / target_binary.name
                )

            if target_lib and target_lib.exists():
                self._copy_artifact(
                    target_lib,
                    build_pkg_dir / "lib" / target_lib.name
                )

    def _require_artifact(self, artifact_path, artifact_name, stage_name):
        """Fail build if required artifact is missing."""
        if artifact_path.exists():
            return

        raise RuntimeError(
            f"[OpenViking build error] {stage_name} did not produce "
            f"required {artifact_name} at {artifact_path}"
        )

    def _run_stage_with_artifact_checks(
        self,
        stage_name,
        build_fn,
        required_artifacts,
        on_success=None
    ):
        """Run build stage and verify required outputs."""
        build_fn()

        for artifact_path, artifact_name in required_artifacts:
            self._require_artifact(
                artifact_path,
                artifact_name,
                stage_name
            )

        if on_success:
            on_success()

    def _resolve_cargo_target_dir(self, cargo_project_dir, env):
        """Resolve Cargo target directory."""
        configured_target_dir = env.get("CARGO_TARGET_DIR")

        if configured_target_dir:
            return Path(configured_target_dir).resolve()

        try:
            result = subprocess.run(
                [
                    "cargo",
                    "metadata",
                    "--format-version",
                    "1",
                    "--no-deps"
                ],
                cwd=str(cargo_project_dir),
                env=env,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            metadata = json.loads(result.stdout.decode("utf-8"))
            target_directory = metadata.get("target_directory")

            if target_directory:
                return Path(target_directory).resolve()

        except Exception as exc:
            print(
                "[Warning] Failed to resolve Cargo target directory: "
                f"{exc}"
            )

        return cargo_project_dir.parents[1] / "target"

    def build_agfs_artifacts(self):
        """Build AGFS server binary and binding library."""

        binary_name = (
            "agfs-server.exe"
            if sys.platform == "win32"
            else "agfs-server"
        )

        if sys.platform == "win32":
            lib_name = "libagfsbinding.dll"
        elif sys.platform == "darwin":
            lib_name = "libagfsbinding.dylib"
        else:
            lib_name = "libagfsbinding.so"

        agfs_server_dir = Path(
            "third_party/agfs/agfs-server"
        ).resolve()

        agfs_bin_dir = Path("openviking/bin").resolve()
        agfs_lib_dir = Path("openviking/lib").resolve()

        agfs_target_binary = agfs_bin_dir / binary_name
        agfs_target_lib = agfs_lib_dir / lib_name

        self._run_stage_with_artifact_checks(
            "AGFS build",
            lambda: self._build_agfs_artifacts_impl(
                agfs_server_dir,
                binary_name,
                lib_name,
                agfs_target_binary,
                agfs_target_lib,
            ),
            [
                (agfs_target_binary, binary_name),
                (agfs_target_lib, lib_name),
            ],
            on_success=lambda: self._copy_artifacts_to_build_lib(
                agfs_target_binary,
                agfs_target_lib
            ),
        )

    def build_ov_cli_artifact(self):
        """Build ov Rust CLI binary."""

        binary_name = (
            "ov.exe"
            if sys.platform == "win32"
            else "ov"
        )

        ov_cli_dir = Path("crates/ov_cli").resolve()

        ov_target_binary = (
            Path("openviking/bin").resolve() / binary_name
        )

        self._run_stage_with_artifact_checks(
            "ov CLI build",
            lambda: self._build_ov_cli_artifact_impl(
                ov_cli_dir,
                binary_name,
                ov_target_binary
            ),
            [(ov_target_binary, binary_name)],
            on_success=lambda: self._copy_artifacts_to_build_lib(
                ov_target_binary,
                None
            ),
        )

    def build_extension(self, ext):
        """Build Python native extension using CMake."""

        if getattr(self, "_engine_extensions_built", False):
            return

        ext_fullpath = Path(
            self.get_ext_fullpath(ext.name)
        )

        ext_dir = ext_fullpath.parent.resolve()

        build_dir = (
            Path(self.build_temp) / "cmake_build"
        )

        build_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        self._run_stage_with_artifact_checks(
            "CMake build",
            lambda: self._build_extension_impl(
                ext_fullpath,
                ext_dir,
                build_dir
            ),
            [
                (
                    ext_fullpath,
                    f"native extension '{ext.name}'"
                )
            ],
        )

        self._engine_extensions_built = True

    def _build_extension_impl(
        self,
        ext_fullpath,
        ext_dir,
        build_dir
    ):
        """Invoke CMake build."""

        py_ext_suffix = (
            sysconfig.get_config_var("EXT_SUFFIX")
            or ext_fullpath.suffix
        )

        cmake_args = [
            f"-S{Path(ENGINE_SOURCE_DIR).resolve()}",
            f"-B{build_dir}",
            "-DCMAKE_BUILD_TYPE=Release",

            f"-DOV_PY_OUTPUT_DIR={ext_dir}",
            f"-DOV_PY_EXT_SUFFIX={py_ext_suffix}",

            f"-DOV_X86_BUILD_VARIANTS={';'.join(ENGINE_BUILD_CONFIG.cmake_variants)}",

            "-DCMAKE_VERBOSE_MAKEFILE=ON",
            "-DCMAKE_INSTALL_RPATH=$ORIGIN",

            f"-DPython3_EXECUTABLE={sys.executable}",
            f"-DPython3_INCLUDE_DIRS={sysconfig.get_path('include')}",
            f"-DPython3_LIBRARIES={sysconfig.get_config_vars().get('LIBRARY')}",

            f"-Dpybind11_DIR={pybind11.get_cmake_dir()}",

            f"-DCMAKE_C_COMPILER={C_COMPILER_PATH}",
            f"-DCMAKE_CXX_COMPILER={CXX_COMPILER_PATH}",
        ]

        if sys.platform == "darwin":

            cmake_args.append(
                "-DCMAKE_OSX_DEPLOYMENT_TARGET=10.15"
            )

            target_arch = os.environ.get(
                "CMAKE_OSX_ARCHITECTURES"
            )

            if target_arch:
                cmake_args.append(
                    f"-DCMAKE_OSX_ARCHITECTURES={target_arch}"
                )

        elif sys.platform == "win32":

            cmake_args.extend(
                ["-G", "MinGW Makefiles"]
            )

        self.spawn(
            [self.cmake_executable] + cmake_args
        )

        cpu_count = max(
            1,
            os.cpu_count() or 4
        )

        build_args = [
            "--build",
            str(build_dir),
            "--config",
            "Release",
            f"-j{cpu_count}"
        ]

        self.spawn(
            [self.cmake_executable] + build_args
        )


setup(

    ext_modules=[
        Extension(
            name=ENGINE_BUILD_CONFIG.primary_extension,
            sources=[],
        )
    ],

    cmdclass={
        "build_ext": OpenVikingBuildExt,
    },

    package_data={
        "openviking": [

            "bin/agfs-server",
            "bin/agfs-server.exe",

            "lib/libagfsbinding.so",
            "lib/libagfsbinding.dylib",
            "lib/libagfsbinding.dll",

            "bin/ov",
            "bin/ov.exe",

            "storage/vectordb/engine/*.so",
            "storage/vectordb/engine/*.pyd",
        ],
    },

    include_package_data=True,
)
