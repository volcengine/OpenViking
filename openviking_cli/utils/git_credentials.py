# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Git credential management for private repository access.

Resolution order (highest to lowest priority):
1. Explicit credentials dict passed to get_token_for_url()
2. GITHUB_TOKEN / GITLAB_TOKEN environment variables
3. ovcli.conf git_credentials map (keyed by hostname)
"""

import json
import os
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from .config.consts import DEFAULT_CONFIG_DIR, DEFAULT_OVCLI_CONF, OPENVIKING_CLI_CONFIG_ENV


def _extract_url_host(url: str) -> str:
    """Extract normalized hostname from a URL.

    Handles both standard HTTP(S) URLs and git@ SSH URLs.
    """
    if url.startswith("git@"):
        rest = url[4:]
        if ":" not in rest:
            return ""
        return rest.split(":", 1)[0].strip().lower()
    parsed = urlparse(url)
    return (parsed.hostname or parsed.netloc or "").strip().lower()


def is_git_url(url: str) -> bool:
    """Return True if the URL looks like a cloneable git URL."""
    return url.startswith(("https://", "http://", "git@", "git://", "ssh://"))


def inject_token(url: str, token: str) -> str:
    """Inject a token into an HTTPS/HTTP git URL as URL userinfo.

    Transforms ``https://github.com/org/repo`` to
    ``https://token@github.com/org/repo``.

    SSH (``git@``, ``ssh://``) and ``git://`` URLs are returned unchanged —
    token injection is not applicable for those schemes.

    Args:
        url: The repository URL.
        token: The authentication token to embed.

    Returns:
        URL with token injected as userinfo, or the original URL unchanged.
    """
    if not url.startswith(("https://", "http://")):
        return url
    parsed = urlparse(url)
    # Build netloc with token as userinfo, replacing any pre-existing credentials.
    hostname = parsed.hostname or ""
    host_with_port = f"{hostname}:{parsed.port}" if parsed.port else hostname
    netloc_with_token = f"{token}@{host_with_port}"
    return parsed._replace(netloc=netloc_with_token).geturl()


def mask_token_in_url(url: str) -> str:
    """Mask any embedded token in a URL for safe logging.

    Transforms ``https://token@github.com/org/repo`` to
    ``https://***@github.com/org/repo``.

    Args:
        url: URL potentially containing an embedded token.

    Returns:
        URL with token replaced by ``***``, or the original URL if no token found.
    """
    if not url.startswith(("https://", "http://")):
        return url
    parsed = urlparse(url)
    if not parsed.username:
        return url
    hostname = parsed.hostname or ""
    host_with_port = f"{hostname}:{parsed.port}" if parsed.port else hostname
    masked_netloc = f"***@{host_with_port}"
    return parsed._replace(netloc=masked_netloc).geturl()


def get_token_for_url(url: str, credentials: Optional[dict] = None) -> Optional[str]:
    """Return an authentication token for a git URL.

    Resolution order:
    1. ``credentials`` dict (host → token), if provided explicitly.
    2. ``GITHUB_TOKEN`` / ``GITLAB_TOKEN`` environment variables.
    3. ``git_credentials`` map from ``~/.openviking/ovcli.conf``.

    Args:
        url: Repository URL to look up a token for.
        credentials: Optional explicit host-to-token mapping to check first.

    Returns:
        Token string, or ``None`` if no token is found.
    """
    host = _extract_url_host(url)
    if not host:
        return None

    bare_host = host.split(":")[0]  # strip port if present

    # 1. Explicit credentials dict (highest priority)
    if credentials:
        token = credentials.get(host) or credentials.get(bare_host)
        if token:
            return token

    # 2. Environment variable fallback (backward-compatible with existing GITHUB_TOKEN usage)
    if "github" in bare_host:
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            return token
    if "gitlab" in bare_host:
        token = os.environ.get("GITLAB_TOKEN")
        if token:
            return token

    # 3. ovcli.conf git_credentials map
    ovcli_creds = _load_ovcli_git_credentials()
    if ovcli_creds:
        token = ovcli_creds.get(host) or ovcli_creds.get(bare_host)
        if token:
            return token

    return None


def _load_ovcli_git_credentials() -> Optional[dict]:
    """Load the git_credentials map from ovcli.conf without full Pydantic validation.

    Uses a minimal JSON loader to avoid heavy imports and circular dependencies
    at CLI startup time.
    """
    config_path_env = os.environ.get(OPENVIKING_CLI_CONFIG_ENV)
    candidates: list[Path] = []
    if config_path_env:
        candidates.append(Path(config_path_env).expanduser())
    candidates.append(DEFAULT_CONFIG_DIR / DEFAULT_OVCLI_CONF)

    for candidate in candidates:
        if candidate.exists():
            try:
                with open(candidate, "r", encoding="utf-8-sig") as f:
                    data = json.loads(f.read())
                creds = data.get("git_credentials")
                if isinstance(creds, dict):
                    return creds
            except Exception:
                pass
    return None


def save_git_credentials(host: str, token: str, config_path: Optional[str] = None) -> Path:
    """Save a git token for a hostname to ovcli.conf.

    Merges with any existing ``git_credentials`` already present in the file.

    Args:
        host: Hostname to associate the token with (e.g. ``"github.com"``).
        token: Authentication token to store.
        config_path: Explicit path to ovcli.conf; defaults to
            ``~/.openviking/ovcli.conf``.

    Returns:
        Path to the config file that was written.
    """
    if config_path:
        path = Path(config_path).expanduser()
    else:
        path = DEFAULT_CONFIG_DIR / DEFAULT_OVCLI_CONF

    # Load existing config (the file may not exist yet).
    data: dict = {}
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                data = json.loads(f.read())
        except Exception:
            data = {}

    # Merge into existing credentials (don't overwrite unrelated keys).
    existing_creds = data.get("git_credentials")
    if not isinstance(existing_creds, dict):
        existing_creds = {}
    existing_creds[host] = token
    data["git_credentials"] = existing_creds

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return path
