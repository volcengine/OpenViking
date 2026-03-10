import os
import shutil
import subprocess
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
    """Custom CMake build extension that builds AGFS, ov CLI and C++ extensions."""

    def run(self):
        self.build_agfs()
        self.build_ov()
        self.cmake_executable = CMAKE_PATH

        for ext in self.extensions:
            self.build_extension(ext)

    def _copy_binary(self, src, dst):
        """Helper to copy binary and set permissions."""
        print(f"Copying binary from {src} to {dst}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))
        if sys.platform != "win32":
            os.chmod(str(dst), 0o755)

    def _ensure_build_lib_copied(self, target_binary=None, target_lib=None):
        """Ensure binaries are copied to the build directory (where wheel is packaged from)."""
        if self.build_lib:
            build_pkg_dir = Path(self.build_lib) / "openviking"
            if target_binary and target_binary.exists():
                self._copy_binary(target_binary, build_pkg_dir / "bin" / target_binary.name)
            if target_lib and target_lib.exists():
                # Libs go to lib/ as expected by agfs_utils.py
                self._copy_binary(target_lib, build_pkg_dir / "lib" / target_lib.name)

    def build_agfs(self):
        """Build AGFS server and binding library."""
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
        agfs_lib_dir = Path("openviking/lib").resolve()
        agfs_target_binary = agfs_bin_dir / binary_name
        agfs_target_lib = agfs_lib_dir / lib_name

        # 1. Check for pre-built binaries in a specified directory
        prebuilt_dir = os.environ.get("OV_PREBUILT_BIN_DIR")
        if prebuilt_dir:
            prebuilt_path = Path(prebuilt_dir).resolve()
            print(f"Checking for pre-built AGFS binaries in {prebuilt_path}...")
            src_bin = prebuilt_path / binary_name
            src_lib = prebuilt_path / lib_name

            if src_bin.exists():
                self._copy_binary(src_bin, agfs_target_binary)
            if src_lib.exists():
                self._copy_binary(src_lib, agfs_target_lib)

            if agfs_target_binary.exists() and agfs_target_lib.exists():
                print(f"[OK] Used pre-built AGFS binaries from {prebuilt_dir}")
                self._ensure_build_lib_copied(agfs_target_binary, agfs_target_lib)
                return

        # 2. Skip build if requested and binaries exist
        if os.environ.get("OV_SKIP_AGFS_BUILD") == "1":
            if agfs_target_binary.exists() and agfs_target_lib.exists():
                print("[OK] Skipping AGFS build, using existing binaries")
                self._ensure_build_lib_copied(agfs_target_binary, agfs_target_lib)
                return
            else:
                print("[Warning] OV_SKIP_AGFS_BUILD=1 but binaries not found. Will try to build.")

        # 3. Try to build from source
        if agfs_server_dir.exists() and shutil.which("go"):
            print("Building AGFS from source...")

            # Build server
            try:
                print(f"Building AGFS server: {binary_name}")
                env = os.environ.copy()
                if "GOOS" in env or "GOARCH" in env:
                    print(f"Cross-compiling with GOOS={env.get('GOOS')} GOARCH={env.get('GOARCH')}")

                build_args = (
                    ["go", "build", "-o", f"build/{binary_name}", "cmd/server/main.go"]
                    if sys.platform == "win32"
                    else ["make", "build"]
                )

                result = subprocess.run(
                    build_args,
                    cwd=str(agfs_server_dir),
                    env=env,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                if result.stdout:
                    print(f"Build stdout: {result.stdout.decode('utf-8', errors='replace')}")
                if result.stderr:
                    print(f"Build stderr: {result.stderr.decode('utf-8', errors='replace')}")

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
                print(f"[Error] {error_msg}")
                raise RuntimeError(error_msg)

            # Build binding library
            try:
                print(f"Building AGFS binding library: {lib_name}")
                # Use CGO_ENABLED=1 for shared library
                env = os.environ.copy()
                env["CGO_ENABLED"] = "1"

                lib_build_args = ["make", "build-lib"]

                result = subprocess.run(
                    lib_build_args,
                    cwd=str(agfs_server_dir),
                    env=env,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                if result.stdout:
                    print(f"Build stdout: {result.stdout.decode('utf-8', errors='replace')}")
                if result.stderr:
                    print(f"Build stderr: {result.stderr.decode('utf-8', errors='replace')}")

                agfs_built_lib = agfs_server_dir / "build" / lib_name
                if agfs_built_lib.exists():
                    self._copy_binary(agfs_built_lib, agfs_target_lib)
                    print("[OK] AGFS binding library built successfully")
                else:
                    print(f"[Warning] Binding library not found at {agfs_built_lib}")
            except Exception as e:
                error_msg = f"Failed to build AGFS binding library: {e}"
                if isinstance(e, subprocess.CalledProcessError):
                    if e.stdout:
                        error_msg += f"\nBuild stdout: {e.stdout.decode('utf-8', errors='replace')}"
                    if e.stderr:
                        error_msg += f"\nBuild stderr: {e.stderr.decode('utf-8', errors='replace')}"
                print(f"[Error] {error_msg}")
                raise RuntimeError(error_msg)

        else:
            if agfs_target_binary.exists():
                print(
                    f"[Info] Go compiler not found, but AGFS binary exists at {agfs_target_binary}. Skipping build."
                )
            elif not agfs_server_dir.exists():
                print(f"[Warning] AGFS source directory not found at {agfs_server_dir}")
            else:
                print("[Warning] Go compiler not found. Cannot build AGFS from source.")

        # Final check and copy to build dir
        self._ensure_build_lib_copied(agfs_target_binary, agfs_target_lib)

    def build_ov(self):
        """Build or copy ov Rust CLI."""
        binary_name = "ov.exe" if sys.platform == "win32" else "ov"
        ov_cli_dir = Path("crates/ov_cli").resolve()

        agfs_bin_dir = Path("openviking/bin").resolve()
        ov_target_binary = agfs_bin_dir / binary_name

        # 1. Check for pre-built
        prebuilt_dir = os.environ.get("OV_PREBUILT_BIN_DIR")
        if prebuilt_dir:
            src_bin = Path(prebuilt_dir).resolve() / binary_name
            if src_bin.exists():
                self._copy_binary(src_bin, ov_target_binary)
                self._ensure_build_lib_copied(ov_target_binary, None)
                return

        # 2. Skip build if requested
        if os.environ.get("OV_SKIP_OV_BUILD") == "1":
            if ov_target_binary.exists():
                print("[OK] Skipping ov CLI build, using existing binary")
                self._ensure_build_lib_copied(ov_target_binary, None)
                return
            else:
                print("[Warning] OV_SKIP_OV_BUILD=1 but binary not found. Will try to build.")

        # 3. Build from source
        if ov_cli_dir.exists() and shutil.which("cargo"):
            print("Building ov CLI from source...")
            try:
                env = os.environ.copy()
                build_args = ["cargo", "build", "--release"]

                # Support cross-compilation via CARGO_BUILD_TARGET
                target = env.get("CARGO_BUILD_TARGET")
                if target:
                    print(f"Cross-compiling with CARGO_BUILD_TARGET={target}")
                    build_args.extend(["--target", target])

                result = subprocess.run(
                    build_args,
                    cwd=str(ov_cli_dir),
                    env=env,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                if result.stdout:
                    print(f"Build stdout: {result.stdout.decode('utf-8', errors='replace')}")
                if result.stderr:
                    print(f"Build stderr: {result.stderr.decode('utf-8', errors='replace')}")

                # Find built binary
                if target:
                    built_bin = ov_cli_dir / "target" / target / "release" / binary_name
                else:
                    built_bin = ov_cli_dir / "target" / "release" / binary_name

                if built_bin.exists():
                    self._copy_binary(built_bin, ov_target_binary)
                    print("[OK] ov CLI built successfully from source")
                else:
                    print(f"[Warning] Built ov binary not found at {built_bin}")
            except Exception as e:
                error_msg = f"Failed to build ov CLI from source: {e}"
                if isinstance(e, subprocess.CalledProcessError):
                    if e.stdout:
                        error_msg += f"\nBuild stdout: {e.stdout.decode('utf-8', errors='replace')}"
                    if e.stderr:
                        error_msg += f"\nBuild stderr: {e.stderr.decode('utf-8', errors='replace')}"
                print(f"[Error] {error_msg}")
                raise RuntimeError(error_msg)
        else:
            if not ov_cli_dir.exists():
                print(f"[Warning] ov CLI source directory not found at {ov_cli_dir}")
            else:
                print("[Warning] Cargo not found. Cannot build ov CLI from source.")

        # Final check and copy to build dir
        self._ensure_build_lib_copied(ov_target_binary, None)

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
            target_arch = os.environ.get("CMAKE_OSX_ARCHITECTURES")
            if target_arch:
                cmake_args.append(f"-DCMAKE_OSX_ARCHITECTURES={target_arch}")
        elif sys.platform == "win32":
            cmake_args.extend(["-G", "MinGW Makefiles"])

        self.spawn([self.cmake_executable] + cmake_args)

        build_args = ["--build", str(build_dir), "--config", "Release", f"-j{os.cpu_count() or 4}"]
        self.spawn([self.cmake_executable] + build_args)


setup(
    # install_requires=[
    #     f"pyagfs @ file://localhost/{os.path.abspath('third_party/agfs/agfs-sdk/python')}"
    # ],
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
            "lib/libagfsbinding.so",
            "lib/libagfsbinding.dylib",
            "lib/libagfsbinding.dll",
            "bin/ov",
            "bin/ov.exe",
        ],
    },
    include_package_data=True,
)
