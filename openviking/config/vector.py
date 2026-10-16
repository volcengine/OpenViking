# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Pure account vector configuration resolution and compatibility checks."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from openviking.config.account_vector import AccountEmbeddingConfig, AccountVectorDBConfig
from openviking.config.manager import AccountCandidateContext
from openviking.config.validate import ConfigPatchError
from openviking_cli.utils.config.embedding_config import EmbeddingConfig
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig

PROVIDER_BINDING = frozenset(
    {
        "credentials",
        "provider",
        "backend",
        "api_key",
        "api_base",
        "api_version",
        "ak",
        "sk",
        "region",
        "host",
        "extra_headers",
    }
)
# Provider-specific fields that must not leak from Cluster once an Account takes
# over the provider binding. ``extra_body`` can carry provider routing/selection
# and ``model_path`` is the local model identity, so both belong to the tenant
# binding. Cluster-maintained runtime switches (encoding_format, cache_dir,
# enable_fusion, res_level, max_video_frames) are intentionally not listed: they
# are behavior/infra defaults, not tenant routing or credentials.
PROVIDER_SPECIFIC_ISOLATED = frozenset({"extra_body", "model_path"})
BACKEND_CONNECTIONS = frozenset({"path", "url", "volcengine", "vikingdb", "cuvs", "custom_params"})


def _account_values(section: Any) -> dict:
    if section is None:
        return {}
    if isinstance(section, dict):
        return copy.deepcopy(section)
    return section.model_dump(by_alias=True, exclude_unset=True, exclude_none=True)


def _overlay(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _overlay(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def resolve_effective_embedding(cluster: EmbeddingConfig, account: Any) -> EmbeddingConfig:
    """Merge ordinary fields, replacing provider bindings as complete units."""
    if isinstance(account, dict):
        account = AccountEmbeddingConfig.model_validate(account)
    override = _account_values(account)
    base = cluster.model_dump(exclude_unset=True)
    if override.get("credentials"):
        # An Account owns its complete provider binding. Never inherit Cluster
        # credentials, routing, or local model paths into that binding.
        dropped = PROVIDER_BINDING | PROVIDER_SPECIFIC_ISOLATED
        base = {key: value for key, value in base.items() if key not in dropped}
        override["output"] = account.output
    return EmbeddingConfig.model_validate(_overlay(base, override))


def resolve_effective_vectordb(
    cluster: VectorDBBackendConfig, account: Any
) -> VectorDBBackendConfig:
    """Keep index defaults; an Account owns its complete connection and auth."""
    if isinstance(account, dict):
        account = AccountVectorDBConfig.model_validate(account)
    base = cluster.model_dump(by_alias=True)
    override = _account_values(account)
    if "project_name" in override:
        override["project"] = override.pop("project_name")
    if account is not None:
        base = {key: value for key, value in base.items() if key not in BACKEND_CONNECTIONS}
    return VectorDBBackendConfig.model_validate(_overlay(base, override))


def validate_vector_settings(
    embedding: EmbeddingConfig, vectordb: VectorDBBackendConfig
) -> VectorDBBackendConfig:
    """Validate a pair locally, returning a normalized copy of the DB config."""
    dimension = embedding.dimension
    if dimension <= 0:
        raise ValueError("embedding dimension must be positive")
    if vectordb.dimension not in (0, dimension):
        raise ValueError(
            f"embedding dimension {dimension} differs from vectordb dimension {vectordb.dimension}"
        )
    if not vectordb.name or not vectordb.index_name:
        raise ValueError("vectordb collection name and index_name must not be empty")
    sparse = embedding.output == "dense_sparse"
    if vectordb.sparse_weight < 0 or (vectordb.sparse_weight > 0 and not sparse):
        raise ValueError("vectordb.sparse_weight requires embedding.output=dense_sparse")
    if vectordb.backend in {"local", "cuvs", "http", "volcengine", "vikingdb"}:
        if vectordb.distance_metric not in {"cosine", "l2", "ip"}:
            raise ValueError("vectordb.distance_metric must be cosine, l2 or ip")
    if sparse and vectordb.backend == "cuvs" and vectordb.cuvs:
        if not vectordb.cuvs.fallback_to_native:
            raise ValueError("dense_sparse output requires cuvs.fallback_to_native")
    return vectordb.model_copy(update={"dimension": dimension})


@dataclass(frozen=True)
class VectorRuntimeSettings:
    embedding: EmbeddingConfig
    vectordb: VectorDBBackendConfig
    embedding_profile: str
    dedicated_vectordb: bool


class AccountVectorConfigResolver:
    """Resolve effective vector settings from paired Account/Cluster publications."""

    def __init__(self, manager):
        self._manager = manager

    async def resolve(self, account_id: str) -> VectorRuntimeSettings:
        if not account_id:
            raise ValueError("account_id is required for vector configuration")
        return await self._manager.resolve_account(account_id, vector_settings_from_view)


def resolve_account_vector_settings(account: Any, cluster: Any) -> VectorRuntimeSettings:
    # Account and Cluster remain independent publications. Choosing Cluster
    # when an Account section is absent is vector-domain behavior, not generic
    # RuntimeField fallback. This path also keeps pre-materialization Account
    # documents working; new Account provisioning should snapshot defaults into
    # Account-owned settings instead of adding further runtime Cluster coupling.
    embedding = resolve_effective_embedding(cluster.embedding, account.embedding)
    vectordb = resolve_effective_vectordb(cluster.storage.vectordb, account.vectordb)
    if account.vectordb is None:
        vectordb = validate_vector_settings(cluster.embedding, vectordb)
    vectordb = validate_vector_settings(embedding, vectordb)
    identity = {
        "output": embedding.output,
        "provider": embedding._effective_provider(),
        "model": embedding.model,
        "credentials": [
            {
                "provider": credential.provider or embedding.provider,
                "model": credential.model or embedding.model,
            }
            for credential in embedding.credentials
        ],
        "dimension": embedding.get_effective_dimension(),
        "input": embedding.input,
        "query_param": embedding.query_param,
        "document_param": embedding.document_param,
        "version": embedding.version,
        "extra_body": embedding.extra_body,
        "model_path": embedding.model_path,
        "enable_fusion": embedding.enable_fusion,
        "res_level": embedding.res_level,
        "max_video_frames": embedding.max_video_frames,
    }
    profile = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return VectorRuntimeSettings(embedding, vectordb, profile, account.vectordb is not None)


def vector_settings_from_view(view) -> VectorRuntimeSettings:
    return resolve_account_vector_settings(view.account, view.cluster)


def validate_account_vector_candidate(context: AccountCandidateContext) -> None:
    """Check the effective pair and preserve an existing Account's vector contract."""
    after = vector_settings_from_view(context.new_view).embedding
    if context.creating or context.old_view is None:
        return
    if context.changed_sections is not None and "embedding" not in context.changed_sections:
        return
    before = vector_settings_from_view(context.old_view).embedding
    if before.output != after.output:
        raise ConfigPatchError("embedding output is create-only", path=("embedding", "output"))
    if before.dimension != after.dimension:
        raise ConfigPatchError("embedding dimension is create-only", path=("embedding",))
