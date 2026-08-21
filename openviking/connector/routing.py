# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Connector source-type detection shared by routing and input guards.

Detection mirrors the standard pipeline's own routing (accessor
``can_handle`` predicates) instead of raw URL schemes, so a Connector
add_type matches exactly the sources its plugin can import: ``tos`` by the
``tos://`` prefix, ``git`` by the same repository-URL predicate the
standard ``GitAccessor`` uses (``git@``/``ssh://``/``git://`` and code
hosting ``http(s)`` URLs).
"""

from __future__ import annotations

from typing import Dict, FrozenSet, List, Optional, Tuple

from openviking.utils import is_git_repo_url

# add_resource ``args`` keys each Connector type forwards to its plugin via
# param_config. A key's meaning must match what the standard pipeline's
# accessor gives it (git: branch/ref select the branch to sync, commit pins
# an exact snapshot and takes precedence when both are supplied). Keys outside
# the set flow into the unsupported-parameter framework: connector-only
# sources reject them, shared sources degrade to the standard pipeline.
CONNECTOR_SUPPORTED_ARGS: Dict[str, FrozenSet[str]] = {
    "tos": frozenset({"tos_prefix", "exclude"}),
    "git": frozenset({"branch", "ref", "commit"}),
}

# add_resource ``args`` keys carrying source credentials (git: HTTPS PAT as
# the Basic-Auth password, username defaults to "oauth2" plugin-side). They
# are stripped out of args and transported in the top-level ``auth_config``
# request field -- never ``param_config`` -- so Connector and plugin logs
# redact them while logging param_config verbatim. The incoming OpenViking
# HTTP body may still be captured when the explicitly unsafe observability
# body dump is enabled. These flat fields belong only to Connector requests:
# they are never persisted and cannot fall back to the standard Git pipeline.
# The standard pipeline accepts its separate nested ``args.auth_config``
# contract and stores that state only in the private task-auth field.
CONNECTOR_CREDENTIAL_ARGS: Dict[str, FrozenSet[str]] = {
    "tos": frozenset(),
    "git": frozenset({"token", "username"}),
}


# Reserved ``args`` key for source credentials of declared add_types outside
# the registries above (e.g. add_type="feishu"). OpenViking cannot know each
# plugin's credential fields, so the caller supplies them as a mapping under
# this key; it is lifted verbatim into the top-level ``auth_config`` request
# field and never merged into param_config. All other args keys travel to the
# plugin wholesale through param_config.
CONNECTOR_ARGS_AUTH_CONFIG_KEY = "auth_config"


def credential_arg_names(add_type: str, args: Optional[Dict[str, object]]) -> List[str]:
    """Sorted credential-arg keys of *add_type* that are present in *args*."""
    return sorted(
        set(args or ()).intersection(CONNECTOR_CREDENTIAL_ARGS.get(add_type, frozenset()))
    )


def is_full_commit_sha(ref: str) -> bool:
    """True when *ref* is a full 40-hex commit SHA.

    The Connector git plugin fetches pinned commits by SHA over the wire,
    which cannot resolve abbreviated forms; only the full form may be
    delegated, shorter SHAs stay on the standard pipeline where a local
    clone resolves them.
    """
    return len(ref) == 40 and all(c in "0123456789abcdefABCDEF" for c in ref)


def detect_connector_add_type(path: str) -> Optional[Tuple[str, bool]]:
    """Map *path* to ``(add_type, connector_only)``; None when no type matches.

    connector_only types have no standard-pipeline accessor, so requests
    that cannot be delegated raise instead of degrading.
    """
    if not isinstance(path, str):
        return None
    if path.startswith("tos://"):
        return ("tos", True)
    if is_git_repo_url(path):
        return ("git", False)
    return None
