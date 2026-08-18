# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Request-local HTTP authentication for Git subprocesses."""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from typing import Mapping

from openviking_cli.exceptions import InvalidArgumentError


@dataclass(frozen=True)
class GitHttpAuthConfig:
    """Validated request-local HTTP Basic credentials for one Git operation."""

    username: str
    token: str = field(repr=False)


def _validate_git_https_auth_url(repo_url: str) -> str:
    normalized = repo_url.strip() if isinstance(repo_url, str) else ""
    if not normalized.lower().startswith("https://"):
        raise InvalidArgumentError(
            "args.auth_config token authentication requires an HTTPS Git URL."
        )
    return normalized


def parse_git_http_auth_config(
    value: object,
    repo_url: str,
) -> GitHttpAuthConfig | None:
    """Validate an ``args.auth_config`` value for an HTTP(S) Git source."""

    if value is None:
        return None
    if not isinstance(value, dict):
        raise InvalidArgumentError("args.auth_config must be an object.")

    if set(value) - {"username", "token"}:
        raise InvalidArgumentError("args.auth_config contains unsupported fields.")

    token = value.get("token")
    if not isinstance(token, str) or not token.strip():
        raise InvalidArgumentError("args.auth_config.token must be a non-empty string.")

    username = value.get("username", "oauth2")
    if not isinstance(username, str) or not username.strip():
        raise InvalidArgumentError("args.auth_config.username must be a non-empty string.")

    _validate_git_https_auth_url(repo_url)

    return GitHttpAuthConfig(username=username.strip(), token=token)


def git_http_basic_auth_header(config: GitHttpAuthConfig) -> str:
    """Return the Git ``http.extraHeader`` value for validated credentials."""

    credential = f"{config.username}:{config.token}".encode()
    encoded = base64.b64encode(credential).decode("ascii")
    return f"Authorization: Basic {encoded}"


def _append_git_process_config(env: dict[str, str], key: str, value: str) -> None:
    """Append a process-local Git config entry without putting it in argv."""

    try:
        count = int(env.get("GIT_CONFIG_COUNT", "0"))
    except ValueError:
        count = 0
    env[f"GIT_CONFIG_KEY_{count}"] = key
    env[f"GIT_CONFIG_VALUE_{count}"] = value
    env["GIT_CONFIG_COUNT"] = str(count + 1)


def build_git_http_auth_env(
    config: GitHttpAuthConfig,
    repo_url: str,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build an isolated child-process environment containing Git HTTP auth."""

    normalized_repo_url = _validate_git_https_auth_url(repo_url)
    env = dict(os.environ if base_env is None else base_env)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "Never"
    _append_git_process_config(env, "credential.helper", "")
    _append_git_process_config(env, f"credential.{normalized_repo_url}.helper", "")
    # Clear inherited extraHeader values before adding the one request-local
    # Authorization value. Git treats extraHeader as multi-valued otherwise.
    _append_git_process_config(env, "http.extraHeader", "")
    scoped_extra_header = f"http.{normalized_repo_url}.extraHeader"
    _append_git_process_config(env, scoped_extra_header, "")
    _append_git_process_config(
        env,
        scoped_extra_header,
        git_http_basic_auth_header(config),
    )
    # Refuse redirects at the same URL-specific precedence as the header, so
    # inherited per-repository config cannot move it to another host or HTTP.
    _append_git_process_config(
        env,
        f"http.{normalized_repo_url}.followRedirects",
        "false",
    )
    return env
