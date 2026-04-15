from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable

DEFAULT_X86_VARIANTS = ("sse3", "avx2", "avx512", "avx512_vnni", "amx")
KNOWN_X86_VARIANTS = frozenset(DEFAULT_X86_VARIANTS)
X86_ARCHITECTURES = ("x86_64", "amd64", "x64", "i386", "i686")


@dataclass(frozen=True)
class EngineBuildConfig:
    is_x86: bool
    primary_extension: str
    cmake_variants: tuple[str, ...]


def _normalize_machine(machine: str | None) -> str:
    return (machine or "").strip().lower()


def is_x86_machine(machine: str | None) -> bool:
    normalized = _normalize_machine(machine)
    return any(token in normalized for token in X86_ARCHITECTURES)


def _normalize_x86_variants(raw_variants: Iterable[str]) -> tuple[str, ...]:
    requested = []
    for variant in raw_variants:
        normalized = variant.strip().lower()
        if not normalized or normalized not in KNOWN_X86_VARIANTS or normalized in requested:
            continue
        requested.append(normalized)

    if "sse3" not in requested:
        requested.insert(0, "sse3")

    return tuple(requested or DEFAULT_X86_VARIANTS)


def get_requested_x86_build_variants(raw_value: str | None = None) -> tuple[str, ...]:
    if raw_value is None:
        raw_value = os.environ.get("OV_X86_BUILD_VARIANTS", "")

    if not raw_value.strip():
        return DEFAULT_X86_VARIANTS

    return _normalize_x86_variants(raw_value.replace(";", ",").split(","))


def get_host_engine_build_config(machine: str | None) -> EngineBuildConfig:
    if is_x86_machine(machine):
        return EngineBuildConfig(
            is_x86=True,
            primary_extension="openviking.storage.vectordb.engine._x86_sse3",
            cmake_variants=get_requested_x86_build_variants(),
        )

    return EngineBuildConfig(
        is_x86=False,
        primary_extension="openviking.storage.vectordb.engine._native",
        cmake_variants=(),
    )
