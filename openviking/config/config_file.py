"""Versioned, atomic edits to the actual startup configuration file."""

import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

from openviking_cli.utils.config.embedding_config import EmbeddingCredential, EmbeddingModelConfig
from openviking_cli.utils.config.open_viking_config import (
    OpenVikingConfig,
    OpenVikingConfigSingleton,
)
from openviking_cli.utils.config.vlm_config import VLMConfig, VLMCredential

FORM_MODEL_KINDS = ("vlm", "embedding")
# Match complete JSON strings first so references inside them stay quoted.
_JSON_TOKENS = re.compile(r'"(?:[^"\\]|\\.)*"|\$(?:\{[^}]*\}|[\w]+)', re.ASCII)


class _EnvironmentReference(str):
    """An unquoted JSON value supplied by startup environment expansion."""


def _dump(value, **kwargs) -> str:
    references = {}

    def encode(node):
        if isinstance(node, _EnvironmentReference):
            token = uuid4().hex
            references[token] = str(node)
            return token
        if isinstance(node, dict):
            return {key: encode(item) for key, item in node.items()}
        if isinstance(node, list):
            return [encode(item) for item in node]
        return node

    content = json.dumps(encode(value), **kwargs)
    for token, reference in references.items():
        content = content.replace(json.dumps(token), reference)
    return content


