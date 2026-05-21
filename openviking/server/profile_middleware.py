# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""HTTP request profiling middleware helpers."""

from __future__ import annotations

import cProfile
import json
import site
import sysconfig
from pathlib import Path
from typing import Awaitable, Callable

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.responses import Response as StarletteResponse
from starlette.responses import StreamingResponse

PROFILE_TRUE_VALUES = {"1", "true", "yes", "on"}
PROFILE_SORT_BY = "cumulative"
PROFILE_TOP_N = 100
PROFILE_MAX_CHARS = 16 * 1024
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _build_profile_roots() -> tuple[Path, ...]:
    roots: list[Path] = [_PROJECT_ROOT]

    try:
        roots.extend(Path(path) for path in site.getsitepackages())
    except Exception:
        pass

    try:
        user_site = site.getusersitepackages()
        if user_site:
            roots.append(Path(user_site))
    except Exception:
        pass

    for key in ("stdlib", "platstdlib"):
        try:
            stdlib_path = sysconfig.get_paths().get(key)
            if stdlib_path:
                roots.append(Path(stdlib_path))
        except Exception:
            pass

    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        raw = root.as_posix()
        resolved = root.resolve().as_posix()
        for candidate in (raw, resolved):
            if candidate and candidate not in seen:
                deduped.append(Path(candidate))
                seen.add(candidate)
    return tuple(deduped)


PROFILE_ROOTS = _build_profile_roots()


def profile_enabled(request: Request) -> bool:
    server_config = getattr(request.app.state, "config", None)
    if server_config is not None and not getattr(server_config, "profile_enabled", False):
        return False
    value = request.query_params.get("profile")
    if value is None:
        return False
    return value.strip().lower() in PROFILE_TRUE_VALUES


def _infer_module_suffix(path: Path) -> str:
    parts = list(path.parts)
    if not parts:
        return path.name

    suffix = [path.name]
    for part in reversed(parts[:-1]):
        if part.isidentifier():
            suffix.insert(0, part)
            continue
        break

    if len(suffix) >= 2:
        return "/".join(suffix)
    if len(parts) >= 2:
        return "/".join(parts[-2:])
    return path.name


def _sanitize_profile_path(path: str) -> str:
    raw_path = Path(path)
    candidates = [raw_path]
    try:
        resolved = raw_path.resolve()
    except Exception:
        resolved = None
    if resolved is not None and resolved != raw_path:
        candidates.append(resolved)

    for candidate in candidates:
        parts = candidate.parts

        for marker in ("site-packages", "dist-packages"):
            if marker in parts:
                idx = parts.index(marker)
                if idx + 1 < len(parts):
                    return "/".join(parts[idx + 1 :])

        for idx, part in enumerate(parts):
            if part.startswith("python") and idx + 1 < len(parts):
                suffix = parts[idx + 1 :]
                if suffix:
                    return "/".join(suffix)

        for root in PROFILE_ROOTS:
            try:
                return candidate.relative_to(root).as_posix()
            except ValueError:
                continue

    return _infer_module_suffix(raw_path)


def format_profile_output(profiler: cProfile.Profile) -> list[str]:
    import pstats

    stats = pstats.Stats(profiler)
    stats.sort_stats(PROFILE_SORT_BY)

    rows = list(stats.stats.items())
    if PROFILE_SORT_BY == "cumulative":
        rows.sort(key=lambda item: (item[1][3], item[1][2]), reverse=True)
    else:
        rows.sort(key=lambda item: item[1][2], reverse=True)

    total_calls = getattr(stats, "total_calls", 0)
    prim_calls = getattr(stats, "prim_calls", 0)
    total_tt = getattr(stats, "total_tt", 0.0)

    lines = [
        f"         {total_calls} function calls ({prim_calls} primitive calls) in {total_tt:.3f} seconds",
        "",
        f"   Ordered by: {PROFILE_SORT_BY} time",
        f"   List reduced from {len(rows)} to {min(len(rows), PROFILE_TOP_N)} due to restriction <{PROFILE_TOP_N}>",
        "",
        "   ncalls  tottime  percall  cumtime  percall filename:lineno(function)",
    ]

    for (filename, lineno, funcname), stat in rows[:PROFILE_TOP_N]:
        cc, nc, tt, ct, _callers = stat
        ncalls = f"{cc}/{nc}" if cc != nc else str(nc)
        percall_tt = tt / nc if nc else 0.0
        percall_ct = ct / cc if cc else 0.0
        location = f"{_sanitize_profile_path(filename)}:{lineno}({funcname})"
        lines.append(
            f"{ncalls:>9} {tt:>8.3f} {percall_tt:>8.3f} {ct:>8.3f} {percall_ct:>8.3f} {location}"
        )

    profile_text = "\n".join(lines)
    if len(profile_text) <= PROFILE_MAX_CHARS:
        return lines

    truncated = profile_text[: PROFILE_MAX_CHARS - len("\n... [truncated]")] + "\n... [truncated]"
    return truncated.splitlines()


async def inject_profile_into_response(response, profile_lines: list[str]):
    if isinstance(response, (FileResponse, StreamingResponse)):
        return response

    content_type = response.headers.get("content-type", "").lower()
    if "application/json" not in content_type and "+json" not in content_type:
        return response

    if hasattr(response, "body") and response.body is not None:
        body = response.body
    else:
        body = b""
        async for chunk in response.body_iterator:
            body += chunk

    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return StarletteResponse(
            content=body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )

    if not isinstance(payload, dict):
        return StarletteResponse(
            content=body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )

    payload["profile"] = profile_lines
    rebuilt = JSONResponse(status_code=response.status_code, content=payload)
    for key, value in response.headers.items():
        if key.lower() not in {"content-length", "content-type"}:
            rebuilt.headers[key] = value
    return rebuilt


def create_profile_http_middleware() -> Callable[[Request, Callable[..., Awaitable]], Awaitable]:
    async def add_profile_output(request: Request, call_next: Callable[..., Awaitable]):
        if not profile_enabled(request):
            return await call_next(request)

        profiler = cProfile.Profile()
        profiler.enable()
        try:
            response = await call_next(request)
        finally:
            profiler.disable()

        profile_lines = format_profile_output(profiler)
        return await inject_profile_into_response(response, profile_lines)

    return add_profile_output
