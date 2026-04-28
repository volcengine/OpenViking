import importlib
import json
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext

try:
    from wheel.bdist_wheel import bdist_wheel
except ImportError:  # pragma: no cover - local build_ext may not have wheel installed
    bdist_wheel = None

SETUP_DIR = Path(__file__).resolve().parent
if str(SETUP_DIR) not in sys.path:
    sys.path.insert(0, str(SETUP_DIR))

get_host_engine_build_config = importlib.import_module(
    "build_support.x86_profiles"
).get_host_engine_build_config
resolve_openviking_version = importlib.import_module(
    "build_support.versioning"
).resolve_openviking_version

CMAKE_PATH = shutil.which("cmake") or "cmake"
C_COMPILER_PATH = os.environ.get("CC") or shutil.which("gcc") or "gcc"
CXX_COMPILER_PATH = os.environ.get("CXX") or shutil.which("g++") or "g++"
ENGINE_SOURCE_DIR = "src/"
ENGINE_BUILD_CONFIG = get_host_engine_build_config(platform.machine())


def _sanitize_native_build_env(env):
    """Keep Rust native builds from accidentally linking against Linuxbrew libs.

    On older glibc systems, Homebrew-provided native libraries can require a newer
    libc than the host linker/runtime supports. When pkg-config resolves xz/bzip2
    from Linuxbrew, Cargo inherits those library search paths and link fails.
    """

    sanitized_env = env.copy()

    pkg_config = sanitized_env.get("PKG_CONFIG") or shutil.which("pkg-config")
    if pkg_config and "linuxbrew" in os.path.realpath(pkg_config).lower():
        system_pkg_config = "/usr/bin/pkg-config"
        if Path(system_pkg_config).exists():
            sanitized_env["PKG_CONFIG"] = system_pkg_config

    for key in ("PKG_CONFIG_PATH", "LIBRARY_PATH", "LD_LIBRARY_PATH"):
        value = sanitized_env.get(key)
        if not value:
            continue
        kept_paths = [
            path
            for path in value.split(os.pathsep)
            if path and "linuxbrew" not in os.path.realpath(path).lower()
        ]
        if kept_paths:
            sanitized_env[key] = os.pathsep.join(kept_paths)
        else:
            sanitized_env.pop(key, None)

    return sanitized_env


def _get_windows_python_sabi_library() -> Path:
    """Return the stable-ABI Python library path for Windows abi3 extensions."""
    candidate_roots = []
    for raw_root in (
        sys.base_prefix,
        sys.base_exec_prefix,
        sysconfig.get_config_var("installed_base"),
        sysconfig.get_config_var("base"),
    ):
        if not raw_root:
            continue
        candidate_root = Path(raw_root).resolve()
        if candidate_root not in candidate_roots:
            candidate_roots.append(candidate_root)

    candidate_paths = []
    for root in candidate_roots:
        candidate_paths.extend(
            [
                root / "libs" / "python3.lib",
                root / "python3.dll",
            ]
        )

    for candidate_path in candidate_paths:
        if candidate_path.exists():
            return candidate_path

    searched = ", ".join(str(path) for path in candidate_paths) or "<none>"
    raise RuntimeError(
        "Could not locate the Windows stable-ABI Python library for abi3 engine modules. "
        f"Searched: {searched}"
    )


