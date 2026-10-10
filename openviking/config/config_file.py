"""Versioned, atomic edits to the actual startup configuration file."""

import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from openviking_cli.utils.config.embedding_config import EmbeddingCredential
from openviking_cli.utils.config.open_viking_config import (
    OpenVikingConfig,
    OpenVikingConfigSingleton,
)
from openviking_cli.utils.config.vlm_config import VLMConfig, VLMCredential

FORM_MODEL_KINDS = ("vlm", "embedding")
# Reserve environment references for the verbatim file editor on every platform.
# Escaped dollars/percents must stay literal when startup expands the original text.
_FORM_REFERENCE = re.compile(r"\$|%[^%]+%|\\u002[45]", re.IGNORECASE)


def _form_readonly(content: str) -> bool:
    return bool(_FORM_REFERENCE.search(content))


def _provider_source(original: dict, provider: str | None) -> dict:
    return next(
        (
            config
            for name, config in (original.get("providers") or {}).items()
            if name.strip().lower() == (provider or "").strip().lower()
        ),
        {},
    )


def _binding_values(binding, parent, embedding: bool, original: dict, index: int) -> dict:
    values = (
        {
            key: value
            for key in EmbeddingCredential.model_fields
            if (value := getattr(binding, key, None) or getattr(parent, key, None)) is not None
        }
        if embedding
        else binding.model_dump(exclude_none=True)
    )
    explicit = original.get("credentials") or []
    source = explicit[index] if index < len(explicit) else {}
    if not embedding and "model" not in source and (explicit or index == 0):
        # A synthesized primary binding must keep inheriting the shared model.
        values.pop("model", None)
    return values


