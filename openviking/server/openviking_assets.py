# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Server-side parsing and validation for OpenViking Assets manifests."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from openviking_cli.exceptions import InvalidArgumentError

PROTOCOL = "openviking-assets/1"
_ASSET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_REMOTE_HELPER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*::")


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
    params: dict[str, Any]
    auth_ref: str | None = None
    watch_interval: float | None = None


class _Catalog(_StrictModel):
    protocol: str
    defaults: _Defaults | None = None
    assets: list[_CatalogAsset]


class _Manifest(_StrictModel):
    protocol: str | None = None
    catalog: str | None = None
    include: list[str] | None = None
    assets: list[str] | None = None


class _GitParams(_StrictModel):
    repo_url: str
    branch: str | None = None


class ResolvedAsset(_StrictModel):
    """A manifest selection joined with its catalog entry and defaults."""

    name: str
    connector: str
    repo_url: str
    branch: str | None = None
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
            "use https://, ssh://, git://, file://, or a plain path"
        )


def _asset_id(connector: str, locator: str, git_ref: str) -> str:
    identity = f"{connector}\n{locator}\n{git_ref}".encode()
    return hashlib.sha1(identity).hexdigest()[:12]  # noqa: S324 - stable identity, not security


def resolve_openviking_assets(
    *,
    manifest_yaml: str,
    catalog_yaml: str,
    manifest_label: str = "manifest.yaml",
    catalog_label: str = "assets.yaml",
) -> ResolveResult:
    """Parse and resolve one flat manifest against one catalog.

    This API deliberately does not follow ``include`` entries and does not
    submit resources. The caller remains responsible for execution.
    """

    manifest = _parse_yaml(manifest_yaml, _Manifest, "manifest", manifest_label)
    catalog = _parse_yaml(catalog_yaml, _Catalog, "catalog", catalog_label)
    assert isinstance(manifest, _Manifest)
    assert isinstance(catalog, _Catalog)

    if manifest.protocol is not None and manifest.protocol != PROTOCOL:
        raise InvalidArgumentError(
            f"manifest '{manifest_label}': unsupported protocol '{manifest.protocol}' "
            f"(expected '{PROTOCOL}')"
        )
    if manifest.catalog is not None and not manifest.catalog.strip():
        raise InvalidArgumentError(
            f"manifest '{manifest_label}': 'catalog' must be a non-empty path string"
        )
    if manifest.include:
        raise InvalidArgumentError(
            f"manifest '{manifest_label}': 'include' is not supported by the server resolver; "
            "use a flat manifest"
        )
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
    for asset in catalog.assets:
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
        raise InvalidArgumentError(
            f"manifest '{manifest_label}' references asset(s) not in catalog "
            f"'{catalog_label}': {listed}"
        )

    resolved: list[ResolvedAsset] = []
    identity_names: dict[str, str] = {}
    for name in names:
        asset = catalog_by_name[name]
        where = f"catalog '{catalog_label}' asset '{name}'"
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
        locator = normalize_repo_url(repo_url)
        git_ref = branch or ""
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
                repo_url=repo_url,
                branch=branch,
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
