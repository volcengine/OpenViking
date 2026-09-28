# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Account-scoped runtime configuration model.

``AccountConfig`` contains Account-owned configuration; ``OpenVikingConfig``
contains Cluster-owned configuration. They are independent models.

Some existing fields still declare the deprecated ``RuntimeField.fallback``
compatibility behavior. It can only select a complete Cluster section when the
Account section is absent. New defaults and any field-level composition belong
in business resolvers, not in this model or the generic configuration manager.

Runtime Cluster fallback also remains necessary for Account documents created
before complete settings were materialized. It is a migration constraint, not
the target model for new configuration. New provisioning flows should copy
Cluster defaults into a complete Account-owned configuration at creation time;
later Cluster changes must not alter that Account implicitly.
"""

from __future__ import annotations

from typing import ClassVar, List, Optional

from pydantic import BaseModel, model_validator

from openviking.config.account_vector import AccountEmbeddingConfig, AccountVectorDBConfig
from openviking.models.vlm.registry import is_valid_provider
from openviking_cli.utils.config.agent_evolution_config import AgentEvolutionConfig
from openviking_cli.utils.config.github_config import GitHubConfig
from openviking_cli.utils.config.runtime_field import RuntimeField
from openviking_cli.utils.config.vlm_config import VLMConfig, VLMCredential


class AccountAclSettings(BaseModel):
    """Account-scoped ACL switch."""

    enabled: bool = RuntimeField(default=False)


class AccountFeishuConfig(BaseModel):
    """Account-level Feishu settings.

    ``domain`` intentionally does not exist here. It is a cluster deployment
    setting and is selected from the cluster configuration by the business
    resolver.
    """

    app_id: Optional[str] = RuntimeField(default=None)
    app_secret: Optional[str] = RuntimeField(default=None)
    max_rows_per_sheet: Optional[int] = RuntimeField(default=None, gt=0)
    max_records_per_table: Optional[int] = RuntimeField(default=None, gt=0)
    download_images: Optional[bool] = RuntimeField(default=None)
    request_timeout: Optional[float] = RuntimeField(default=None, gt=0)


class AccountVLMConfig(BaseModel):
    """Account-owned VLM settings consumed by the VLM business resolver.

    ``to_vlm_config`` explicitly composes selected Cluster runtime behavior
    with Account-owned model-service identity. This is a VLM rule, not generic
    configuration inheritance. New endpoint or credential fields must be added
    to the exclusion set when they are introduced.
    """

    _ACCOUNT_OWNED_MODEL_SERVICE_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "backup",
            "model",
            "api_key",
            "forward_api_key",
            "api_base",
            "provider",
            "backend",
            "providers",
            "default_provider",
            "api_version",
            "extra_headers",
            "extra_request_body",
            "credentials",
        }
    )

    model: str = RuntimeField(min_length=1)
    credentials: List[VLMCredential] = RuntimeField(min_length=1)
    timeout: Optional[float] = RuntimeField(default=None, gt=0)

    def to_vlm_config(self, cluster_vlm: Optional[VLMConfig] = None) -> VLMConfig:
        """Build an isolated VLM config with Cluster-owned runtime behavior."""
        values = {}
        if cluster_vlm is not None:
            values.update(
                cluster_vlm.model_dump(
                    mode="python",
                    exclude=self._ACCOUNT_OWNED_MODEL_SERVICE_FIELDS,
                )
            )
        values.update(self.model_dump(mode="python", exclude_none=True))
        return VLMConfig.model_validate(values)

    @model_validator(mode="after")
    def validate_model_service(self) -> "AccountVLMConfig":
        """Reject incomplete credentials without consulting Cluster config."""
        if not self.model.strip():
            raise ValueError("model is required for Account VLM")
        for index, credential in enumerate(self.credentials):
            provider = credential.provider.strip().lower() if credential.provider else ""
            if not provider:
                raise ValueError(
                    f"credentials[{index}].provider is required for Account VLM"
                )
            if not is_valid_provider(provider):
                raise ValueError(
                    f"credentials[{index}].provider '{credential.provider}' is not supported"
                )
            credential.provider = provider
            for field_name in ("model", "api_key", "api_base", "api_version"):
                value = getattr(credential, field_name)
                if value is not None and not value.strip():
                    raise ValueError(
                        f"credentials[{index}].{field_name} must not be blank"
                    )
        self.to_vlm_config()
        return self


class AccountConfig(BaseModel):
    """Configuration owned by one Account.

    Optional sections are simply not configured for that Account. When a
    section is present, its Pydantic model validates the smallest Account-owned
    configuration needed for that section; business resolvers may use Cluster
    configuration only when the corresponding Account section is absent.
    The few ``fallback`` declarations below are legacy whole-section
    compatibility behavior and should not be copied to new fields. New Account
    creation should materialize defaults instead of introducing new runtime
    dependencies on Cluster configuration.
    """

    # Account-level settings with active business consumers.
    feishu: Optional[AccountFeishuConfig] = RuntimeField(default=None)
    vlm: Optional[AccountVLMConfig] = RuntimeField(default=None)
    query_planner: Optional[AccountVLMConfig] = RuntimeField(default=None)
    github: Optional[GitHubConfig] = RuntimeField(default=None)
    agent_evolution: Optional[AgentEvolutionConfig] = RuntimeField(
        default=None,
        fallback="agent_evolution",
    )
    acl: Optional[AccountAclSettings] = RuntimeField(default=None)

    embedding: Optional[AccountEmbeddingConfig] = RuntimeField(default=None)
    vectordb: Optional[AccountVectorDBConfig] = RuntimeField(
        default=None,
        dynamic=False,
    )

    # Ignore sections persisted by newer binaries; API writes are still gated.
    model_config = {"arbitrary_types_allowed": True, "extra": "ignore"}