def _revision(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate(content: str, server_overrides: dict | None = None) -> OpenVikingConfig:
    try:
        from openviking.server.config import ServerConfig, validate_server_config

        expanded = json.loads(os.path.expandvars(content.lstrip("\ufeff")))
        server = expanded.get("server")
        server_config = ServerConfig.model_validate({} if server is None else server)
        # The CLI reapplies these arguments after loading ov.conf on every restart.
        if server_overrides:
            server_config = server_config.model_copy(update=server_overrides)
        validate_server_config(server_config)
        return OpenVikingConfig.from_dict(expanded)
    except (Exception, SystemExit) as exc:
        # Startup validators exit on failure; API validation must keep the server alive.
        # Validation errors may contain API keys from rejected input.
        raise ValueError("Invalid ov.conf configuration; check configuration fields") from exc


def _parse(content: str) -> dict:
    try:
        raw = json.loads(content.lstrip("\ufeff"))
        if not isinstance(raw, dict):
            raise ValueError()
        return raw
    except ValueError as exc:
        raise ValueError("ov.conf must contain a JSON object") from exc


def _read(path: Path, server_overrides: dict | None = None):
    data = path.read_bytes()
    try:
        content = data.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError("ov.conf must contain UTF-8 JSON") from exc
    config = _validate(content, server_overrides)
    return data, content, config


def _path() -> Path:
    path = OpenVikingConfigSingleton.get_config_file()
    if path is None:
        raise ValueError("Server was initialized without a configuration file")
    return path


def _model_view(content: str, config: OpenVikingConfig) -> dict:
    if _form_readonly(content):
        # Never project expanded credentials into the form response.
        return {kind: {"config": {}} for kind in FORM_MODEL_KINDS}
    raw = _parse(content)
    models = {}
    for kind in FORM_MODEL_KINDS:
        model = getattr(config, kind)
        value = model.model_dump(exclude_none=True)
        original = raw.get(kind) or {}
        value = _merge(value, original)
        if kind == "vlm":
            # VLM normalization synthesizes provider maps from parent fields.
            # Return only the explicit map to preserve legacy form bindings.
            value["providers"] = original.get("providers") or {}
        sections = [(value, original, model)]
        if kind == "embedding":
            sections = [
                (value[mode], original.get(mode) or {}, getattr(model, mode))
                for mode in ("dense", "sparse", "hybrid")
                if getattr(model, mode) is not None
            ]
        for section, original_section, section_model in sections:
            bindings = section_model.credentials
            if kind == "embedding" and not bindings:
                bindings = [section_model]
            section["credentials"] = [
                _binding_values(
                    binding, section_model, kind == "embedding", original_section, index
                )
                for index, binding in enumerate(bindings)
            ]
            if kind == "embedding":
                for index, binding in enumerate(section["credentials"]):
                    if not binding.get("id"):
                        binding["id"] = f"credential-{index}"
        models[kind] = {"config": value}
    return models


def preview_config_file(
    content: str, settings: dict | None = None, server_overrides: dict | None = None
) -> dict:
    """Validate a draft and project its form fields without writing or publishing it."""
    if settings:
        if _form_readonly(content):
            raise ValueError("Configuration with environment references requires file editing")
        raw = _parse(content)
        _apply_model_changes(raw, settings)
        content = json.dumps(raw, ensure_ascii=False, indent=2) + "\n"
        if _form_readonly(content):
            raise ValueError("Environment references must be entered through the file editor")
    config = _validate(content, server_overrides)
    return {
        "content": content,
        "models": _model_view(content, config),
        "form_readonly": _form_readonly(content),
    }


def read_config_file(server_overrides: dict | None = None) -> dict:
    path = _path()
    data, content, config = _read(path, server_overrides)
    active = OpenVikingConfigSingleton.get_instance()
    startup_revision = OpenVikingConfigSingleton.get_config_file_revision()
    return {
        "content": content,
        "models": _model_view(content, config),
        "form_readonly": _form_readonly(content),
        "revision": _revision(data),
        "file_path": str(path),
        "writable": bool(path.stat().st_mode & 0o222)
        and os.access(path, os.W_OK)
        and os.access(path.parent, os.W_OK),
        "restart_required": (
            _revision(data) != startup_revision
            if startup_revision is not None
            else config.model_dump() != active.model_dump()
        ),
    }


def _merge(old: dict, changes: dict) -> dict:
    result = dict(old)
    for key, value in changes.items():
        result[key] = (
            _merge(result[key], value)
            if isinstance(value, dict)
            and isinstance(result.get(key), dict)
            and key not in {"extra_headers", "extra_body", "extra_request_body"}
            else value
        )
    return result


def _merge_credentials(old: dict, changes: dict, embedding: bool) -> dict:
    result = _merge(old, changes)
    if "credentials" not in changes:
        return result
    if not isinstance(changes["credentials"], list) or not all(
        isinstance(binding, dict) for binding in changes["credentials"]
    ):
        return result
    fields = set((EmbeddingCredential if embedding else VLMCredential).model_fields) - {
        "id",
        "model",
    }
    defaults = {}
    if not embedding:
        providers = result.get("providers") or {}
        provider = result.get("provider") or result.get("backend") or result.get("default_provider")
        if not provider and providers:
            legacy = VLMConfig.model_validate({**result, "credentials": []})
            _, provider = legacy.get_provider_config()
        defaults.update(_provider_source(result, provider))
        if provider:
            defaults["provider"] = provider
    defaults.update({key: value for key, value in result.items() if key in fields})
    if embedding and defaults.get("provider") is None and result.get("backend") is not None:
        defaults["provider"] = result["backend"]
    # Materialize inherited bindings before removing legacy fallbacks. Explicit
    # null/empty values must mean clearing, not re-inheriting obsolete secrets.
    defaults = {key: value for key, value in defaults.items() if key in fields}
    result["credentials"] = [_merge(defaults, binding) for binding in changes["credentials"]]
    for key in fields:
        result.pop(key, None)
    result.pop("backend", None)
    if not embedding:
        for key in ("providers", "default_provider", "backup"):
            result.pop(key, None)
    return result


def _merge_model(old: dict, changes: dict, kind: str) -> dict:
    if kind == "vlm":
        return _merge_credentials(old, changes, False)
    result = _merge(old, changes)
    if kind == "embedding":
        for mode in ("dense", "sparse", "hybrid"):
            if isinstance(changes.get(mode), dict):
                result[mode] = _merge_credentials(old.get(mode) or {}, changes[mode], True)
    return result


def _atomic_write(path: Path, data: bytes, mode: int) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            if sys.platform == "win32":
                os.chmod(temporary, mode)
            else:
                os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if sys.platform != "win32":
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _apply_model_changes(raw: dict, settings: dict) -> None:
    if not settings or set(settings) - set(FORM_MODEL_KINDS):
        raise ValueError("Only model sections can be edited through the form")
    for kind, value in settings.items():
        if isinstance(value, dict):
            raw[kind] = _merge_model(raw.get(kind) or {}, value, kind)
        else:
            raise ValueError("Model configuration must be an object")


@contextmanager
def _config_file_lock(path: Path):
    """Serialize Studio writes and restart recovery across processes."""
    lock_fd = os.open(path.with_name(f".{path.name}.studio.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(lock_fd, "r+b") as lock:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def save_config_file(content: str, revision: str, server_overrides: dict | None = None) -> dict:
    """Save one startup-file revision; never update the running configuration."""
    path = _path()
    with _config_file_lock(path):
        data, _, _ = _read(path, server_overrides)
        if not revision or revision != _revision(data):
            raise ValueError("ov.conf changed; reload before saving")
        mode = stat.S_IMODE(path.stat().st_mode)
        if not mode & 0o222 or not os.access(path, os.W_OK):
            raise ValueError("ov.conf is read-only")
        _validate(content, server_overrides)
        if path.read_bytes() != data:
            raise ValueError("ov.conf changed; reload before saving")
        output = content.encode("utf-8")
        if output != data:
            _atomic_write(path.with_name(f"{path.name}.studio.bak"), data, 0o600)
            _atomic_write(path, output, mode)
    return {"revision": _revision(output), "restart_required": True}
