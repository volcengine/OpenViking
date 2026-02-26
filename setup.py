import os
import shutil
import sys
import sysconfig
from pathlib import Path

import pybind11
from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext

CMAKE_PATH = shutil.which("cmake") or "cmake"
C_COMPILER_PATH = shutil.which("gcc") or "gcc"
CXX_COMPILER_PATH = shutil.which("g++") or "g++"
ENGINE_SOURCE_DIR = "src/"


class CMakeBuildExtension(build_ext):
    """Custom CMake build extension that builds AGFS and C++ extensions."""

    def run(self):
        self.build_agfs()
        self.cmake_executable = CMAKE_PATH

        for ext in self.extensions:
            self.build_extension(ext)

    def _copy_binary(self, src, dst):
        """Helper to copy binary and set permissions."""
        print(f"Copying AGFS binary from {src} to {dst}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))
        if sys.platform != "win32":
            os.chmod(str(dst), 0o755)

    def build_agfs(self):
        """Build AGFS server and binding library from source."""
        # Paths
        binary_name = "agfs-server.exe" if sys.platform == "win32" else "agfs-server"
        if sys.platform == "win32":
            lib_name = "libagfsbinding.dll"
        elif sys.platform == "darwin":
            lib_name = "libagfsbinding.dylib"
        else:
            lib_name = "libagfsbinding.so"

        agfs_server_dir = Path("third_party/agfs/agfs-server").resolve()

        # Target in source tree (for development/install)
        agfs_bin_dir = Path("openviking/bin").resolve()
        agfs_target_binary = agfs_bin_dir / binary_name
        agfs_target_lib = agfs_bin_dir / lib_name

        # 1. Try to build from source
        if agfs_server_dir.exists() and shutil.which("go"):
            print("Building AGFS from source...")
            import subprocess

            # Build server
            try:
                print(f"Building AGFS server: {binary_name}")
                build_args = (
                    ["go", "build", "-o", f"build/{binary_name}", "cmd/server/main.go"]
                    if sys.platform == "win32"
                    else ["make", "build"]
                )

                subprocess.run(
                    build_args,
                    cwd=str(agfs_server_dir),
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )

                agfs_built_binary = agfs_server_dir / "build" / binary_name
                if agfs_built_binary.exists():
                    self._copy_binary(agfs_built_binary, agfs_target_binary)
                    print("[OK] AGFS server built successfully from source")
                else:
                    raise FileNotFoundError(
                        f"Build succeeded but binary not found at {agfs_built_binary}"
                    )
            except (subprocess.CalledProcessError, Exception) as e:
                error_msg = f"Failed to build AGFS server from source: {e}"
                if isinstance(e, subprocess.CalledProcessError):
                    if e.stdout:
                        error_msg += (
                            f"\nBuild stdout:\n{e.stdout.decode('utf-8', errors='replace')}"
                        )
                    if e.stderr:
                        error_msg += (
                            f"\nBuild stderr:\n{e.stderr.decode('utf-8', errors='replace')}"
                        )
                print(f"[Warning] {error_msg}")

            # Build binding library
            try:
                print(f"Building AGFS binding library: {lib_name}")
                # Use CGO_ENABLED=1 for shared library
                env = os.environ.copy()
                env["CGO_ENABLED"] = "1"

                pybinding_dir = agfs_server_dir / "cmd/pybinding"
                lib_build_args = [
                    "go",
                    "build",
                    "-buildmode=c-shared",
                    "-o",
                    f"build/{lib_name}",
                    ".",
                ]

                subprocess.run(
                    lib_build_args,
                    cwd=str(pybinding_dir),
                    env=env,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )

                agfs_built_lib = pybinding_dir / "build" / lib_name
                if agfs_built_lib.exists():
                    self._copy_binary(agfs_built_lib, agfs_target_lib)
                    print("[OK] AGFS binding library built successfully")
                else:
                    print(f"[Warning] Binding library not found at {agfs_built_lib}")
            except Exception as e:
                print(f"[Warning] Failed to build AGFS binding library: {e}")
                if isinstance(e, subprocess.CalledProcessError):
                    if e.stdout:
                        print(f"Build stdout: {e.stdout.decode('utf-8', errors='replace')}")
                    if e.stderr:
                        print(f"Build stderr: {e.stderr.decode('utf-8', errors='replace')}")

        else:
            if not agfs_server_dir.exists():
                raise FileNotFoundError(f"AGFS source directory not found at {agfs_server_dir}")
            else:
                raise RuntimeError("Go compiler not found. Please install Go to build AGFS.")

        # 2. Ensure binaries are copied to the build directory (where wheel is packaged from)
        if self.build_lib:
            agfs_bin_dir_build = Path(self.build_lib) / "openviking/bin"
            if agfs_target_binary.exists():
                self._copy_binary(agfs_target_binary, agfs_bin_dir_build / binary_name)
            if agfs_target_lib.exists():
                self._copy_binary(agfs_target_lib, agfs_bin_dir_build / lib_name)

    def build_extension(self, ext):
        """Build a single C++ extension module using CMake."""
        ext_fullpath = Path(self.get_ext_fullpath(ext.name))
        ext_dir = ext_fullpath.parent.resolve()
        build_dir = Path(self.build_temp) / "cmake_build"
        build_dir.mkdir(parents=True, exist_ok=True)

        cmake_args = [
            f"-S{Path(ENGINE_SOURCE_DIR).resolve()}",
            f"-B{build_dir}",
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DPY_OUTPUT_DIR={ext_dir}",
            "-DCMAKE_VERBOSE_MAKEFILE=ON",
            "-DCMAKE_INSTALL_RPATH=$ORIGIN",
            f"-DPython3_EXECUTABLE={sys.executable}",
            f"-DPython3_INCLUDE_DIRS={sysconfig.get_path('include')}",
            f"-DPython3_LIBRARIES={sysconfig.get_config_vars().get('LIBRARY')}",
            f"-Dpybind11_DIR={pybind11.get_cmake_dir()}",
            f"-DCMAKE_C_COMPILER={C_COMPILER_PATH}",
            f"-DCMAKE_CXX_COMPILER={CXX_COMPILER_PATH}",
            f"-DOV_X86_SIMD_LEVEL={os.environ.get('OV_X86_SIMD_LEVEL', 'AVX2')}",
        ]

        if sys.platform == "darwin":
            cmake_args.append("-DCMAKE_OSX_DEPLOYMENT_TARGET=10.15")
        elif sys.platform == "win32":
            cmake_args.extend(["-G", "MinGW Makefiles"])

        self.spawn([self.cmake_executable] + cmake_args)

        build_args = ["--build", str(build_dir), "--config", "Release", f"-j{os.cpu_count() or 4}"]
        self.spawn([self.cmake_executable] + build_args)


setup(
    ext_modules=[
        Extension(
            name="openviking.storage.vectordb.engine",
            sources=[],
        )
    ],
    cmdclass={
        "build_ext": CMakeBuildExtension,
    },
    package_data={
        "openviking": [
            "bin/agfs-server",
            "bin/agfs-server.exe",
            "bin/libagfsbinding.so",
            "bin/libagfsbinding.dylib",
            "bin/libagfsbinding.dll",
        ],
    },
    include_package_data=True,
)
