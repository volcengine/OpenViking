"""Versioned, atomic model edits to the actual startup configuration file."""

import fcntl
import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path

from openviking_cli.utils.config.embedding_config import EmbeddingCredential
from openviking_cli.utils.config.open_viking_config import (
    OpenVikingConfig,
    OpenVikingConfigSingleton,
)
from openviking_cli.utils.config.vlm_config import VLMCredential

MODEL_KINDS = ("vlm", "embedding", "query_planner", "rerank")


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
    original = dict(original)
    if original.get("provider") is None:
        original["provider"] = original.get("backend") or original.get("default_provider")
    provider = values.get("provider")
    provider_config = next(
        (
            config
            for name, config in (original.get("providers") or {}).items()
            if os.path.expandvars(name).strip().lower() == provider
        ),
        {},
    )
    for key, value in values.items():
        # Follow the source of this field only. Matching expanded values globally
        # loses identity when independent environment references have equal values.
        fallbacks = (provider_config, original) if not explicit or key == "api_key" else (original,)
        for node in (source, *fallbacks):
            if key in node and json.loads(os.path.expandvars(json.dumps(node[key]))) == value:
                values[key] = node[key]
                break
    return values


def _revision(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate(raw: dict) -> OpenVikingConfig:
    try:
        return OpenVikingConfig.from_dict(json.loads(os.path.expandvars(json.dumps(raw))))
    except Exception as exc:
        # Validation errors may contain API keys from rejected input.
        raise ValueError("Invalid ov.conf configuration; check model fields") from exc


def _read(path: Path):
    data = path.read_bytes()
    try:
        raw = json.loads(data.decode("utf-8-sig"))
        if not isinstance(raw, dict):
            raise ValueError()
    except (ValueError, UnicodeError) as exc:
        raise ValueError("ov.conf must contain a JSON object") from exc
    return data, raw, _validate(raw)


def _path() -> Path:
    path = OpenVikingConfigSingleton.get_config_file()
    if path is None:
        raise ValueError("Server was initialized without a configuration file")
    return path


def read_model_file() -> dict:
    path = _path()
    data, raw, config = _read(path)
    models = {}
    for kind in MODEL_KINDS:
        inherited = kind == "query_planner" and (
            config.query_planner is None or not config.query_planner._has_any_config()
        )
        model = config.get_query_planner() if inherited else getattr(config, kind)
        value = model.model_dump(exclude_none=True)
        original = raw.get("vlm" if inherited else kind) or {}
        # Display literal environment references; never replace them with resolved secrets.
        value = _merge(value, original)
        if kind in ("vlm", "query_planner"):
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
        if kind != "rerank":
            for section, original_section, section_model in sections:
                bindings = section_model.credentials or [section_model]
                section["credentials"] = [
                    _binding_values(
                        binding, section_model, kind == "embedding", original_section, index
                    )
                    for index, binding in enumerate(bindings)
                ]
        models[kind] = {
            "source": "vlm" if inherited else "server",
            "config": value,
            **({"available": model.is_available()} if kind == "rerank" else {}),
        }
    active = OpenVikingConfigSingleton.get_instance()
    pending = any(
        getattr(config, kind).model_dump() != getattr(active, kind).model_dump()
        if getattr(config, kind) is not None and getattr(active, kind) is not None
        else getattr(config, kind) != getattr(active, kind)
        for kind in MODEL_KINDS
    )
    return {
        "settings": {kind: raw[kind] for kind in MODEL_KINDS if kind in raw},
        "models": models,
        "revision": _revision(data),
        "file_path": str(path),
        "writable": bool(path.stat().st_mode & 0o222)
        and os.access(path, os.W_OK)
        and os.access(path.parent, os.W_OK),
        "restart_required": pending,
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
        if not provider and len(providers) == 1:
            provider = next(iter(providers))
        defaults.update(providers.get(provider) or {})
        if provider:
            defaults["provider"] = provider
    defaults.update({key: value for key, value in result.items() if key in fields})
    if embedding and defaults.get("provider") is None and result.get("backend") is not None:
        defaults["provider"] = result["backend"]
    # Materialize inherited bindings before removing legacy fallbacks. Explicit
    # null/empty values must mean clearing, not re-inheriting obsolete secrets.
    result["credentials"] = [
        {**{key: value for key, value in defaults.items() if key in fields}, **binding}
        for binding in changes["credentials"]
    ]
    for key in fields:
        result.pop(key, None)
    result.pop("backend", None)
    if not embedding:
        for key in ("providers", "default_provider", "backup"):
            result.pop(key, None)
    return result


def _merge_model(old: dict, changes: dict, kind: str) -> dict:
    if kind in ("vlm", "query_planner"):
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
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_model_file(settings: dict, revision: str) -> dict:
    if not settings or set(settings) - set(MODEL_KINDS):
        raise ValueError("Only model sections can be edited")
    path = _path()
    lock_fd = os.open(path.with_name(f".{path.name}.studio.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(lock_fd, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data, raw, _ = _read(path)
        if not revision or revision != _revision(data):
            raise ValueError("ov.conf changed; reload before saving")
        mode = stat.S_IMODE(path.stat().st_mode)
        if not mode & 0o222 or not os.access(path, os.W_OK):
            raise ValueError("ov.conf is read-only")
        for kind, value in settings.items():
            if value is None:
                if kind != "query_planner":
                    raise ValueError("Only query_planner can be reset to inherit VLM")
                raw.pop(kind, None)
            elif isinstance(value, dict):
                raw[kind] = _merge_model(raw.get(kind) or {}, value, kind)
            else:
                raise ValueError("Model configuration must be an object")
        _validate(raw)
        if path.read_bytes() != data:
            raise ValueError("ov.conf changed; reload before saving")
        _atomic_write(path.with_name(f"{path.name}.studio.bak"), data, 0o600)
        _atomic_write(path, (json.dumps(raw, ensure_ascii=False, indent=2) + "\n").encode(), mode)
    return {"revision": _revision(path.read_bytes()), "restart_required": True}
