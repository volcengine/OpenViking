# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Stable runtime loader for vectordb native engine variants."""

from __future__ import annotations

import importlib
import importlib.util
import os
import platform
import sys
from pathlib import Path
from types import ModuleType

from ._python_api import build_abi3_exports

_BACKEND_MODULES = {
    "x86_sse3": "_x86_sse3",
    "x86_avx2": "_x86_avx2",
    "x86_avx512": "_x86_avx512",
    "native": "_native",
}
_X86_DISPLAY_ORDER = ("x86_sse3", "x86_avx2", "x86_avx512")
_X86_PRIORITY = ("x86_avx512", "x86_avx2", "x86_sse3")
_REQUEST_ALIASES = {
    "sse3": "x86_sse3",
    "avx2": "x86_avx2",
    "avx512": "x86_avx512",
}


def _is_x86_machine(machine: str | None = None) -> bool:
    normalized = (machine or platform.machine() or "").strip().lower()
    return any(token in normalized for token in ("x86_64", "amd64", "x64", "i386", "i686"))


def _module_exists(module_name: str) -> bool:
    return importlib.util.find_spec(f".{module_name}", __name__) is not None


def _available_variants(is_x86: bool) -> tuple[str, ...]:
    ordered = _X86_DISPLAY_ORDER if is_x86 else ("native",)
    return tuple(variant for variant in ordered if _module_exists(_BACKEND_MODULES[variant]))


def _supported_x86_variants() -> set[str]:
    supported = {"x86_sse3"}
    if not _module_exists("_x86_caps"):
        return supported

    try:
        caps = importlib.import_module("._x86_caps", __name__)
    except ImportError:
        return supported

    reported = getattr(caps, "get_supported_variants", lambda: [])()
    for variant in reported:
        normalized = str(variant).strip().lower()
        if normalized in _BACKEND_MODULES:
            supported.add(normalized)
    return supported


def _normalize_requested_variant(value: str | None) -> str:
    normalized = (value or "auto").strip().lower()
    return _REQUEST_ALIASES.get(normalized, normalized)


def _validate_forced_variant(
    requested: str, *, is_x86: bool, available: tuple[str, ...], supported_x86: set[str]
) -> None:
    if is_x86 and requested == "native":
        raise ImportError("OV_ENGINE_VARIANT=native is only valid on non-x86 platforms")

    if not is_x86 and requested != "native":
        raise ImportError(
            f"OV_ENGINE_VARIANT={requested} is not valid on non-x86 platforms; use native"
        )

    if requested not in _BACKEND_MODULES:
        raise ImportError(f"Unknown OV_ENGINE_VARIANT={requested}")

    if requested not in available:
        raise ImportError(
            f"Requested engine variant {requested} is not packaged in this wheel. "
            f"Available variants: {', '.join(available) or 'none'}"
        )

    if is_x86 and requested not in supported_x86:
        raise ImportError(f"Requested engine variant {requested} is not supported by this CPU")


def _select_variant() -> tuple[str | None, tuple[str, ...], str | None]:
    is_x86 = _is_x86_machine()
    available = _available_variants(is_x86)
    requested = _normalize_requested_variant(os.environ.get("OV_ENGINE_VARIANT"))

    if requested != "auto":
        supported_x86 = _supported_x86_variants() if is_x86 else set()
        _validate_forced_variant(
            requested, is_x86=is_x86, available=available, supported_x86=supported_x86
        )
        return requested, available, None

    if not is_x86:
        if "native" not in available:
            return None, available, "Native engine backend is missing from this wheel"
        return "native", available, None

    supported_x86 = _supported_x86_variants()
    for variant in _X86_PRIORITY:
        if variant in available and variant in supported_x86:
            return variant, available, None

    if "x86_sse3" in available:
        return "x86_sse3", available, None

    return None, available, "No compatible x86 engine backend was packaged in this wheel"


def _load_backend(variant: str) -> ModuleType:
    module_name = _BACKEND_MODULES[variant]
    module_path = Path(__file__).resolve().parent
    qualified_name = f"{__name__}.{module_name}"

    if qualified_name in sys.modules:
        return sys.modules[qualified_name]

    for suffix in importlib.machinery.EXTENSION_SUFFIXES:
        if "abi3" not in suffix:
            continue
        candidate = module_path / f"{module_name}{suffix}"
        if not candidate.exists():
            continue
        spec = importlib.util.spec_from_file_location(qualified_name, candidate)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules[qualified_name] = module
        spec.loader.exec_module(module)
        return module

    return importlib.import_module(f".{module_name}", __name__)


def _export_backend(module: ModuleType) -> tuple[str, ...]:
    if getattr(module, "_ENGINE_BACKEND_API", None) == "abi3-v1":
        exports = build_abi3_exports(module)
        for name, value in exports.items():
            globals()[name] = value
        return tuple(exports)

    names = getattr(module, "__all__", None)
    if names is None:
        names = tuple(name for name in dir(module) if not name.startswith("_"))

    for name in names:
        globals()[name] = getattr(module, name)

    return tuple(names)


class _MissingBackendSymbol:
    def __init__(self, symbol_name: str, message: str):
        self._symbol_name = symbol_name
        self._message = message

    def __call__(self, *args, **kwargs):
        raise ImportError(f"{self._message}. Missing symbol: {self._symbol_name}")

    def __getattr__(self, name: str):
        return _MissingBackendSymbol(f"{self._symbol_name}.{name}", self._message)

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return f"<missing vectordb engine symbol {self._symbol_name}>"


_SELECTED_VARIANT, AVAILABLE_ENGINE_VARIANTS, _ENGINE_IMPORT_ERROR = _select_variant()
if _SELECTED_VARIANT is None:
    ENGINE_VARIANT = "unavailable"
    _BACKEND = None
    _EXPORTED_NAMES = ()
else:
    ENGINE_VARIANT = _SELECTED_VARIANT
    _BACKEND = _load_backend(ENGINE_VARIANT)
    _EXPORTED_NAMES = _export_backend(_BACKEND)


def __getattr__(name: str):
    if _BACKEND is None and _ENGINE_IMPORT_ERROR is not None:
        return _MissingBackendSymbol(name, _ENGINE_IMPORT_ERROR)
    raise AttributeError(name)


__all__ = tuple(
    sorted(
        set(_EXPORTED_NAMES).union(
            {
                "AVAILABLE_ENGINE_VARIANTS",
                "ENGINE_VARIANT",
            }
        )
    )
)
