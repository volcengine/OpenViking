# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
import importlib
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field, model_validator


def _load_codex_auth_module():
    importlib.import_module("openviking.models.vlm")
    return importlib.import_module("openviking.models.vlm.backends.codex_auth")


def _normalize_provider_name(name: Optional[str]) -> Optional[str]:
    if not isinstance(name, str):
        return name
    cleaned = name.strip().lower()
    return cleaned or None


class VLMCredential(BaseModel):
    """Single VLM credential configuration for multi-credential failover."""

    id: Optional[str] = Field(default=None, description="Unique identifier for this credential")
    provider: Optional[str] = Field(default=None, description="Provider type")
    api_key: Optional[str] = Field(default=None, description="API key")
    api_base: Optional[str] = Field(default=None, description="API base URL")
    api_version: Optional[str] = Field(default=None, description="API version")
    forward_api_key: Optional[bool] = Field(
        default=None, description="Whether to pass api_key through to LiteLLM"
    )
    extra_headers: Optional[Dict[str, str]] = Field(
        default=None, description="Extra HTTP headers"
    )
    extra_request_body: Optional[Dict[str, Any]] = Field(
        default=None, description="Extra JSON body fields"
    )
    stream: Optional[bool] = Field(default=None, description="Enable streaming mode")

    model_config = {"extra": "forbid"}