def _provider_source(original: dict, provider: str | None) -> dict:
    return next(
        (
            config
            for name, config in (original.get("providers") or {}).items()
            if os.path.expandvars(name).strip().lower()
            == os.path.expandvars(provider or "").strip().lower()
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
    if explicit:
        source = explicit[index] if index < len(explicit) else {}
    else:
        # Legacy backups are normalized into a second credential by VLMConfig.
        if index == 1:
            original = original.get("backup") or {}
        source = {}
    if not embedding and "model" not in source and (explicit or index == 0):
        # A synthesized primary binding must keep inheriting the shared model.
        values.pop("model", None)
    original = dict(original)
    if original.get("provider") is None:
        original["provider"] = original.get("backend") or original.get("default_provider")
    provider = values.get("provider")
    provider_config = _provider_source(original, provider)
    for key, value in values.items():
        # Follow the source of this field only. Matching expanded values globally
        # loses identity when independent environment references have equal values.
        fallbacks = (provider_config, original) if not explicit or key == "api_key" else (original,)
        for node in (source, *fallbacks):
            if key not in node:
                continue
            expanded = json.loads(os.path.expandvars(_dump(node[key])))
            matches = expanded == value
            if key == "provider" and isinstance(expanded, str) and isinstance(value, str):
                matches = expanded.strip().lower() == value.strip().lower()
            if matches:
                values[key] = node[key]
                break
    return values


def _revision(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate(raw: dict) -> OpenVikingConfig:
    try:
        from openviking.server.config import ServerConfig, validate_server_config

        expanded = json.loads(os.path.expandvars(_dump(raw)))
        server = expanded.get("server")
        validate_server_config(ServerConfig.model_validate({} if server is None else server))
        return OpenVikingConfig.from_dict(expanded)
    except (Exception, SystemExit) as exc:
        # Startup validators exit on failure; API validation must keep the server alive.
        # Validation errors may contain API keys from rejected input.
        raise ValueError("Invalid ov.conf configuration; check configuration fields") from exc


def _parse(content: str) -> dict:
    try:
        references = {}

        def quote_reference(match):
            token = match.group()
            if token.startswith('"'):
                return token
            placeholder = uuid4().hex
            references[placeholder] = _EnvironmentReference(token)
            return json.dumps(placeholder)

        def decode(node):
            if isinstance(node, str):
                return references.get(node, node)
            if isinstance(node, dict):
                return {key: decode(value) for key, value in node.items()}
            if isinstance(node, list):
                return [decode(value) for value in node]
            return node

        raw = decode(json.loads(_JSON_TOKENS.sub(quote_reference, content.lstrip("\ufeff"))))
        if not isinstance(raw, dict):
            raise ValueError()
        return raw
    except ValueError as exc:
        raise ValueError("ov.conf must contain a JSON object") from exc


def _read(path: Path):
    data = path.read_bytes()
    try:
        raw = _parse(data.decode("utf-8-sig"))
    except UnicodeError as exc:
        raise ValueError("ov.conf must contain UTF-8 JSON") from exc
    return data, raw, _validate(raw)


def _path() -> Path:
    path = OpenVikingConfigSingleton.get_config_file()
    if path is None:
        raise ValueError("Server was initialized without a configuration file")
    return path


def _structured_environment_references(node) -> list[str]:
    if isinstance(node, _EnvironmentReference):
        expanded = json.loads(os.path.expandvars(_dump(node)))
        return [str(node)] if isinstance(expanded, (dict, list)) else []
    values = node.values() if isinstance(node, dict) else node if isinstance(node, list) else []
    return list(
        dict.fromkeys(ref for value in values for ref in _structured_environment_references(value))
    )


def _model_view(raw: dict, config: OpenVikingConfig) -> dict:
    models = {}
    for kind in FORM_MODEL_KINDS:
        model = getattr(config, kind)
        value = model.model_dump(exclude_none=True)
        original = raw.get(kind) or {}
        references = _structured_environment_references(original)
        if references:
            # An object/array reference has no literal fields to project. Keep it
            # opaque rather than exposing or materializing environment secrets.
            models[kind] = {
                "config": {},
                "environment_references": references,
            }
            continue
        # Display literal environment references; never replace them with resolved secrets.
        value = _merge(value, original)
        if kind == "vlm":
            # VLM normalization synthesizes provider maps from parent fields.
            # Return only the literal map to avoid exposing expanded secrets.
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
        models[kind] = {
            "config": value,
        }
    return models


def preview_config_file(content: str, settings: dict | None = None) -> dict:
    """Validate a draft and project its form fields without writing or publishing it."""
    raw = _parse(content)
    if settings:
        _apply_model_changes(raw, settings)
        content = _dump(raw, ensure_ascii=False, indent=2) + "\n"
    config = _validate(raw)
    return {"content": content, "models": _model_view(raw, config)}


def read_config_file() -> dict:
    path = _path()
    data, raw, config = _read(path)
    active = OpenVikingConfigSingleton.get_instance()
    startup_revision = OpenVikingConfigSingleton.get_config_file_revision()
    return {
        "content": data.decode("utf-8-sig"),
        "models": _model_view(raw, config),
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
        if isinstance(result.get(key), _EnvironmentReference) and value == result[key]:
            continue
        result[key] = (
            _merge(result[key], value)
            if isinstance(value, dict)
            and isinstance(result.get(key), dict)
            and key not in {"extra_headers", "extra_body", "extra_request_body"}
            else value
        )
    return result


def _restore_environment_references(original, value):
    if isinstance(original, _EnvironmentReference) and value == original:
        return original
    if isinstance(original, dict) and isinstance(value, dict):
        return {
            key: _restore_environment_references(original.get(key), item)
            for key, item in value.items()
        }
    if isinstance(original, list) and isinstance(value, list):
        return [
            _restore_environment_references(
                original[index] if index < len(original) else None, item
            )
            for index, item in enumerate(value)
        ]
    return value


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
            legacy = VLMConfig.model_validate(
                json.loads(os.path.expandvars(_dump({**result, "credentials": []})))
            )
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
    original_bindings = {}
    if old:
        parent = (EmbeddingModelConfig if embedding else VLMConfig).model_validate(
            json.loads(os.path.expandvars(_dump(old)))
        )
        for index, credential in enumerate(parent.credentials or [parent]):
            original = _binding_values(credential, parent, embedding, old, index)
            original_bindings[original.get("id") or f"credential-{index}"] = original
    result["credentials"] = []
    for index, binding in enumerate(changes["credentials"]):
        original = original_bindings.get(binding.get("id") or f"credential-{index}", {})
        # JSON requests carry references as plain strings. Preserve their original
        # quoting when the form keeps the same field, including after reordering.
        binding = _restore_environment_references(original, binding)
        result["credentials"].append(_merge(defaults, binding))
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
            if _structured_environment_references(raw.get(kind)):
                raise ValueError(
                    "Model objects supplied by environment references require file editing"
                )
            raw[kind] = _merge_model(raw.get(kind) or {}, value, kind)
        else:
            raise ValueError("Model configuration must be an object")


def save_config_file(content: str, revision: str) -> dict:
    """Save one startup-file revision; never update the running configuration."""
    path = _path()
    lock_fd = os.open(path.with_name(f".{path.name}.studio.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(lock_fd, "r+b") as lock:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock, fcntl.LOCK_EX)
        data, _, _ = _read(path)
        if not revision or revision != _revision(data):
            raise ValueError("ov.conf changed; reload before saving")
        mode = stat.S_IMODE(path.stat().st_mode)
        if not mode & 0o222 or not os.access(path, os.W_OK):
            raise ValueError("ov.conf is read-only")
        raw = _parse(content)
        _validate(raw)
        if path.read_bytes() != data:
            raise ValueError("ov.conf changed; reload before saving")
        output = content.encode("utf-8")
        if output != data:
            _atomic_write(path.with_name(f"{path.name}.studio.bak"), data, 0o600)
            _atomic_write(path, output, mode)
    return {"revision": _revision(output), "restart_required": True}