class OpenVikingBuildExt(build_ext):
    """Build OpenViking runtime artifacts and Python native extensions."""

    def run(self):
        self.build_ov_cli_artifact()
        self.build_ragfs_python_artifact()
        self.cmake_executable = CMAKE_PATH

        for ext in self.extensions:
            self.build_extension(ext)

    def _copy_artifact(self, src, dst):
        """Copy a build artifact into the package tree and preserve executability."""
        print(f"Copying artifact from {src} to {dst}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))
        if sys.platform != "win32":
            os.chmod(str(dst), 0o755)

    def _copy_artifacts_to_build_lib(self, target_binary=None, target_lib=None):
        """Copy built artifacts into build_lib so wheel packaging can include them."""
        if self.build_lib:
            build_pkg_dir = Path(self.build_lib) / "openviking"
            if target_binary and target_binary.exists():
                self._copy_artifact(target_binary, build_pkg_dir / "bin" / target_binary.name)
            if target_lib and target_lib.exists():
                self._copy_artifact(target_lib, build_pkg_dir / "lib" / target_lib.name)

    def _require_artifact(self, artifact_path, artifact_name, stage_name):
        """Abort the build immediately when a required artifact is missing."""
        if artifact_path.exists():
            return
        raise RuntimeError(
            f"{stage_name} did not produce required {artifact_name} at {artifact_path}"
        )

    def _run_stage_with_artifact_checks(
        self, stage_name, build_fn, required_artifacts, on_success=None
    ):
        """Run a build stage and always validate its required outputs on normal return."""
        build_fn()
        for artifact_path, artifact_name in required_artifacts:
            self._require_artifact(artifact_path, artifact_name, stage_name)
        if on_success:
            on_success()

    def _resolve_cargo_target_dir(self, cargo_project_dir, env):
        """Resolve the Cargo target directory for workspace and overridden builds."""
        configured_target_dir = env.get("CARGO_TARGET_DIR")
        if configured_target_dir:
            return Path(configured_target_dir).resolve()

        try:
            result = subprocess.run(
                ["cargo", "metadata", "--format-version", "1", "--no-deps"],
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
            print(f"[Warning] Failed to resolve Cargo target directory via metadata: {exc}")

        return cargo_project_dir.parents[1] / "target"

    def build_ov_cli_artifact(self):
        """Build or reuse the ov Rust CLI binary."""
        binary_name = "ov.exe" if sys.platform == "win32" else "ov"
        ov_cli_dir = Path("crates/ov_cli").resolve()
        ov_target_binary = Path("openviking/bin").resolve() / binary_name

        self._run_stage_with_artifact_checks(
            "ov CLI build",
            lambda: self._build_ov_cli_artifact_impl(ov_cli_dir, binary_name, ov_target_binary),
            [(ov_target_binary, binary_name)],
            on_success=lambda: self._copy_artifacts_to_build_lib(ov_target_binary, None),
        )

    def _build_ov_cli_artifact_impl(self, ov_cli_dir, binary_name, ov_target_binary):
        """Implement ov CLI building without final artifact checks."""

        prebuilt_dir = os.environ.get("OV_PREBUILT_BIN_DIR")
        if prebuilt_dir:
            src_bin = Path(prebuilt_dir).resolve() / binary_name
            if src_bin.exists():
                self._copy_artifact(src_bin, ov_target_binary)
                return

        if os.environ.get("OV_SKIP_OV_BUILD") == "1":
            if ov_target_binary.exists():
                print("[OK] Skipping ov CLI build, using existing binary")
                return
            print("[Warning] OV_SKIP_OV_BUILD=1 but binary is missing. Will try to build.")

        if ov_cli_dir.exists() and shutil.which("cargo"):
            print("Building ov CLI from source...")
            try:
                env = _sanitize_native_build_env(os.environ.copy())
                env["OPENVIKING_VERSION"] = resolve_openviking_version(
                    env=env, project_root=SETUP_DIR
                )
                build_args = ["cargo", "build", "--release"]
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

                cargo_target_dir = self._resolve_cargo_target_dir(ov_cli_dir, env)
                if target:
                    built_bin = cargo_target_dir / target / "release" / binary_name
                else:
                    built_bin = cargo_target_dir / "release" / binary_name

                self._require_artifact(built_bin, binary_name, "ov CLI build")
                self._copy_artifact(built_bin, ov_target_binary)
                print("[OK] ov CLI built successfully from source")
            except Exception as exc:
                error_msg = f"Failed to build ov CLI from source: {exc}"
                if isinstance(exc, subprocess.CalledProcessError):
                    if exc.stdout:
                        error_msg += (
                            f"\nBuild stdout:\n{exc.stdout.decode('utf-8', errors='replace')}"
                        )
                    if exc.stderr:
                        error_msg += (
                            f"\nBuild stderr:\n{exc.stderr.decode('utf-8', errors='replace')}"
                        )
                print(f"[Error] {error_msg}")
                raise RuntimeError(error_msg)
        else:
            if ov_target_binary.exists():
                print("[Info] ov CLI binary already exists locally. Skipping source build.")
            elif not ov_cli_dir.exists():
                print(f"[Warning] ov CLI source directory not found at {ov_cli_dir}")
            else:
                print("[Warning] Cargo not found. Cannot build ov CLI from source.")

    def build_ragfs_python_artifact(self):
        """Build ragfs-python (Rust RAGFS binding) via maturin and copy the native
        extension into ``openviking/lib/`` so it ships inside the openviking wheel.
        """
        require_ragfs_artifact = self._should_require_ragfs_artifact()
        ragfs_python_dir = Path("crates/ragfs-python").resolve()
        ragfs_lib_dir = Path("openviking/lib").resolve()

        if not ragfs_python_dir.exists():
            message = "ragfs-python source directory not found."
            if require_ragfs_artifact:
                raise RuntimeError(message)
            print(f"[Info] {message} Skipping.")
            return

        if os.environ.get("OV_SKIP_RAGFS_BUILD") == "1":
            message = "Skipping ragfs-python build (OV_SKIP_RAGFS_BUILD=1)"
            if require_ragfs_artifact:
                raise RuntimeError(f"{message} is incompatible with required wheel artifacts.")
            print(f"[OK] {message}")
            return

        if importlib.util.find_spec("maturin") is None:
            message = (
                "maturin not found. ragfs-python (Rust binding) will not be built.\n"
                "       Install maturin to enable: pip install maturin"
            )
            if require_ragfs_artifact:
                raise RuntimeError(message)
            print(f"[SKIP] {message}")
            return

        import tempfile
        import zipfile

        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                print("Building ragfs-python (Rust RAGFS binding) via maturin...")
                env = _sanitize_native_build_env(os.environ.copy())
                build_args = [
                    sys.executable,
                    "-m",
                    "maturin",
                    "build",
                    "--release",
                    "--out",
                    tmpdir,
                ]
                # Respect CARGO_BUILD_TARGET for cross-compilation
                target = env.get("CARGO_BUILD_TARGET")
                if target:
                    build_args.extend(["--target", target])

                result = subprocess.run(
                    build_args,
                    cwd=str(ragfs_python_dir),
                    env=env,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                if result.stdout:
                    print(result.stdout.decode("utf-8", errors="replace"))
                if result.stderr:
                    print(result.stderr.decode("utf-8", errors="replace"))

                # Extract the stable-ABI native extension from the built wheel.
                whl_files = list(Path(tmpdir).glob("ragfs_python-*.whl"))
                if not whl_files:
                    message = "maturin produced no wheel for ragfs-python."
                    if require_ragfs_artifact:
                        raise RuntimeError(message)
                    print(f"[Warning] {message}")
                    return

                ragfs_lib_dir.mkdir(parents=True, exist_ok=True)
                for stale_artifact in ragfs_lib_dir.glob("ragfs_python*.so"):
                    stale_artifact.unlink()
                for stale_artifact in ragfs_lib_dir.glob("ragfs_python*.pyd"):
                    stale_artifact.unlink()
                for stale_artifact in ragfs_lib_dir.glob("ragfs_python*.dylib"):
                    stale_artifact.unlink()

                extracted = False
                with zipfile.ZipFile(str(whl_files[0])) as zf:
                    for name in zf.namelist():
                        basename = Path(name).name
                        if basename.startswith("ragfs_python.abi3.") and (
                            basename.endswith(".so") or basename.endswith(".pyd")
                        ):
                            target_path = ragfs_lib_dir / basename
                            with zf.open(name) as src, open(target_path, "wb") as dst:
                                dst.write(src.read())
                            if sys.platform != "win32":
                                os.chmod(str(target_path), 0o755)
                            print(f"[OK] ragfs-python: extracted {basename} -> {target_path}")
                            extracted = True
                            break

                if not extracted:
                    message = "Could not find ragfs_python abi3 .so/.pyd in built wheel."
                    if require_ragfs_artifact:
                        raise RuntimeError(message)
                    print(f"[Warning] {message}")
                else:
                    self._copy_artifacts_to_build_lib(target_lib=target_path)

            except Exception as exc:
                error_detail = ""
                if isinstance(exc, subprocess.CalledProcessError):
                    if exc.stdout:
                        error_detail += exc.stdout.decode("utf-8", errors="replace")
                    if exc.stderr:
                        error_detail += exc.stderr.decode("utf-8", errors="replace")
                if require_ragfs_artifact:
                    error_message = f"Failed to build ragfs-python: {exc}"
                    if error_detail:
                        error_message += f"\n{error_detail}"
                    raise RuntimeError(error_message) from exc
                print(f"[Warning] Failed to build ragfs-python: {exc}")
                if error_detail:
                    print(error_detail)

    def _should_require_ragfs_artifact(self) -> bool:
        """Fail wheel builds closed when ragfs-python cannot be bundled."""
        required = os.environ.get("OV_REQUIRE_RAGFS_BUILD")
        if required is not None:
            return required == "1"
        return "bdist_wheel" in sys.argv

    def build_extension(self, ext):
        """Build a single Python native extension artifact using CMake."""
        if getattr(self, "_engine_extensions_built", False):
            return

        ext_fullpath = Path(self.get_ext_fullpath(ext.name))
        ext_dir = ext_fullpath.parent.resolve()
        build_dir = Path(self.build_temp) / "cmake_build"
        build_dir.mkdir(parents=True, exist_ok=True)
        self._clean_stale_engine_artifacts(ext_dir)

        self._run_stage_with_artifact_checks(
            "CMake build",
            lambda: self._build_extension_impl(ext_fullpath, ext_dir, build_dir),
            [(ext_fullpath, f"native extension '{ext.name}'")],
        )
        self._engine_extensions_built = True

    def _clean_stale_engine_artifacts(self, ext_dir: Path):
        """Remove stale non-abi3 engine binaries from wheel build output directories."""
        source_engine_dir = (SETUP_DIR / "openviking" / "storage" / "vectordb" / "engine").resolve()
        if ext_dir == source_engine_dir:
            return

        for pattern in ("*.so", "*.pyd"):
            for artifact in ext_dir.glob(pattern):
                artifact.unlink()

    def _build_extension_impl(self, ext_fullpath, ext_dir, build_dir):
        """Invoke CMake to build the Python native extension."""
        ext_basename = ext_fullpath.stem.split(".")[0]
        built_filename = Path(self.get_ext_filename(self.extensions[0].name)).name
        py_ext_suffix = built_filename.removeprefix(ext_basename)
        if not py_ext_suffix:
            py_ext_suffix = sysconfig.get_config_var("EXT_SUFFIX") or ext_fullpath.suffix

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
            f"-DCMAKE_C_COMPILER={C_COMPILER_PATH}",
            f"-DCMAKE_CXX_COMPILER={CXX_COMPILER_PATH}",
        ]

        if sys.platform == "darwin":
            cmake_args.append("-DCMAKE_OSX_DEPLOYMENT_TARGET=10.15")
            target_arch = os.environ.get("CMAKE_OSX_ARCHITECTURES")
            if target_arch:
                cmake_args.append(f"-DCMAKE_OSX_ARCHITECTURES={target_arch}")
        elif sys.platform == "win32":
            windows_python_sabi_library = _get_windows_python_sabi_library()
            cmake_args.append(f"-DOV_PYTHON_SABI_LIBRARY={windows_python_sabi_library}")
            cmake_args.extend(["-G", "MinGW Makefiles"])

        self.spawn([self.cmake_executable] + cmake_args)

        build_args = ["--build", str(build_dir), "--config", "Release", f"-j{os.cpu_count() or 4}"]
        self.spawn([self.cmake_executable] + build_args)


if bdist_wheel is not None:

    class OpenVikingBdistWheel(bdist_wheel):
        def finalize_options(self):
            super().finalize_options()
            self.py_limited_api = "cp310"
else:
    OpenVikingBdistWheel = None


cmdclass = {
    "build_ext": OpenVikingBuildExt,
}
if OpenVikingBdistWheel is not None:
    cmdclass["bdist_wheel"] = OpenVikingBdistWheel


setup(
    ext_modules=[
        Extension(
            name=ENGINE_BUILD_CONFIG.primary_extension,
            sources=[],
            py_limited_api=True,
        )
    ],
    cmdclass=cmdclass,
    package_data={
        "openviking": [
            "lib/ragfs_python*.so",
            "lib/ragfs_python*.pyd",
            "bin/ov",
            "bin/ov.exe",
            "console/static/**/*",
            "storage/vectordb/engine/*.abi3.so",
            "storage/vectordb/engine/*.pyd",
        ],
    },
    include_package_data=True,
)