class VLMConfig(BaseModel):
    """VLM configuration, supports multiple provider backends and multi-credential failover."""

    backup: Optional["VLMConfig"] = Field(
        default=None, description="Backup VLM configuration for failover (legacy)"
    )
    model: Optional[str] = Field(default=None, description="Model name")
    api_key: Optional[str] = Field(default=None, description="API key")
    forward_api_key: Optional[bool] = Field(
        default=None,
        description=(
            "Whether to pass api_key through to LiteLLM. None uses provider-aware defaults."
        ),
    )
    api_base: Optional[str] = Field(default=None, description="API base URL")
    temperature: float = Field(default=0.0, description="Generation temperature")
    max_retries: int = Field(default=3, description="Maximum retry attempts")
    timeout: float = Field(
        default=60.0,
        gt=0.0,
        description=(
            "Per-request HTTP timeout in seconds for VLM API calls. Applied to "
            "the underlying OpenAI/Azure/LiteLLM clients. Increase for slow or "
            "high-latency endpoints (e.g., DashScope, local inference servers)."
        ),
    )

    provider: Optional[str] = Field(default=None, description="Provider type")
    backend: Optional[str] = Field(
        default=None, description="Backend provider (Deprecated, use 'provider' instead)"
    )

    providers: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Multi-provider configuration, e.g. {'openai': {'api_key': 'xxx', 'api_base': 'xxx'}}",
    )

    default_provider: Optional[str] = Field(default=None, description="Default provider name")

    max_tokens: Optional[int] = Field(
        default=None,
        description="Maximum tokens for VLM completion output (None = provider default)",
    )

    thinking: bool = Field(default=False, description="Enable thinking mode for VolcEngine models")

    max_concurrent: int = Field(
        default=64, description="Maximum number of concurrent LLM calls for semantic processing"
    )

    api_version: Optional[str] = Field(
        default=None,
        description="API version for Azure OpenAI (e.g., '2025-01-01-preview').",
    )

    extra_headers: Optional[Dict[str, str]] = Field(
        default=None, description="Extra HTTP headers for OpenAI-compatible providers"
    )

    extra_request_body: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Extra JSON body fields passed to OpenAI-compatible VLM completion requests. "
            "Useful for provider-specific options such as Ollama's {'think': false}."
        ),
    )

    stream: bool = Field(
        default=False, description="Enable streaming mode for OpenAI-compatible providers"
    )

    # New multi-credential configuration
    credentials: List[VLMCredential] = Field(
        default_factory=list,
        description="Ordered list of credentials for failover. Call order matches array index (0 is highest priority).",
    )

    failback_timeout_seconds: float = Field(
        default=600.0, description="Time in seconds after which to attempt failback to primary"
    )
    failback_request_count: int = Field(
        default=50, description="Number of backup requests after which to attempt failback"
    )

    _vlm_instance: Optional[Any] = None

    model_config = {"arbitrary_types_allowed": True, "extra": "forbid"}

    @model_validator(mode="before")
    @classmethod
    def sync_provider_backend(cls, data: Any) -> Any:
        if isinstance(data, dict):
            provider = data.get("provider")
            backend = data.get("backend")

            if backend is not None and provider is None:
                data["provider"] = backend
            data["provider"] = _normalize_provider_name(data.get("provider"))
            data["backend"] = _normalize_provider_name(data.get("backend"))
            data["default_provider"] = _normalize_provider_name(data.get("default_provider"))
            providers = data.get("providers")
            if isinstance(providers, dict):
                normalized: Dict[str, Dict[str, Any]] = {}
                provider_sources: Dict[str, str] = {}
                for name, config in providers.items():
                    normalized_name = _normalize_provider_name(name) or str(name)
                    existing_name = provider_sources.get(normalized_name)
                    if existing_name is not None and existing_name != str(name):
                        raise ValueError(
                            "Duplicate VLM provider config after normalization: "
                            f"{existing_name} and {name}"
                        )
                    normalized[normalized_name] = config
                    provider_sources[normalized_name] = str(name)
                data["providers"] = normalized
        return data

    @model_validator(mode="after")
    def validate_config(self):
        """Validate configuration completeness and consistency"""
        # Validate recursive backup BEFORE normalizing credentials (which clears backup)
        self._validate_no_recursive_backup()
        
        self._migrate_legacy_config()
        self._normalize_credentials()

        if self._has_any_config():
            if not self.model:
                raise ValueError("VLM configuration requires 'model' to be set")
            # When credentials are configured, validate each credential
            if self.credentials:
                for i, cred in enumerate(self.credentials):
                    provider_name = cred.provider or self.provider
                    if provider_name == "openai-codex":
                        has_codex_auth_available = _load_codex_auth_module().has_codex_auth_available
                        if not cred.api_key and not has_codex_auth_available():
                            raise ValueError(
                                f"Credential {i} ({cred.id or 'unnamed'}): requires Codex OAuth credentials in ~/.openviking/codex_auth.json"
                            )
                    elif provider_name not in ("litellm", None) and not cred.api_key:
                        # Also check providers dict for fallback
                        if not self._get_credential_api_key(cred):
                            raise ValueError(
                                f"Credential {i} ({cred.id or 'unnamed'}): requires 'api_key' to be set"
                            )
            else:
                # Legacy validation
                provider_name = self._resolve_provider_name()
                if provider_name == "openai-codex":
                    has_codex_auth_available = _load_codex_auth_module().has_codex_auth_available
                    if not self._get_effective_api_key() and not has_codex_auth_available():
                        raise ValueError(
                            "VLM configuration requires Codex OAuth credentials in ~/.openviking/codex_auth.json or an importable Codex CLI auth file"
                        )
                elif provider_name != "litellm" and not self._get_effective_api_key():
                    raise ValueError("VLM configuration requires 'api_key' to be set")
        return self

    def _validate_no_recursive_backup(self):
        """Prevent recursive backup configurations"""
        if self.backup is not None:
            if self.backup.backup is not None:
                raise ValueError(
                    "Backup VLM configuration cannot have its own backup (recursive backups are not allowed)"
                )

    def _migrate_legacy_config(self):
        """Migrate legacy config to providers structure."""
        if self.provider and (
            self.api_key
            or self.api_base
            or self.extra_headers
            or self.extra_request_body
            or self.stream
            or self.forward_api_key is not None
        ):
            if self.provider not in self.providers:
                self.providers[self.provider] = {}
            if self.api_key and "api_key" not in self.providers[self.provider]:
                self.providers[self.provider]["api_key"] = self.api_key
            if (
                self.forward_api_key is not None
                and "forward_api_key" not in self.providers[self.provider]
            ):
                self.providers[self.provider]["forward_api_key"] = self.forward_api_key
            if self.api_base and "api_base" not in self.providers[self.provider]:
                self.providers[self.provider]["api_base"] = self.api_base
            if self.extra_headers and "extra_headers" not in self.providers[self.provider]:
                self.providers[self.provider]["extra_headers"] = self.extra_headers
            if (
                self.extra_request_body
                and "extra_request_body" not in self.providers[self.provider]
            ):
                self.providers[self.provider]["extra_request_body"] = self.extra_request_body
            if self.stream and "stream" not in self.providers[self.provider]:
                self.providers[self.provider]["stream"] = self.stream

    def _normalize_credentials(self):
        """Normalize credentials configuration:
        1. Migrate legacy backup config to credentials
        2. Migrate top-level credentials to credentials list
        3. Assign default IDs to credentials without IDs
        4. Apply top-level defaults to credentials
        """
        migrated_credentials = []

        # Step 1: Migrate legacy backup config
        if self.backup is not None and self.backup._has_any_config():
            # Primary credential from top-level
            primary_cred = VLMCredential(
                id="legacy-primary",
                provider=self.provider,
                api_key=self.api_key,
                api_base=self.api_base,
                api_version=self.api_version,
                forward_api_key=self.forward_api_key,
                extra_headers=self.extra_headers,
                extra_request_body=self.extra_request_body,
                stream=self.stream,
            )
            migrated_credentials.append(primary_cred)

            # Backup credential
            backup_cred = VLMCredential(
                id="legacy-backup",
                provider=self.backup.provider,
                api_key=self.backup.api_key,
                api_base=self.backup.api_base,
                api_version=self.backup.api_version,
                forward_api_key=self.backup.forward_api_key,
                extra_headers=self.backup.extra_headers,
                extra_request_body=self.backup.extra_request_body,
                stream=self.backup.stream,
            )
            migrated_credentials.append(backup_cred)

            # Clear backup to avoid double processing
            self.backup = None

        # Step 2: If no credentials from backup migration and no explicit credentials,
        # create credentials from top-level config
        if not migrated_credentials and not self.credentials:
            if self._has_legacy_provider_config():
                migrated_credentials.append(
                    VLMCredential(
                        id="default",
                        provider=self.provider,
                        api_key=self.api_key,
                        api_base=self.api_base,
                        api_version=self.api_version,
                        forward_api_key=self.forward_api_key,
                        extra_headers=self.extra_headers,
                        extra_request_body=self.extra_request_body,
                        stream=self.stream,
                    )
                )

        # Step 3: Merge migrated credentials with existing credentials
        if migrated_credentials:
            # Only use migrated if no explicit credentials exist
            if not self.credentials:
                self.credentials = migrated_credentials

        # Step 4: Assign default IDs and apply top-level defaults
        for i, cred in enumerate(self.credentials):
            if not cred.id:
                cred.id = f"credential-{i}"
            # Apply top-level defaults where credential doesn't specify
            if not cred.provider:
                cred.provider = self.provider
            if not cred.api_key:
                cred.api_key = self._get_credential_api_key(cred)
            if not cred.api_base:
                cred.api_base = self.api_base
            if not cred.api_version:
                cred.api_version = self.api_version
            if cred.forward_api_key is None:
                cred.forward_api_key = self.forward_api_key
            if not cred.extra_headers:
                cred.extra_headers = self.extra_headers
            if not cred.extra_request_body:
                cred.extra_request_body = self.extra_request_body
            if cred.stream is None:
                cred.stream = self.stream

    def _has_legacy_provider_config(self) -> bool:
        """Check if there's legacy provider config (not credentials-based)."""
        return (
            self.provider is not None
            or self.api_key is not None
            or self.api_base is not None
            or self.providers
        )

    def _get_credential_api_key(self, cred: VLMCredential) -> str | None:
        """Get effective API key for a credential, checking providers dict."""
        if cred.api_key:
            return cred.api_key
        if cred.provider and cred.provider in self.providers:
            return self.providers[cred.provider].get("api_key")
        return None

    def _has_any_config(self) -> bool:
        """Check if any config is provided."""
        if self.credentials:
            return True
        if self.api_key or self.model or self.api_base or self.provider or self.default_provider:
            return True
        if self.providers:
            return True
        return False

    def _get_effective_api_key(self) -> str | None:
        """Get effective API key."""
        if self.credentials:
            return self.credentials[0].api_key or self._get_credential_api_key(self.credentials[0])
        if self.api_key:
            return self.api_key
        config, _ = self._match_provider()
        if config and config.get("api_key"):
            return config["api_key"]
        return None

    def _get_provider_config_by_name(self, provider_name: str) -> Dict[str, Any]:
        config = dict(self.providers.get(provider_name) or {})
        if self.api_key and "api_key" not in config:
            config["api_key"] = self.api_key
        if self.forward_api_key is not None and "forward_api_key" not in config:
            config["forward_api_key"] = self.forward_api_key
        if self.api_base and "api_base" not in config:
            config["api_base"] = self.api_base
        if self.extra_headers and "extra_headers" not in config:
            config["extra_headers"] = self.extra_headers
        if self.extra_request_body and "extra_request_body" not in config:
            config["extra_request_body"] = self.extra_request_body
        if self.stream and "stream" not in config:
            config["stream"] = self.stream
        return config

    def _provider_has_usable_credentials(self, provider_name: str, config: Dict[str, Any]) -> bool:
        if config.get("api_key"):
            return True
        if provider_name == "litellm":
            return True
        if provider_name == "openai-codex":
            has_codex_auth_available = _load_codex_auth_module().has_codex_auth_available

            return has_codex_auth_available()
        return False

    def _match_provider(self, model: str | None = None) -> tuple[Dict[str, Any] | None, str | None]:
        """Match provider config.

        Returns:
            (provider_config_dict, provider_name)
        """
        del model
        # If credentials are configured, use the first one
        if self.credentials:
            cred = self.credentials[0]
            return (
                {
                    "api_key": cred.api_key,
                    "api_base": cred.api_base,
                    "api_version": cred.api_version,
                    "forward_api_key": cred.forward_api_key,
                    "extra_headers": cred.extra_headers,
                    "extra_request_body": cred.extra_request_body,
                    "stream": cred.stream,
                },
                cred.provider,
            )

        if self.provider:
            return self._get_provider_config_by_name(self.provider) or {}, self.provider

        if self.default_provider:
            return (
                self._get_provider_config_by_name(self.default_provider) or {},
                self.default_provider,
            )

        if len(self.providers) == 1:
            provider_name = next(iter(self.providers))
            return self._get_provider_config_by_name(provider_name), provider_name

        for provider_name in self.providers:
            config = self._get_provider_config_by_name(provider_name)
            if self._provider_has_usable_credentials(provider_name, config):
                return config, provider_name

        return None, None

    def _resolve_provider_name(self) -> str | None:
        """Resolve provider name from credentials or legacy config."""
        if self.credentials:
            return self.credentials[0].provider
        _, name = self._match_provider()
        return name

    def get_provider_config(
        self, model: str | None = None
    ) -> tuple[Dict[str, Any] | None, str | None]:
        """Get provider config.

        Returns:
            (provider_config_dict, provider_name)
        """
        return self._match_provider(model)

    def get_vlm_instance(self) -> Any:
        """Get VLM instance with multi-credential failover support."""
        if self._vlm_instance is None:
            from openviking.models.vlm import FailoverVLM, MultiCredentialVLM, VLMFactory

            if self.credentials:
                # Build VLM instances for each credential
                vlm_instances = []
                for cred in self.credentials:
                    config_dict = self._build_vlm_config_dict_for_credential(cred)
                    vlm_instances.append(VLMFactory.create(config_dict))

                if len(vlm_instances) == 1:
                    self._vlm_instance = vlm_instances[0]
                else:
                    self._vlm_instance = MultiCredentialVLM(
                        vlm_instances,
                        credential_ids=[c.id for c in self.credentials],
                        failback_timeout_seconds=self.failback_timeout_seconds,
                        failback_request_count=self.failback_request_count,
                    )
            else:
                # Legacy mode: single VLM with optional backup
                config_dict = self._build_vlm_config_dict()
                primary = VLMFactory.create(config_dict)

                if self.backup is not None and self.backup._has_any_config():
                    backup_config_dict = self.backup._build_vlm_config_dict()
                    backup = VLMFactory.create(backup_config_dict)
                    self._vlm_instance = FailoverVLM(primary, backup)
                else:
                    self._vlm_instance = primary

        return self._vlm_instance

    def _build_vlm_config_dict_for_credential(self, credential: VLMCredential) -> Dict[str, Any]:
        """Build VLM instance config dict for a specific credential."""
        result = {
            "model": self.model,
            "temperature": self.temperature,
            "max_retries": self.max_retries,
            "timeout": self.timeout,
            "provider": credential.provider,
            "thinking": self.thinking,
            "max_tokens": self.max_tokens,
            "stream": credential.stream if credential.stream is not None else self.stream,
            "api_version": credential.api_version,
        }

        if credential.api_key:
            result["api_key"] = credential.api_key
        if credential.forward_api_key is not None:
            result["forward_api_key"] = credential.forward_api_key
        if credential.api_base:
            result["api_base"] = credential.api_base
        if credential.extra_headers:
            result["extra_headers"] = credential.extra_headers
        if credential.extra_request_body:
            result["extra_request_body"] = credential.extra_request_body

        return result

    def _build_vlm_config_dict(self) -> Dict[str, Any]:
        """Build VLM instance config dict."""
        config, name = self.get_provider_config()

        # Get stream from provider config if available, fallback to self.stream
        stream = (
            config.get("stream") if config and config.get("stream") is not None else self.stream
        )

        result = {
            "model": self.model,
            "temperature": self.temperature,
            "max_retries": self.max_retries,
            "timeout": self.timeout,
            "provider": name,
            "thinking": self.thinking,
            "max_tokens": self.max_tokens,
            "stream": stream,
            "api_version": self.api_version,
        }

        if config:
            if config.get("api_key"):
                result["api_key"] = config.get("api_key")
            if config.get("forward_api_key") is not None:
                result["forward_api_key"] = config.get("forward_api_key")
            if config.get("api_base"):
                result["api_base"] = config.get("api_base")
            if config.get("extra_headers"):
                result["extra_headers"] = config.get("extra_headers")
            if config.get("extra_request_body"):
                result["extra_request_body"] = config.get("extra_request_body")

        return result

    def get_completion(
        self,
        prompt: str = "",
        thinking: Optional[bool] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, Any]:
        """Get LLM completion."""
        effective_thinking = self.thinking if thinking is None else thinking
        return self.get_vlm_instance().get_completion(
            prompt=prompt,
            thinking=effective_thinking,
            tools=tools,
            messages=messages,
        )

    async def get_completion_async(
        self,
        prompt: str = "",
        thinking: Optional[bool] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, Any]:
        """Get LLM completion asynchronously."""
        effective_thinking = self.thinking if thinking is None else thinking
        return await self.get_vlm_instance().get_completion_async(
            prompt=prompt,
            thinking=effective_thinking,
            tools=tools,
            tool_choice=tool_choice,
            messages=messages,
        )

    def is_available(self) -> bool:
        """Check if LLM is configured."""
        if self._resolve_provider_name() == "openai-codex":
            has_codex_auth_available = _load_codex_auth_module().has_codex_auth_available

            return bool(self._get_effective_api_key() or has_codex_auth_available())
        if self._resolve_provider_name() == "litellm":
            return bool(self.model)
        return self._get_effective_api_key() is not None

    def get_vision_completion(
        self,
        prompt: str = "",
        images: Optional[list] = None,
        thinking: Optional[bool] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, Any]:
        """Get LLM completion with images."""
        effective_thinking = self.thinking if thinking is None else thinking
        return self.get_vlm_instance().get_vision_completion(
            prompt=prompt,
            images=images,
            thinking=effective_thinking,
            tools=tools,
            messages=messages,
        )

    async def get_vision_completion_async(
        self,
        prompt: str = "",
        images: Optional[list] = None,
        thinking: Optional[bool] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Union[str, Any]:
        """Get LLM completion with images asynchronously."""
        effective_thinking = self.thinking if thinking is None else thinking
        return await self.get_vlm_instance().get_vision_completion_async(
            prompt=prompt,
            images=images,
            thinking=effective_thinking,
            tools=tools,
            messages=messages,
        )


# Resolve forward reference for backup field
VLMConfig.model_rebuild()
