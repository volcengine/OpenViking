"""Reusable, non-interactive OpenViking Quick Local provisioning.

User interfaces own prompts and rendering.  This module owns the bounded
installation, Hermes-profile-scoped configuration, and temporary validation
needed to produce a ready-to-link OpenViking CLI profile.
"""

from __future__ import annotations

import importlib.util
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from urllib.parse import urlparse

from packaging.requirements import Requirement
from packaging.version import InvalidVersion, Version
from utils import atomic_json_write

DEPLOYMENT = "quick_local"
EMBEDDING_MODEL = "bge-small-zh-v1.5-f16"
EMBEDDING_DIMENSION = 512
OPENVIKING_REQUIREMENT = "openviking[local-embed]==0.4.22"
# Later LiteLLM releases exclude Python 3.14. OpenViking supports this version.
# This restriction applies only to the private server, never Hermes's dependencies.

_OPENVIKING_REQUIREMENT = Requirement(OPENVIKING_REQUIREMENT)
_OPENVIKING_VERSION_SPECIFIER = _OPENVIKING_REQUIREMENT.specifier
_ROOT_DIRNAME = "openviking"
_SERVER_CONFIG_FILENAME = "ov.conf"
_OVCLI_CONFIG_FILENAME = "ovcli.conf"
_RESTART_REQUIRED_FILENAME = ".restart-required"
_WORKSPACE_DIRNAME = "data"
_MODEL_CACHE_DIRNAME = "models"
# Leave OpenViking's default 1933 available for independently managed servers.
_DEFAULT_PORT = 1934
_PORT_ATTEMPTS = 20
_MODEL_DOWNLOAD_SIZE = "approximately 46 MiB"
# OpenViking downloads the built-in model while its server lifespan starts.
# Give a slow first download a bounded ten minutes, then leave another minute
# for model loading and service initialization.
_MODEL_PREPARATION_TIMEOUT_SECONDS = 600.0
_HEALTH_TIMEOUT_SECONDS = _MODEL_PREPARATION_TIMEOUT_SECONDS + 60.0
_HEALTH_POLL_INTERVAL_SECONDS = 0.5
_PROCESS_STOP_TIMEOUT_SECONDS = 10.0


class QuickLocalStage(str, Enum):
    PREFLIGHT = "preflight"
    INSTALL_OPENVIKING = "install_openviking"
    PREPARE_EMBEDDING = "prepare_embedding"
    VALIDATE = "validate"
    WRITE_CONFIG = "write_config"
    COMPLETE = "complete"


@dataclass(frozen=True)
class QuickLocalProgress:
    stage: QuickLocalStage
    message: str


@dataclass(frozen=True)
class QuickLocalPaths:
    root: Path
    runtime: Path
    server_config: Path
    ovcli_config: Path
    workspace: Path
    model_cache: Path

    @property
    def runtime_python(self) -> Path:
        scripts = "Scripts" if os.name == "nt" else "bin"
        executable = "python.exe" if os.name == "nt" else "python"
        if _pm_available():
            from pm.operations import environment_python

            selected = environment_python("openviking-local", root=self.runtime)
        else:
            selected = None
        return selected or self.runtime / scripts / executable

    @property
    def server_command(self) -> Path:
        scripts = "Scripts" if os.name == "nt" else "bin"
        executable = "openviking-server.exe" if os.name == "nt" else "openviking-server"
        if _pm_available():
            from pm.operations import python_tool

            selected = python_tool("openviking-local", "openviking-server", root=self.runtime)
        else:
            selected = None
        return selected or self.runtime / scripts / executable

    @property
    def restart_required_marker(self) -> Path:
        return self.root / _RESTART_REQUIRED_FILENAME


@dataclass(frozen=True)
class QuickLocalPreflight:
    paths: QuickLocalPaths
    reusable_endpoint: Optional[str]


@dataclass(frozen=True)
class QuickLocalSetupResult:
    paths: QuickLocalPaths
    endpoint: str
    reused: bool
    server_restart_required: bool = False


class QuickLocalSetupError(RuntimeError):
    """Quick Local could not finish without partially activating it."""


class SourceBuildRequired(QuickLocalSetupError):
    """No reviewed binary matches this platform; ask before compiling."""


ProgressReporter = Callable[[QuickLocalProgress], None]
HealthCheck = Callable[[str], tuple[bool, str]]


def managed_paths(hermes_home: Path) -> QuickLocalPaths:
    root = Path(hermes_home).expanduser().absolute() / _ROOT_DIRNAME
    if root.is_symlink():
        raise QuickLocalSetupError("Quick Local directory must belong to this Hermes profile.")
    return QuickLocalPaths(
        root=root,
        runtime=root / "runtime",
        server_config=root / _SERVER_CONFIG_FILENAME,
        ovcli_config=root / _OVCLI_CONFIG_FILENAME,
        workspace=root / _WORKSPACE_DIRNAME,
        model_cache=root / _MODEL_CACHE_DIRNAME,
    )


