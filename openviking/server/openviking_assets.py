# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Server-side parsing and validation for OpenViking Assets manifests."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import math
import os
import re
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from openviking.core.namespace import classify_uri, uri_parts
from openviking.core.uri_validation import validate_content_target_uri, validate_viking_uri
from openviking.server.identity import RequestContext
from openviking.server.local_input_guard import require_remote_resource_source
from openviking.utils.git_auth import (
    GitHttpAuthConfig,
    build_git_http_auth_env,
)
from openviking_cli.exceptions import (
    DeadlineExceededError,
    InvalidArgumentError,
    NotFoundError,
    PermissionDeniedError,
    UnavailableError,
)

PROTOCOL = "openviking-assets/1"
GIT_PREFLIGHT_TIMEOUT_SECONDS = 15.0
_ASSET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_FULL_COMMIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_REMOTE_HELPER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*::")
_NETWORK_FAILURE_PATTERNS = (
    "could not resolve host",
    "connection refused",
    "connection timed out",
    "network is unreachable",
    "no route to host",
    "failed to connect",
    "temporary failure in name resolution",
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _GitDefaults(_StrictModel):
    auth_ref: str | None = None
    watch_interval: float | None = None


class _Defaults(_StrictModel):
    git: _GitDefaults | None = None


class _CatalogAsset(_StrictModel):
    name: str
    connector: str
    description: str = ""
    to: str | None = None
    params: dict[str, Any]
    auth_ref: str | None = None
    watch_interval: float | None = None


class _Catalog(_StrictModel):
    protocol: str
    defaults: _Defaults | None = None
    catalog: list[_CatalogAsset]


class _Manifest(_StrictModel):
    """One build: asset definitions under ``catalog``, chosen names under ``assets``.

    ``catalog`` holds the definitions themselves, inline here or supplied as a
    separate catalog file. ``assets`` names which of them this build applies;
    omitting it applies every defined asset.
    """

    protocol: str | None = None
    defaults: _Defaults | None = None
    catalog: list[_CatalogAsset] | str | None = None
    include: list[str] | None = None
    assets: list[str] | None = None


class _GitParams(_StrictModel):
    repo_url: str
    branch: str | None = None
    commit: str | None = None


class ResolvedAsset(_StrictModel):
    """A manifest selection joined with its catalog entry and defaults."""

    name: str
    connector: str
    to: str | None = None
    repo_url: str
    branch: str | None = None
    commit: str | None = None
    auth_ref: str | None = None
    watch_interval: float
    locator: str
    git_ref: str
    asset_id: str


class ResolveResult(_StrictModel):
    protocol: str = PROTOCOL
    manifest: str
    catalog: str
    assets: list[ResolvedAsset]


def _validation_message(what: str, label: str, exc: ValidationError) -> str:
    error = exc.errors(include_url=False)[0]
    location = ".".join(str(part) for part in error.get("loc", ()))
    detail = str(error.get("msg") or "invalid value")
    suffix = f" at '{location}'" if location else ""
    return f"{what} '{label}'{suffix}: {detail}"


def _parse_yaml(text: str, model: type[_StrictModel], what: str, label: str) -> _StrictModel:
    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise InvalidArgumentError(f"{what} '{label}': invalid YAML: {exc}") from exc
    if not isinstance(value, dict):
        raise InvalidArgumentError(f"{what} '{label}' must contain a YAML mapping")
    try:
        return model.model_validate(value)
    except ValidationError as exc:
        raise InvalidArgumentError(_validation_message(what, label, exc)) from exc


def _check_watch_interval(value: float | None, where: str) -> float | None:
    if value is not None and (not math.isfinite(value) or value < 0):
        raise InvalidArgumentError(f"{where}: 'watch_interval' must be >= 0")
    return value


def _normalize_asset_target_uri(
    value: str | None,
    where: str,
    ctx: RequestContext | None,
) -> str | None:
    """Validate one optional exact resource target and return its normalized URI."""

    if value is None:
        return None
    target = value.strip()
    if not target:
        raise InvalidArgumentError(f"{where}: 'to' must be a non-empty string when set")

    if ctx is not None:
        # Use the same request-boundary URI, namespace-shape and access checks as
        # add_resource itself. In particular, this expands current-user
        # shorthand before the plan reaches the CLI and its local State file.
        return validate_content_target_uri(target, ctx, kind="resource", field_name="to")

    # Direct resolver callers do not always have a request identity. Preserve
    # that API while still enforcing the public URI grammar and resource shape;
    # HTTP callers take the context-aware path above.
    normalized = validate_viking_uri(target, field_name="to").rstrip("/")
    parts = uri_parts(normalized)
    classification = classify_uri(normalized)
    if parts[:1] == ["resources"] or (
        classification.context_type == "resource" and classification.content_index is not None
    ):
        return normalized
    raise InvalidArgumentError(f"{where}: 'to' must target resource content")


def _strip_port(host: str) -> str:
    head, separator, tail = host.rpartition(":")
    if separator and tail and tail.isascii() and tail.isdigit():
        return head
    return host


def normalize_repo_url(url: str) -> str:
    """Normalize supported Git URL forms into a stable host/path locator."""

    value = url.strip()
    if not value:
        return ""
    lowered = value.lower()
    protocol = next(
        (
            prefix
            for prefix in ("ssh://", "git://", "http://", "https://")
            if lowered.startswith(prefix)
        ),
        None,
    )
    if protocol:
        value = value[len(protocol) :]
    if "@" in value:
        user, remainder = value.split("@", 1)
        if user and "/" not in user and ":" not in user:
            value = remainder
    if protocol is None:
        slash = value.find("/")
        colon = value.find(":")
        if colon >= 0 and (slash < 0 or colon < slash):
            value = f"{value[:colon]}/{value[colon + 1 :]}"
    while "//" in value:
        value = value.replace("//", "/")
    if "/" in value:
        host, path = value.split("/", 1)
        value = f"{_strip_port(host).lower()}/{path}"
    else:
        value = _strip_port(value).lower()
    if value.lower().endswith(".git"):
        value = value[:-4]
    return value.rstrip("/")


def _validate_clone_url(url: str, asset_name: str) -> None:
    label = f"asset '{asset_name}' params.repo_url"
    if not url.strip():
        raise InvalidArgumentError(f"{label} is empty")
    if any(ord(char) < 0x20 for char in url):
        raise InvalidArgumentError(f"{label} contains control characters")
    value = url.strip()
    if value.startswith("-"):
        raise InvalidArgumentError(f"{label} starts with '-' (git would read it as a flag)")
    if _REMOTE_HELPER_RE.match(value):
        raise InvalidArgumentError(
            f"{label} uses a git remote-helper transport ('helper::...'); "
            "use https://, ssh://, git://, or a git@host:path URL"
        )


def _asset_id(connector: str, locator: str, git_ref: str) -> str:
    identity = f"{connector}\n{locator}\n{git_ref}".encode()
    return hashlib.sha1(identity).hexdigest()[:12]  # noqa: S324 - stable identity, not security


def _normalize_commit_sha(commit: str | None, where: str) -> str | None:
    if commit is None:
        return None
    normalized = commit.strip()
    if not normalized:
        raise InvalidArgumentError(f"{where}: commit must be a non-empty string when set")
    if not _FULL_COMMIT_SHA_RE.fullmatch(normalized):
        raise InvalidArgumentError(f"{where}: commit must be a full 40-character hexadecimal SHA")
    return normalized.lower()


def _append_git_process_config(env: dict[str, str], key: str, value: str) -> None:
    """Append one process-local Git config entry without putting secrets in argv."""

    try:
        count = int(env.get("GIT_CONFIG_COUNT", "0"))
    except ValueError:
        count = 0
    env[f"GIT_CONFIG_KEY_{count}"] = key
    env[f"GIT_CONFIG_VALUE_{count}"] = value
    env["GIT_CONFIG_COUNT"] = str(count + 1)


async def preflight_git_repository(
    *,
    asset_name: str,
    repo_url: str,
    branch: str | None = None,
    commit: str | None = None,
    username: str | None = None,
    token: str | None = None,
    timeout: float = GIT_PREFLIGHT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Verify that the server can read a Git source without cloning or creating a task."""

    _validate_clone_url(repo_url, asset_name)
    repo_url = require_remote_resource_source(repo_url)
    if timeout <= 0 or not math.isfinite(timeout):
        raise InvalidArgumentError("Git preflight timeout must be a positive finite number")

    normalized_branch = branch.strip() if branch else ""
    if branch is not None and not normalized_branch:
        raise InvalidArgumentError(
            f"asset '{asset_name}': branch must be a non-empty string when set"
        )
    normalized_commit = _normalize_commit_sha(commit, f"asset '{asset_name}'")
    if normalized_branch and normalized_commit:
        raise InvalidArgumentError(
            f"asset '{asset_name}': branch and commit are mutually exclusive"
        )
    git_ref = normalized_commit or normalized_branch

    normalized_token = token or ""
    normalized_username = (username or ("oauth2" if normalized_token else "")).strip()
    if normalized_token and not repo_url.strip().lower().startswith("https://"):
        raise InvalidArgumentError(
            f"asset '{asset_name}': token authentication requires an HTTPS Git URL"
        )
    if normalized_token and not normalized_username:
        raise InvalidArgumentError(
            f"asset '{asset_name}': username must be non-empty when token is set"
        )

    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "Never"
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    if normalized_token:
        # An explicit auth_ref must be authoritative: disable credential-helper
        # fallback and pass HTTP Basic auth through process-local config.
        env = build_git_http_auth_env(
            GitHttpAuthConfig(username=normalized_username, token=normalized_token),
            repo_url,
            base_env=env,
        )
    elif normalized_username:
        _append_git_process_config(env, "credential.username", normalized_username)

    command = ["git", "ls-remote", "--exit-code", repo_url]
    if normalized_branch:
        command.extend(
            [
                f"refs/heads/{normalized_branch}",
                f"refs/tags/{normalized_branch}",
            ]
        )
    else:
        # ls-remote cannot prove that an arbitrary historical commit is
        # reachable. For a pinned commit, preflight therefore verifies repository
        # access via HEAD; the import pipeline validates the exact SHA when it
        # fetches and checks out the commit.
        command.append("HEAD")

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except FileNotFoundError as exc:
        raise UnavailableError("git", "executable not found on the OpenViking Server") from exc

    try:
        _stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise DeadlineExceededError("Git repository permission preflight", timeout) from exc
    except BaseException:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        with contextlib.suppress(Exception):
            await asyncio.shield(process.wait())
        raise

    locator = normalize_repo_url(repo_url)
    if process.returncode == 0:
        return {
            "name": asset_name,
            "connector": "git",
            "locator": locator,
            "git_ref": git_ref,
            "accessible": True,
        }

    error_text = stderr.decode(errors="replace").lower()
    if any(pattern in error_text for pattern in _NETWORK_FAILURE_PATTERNS):
        raise UnavailableError(
            "Git repository",
            f"network preflight failed for asset '{asset_name}' ({locator})",
        )
    if process.returncode == 2 and normalized_branch:
        raise NotFoundError(normalized_branch, "git_ref")
    raise PermissionDeniedError(
        f"Cannot read Git repository for asset '{asset_name}' ({locator}); "
        "verify auth_ref and repository permissions",
        resource=locator,
    )


def resolve_openviking_assets(
    *,
    manifest_yaml: str,
    catalog_yaml: str | None = None,
    manifest_label: str = "manifest.yaml",
    catalog_label: str = "catalog.yaml",
    ctx: RequestContext | None = None,
) -> ResolveResult:
    """Parse and resolve one flat manifest, optionally against a separate catalog.

    A manifest either defines its assets directly under ``catalog`` or selects
    assets by name from a separate catalog file. This API deliberately does not
    follow ``include`` entries and does not submit resources. The caller
    remains responsible for execution.
    """

    manifest = _parse_yaml(manifest_yaml, _Manifest, "manifest", manifest_label)
    assert isinstance(manifest, _Manifest)

    if manifest.protocol is not None and manifest.protocol != PROTOCOL:
        raise InvalidArgumentError(
            f"manifest '{manifest_label}': unsupported protocol '{manifest.protocol}' "
            f"(expected '{PROTOCOL}')"
        )
    if manifest.include:
        raise InvalidArgumentError(
            f"manifest '{manifest_label}': 'include' is not supported by the server resolver; "
            "use a flat manifest"
        )
    if isinstance(manifest.catalog, str):
        raise InvalidArgumentError(
            f"manifest '{manifest_label}': 'catalog' holds asset definitions, not a file "
            "path; define assets under 'catalog' or pass the catalog file separately"
        )

    if manifest.catalog is not None:
        if catalog_yaml is not None:
            raise InvalidArgumentError(
                f"manifest '{manifest_label}' already defines 'catalog'; "
                "do not pass a separate catalog file"
            )
        if manifest.protocol is None:
            raise InvalidArgumentError(
                f"manifest '{manifest_label}': 'protocol' is required when the manifest "
                f"defines 'catalog' (expected '{PROTOCOL}')"
            )
        catalog = _Catalog(protocol=PROTOCOL, defaults=manifest.defaults, catalog=manifest.catalog)
        catalog_label = manifest_label
    else:
        if manifest.defaults is not None:
            raise InvalidArgumentError(
                f"manifest '{manifest_label}': 'defaults' is only allowed when the manifest "
                "defines 'catalog'"
            )
        if catalog_yaml is None:
            raise InvalidArgumentError(
                f"manifest '{manifest_label}' selects assets by name but no catalog was "
                "provided; define assets under 'catalog' or pass a catalog file"
            )
        parsed_catalog = _parse_yaml(catalog_yaml, _Catalog, "catalog", catalog_label)
        assert isinstance(parsed_catalog, _Catalog)
        catalog = parsed_catalog
        if catalog.protocol != PROTOCOL:
            raise InvalidArgumentError(
                f"catalog '{catalog_label}': unsupported protocol '{catalog.protocol}' "
                f"(expected '{PROTOCOL}')"
            )

    names: list[str] = []
    seen_names: set[str] = set()
    for name in manifest.assets or []:
        if not name:
            raise InvalidArgumentError(
                f"manifest '{manifest_label}': 'assets' entries must be non-empty strings"
            )
        if name not in seen_names:
            names.append(name)
            seen_names.add(name)
    if manifest.assets is None and manifest.catalog is not None:
        # A single-file manifest without an explicit selection applies everything
        # it defines; duplicate definition names are rejected below.
        names = [asset.name for asset in catalog.catalog]
    if not names:
        raise InvalidArgumentError(f"manifest '{manifest_label}' selects no assets")

    git_defaults = catalog.defaults.git if catalog.defaults else None
    default_auth_ref = git_defaults.auth_ref if git_defaults else None
    if default_auth_ref is not None and not default_auth_ref:
        raise InvalidArgumentError("catalog defaults.git: 'auth_ref' must be a non-empty string")
    default_watch = _check_watch_interval(
        git_defaults.watch_interval if git_defaults else None,
        "catalog defaults.git",
    )
    if default_watch is None:
        default_watch = 0.0

    catalog_by_name: dict[str, _CatalogAsset] = {}
    for asset in catalog.catalog:
        where = f"catalog '{catalog_label}' asset '{asset.name}'"
        if not _ASSET_NAME_RE.fullmatch(asset.name):
            raise InvalidArgumentError(
                f"catalog '{catalog_label}': asset 'name' is required and must match "
                "[A-Za-z0-9][A-Za-z0-9._-]*"
            )
        if asset.connector != "git":
            raise InvalidArgumentError(
                f"{where}: connector '{asset.connector}' is not supported in {PROTOCOL} "
                "(supported: git)"
            )
        if asset.name in catalog_by_name:
            raise InvalidArgumentError(
                f"catalog '{catalog_label}': duplicate asset name '{asset.name}'"
            )
        if asset.auth_ref is not None and not asset.auth_ref:
            raise InvalidArgumentError(f"{where}: 'auth_ref' must be a non-empty string")
        _check_watch_interval(asset.watch_interval, where)
        catalog_by_name[asset.name] = asset

    missing = [name for name in names if name not in catalog_by_name]
    if missing:
        listed = ", ".join(f"'{name}'" for name in missing)
        if manifest.catalog is not None:
            raise InvalidArgumentError(
                f"manifest '{manifest_label}' selects asset(s) not defined in its "
                f"'catalog': {listed}"
            )
        raise InvalidArgumentError(
            f"manifest '{manifest_label}' references asset(s) not in catalog "
            f"'{catalog_label}': {listed}"
        )

    resolved: list[ResolvedAsset] = []
    identity_names: dict[str, str] = {}
    target_names: dict[str, str] = {}
    for name in names:
        asset = catalog_by_name[name]
        where = f"catalog '{catalog_label}' asset '{name}'"
        target = _normalize_asset_target_uri(asset.to, where, ctx)
        if target is not None:
            if target in target_names:
                other = target_names[target]
                raise InvalidArgumentError(
                    f"assets '{other}' and '{name}' declare the same target URI '{target}'; "
                    "each selected asset must use a unique 'to'"
                )
            target_names[target] = name
        try:
            params = _GitParams.model_validate(asset.params)
        except ValidationError as exc:
            raise InvalidArgumentError(_validation_message("asset params", where, exc)) from exc
        repo_url = params.repo_url.strip()
        _validate_clone_url(repo_url, name)
        branch = params.branch.strip() if params.branch is not None else None
        if branch == "":
            raise InvalidArgumentError(
                f"{where}: params.branch must be a non-empty string when set"
            )
        commit = _normalize_commit_sha(params.commit, f"{where}: params")
        if branch and commit:
            raise InvalidArgumentError(
                f"{where}: params.branch and params.commit are mutually exclusive"
            )
        locator = normalize_repo_url(repo_url)
        git_ref = commit or branch or ""
        asset_id = _asset_id(asset.connector, locator, git_ref)
        if asset_id in identity_names:
            other = identity_names[asset_id]
            shown_ref = git_ref or "default"
            raise InvalidArgumentError(
                f"assets '{other}' and '{name}' resolve to the same source "
                f"(git:{locator}@{shown_ref}); remove one of them"
            )
        identity_names[asset_id] = name
        resolved.append(
            ResolvedAsset(
                name=name,
                connector=asset.connector,
                to=target,
                repo_url=repo_url,
                branch=branch,
                commit=commit,
                auth_ref=asset.auth_ref or default_auth_ref,
                watch_interval=(
                    asset.watch_interval if asset.watch_interval is not None else default_watch
                ),
                locator=locator,
                git_ref=git_ref,
                asset_id=asset_id,
            )
        )

    return ResolveResult(
        manifest=manifest_label,
        catalog=catalog_label,
        assets=resolved,
    )