def clear_managed_settings(provider_config: dict[str, Any]) -> None:
    provider_config.pop("deployment", None)
    provider_config.pop("server_config_path", None)
    provider_config.pop("server_command_path", None)


def clear_server_restart_required(server_config_path: Path) -> bool:
    """Clear the durable marker once no stale managed server remains."""
    marker = Path(server_config_path).with_name(_RESTART_REQUIRED_FILENAME)
    try:
        marker.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _mark_server_restart_required(paths: QuickLocalPaths) -> None:
    _prepare_private_directory(paths.root)
    atomic_json_write(
        paths.restart_required_marker,
        {"restart_required": True},
        mode=0o600,
    )


def build_server_config(
    paths: QuickLocalPaths,
    vlm: Mapping[str, Any],
    *,
    port: int = _DEFAULT_PORT,
) -> dict[str, Any]:
    return {
        "server": {
            "host": "127.0.0.1",
            "port": port,
            "auth_mode": "trusted",
            "root_api_key": _server_key(paths),
        },
        "storage": {"workspace": str(paths.workspace)},
        "embedding": {
            "dense": {
                "provider": "local",
                "model": EMBEDDING_MODEL,
                "dimension": EMBEDDING_DIMENSION,
                "cache_dir": str(paths.model_cache),
            }
        },
        "vlm": dict(vlm),
    }


def resolve_hermes_vlm_config() -> dict[str, Any]:
    """Translate the active persisted Hermes LLM into OpenViking VLM config."""

    from hermes_cli.config import load_config
    from hermes_cli.runtime_provider import resolve_runtime_provider

    config = load_config()
    model_config = config.get("model", {}) if isinstance(config, Mapping) else {}
    if isinstance(model_config, str):
        model_config = {"default": model_config}
    if not isinstance(model_config, Mapping):
        model_config = {}

    default_model = model_config.get("default")
    requested_provider = _clean_value(model_config.get("provider")) or None
    if isinstance(default_model, Mapping):
        from hermes_cli.config import split_model_config_default

        nested_model, nested_provider = split_model_config_default(default_model)
        default_model = nested_model
        requested_provider = requested_provider or nested_provider or None
    model = _clean_value(default_model or model_config.get("model") or model_config.get("name"))
    if not model:
        raise QuickLocalSetupError("Hermes has no default LLM model configured.")

    runtime = resolve_runtime_provider(
        requested=requested_provider,
        target_model=model,
    )
    runtime_model = _clean_value(runtime.get("model")) or model
    provider = _clean_value(runtime.get("provider")).lower()
    api_mode = _clean_value(runtime.get("api_mode")).lower()
    source = _clean_value(runtime.get("source")).lower()
    raw_api_base = runtime.get("base_url")
    raw_api_key = runtime.get("api_key")
    api_base = _clean_value(raw_api_base)
    api_key = _clean_value(raw_api_key)

    if raw_api_base is not None and not isinstance(raw_api_base, str):
        raise QuickLocalSetupError("Hermes' LLM provider API base URL must be a string.")
    if not api_base:
        raise QuickLocalSetupError("Hermes' LLM provider did not resolve an API base URL.")
    if raw_api_key is None or (isinstance(raw_api_key, str) and not api_key):
        raise QuickLocalSetupError(
            "Hermes' LLM provider did not resolve reusable static credentials."
        )
    key_env = _clean_value(model_config.get("key_env") or model_config.get("api_key_env")).lower()
    local_runtime = (
        provider == "custom"
        and source == "local-runtime"
        and isinstance(raw_api_key, str)
        and bool(api_key)
        and urlparse(api_base).hostname in {"localhost", "127.0.0.1", "::1"}
    )
    declared_key = bool(key_env and source in {key_env, f"env:{key_env}"})
    # A declared env variable identifies a source, not a static credential.
    # Hermes owns native Anthropic OAuth authentication and token refresh.
    oauth_route = False
    if api_mode == "anthropic_messages" and isinstance(raw_api_key, str):
        from agent.anthropic_credentials import anthropic_route_is_oauth

        oauth_route = anthropic_route_is_oauth(api_base, raw_api_key, provider=provider)
    if oauth_route or not isinstance(raw_api_key, str) or not (
        local_runtime or declared_key or _has_copyable_static_credentials(provider, source, api_key)
    ):
        raise QuickLocalSetupError(
            "Hermes is using refreshed OAuth, cloud-native, or external-process "
            "credentials that cannot be copied safely into OpenViking. Configure "
            "a static API-key LLM for Hermes, or connect to an OpenViking server "
            "configured separately."
        )
    # These static-key API endpoints also provide Chat Completions. OAuth
    # Responses routes and third-party Responses-only endpoints stay excluded.
    if api_mode == "codex_responses" and urlparse(api_base).hostname in {
        "api.openai.com", "api.x.ai"
    } and provider in {"openai-api", "xai"}:
        api_mode = "chat_completions"
    if api_mode not in {"chat_completions", "anthropic_messages"}:
        raise QuickLocalSetupError(
            f"Hermes' {api_mode or 'unknown'} LLM transport is not supported by "
            "Quick Local. Use an OpenAI-compatible or Anthropic-compatible "
            "API-key provider, or connect to an OpenViking server configured "
            "separately."
        )
    vlm: dict[str, Any]
    if api_mode == "anthropic_messages":
        # Like Hermes's Anthropic SDK, LiteLLM appends /v1/messages itself.
        api_base = api_base.rstrip("/").removesuffix("/v1")
        from agent.anthropic_adapter import _attribution_headers, _auth_style

        style = _auth_style(api_key, api_base, api_base)
        # OV's pinned LiteLLM backend cannot reproduce Hermes's bearer/query
        # handling. It also treats sk-ant-oat-shaped proxy keys as native OAuth.
        if style == "bearer" or api_key.startswith("sk-ant-oat"):
            raise QuickLocalSetupError(
                "Quick Local cannot reproduce authentication for this Anthropic route. "
                "Use an OpenAI-compatible route for Hermes, or Custom setup "
                "with a separately configured OpenViking server."
            )
        if not runtime_model.startswith("anthropic/"):
            runtime_model = f"anthropic/{runtime_model}"
        vlm = {
            "provider": "litellm",
            "model": runtime_model,
            "api_key": api_key,
            "api_base": api_base,
        }
        if style == "kimi":
            vlm["extra_headers"] = _attribution_headers()
    else:
        base = urlparse(api_base)
        if base.hostname == "generativelanguage.googleapis.com" and not base.path.rstrip("/").endswith("/openai"):
            # Hermes uses Google's native client despite chat_completions in
            # its runtime record. OV needs Google's documented OpenAI route.
            api_base = base._replace(path="/v1beta/openai/").geturl()
        vlm = {
            "provider": "openai",
            "model": runtime_model,
            "api_key": api_key,
            "api_base": api_base,
        }

    extra_headers = runtime.get("extra_headers")
    if isinstance(extra_headers, dict) and extra_headers:
        vlm["extra_headers"] = {**vlm.get("extra_headers", {}), **extra_headers}
    request_overrides = runtime.get("request_overrides")
    if isinstance(request_overrides, dict):
        extra_body = request_overrides.get("extra_body")
        if isinstance(extra_body, dict) and extra_body:
            vlm["extra_request_body"] = dict(extra_body)
    vlm.update({"temperature": 0.0, "max_retries": 2})
    return vlm


class QuickLocalSetup:
    """Provision one Hermes-home-scoped Quick Local configuration."""

    def __init__(
        self,
        *,
        health_check: HealthCheck,
        progress: Optional[ProgressReporter] = None,
        allow_source_build: bool = False,
    ) -> None:
        self._health_check = health_check
        self._progress = progress or (lambda _event: None)
        self.allow_source_build = allow_source_build

    def preflight(self, hermes_home: Path) -> QuickLocalPreflight:
        self._emit(QuickLocalStage.PREFLIGHT, "Checking local requirements...")
        paths = managed_paths(hermes_home)
        reusable_endpoint = find_reusable_endpoint(paths, self._health_check)
        return QuickLocalPreflight(
            paths=paths,
            reusable_endpoint=reusable_endpoint,
        )

    def provision(
        self,
        *,
        hermes_home: Path,
        preflight: Optional[QuickLocalPreflight] = None,
    ) -> QuickLocalSetupResult:
        try:
            return self._provision(
                hermes_home=Path(hermes_home),
                preflight=preflight,
            )
        except QuickLocalSetupError:
            raise
        except Exception as exc:
            raise QuickLocalSetupError(
                f"Could not prepare Quick Local ({type(exc).__name__}). Review the server log and retry."
            ) from exc

    def _provision(
        self,
        *,
        hermes_home: Path,
        preflight: Optional[QuickLocalPreflight],
    ) -> QuickLocalSetupResult:
        preflight = preflight or self.preflight(hermes_home)
        if preflight.paths != managed_paths(hermes_home):
            raise QuickLocalSetupError(
                "Quick Local preflight belongs to a different Hermes profile."
            )
        vlm = resolve_hermes_vlm_config()
        runtime_changed = self._ensure_openviking_installed(preflight.paths)

        reusable_endpoint = find_reusable_endpoint(preflight.paths, self._health_check)
        if reusable_endpoint:
            port = _endpoint_port(reusable_endpoint)
            if port is None:
                raise QuickLocalSetupError(
                    "Quick Local's saved endpoint does not contain a valid port."
                )
            server_config = build_server_config(preflight.paths, vlm, port=port)
            config_changed = not _stored_server_config_matches(preflight.paths, server_config)
            if (
                runtime_changed
                or config_changed
                or preflight.paths.restart_required_marker.is_file()
            ):
                # Validate in disposable storage before changing the live service.
                validation_port = find_available_port()
                if validation_port is None:
                    raise QuickLocalSetupError(
                        "No free port is available to validate updated settings."
                    )
                self._validate_generated_config(
                    paths=preflight.paths,
                    endpoint=f"http://127.0.0.1:{validation_port}",
                    server_config=server_config,
                )
                from .local_server import LocalServer

                LocalServer(hermes_home).configure(server_config, runtime_changed=runtime_changed)
                self._emit(
                    QuickLocalStage.WRITE_CONFIG,
                    "Restarting Quick Local with the updated settings...",
                )
                reusable_endpoint = self._start_managed_server(preflight.paths, reusable_endpoint)
            self._emit(
                QuickLocalStage.COMPLETE,
                "Quick Local is ready with the updated settings."
                if runtime_changed or config_changed
                else "Existing Quick Local server is reachable; reusing it.",
            )
            return QuickLocalSetupResult(
                paths=preflight.paths,
                endpoint=reusable_endpoint,
                reused=True,
            )

        port = find_available_port(preferred_endpoint=_configured_endpoint(preflight.paths))
        if port is None:
            last_port = _DEFAULT_PORT + _PORT_ATTEMPTS - 1
            raise QuickLocalSetupError(
                "No available local port was found for OpenViking "
                f"(checked {_DEFAULT_PORT}-{last_port})."
            )
        endpoint = f"http://127.0.0.1:{port}"
        server_config = build_server_config(preflight.paths, vlm, port=port)

        self._validate_generated_config(
            paths=preflight.paths,
            endpoint=endpoint,
            server_config=server_config,
        )

        from .local_server import LocalServer

        preflight.paths.workspace.mkdir(parents=True, exist_ok=True)
        LocalServer(hermes_home).configure(server_config, runtime_changed=runtime_changed)
        endpoint = self._start_managed_server(preflight.paths, endpoint)
        self._emit(
            QuickLocalStage.WRITE_CONFIG,
            f"Saved Quick Local configuration to {preflight.paths.server_config}.",
        )
        self._emit(
            QuickLocalStage.COMPLETE,
            f"Quick Local is configured with {EMBEDDING_MODEL}.",
        )
        return QuickLocalSetupResult(
            paths=preflight.paths,
            endpoint=endpoint,
            reused=False,
        )

    def _ensure_openviking_installed(self, paths: QuickLocalPaths) -> bool:
        """Ensure a compatible runtime, returning whether installation was needed."""
        from .local_packages import install_requirements, verified_requirements

        requirements = install_requirements(allow_source_build=self.allow_source_build)
        receipt = paths.root / "runtime-requirements.json"
        try:
            installed_requirements = json.loads(receipt.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            installed_requirements = None
        if (
            openviking_install_satisfies_requirement(paths)
            and installed_requirements == requirements
        ):
            return False
        self._emit(
            QuickLocalStage.INSTALL_OPENVIKING,
            f"Installing {OPENVIKING_REQUIREMENT}...",
        )
        _prepare_private_directory(paths.root)
        if self.allow_source_build:
            self._emit(
                QuickLocalStage.INSTALL_OPENVIKING,
                "Source builds are allowed. Native compilation can take several minutes.",
            )
        if _pm_available():
            from pm.client import ensure_python_tool

            try:
                verified = verified_requirements(requirements, paths.root / "wheels")
                ensure_python_tool(
                    "openviking-local",
                    verified,
                    "openviking-server",
                    root=paths.runtime,
                    explicit=True,
                    timeout=1800,
                )
            except QuickLocalSetupError:
                raise
            except Exception as exc:
                raise QuickLocalSetupError(
                    "Could not install the private OpenViking runtime. Review the installer output."
                ) from exc
        else:  # Genuine pre-PM Hermes only; never fall back on a broken PM import.
            self._install_with_uv(paths, verified_requirements(requirements, paths.root / "wheels"))
        if not openviking_install_satisfies_requirement(paths):
            raise QuickLocalSetupError(
                "Could not install a compatible OpenViking local embedding runtime."
            )
        atomic_json_write(receipt, requirements, mode=0o600)
        return True

    def _install_with_uv(self, paths: QuickLocalPaths, requirements: list[str]) -> None:
        from hermes_cli.managed_uv import resolve_uv
        from hermes_constants import get_default_hermes_root

        # ensure_uv() can replace the active Hermes environment while setup is
        # running. Use an existing installer without repairing the host runtime.
        shared_uv = get_default_hermes_root() / "bin" / ("uv.exe" if os.name == "nt" else "uv")
        uv = resolve_uv() or shutil.which("uv") or (str(shared_uv) if shared_uv.is_file() else None)
        if not uv:
            raise QuickLocalSetupError("Quick Local needs uv. Install uv, then retry setup.")
        env = _private_child_env(paths.root.parent)
        env.update(UV_NATIVE_TLS="true", UV_SYSTEM_CERTS="true")
        commands = []
        if not paths.runtime_python.is_file():
            commands.append(([uv, "venv", str(paths.runtime), "--python", sys.executable], 120))
        commands.append(
            (
                [
                    uv,
                    "pip",
                    "install",
                    "--python",
                    str(paths.runtime_python),
                    *requirements,
                ],
                1800,
            )
        )
        for command, timeout in commands:
            result = subprocess.run(
                command,
                cwd=paths.root,
                env=env,
                stdin=subprocess.DEVNULL,
                check=False,
                timeout=timeout,
            )
            if result.returncode:
                raise QuickLocalSetupError(
                    "Could not install the private OpenViking runtime. Review the installer output."
                )

    def _validate_generated_config(
        self,
        *,
        paths: QuickLocalPaths,
        endpoint: str,
        server_config: dict[str, Any],
    ) -> None:
        _prepare_private_directory(paths.root)
        with tempfile.TemporaryDirectory(prefix="setup-validation-", dir=paths.root) as root:
            validation_root = Path(root)
            validation_config = json.loads(json.dumps(server_config))
            validation_config["storage"]["workspace"] = str(validation_root / _WORKSPACE_DIRNAME)
            config_path = validation_root / _SERVER_CONFIG_FILENAME
            atomic_json_write(config_path, validation_config, mode=0o600)

            self._emit(QuickLocalStage.VALIDATE, "Checking the copied Hermes LLM settings...")
            _validate_vlm(paths, config_path)

            _prepare_private_directory(paths.model_cache)
            self._emit(
                QuickLocalStage.PREPARE_EMBEDDING,
                f"Preparing {EMBEDDING_MODEL}; its {_MODEL_DOWNLOAD_SIZE} model "
                "is downloaded once if needed...",
            )
            _validate_local_embedding(paths)
            self._emit(
                QuickLocalStage.VALIDATE,
                "Validating OpenViking with a temporary local server...",
            )
            process = _start_validation_server(
                endpoint,
                config_path,
                paths.root.parent,
                paths.server_command,
            )
            primary_error: BaseException | None = None
            try:

                def validated_health(url):
                    healthy, message = self._health_check(url)
                    return healthy and _server_accepts_config(validation_config, url), message

                if not _wait_for_health(
                    endpoint,
                    validated_health,
                    process=process,
                ):
                    returncode = process.poll()
                    if returncode is not None:
                        raise QuickLocalSetupError(
                            "OpenViking exited before becoming reachable "
                            f"(status {returncode}). Review the server log at "
                            f"{_server_log_path(paths.root.parent)} and retry."
                        )
                    raise QuickLocalSetupError(
                        "OpenViking did not become reachable before the local model "
                        "preparation timeout. Review the server log at "
                        f"{_server_log_path(paths.root.parent)} and retry."
                    )
                _validate_tenant_access(endpoint, server_config["server"]["root_api_key"])
            except BaseException as exc:
                primary_error = exc
                raise
            finally:
                if not _stop_process(process):
                    message = "The temporary OpenViking validation server could not be stopped."
                    if primary_error is None:
                        raise QuickLocalSetupError(message)
                    primary_error.add_note(message)

    def _start_managed_server(self, paths: QuickLocalPaths, endpoint: str) -> str:
        """Leave a validated service ready for the first turn and reserve its port."""
        self._emit(QuickLocalStage.VALIDATE, "Starting this profile's OpenViking server...")
        from .local_server import LocalServer

        server = LocalServer(paths.root.parent)
        started = server.start()
        ready = server.wait_ready(started, self._health_check)
        return ready.endpoint

    def _emit(self, stage: QuickLocalStage, message: str) -> None:
        self._progress(QuickLocalProgress(stage=stage, message=message))


def _validate_vlm(paths: QuickLocalPaths, config_path: Path) -> None:
    """Check auth and transport through the exact private OV backend before activation."""
    script = """
import json, sys
from openviking.models.vlm import VLMFactory
try:
    config = json.load(open(sys.argv[1], encoding="utf-8"))["vlm"]
    vlm = VLMFactory.create({**config, "timeout": 30, "max_retries": 0})
    reply = vlm.get_completion(prompt="Reply with OK only.")
    if not isinstance(reply, str) or not reply.strip():
        raise ValueError("Empty completion")
except Exception as exc:
    print(json.dumps({"error": type(exc).__name__}))
    sys.exit(1)
"""
    try:
        result = subprocess.run(
            [str(paths.runtime_python), "-c", script, str(config_path)],
            cwd=paths.root.parent,
            env=_private_child_env(paths.root.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=90,
            check=False,
        )
        if result.returncode:
            raise QuickLocalSetupError(
                "The copied Hermes LLM settings failed the access check. "
                "Check the model, API key and endpoint in Hermes, then retry. "
                "The model must support Chat Completions or Anthropic Messages."
            )
    except subprocess.TimeoutExpired as exc:
        raise QuickLocalSetupError(
            "The Hermes LLM access check timed out. Check its endpoint, then retry setup."
        ) from exc


def openviking_install_satisfies_requirement(paths: QuickLocalPaths) -> bool:
    if not paths.runtime_python.is_file() or not paths.server_command.is_file():
        return False
    try:
        result = subprocess.run(
            [
                str(paths.runtime_python),
                "-c",
                "import importlib.metadata; import llama_cpp; "
                "print(importlib.metadata.version('openviking'))",
            ],
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=120,
        )
        if result.returncode != 0:
            return False
        version = Version(result.stdout.strip())
    except subprocess.TimeoutExpired as exc:
        # A slow cold native import is not evidence of a missing installation.
        raise QuickLocalSetupError(
            "Quick Local's runtime check timed out. Retry setup; the runtime was not reinstalled."
        ) from exc
    except (InvalidVersion, OSError, subprocess.SubprocessError):
        return False
    return version in _OPENVIKING_VERSION_SPECIFIER


def _validate_local_embedding(paths: QuickLocalPaths) -> None:
    """Health can pass with failed embeddings; exercise the native backend."""
    script = (
        "import math; "
        "from openviking.models.embedder.local_embedders import LocalDenseEmbedder; "
        f"e=LocalDenseEmbedder(model_name={EMBEDDING_MODEL!r},cache_dir={str(paths.model_cache)!r}); "
        "v=e.embed('memory setup validation').dense_vector; "
        f"assert len(v)=={EMBEDDING_DIMENSION} and all(math.isfinite(x) for x in v) and any(v)"
    )
    log_path = _server_log_path(paths.root.parent)
    _prepare_private_directory(log_path.parent)
    if log_path.is_symlink():
        raise QuickLocalSetupError("Quick Local log must belong to this profile.")
    if not log_path.exists():
        log_path.touch(mode=0o600)
    if os.name != "nt":
        log_path.chmod(0o600)
    try:
        with log_path.open("ab") as log:
            result = subprocess.run(
                [str(paths.runtime_python), "-c", script],
                env=_private_child_env(paths.root.parent),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=_HEALTH_TIMEOUT_SECONDS,
            )
        if result.returncode:
            raise QuickLocalSetupError(
                "Quick Local's embedding model could not run. "
                f"Review {log_path}; use Custom setup with a separate server if needed."
            )
    except subprocess.TimeoutExpired as exc:
        raise QuickLocalSetupError(
            f"Quick Local's embedding check timed out. Review {log_path} and retry setup."
        ) from exc


def find_available_port(
    *,
    preferred_endpoint: Optional[str] = None,
    first_port: int = _DEFAULT_PORT,
    attempts: int = _PORT_ATTEMPTS,
) -> Optional[int]:
    preferred_port = _endpoint_port(preferred_endpoint)
    candidates = range(first_port, first_port + attempts)
    if preferred_port is not None and (
        preferred_port in candidates or (first_port == _DEFAULT_PORT and preferred_port == 1933)
    ):
        candidates = [
            preferred_port,
            *(port for port in candidates if port != preferred_port),
        ]
    for port in candidates:
        if _can_bind_local_port("127.0.0.1", port):
            return port
    return None


def _can_bind_local_port(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        # Some Unix kernels allow a specific-address bind beside a wildcard
        # listener with SO_REUSEADDR. Never take an already reachable port.
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.2)
            if probe.connect_ex((host, port)) == 0:
                return False
        with socket.socket(family, socket.SOCK_STREAM) as candidate:
            if os.name == "nt":
                candidate.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                # Match Uvicorn: permit restart after closed connections remain
                # in TIME_WAIT, but never bind beside a listening server.
                candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            candidate.bind((host, port))
        return True
    except OSError:
        return False


def find_reusable_endpoint(
    paths: QuickLocalPaths,
    health_check: HealthCheck,
) -> Optional[str]:
    endpoint = _configured_endpoint(paths)
    if endpoint is None:
        return None
    healthy, _message = health_check(endpoint)
    return endpoint if healthy and server_belongs_to_profile(paths, endpoint) else None


def _configured_endpoint(paths: QuickLocalPaths) -> Optional[str]:
    if not paths.server_config.is_file() or not paths.ovcli_config.is_file():
        return None
    try:
        server_config = json.loads(paths.server_config.read_text(encoding="utf-8"))
        storage = server_config.get("storage", {}) if isinstance(server_config, dict) else {}
        if not isinstance(storage, dict) or not _paths_equivalent(
            storage.get("workspace"), paths.workspace
        ):
            return None
        profile = json.loads(paths.ovcli_config.read_text(encoding="utf-8"))
        return _normalize_local_endpoint(profile.get("url") if isinstance(profile, dict) else "")
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _server_key(paths: QuickLocalPaths) -> str:
    try:
        saved = json.loads(paths.server_config.read_text(encoding="utf-8"))
        if _paths_equivalent(saved.get("storage", {}).get("workspace"), paths.workspace):
            key = _clean_value(saved.get("server", {}).get("root_api_key"))
            if key:
                return key
    except (OSError, ValueError, AttributeError):
        pass
    return secrets.token_urlsafe(32)


def server_belongs_to_profile(paths: QuickLocalPaths, endpoint: str) -> bool:
    """Do not reuse an unrelated service that took the saved local port."""
    try:
        saved = json.loads(paths.server_config.read_text(encoding="utf-8"))
        if not _paths_equivalent(saved.get("storage", {}).get("workspace"), paths.workspace):
            return False
        return _server_accepts_config(saved, endpoint)
    except (OSError, ValueError, AttributeError):
        return False


def _server_accepts_config(config: Mapping[str, Any], endpoint: str) -> bool:
    import httpx

    try:
        key = _clean_value(config.get("server", {}).get("root_api_key"))
        if not key:
            return False
        with httpx.Client(timeout=3.0, trust_env=False) as client:
            health = client.get(f"{endpoint}/health").json()
            if (
                health.get("auth_mode") != "trusted"
                or health.get("root_api_key_required") is not True
            ):
                return False
            response = client.get(f"{endpoint}/api/v1/admin/accounts", headers={"X-API-Key": key})
            return response.status_code == 200 and response.json().get("status") == "ok"
    except (ValueError, AttributeError, httpx.HTTPError):
        return False


def _validate_tenant_access(endpoint: str, key: str) -> None:
    """Exercise a data write only in the disposable validation workspace."""
    import httpx

    try:
        response = httpx.post(
            f"{endpoint}/api/v1/sessions",
            json={},
            headers={
                "X-API-Key": key,
                "X-OpenViking-Account": "default",
                "X-OpenViking-User": "default",
            },
            timeout=5.0,
            trust_env=False,
        )
        response.raise_for_status()
        if response.json().get("status") != "ok":
            raise ValueError("session creation failed")
    except (ValueError, httpx.HTTPError) as exc:
        raise QuickLocalSetupError(
            "Quick Local could not validate memory data access. Review the server log and retry."
        ) from exc


def _write_ovcli_profile(path: Path, endpoint: str, server_config: Mapping[str, Any]) -> None:
    atomic_json_write(
        path,
        {
            "url": endpoint,
            "root_api_key": server_config["server"]["root_api_key"],
            "account": "default",
            "user": "default",
            "actor_peer_id": "hermes",
        },
        mode=0o600,
    )


def connection_config(provider_config: Mapping[str, Any], hermes_home: Path) -> dict[str, Any]:
    """Resolve only this profile's managed files, including after profile import."""
    paths = managed_paths(hermes_home)
    repair_private_paths(paths)
    endpoint = _configured_endpoint(paths)
    if endpoint is None:
        raise QuickLocalSetupError(
            "Quick Local configuration is missing or belongs to another profile. Run hermes memory setup openviking again."
        )
    try:
        profile = json.loads(paths.ovcli_config.read_text(encoding="utf-8"))
        saved = json.loads(paths.server_config.read_text(encoding="utf-8"))
        if not isinstance(profile, dict) or not isinstance(saved, dict):
            raise ValueError("invalid configuration")
    except (OSError, ValueError) as exc:
        raise QuickLocalSetupError(
            "Quick Local configuration could not be read. Run hermes memory setup openviking again."
        ) from exc
    if (
        not isinstance(saved.get("server"), dict)
        or not profile.get("root_api_key")
        or profile["root_api_key"] != saved["server"].get("root_api_key")
    ):
        raise QuickLocalSetupError(
            "Quick Local credentials are incomplete. Run hermes memory setup openviking again."
        )
    return profile


def _start_validation_server(
    endpoint: str,
    config_path: Path,
    hermes_home: Path,
    server_command: Path,
) -> subprocess.Popen:
    if not server_command.is_file():
        raise QuickLocalSetupError("openviking-server was not found after installation.")
    command = str(server_command)
    host, port = _endpoint_bind(endpoint)
    if not _can_bind_local_port(host, port):
        raise QuickLocalSetupError(
            f"Local port {host}:{port} became unavailable before OpenViking "
            "could start. Retry Quick Local setup."
        )
    log_path = _server_log_path(hermes_home)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if not log_path.exists():
        log_path.touch(mode=0o600)
    log_path.chmod(0o600)
    child_env = _private_child_env(hermes_home)
    from hermes_cli._subprocess_compat import windows_detach_popen_kwargs

    popen_kwargs: dict[str, Any] = windows_detach_popen_kwargs()
    command_args = [
        command,
        "--config",
        str(config_path),
        "--host",
        host,
        "--port",
        str(port),
    ]
    try:
        with log_path.open("ab") as log_file:
            common_kwargs: dict[str, Any] = {
                "stdout": log_file,
                "stderr": log_file,
                "env": child_env,
                "cwd": hermes_home,
            }
            try:
                return subprocess.Popen(
                    command_args,
                    **common_kwargs,
                    **popen_kwargs,
                    stdin=subprocess.DEVNULL,
                )
            except OSError:
                if os.name != "nt":
                    raise
                from hermes_cli._subprocess_compat import (
                    windows_detach_flags_without_breakaway,
                )

                return subprocess.Popen(
                    command_args,
                    **common_kwargs,
                    creationflags=windows_detach_flags_without_breakaway(),
                    stdin=subprocess.DEVNULL,
                )
    except Exception as exc:
        raise QuickLocalSetupError(f"Could not start the OpenViking server: {exc}") from exc


def _wait_for_health(
    endpoint: str,
    health_check: HealthCheck,
    *,
    process: Optional[subprocess.Popen] = None,
    timeout_seconds: float = _HEALTH_TIMEOUT_SECONDS,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        healthy, _message = health_check(endpoint)
        if healthy:
            return True
        if process is not None and process.poll() is not None:
            return False
        time.sleep(_HEALTH_POLL_INTERVAL_SECONDS)
    return False


def _stop_process(process: subprocess.Popen) -> bool:
    import psutil

    from .local_server import stop_process_tree

    try:
        if process.poll() is not None:
            return True
        stop_process_tree(psutil.Process(process.pid), timeout=_PROCESS_STOP_TIMEOUT_SECONDS)
        process.wait(timeout=_PROCESS_STOP_TIMEOUT_SECONDS)
        return True
    except psutil.NoSuchProcess:
        # The child can finish between poll() and taking its process handle.
        return True
    except Exception:
        return False


def _has_copyable_static_credentials(
    provider: str,
    source: str,
    api_key: str,
) -> bool:
    """Return whether Hermes explicitly classifies this credential as static."""

    if not api_key or api_key == "aws-sdk":
        return False

    if provider == "custom":
        return source in {"direct-alias", "env/config"} or source.startswith(
            (
                "custom_provider:",
                "pool:",
            )
        )
    if provider == "openrouter":
        return source == "env/config" or source.startswith(
            (
                "credential_pool:",
                "env:",
                "manual:",
                "pool:",
            )
        )

    from hermes_cli.auth import PROVIDER_REGISTRY

    provider_config = PROVIDER_REGISTRY.get(provider)
    if provider == "copilot" or provider_config is None or provider_config.auth_type != "api_key":
        return False

    if source in {"config", "default", "env", "local-offline"}:
        return True
    api_key_sources = {value.lower() for value in provider_config.api_key_env_vars}
    if source in api_key_sources:
        return True
    if source.startswith("env:"):
        return source.removeprefix("env:") in api_key_sources
    if source.startswith("credential_pool:"):
        return source.removeprefix("credential_pool:") == provider
    return source.startswith("manual:")


def _stored_server_config_matches(
    paths: QuickLocalPaths,
    expected: Mapping[str, Any],
) -> bool:
    try:
        saved = json.loads(paths.server_config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return False
    return saved == expected


def _prepare_private_directory(path: Path) -> None:
    if path.is_symlink():
        raise QuickLocalSetupError("Quick Local private directories must belong to this profile.")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        path.chmod(0o700)


def repair_private_paths(paths: QuickLocalPaths) -> None:
    """Backup/import may restore permissive modes on plugin-owned files."""
    _prepare_private_directory(paths.root)
    for path in (
        paths.server_config,
        paths.ovcli_config,
        paths.restart_required_marker,
        paths.root / "server-process.json",
        paths.root / "server.lock",
        paths.root / "runtime-requirements.json",
    ):
        if path.is_symlink():
            raise QuickLocalSetupError("Quick Local private files must belong to this profile.")
        if path.exists() and os.name != "nt":
            path.chmod(0o600)


def _pm_available() -> bool:
    return importlib.util.find_spec("pm") is not None


def _private_child_env(hermes_home: Path) -> dict[str, str]:
    from tools.environments import local as subprocess_env

    if hasattr(subprocess_env, "served_profile_child_env"):
        env = subprocess_env.served_profile_child_env(target_home=hermes_home)
    else:
        env = subprocess_env.hermes_subprocess_env()
        env["HERMES_HOME"] = str(hermes_home)
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(key, None)
    return env


def _server_log_path(hermes_home: Path) -> Path:
    return hermes_home / "logs" / "openviking-server.log"


def _normalize_local_endpoint(value: Any) -> Optional[str]:
    endpoint = _clean_value(value).rstrip("/")
    if not endpoint:
        return None
    if "://" not in endpoint:
        endpoint = f"http://{endpoint}"
    parsed = urlparse(endpoint)
    if parsed.scheme.lower() != "http":
        return None
    if (parsed.hostname or "").lower() not in {"localhost", "127.0.0.1", "::1"}:
        return None
    host = f"[{parsed.hostname}]" if parsed.hostname == "::1" else parsed.hostname
    return f"http://{host}:{parsed.port or _DEFAULT_PORT}"


def _endpoint_bind(endpoint: str) -> tuple[str, int]:
    parsed = urlparse(endpoint)
    return parsed.hostname or "127.0.0.1", parsed.port or _DEFAULT_PORT


def _endpoint_port(endpoint: Optional[str]) -> Optional[int]:
    if not endpoint:
        return None
    try:
        return urlparse(endpoint).port or _DEFAULT_PORT
    except ValueError:
        return None


def _paths_equivalent(left: Any, right: Path) -> bool:
    if not isinstance(left, (str, os.PathLike)):
        return False
    try:
        return Path(left).expanduser().resolve() == right.expanduser().resolve()
    except (OSError, RuntimeError, TypeError, ValueError):
        return False


def _clean_value(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
