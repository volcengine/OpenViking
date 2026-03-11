"""Configuration schema using Pydantic."""

from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ChannelType(str, Enum):
    """Channel type enumeration."""

    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"
    DISCORD = "discord"
    FEISHU = "feishu"
    MOCHAT = "mochat"
    DINGTALK = "dingtalk"
    EMAIL = "email"
    SLACK = "slack"
    QQ = "qq"
    OPENAPI = "openapi"


class SandboxBackend(str, Enum):
    """Sandbox backend type enumeration."""

    SRT = "srt"
    DOCKER = "docker"
    OPENSANDBOX = "opensandbox"
    DIRECT = "direct"
    AIOSANDBOX = "aiosandbox"


class SandboxMode(str, Enum):
    """Sandbox mode enumeration."""

    PER_SESSION = "per-session"
    SHARED = "shared"
    PER_CHANNEL = "per-channel"

class AgentMemoryMode(str, Enum):
    """Agent memory mode enumeration."""
    PER_SESSION = "per-session"
    SHARED = "shared"
    PER_CHANNEL = "per-channel"


class BaseChannelConfig(BaseModel):
    """Base channel configuration."""

    type: Any = ChannelType.TELEGRAM  # Default for backwards compatibility
    enabled: bool = True

    def channel_id(self) -> str:
        return "default"

    def channel_key(self):
        return f"{getattr(self.type, 'value', self.type)}__{self.channel_id()}"


# ========== Channel helper configs ==========


class MochatMentionConfig(BaseModel):
    """Mochat mention behavior configuration."""

    require_in_groups: bool = False


class MochatGroupRule(BaseModel):
    """Mochat per-group mention requirement."""

    require_mention: bool = False


class SlackDMConfig(BaseModel):
    """Slack DM policy configuration."""

    enabled: bool = True
    policy: str = "open"  # "open" or "allowlist"
    allow_from: list[str] = Field(default_factory=list)  # Allowed Slack user IDs


# ========== Multi-channel support ==========


class TelegramChannelConfig(BaseChannelConfig):
    """Telegram channel configuration (multi-channel support)."""

    type: ChannelType = ChannelType.TELEGRAM
    token: str = ""
    allow_from: list[str] = Field(default_factory=list)
    proxy: str | None = None

    def channel_id(self) -> str:
        # Use the bot ID from token (before colon)
        return self.token.split(":")[0] if ":" in self.token else self.token


class FeishuChannelConfig(BaseChannelConfig):
    """Feishu/Lark channel configuration (multi-channel support)."""

    type: ChannelType = ChannelType.FEISHU
    app_id: str = ""
    open_id: str = ""
    app_secret: str = ""
    encrypt_key: str = ""
    verification_token: str = ""
    allow_from: list[str] = Field(default_factory=list)  ## 允许更新Agent对话的Feishu用户ID列表
    thread_require_mention: bool = Field(default=True, description="话题群模式下是否需要@才响应：默认True=所有消息必须@才响应；False=新话题首条消息无需@，后续回复必须@")

    def channel_id(self) -> str:
        # Use app_id directly as the ID
        return self.app_id

    def channel_key(self):
        return f"{self.type.value}__{self.channel_id()}"


class DiscordChannelConfig(BaseChannelConfig):
    """Discord channel configuration (multi-channel support)."""

    type: ChannelType = ChannelType.DISCORD
    token: str = ""
    allow_from: list[str] = Field(default_factory=list)
    gateway_url: str = "wss://gateway.discord.gg/?v=10&encoding=json"
    intents: int = 37377

    def channel_id(self) -> str:
        # Use first 20 chars of token as ID
        return self.token[:20]


class WhatsAppChannelConfig(BaseChannelConfig):
    """WhatsApp channel configuration (multi-channel support)."""

    type: ChannelType = ChannelType.WHATSAPP
    bridge_url: str = "ws://localhost:3001"
    bridge_token: str = ""
    allow_from: list[str] = Field(default_factory=list)

    def channel_id(self) -> str:
        # WhatsApp typically only has one instance
        return "whatsapp"


class MochatChannelConfig(BaseChannelConfig):
    """MoChat channel configuration (multi-channel support)."""

    type: ChannelType = ChannelType.MOCHAT
    base_url: str = "https://mochat.io"
    socket_url: str = ""
    socket_path: str = "/socket.io"
    socket_disable_msgpack: bool = False
    socket_reconnect_delay_ms: int = 1000
    socket_max_reconnect_delay_ms: int = 10000
    socket_connect_timeout_ms: int = 10000
    refresh_interval_ms: int = 30000
    watch_timeout_ms: int = 25000
    watch_limit: int = 100
    retry_delay_ms: int = 500
    max_retry_attempts: int = 0
    claw_token: str = ""
    agent_user_id: str = ""
    sessions: list[str] = Field(default_factory=list)
    panels: list[str] = Field(default_factory=list)
    allow_from: list[str] = Field(default_factory=list)
    mention: MochatMentionConfig = Field(default_factory=MochatMentionConfig)
    groups: dict[str, MochatGroupRule] = Field(default_factory=dict)
    reply_delay_mode: str = "non-mention"
    reply_delay_ms: int = 120000

    def _generate_default_id(self) -> str:
        # Use agent_user_id as the ID
        return self.agent_user_id if self.agent_user_id else "mochat"


class DingTalkChannelConfig(BaseChannelConfig):
    """DingTalk channel configuration (multi-channel support)."""

    type: ChannelType = ChannelType.DINGTALK
    client_id: str = ""
    client_secret: str = ""
    allow_from: list[str] = Field(default_factory=list)

    def channel_id(self) -> str:
        # Use client_id directly as the ID
        return self.client_id


class EmailChannelConfig(BaseChannelConfig):
    """Email channel configuration (multi-channel support)."""

    type: ChannelType = ChannelType.EMAIL
    consent_granted: bool = False
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    imap_mailbox: str = "INBOX"
    imap_use_ssl: bool = True
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    from_address: str = ""
    auto_reply_enabled: bool = True
    poll_interval_seconds: int = 30
    mark_seen: bool = True
    max_body_chars: int = 12000
    subject_prefix: str = "Re: "
    allow_from: list[str] = Field(default_factory=list)

    def channel_id(self) -> str:
        # Use from_address directly as the ID
        return self.from_address


class SlackChannelConfig(BaseChannelConfig):
    """Slack channel configuration (multi-channel support)."""

    type: ChannelType = ChannelType.SLACK
    mode: str = "socket"
    webhook_path: str = "/slack/events"
    bot_token: str = ""
    app_token: str = ""
    user_token_read_only: bool = True
    group_policy: str = "mention"
    group_allow_from: list[str] = Field(default_factory=list)
    dm: SlackDMConfig = Field(default_factory=SlackDMConfig)

    def channel_id(self) -> str:
        # Use first 20 chars of bot_token as ID
        return self.bot_token[:20] if self.bot_token else "slack"


class QQChannelConfig(BaseChannelConfig):
    """QQ channel configuration (multi-channel support)."""

    type: ChannelType = ChannelType.QQ
    app_id: str = ""
    secret: str = ""
    allow_from: list[str] = Field(default_factory=list)

    def channel_id(self) -> str:
        # Use app_id directly as the ID
        return self.app_id


class OpenAPIChannelConfig(BaseChannelConfig):
    """OpenAPI channel configuration for HTTP-based chat API."""

    type: ChannelType = ChannelType.OPENAPI
    enabled: bool = True
    api_key: str = ""  # If empty, no auth required
    allow_from: list[str] = Field(default_factory=list)
    max_concurrent_requests: int = 100
    _channel_id: str = "default"

    def channel_id(self) -> str:
        return self._channel_id


class ChannelsConfig(BaseModel):
    """Configuration for chat channels - array of channel configs."""

    channels: list[Any] = Field(default_factory=list)

    def _parse_channel_config(self, config: dict[str, Any]) -> BaseChannelConfig:
        """Parse a single channel config dict into the appropriate type."""
        channel_type = config.get("type")

        # Handle both snake_case and camelCase for feishu
        if "appId" in config and "app_id" not in config:
            config["app_id"] = config.pop("appId")
        if "appSecret" in config and "app_secret" not in config:
            config["app_secret"] = config.pop("appSecret")
        if "encryptKey" in config and "encrypt_key" not in config:
            config["encrypt_key"] = config.pop("encryptKey")
        if "verificationToken" in config and "verification_token" not in config:
            config["verification_token"] = config.pop("verificationToken")

        # Handle camelCase for other fields
        if "allowFrom" in config and "allow_from" not in config:
            config["allow_from"] = config.pop("allowFrom")
        if "bridgeUrl" in config and "bridge_url" not in config:
            config["bridge_url"] = config.pop("bridgeUrl")
        if "bridgeToken" in config and "bridge_token" not in config:
            config["bridge_token"] = config.pop("bridgeToken")
        if "clientId" in config and "client_id" not in config:
            config["client_id"] = config.pop("clientId")
        if "clientSecret" in config and "client_secret" not in config:
            config["client_secret"] = config.pop("clientSecret")
        if "consentGranted" in config and "consent_granted" not in config:
            config["consent_granted"] = config.pop("consentGranted")
        if "imapHost" in config and "imap_host" not in config:
            config["imap_host"] = config.pop("imapHost")
        if "imapPort" in config and "imap_port" not in config:
            config["imap_port"] = config.pop("imapPort")
        if "imapUsername" in config and "imap_username" not in config:
            config["imap_username"] = config.pop("imapUsername")
        if "imapPassword" in config and "imap_password" not in config:
            config["imap_password"] = config.pop("imapPassword")
        if "imapMailbox" in config and "imap_mailbox" not in config:
            config["imap_mailbox"] = config.pop("imapMailbox")
        if "imapUseSsl" in config and "imap_use_ssl" not in config:
            config["imap_use_ssl"] = config.pop("imapUseSsl")
        if "smtpHost" in config and "smtp_host" not in config:
            config["smtp_host"] = config.pop("smtpHost")
        if "smtpPort" in config and "smtp_port" not in config:
            config["smtp_port"] = config.pop("smtpPort")
        if "smtpUsername" in config and "smtp_username" not in config:
            config["smtp_username"] = config.pop("smtpUsername")
        if "smtpPassword" in config and "smtp_password" not in config:
            config["smtp_password"] = config.pop("smtpPassword")
        if "smtpUseTls" in config and "smtp_use_tls" not in config:
            config["smtp_use_tls"] = config.pop("smtpUseTls")
        if "smtpUseSsl" in config and "smtp_use_ssl" not in config:
            config["smtp_use_ssl"] = config.pop("smtpUseSsl")
        if "fromAddress" in config and "from_address" not in config:
            config["from_address"] = config.pop("fromAddress")
        if "autoReplyEnabled" in config and "auto_reply_enabled" not in config:
            config["auto_reply_enabled"] = config.pop("autoReplyEnabled")
        if "pollIntervalSeconds" in config and "poll_interval_seconds" not in config:
            config["poll_interval_seconds"] = config.pop("pollIntervalSeconds")
        if "markSeen" in config and "mark_seen" not in config:
            config["mark_seen"] = config.pop("markSeen")
        if "maxBodyChars" in config and "max_body_chars" not in config:
            config["max_body_chars"] = config.pop("maxBodyChars")
        if "subjectPrefix" in config and "subject_prefix" not in config:
            config["subject_prefix"] = config.pop("subjectPrefix")
        if "botToken" in config and "bot_token" not in config:
            config["bot_token"] = config.pop("botToken")
        if "appToken" in config and "app_token" not in config:
            config["app_token"] = config.pop("appToken")
        if "userTokenReadOnly" in config and "user_token_read_only" not in config:
            config["user_token_read_only"] = config.pop("userTokenReadOnly")
        if "groupPolicy" in config and "group_policy" not in config:
            config["group_policy"] = config.pop("groupPolicy")
        if "groupAllowFrom" in config and "group_allow_from" not in config:
            config["group_allow_from"] = config.pop("groupAllowFrom")

        if channel_type == ChannelType.TELEGRAM:
            return TelegramChannelConfig(**config)
        elif channel_type == ChannelType.FEISHU:
            return FeishuChannelConfig(**config)
        elif channel_type == ChannelType.DISCORD:
            return DiscordChannelConfig(**config)
        elif channel_type == ChannelType.WHATSAPP:
            return WhatsAppChannelConfig(**config)
        elif channel_type == ChannelType.MOCHAT:
            return MochatChannelConfig(**config)
        elif channel_type == ChannelType.DINGTALK:
            return DingTalkChannelConfig(**config)
        elif channel_type == ChannelType.EMAIL:
            return EmailChannelConfig(**config)
        elif channel_type == ChannelType.SLACK:
            return SlackChannelConfig(**config)
        elif channel_type == ChannelType.QQ:
            return QQChannelConfig(**config)
        elif channel_type == ChannelType.OPENAPI:
            return OpenAPIChannelConfig(**config)
        else:
            return BaseChannelConfig(**config)

    def get_all_channels(self) -> list[BaseChannelConfig]:
        """Get all channel configs."""
        result = []
        for item in self.channels:
            if isinstance(item, dict):
                result.append(self._parse_channel_config(item))
            elif isinstance(item, BaseChannelConfig):
                result.append(item)
        return result


class AgentsConfig(BaseModel):
    """Agent configuration."""

    model: str = "openai/doubao-seed-2-0-pro-260215"
    max_tool_iterations: int = 50
    memory_window: int = 50
    gen_image_model: str = "openai/doubao-seedream-4-5-251128"
    provider: str = ""
    api_key: str = ""
    api_base: str = ""
    extra_headers: Optional[dict[str, str]] = Field(default_factory=dict)


class ProviderConfig(BaseModel):
    """LLM provider configuration."""

    api_key: str = ""
    api_base: Optional[str] = None
    extra_headers: Optional[dict[str, str]] = Field(default_factory=dict)  # Custom headers (e.g. APP-Code for AiHubMix)


class ProvidersConfig(BaseModel):
    """Configuration for LLM providers."""

    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    openrouter: ProviderConfig = Field(default_factory=ProviderConfig)
    deepseek: ProviderConfig = Field(default_factory=ProviderConfig)
    groq: ProviderConfig = Field(default_factory=ProviderConfig)
    zhipu: ProviderConfig = Field(default_factory=ProviderConfig)
    dashscope: ProviderConfig = Field(default_factory=ProviderConfig)  # 阿里云通义千问
    vllm: ProviderConfig = Field(default_factory=ProviderConfig)
    gemini: ProviderConfig = Field(default_factory=ProviderConfig)
    moonshot: ProviderConfig = Field(default_factory=ProviderConfig)
    minimax: ProviderConfig = Field(default_factory=ProviderConfig)
    volcengine: ProviderConfig = Field(
        default_factory=ProviderConfig
    )  # VolcEngine (火山引擎) API gateway
    aihubmix: ProviderConfig = Field(default_factory=ProviderConfig)  # AiHubMix API gateway


class HeartbeatConfig(BaseModel):
    """Heartbeat service configuration."""

    enabled: bool = True
    interval_seconds: int = 10 * 60  # Default: 5 minutes


class GatewayConfig(BaseModel):
    """Gateway/server configuration."""

    host: str = "0.0.0.0"
    port: int = 18790


class WebSearchConfig(BaseModel):
    """Web search tool configuration."""

    api_key: str = ""  # Brave Search API key
    max_results: int = 5


class OpenVikingConfig(BaseModel):
    """Viking tools configuration."""

    mode: str = "remote"  # local or remote
    server_url: str = ""
    root_api_key: str = ""
    account_id: str = "default"
    admin_user_id: str = "default"
    agent_id: str = ""


class WebToolsConfig(BaseModel):
    """Web tools configuration."""

    search: WebSearchConfig = Field(default_factory=WebSearchConfig)


class ExecToolConfig(BaseModel):
    """Shell exec tool configuration."""

    timeout: int = 60


class ToolsConfig(BaseModel):
    """Tools configuration."""

    web: WebToolsConfig = Field(default_factory=WebToolsConfig)
    exec: ExecToolConfig = Field(default_factory=ExecToolConfig)


class SandboxNetworkConfig(BaseModel):
    """Sandbox network configuration.

    SRT uses allow-only pattern: all network access is denied by default.
    You must explicitly allow domains.

    - allowed_domains: List of allowed domains (supports wildcards like "*.example.com")
    - denied_domains: List of denied domains (checked first, takes precedence over allowed_domains)
    - allow_local_binding: Allow binding to local ports
    """

    allowed_domains: list[str] = Field(default_factory=list)
    denied_domains: list[str] = Field(default_factory=list)
    allow_local_binding: bool = False


class SandboxFilesystemConfig(BaseModel):
    """Sandbox filesystem configuration."""

    deny_read: list[str] = Field(default_factory=list)
    allow_write: list[str] = Field(default_factory=list)
    deny_write: list[str] = Field(default_factory=list)


class SandboxRuntimeConfig(BaseModel):
    """Sandbox runtime configuration."""

    cleanup_on_exit: bool = True
    timeout: int = 300


class DirectBackendConfig(BaseModel):
    """Direct backend configuration."""

    restrict_to_workspace: bool = False  # If true, restrict file access to workspace directory


class SrtBackendConfig(BaseModel):
    """SRT backend configuration."""

    node_path: str = "node"
    network: SandboxNetworkConfig = Field(default_factory=SandboxNetworkConfig)
    filesystem: SandboxFilesystemConfig = Field(default_factory=SandboxFilesystemConfig)
    runtime: SandboxRuntimeConfig = Field(default_factory=SandboxRuntimeConfig)


class DockerBackendConfig(BaseModel):
    """Docker backend configuration."""

    image: str = "python:3.11-slim"
    network_mode: str = "bridge"


class OpenSandboxNetworkConfig(BaseModel):
    """OpenSandbox network configuration."""

    allowed_domains: list[str] = Field(default_factory=list)
    denied_domains: list[str] = Field(default_factory=list)


class OpenSandboxRuntimeConfig(BaseModel):
    """OpenSandbox runtime configuration."""

    timeout: int = 300
    cpu: str = "500m"
    memory: str = "1Gi"


class OpenSandboxBackendConfig(BaseModel):
    """OpenSandbox backend configuration.

    Auto-detects runtime environment:
    - Local: uses configured server_url (default http://localhost:18792)
    - VKE: auto-detects KUBERNETES_SERVICE_HOST, uses http://opensandbox-server:8080
    """

    server_url: str = "http://localhost:18792"
    api_key: str = ""
    default_image: str = "opensandbox/code-interpreter:v1.0.1"
    network: OpenSandboxNetworkConfig = Field(default_factory=OpenSandboxNetworkConfig)
    runtime: OpenSandboxRuntimeConfig = Field(default_factory=OpenSandboxRuntimeConfig)


class AioSandboxBackendConfig(BaseModel):
    """AIO Sandbox backend configuration."""

    base_url: str = "http://localhost:18794"


class SandboxBackendsConfig(BaseModel):
    """Sandbox backends configuration."""

    srt: SrtBackendConfig = Field(default_factory=SrtBackendConfig)
    docker: DockerBackendConfig = Field(default_factory=DockerBackendConfig)
    opensandbox: OpenSandboxBackendConfig = Field(default_factory=OpenSandboxBackendConfig)
    direct: DirectBackendConfig = Field(default_factory=DirectBackendConfig)
    aiosandbox: AioSandboxBackendConfig = Field(default_factory=AioSandboxBackendConfig)


class LangfuseConfig(BaseModel):
    """Langfuse observability configuration."""

    enabled: bool = False
    secret_key: str = "sk-lf-vikingbot-secret-key-2026"
    public_key: str = "pk-lf-vikingbot-public-key-2026"
    base_url: str = "http://localhost:3000"


class SandboxConfig(BaseModel):
    """Sandbox configuration."""

    backend: SandboxBackend = SandboxBackend.DIRECT
    mode: SandboxMode = SandboxMode.SHARED
    backends: SandboxBackendsConfig = Field(default_factory=SandboxBackendsConfig)


class Config(BaseSettings):
    """Root configuration for vikingbot."""

    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    channels: list[Any] = Field(default_factory=list)
    providers: ProvidersConfig = Field(
        default_factory=ProvidersConfig, deprecated=True
    )  # Deprecated: Use ov.conf vlm config instead
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    ov_server: OpenVikingConfig = Field(default_factory=OpenVikingConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)
    langfuse: LangfuseConfig = Field(default_factory=LangfuseConfig)
    hooks: list[str] = Field(["vikingbot.hooks.builtins.openviking_hooks.hooks"])
    skills: list[str] = Field(
        default_factory=lambda: [
            "github-proxy",
            "github",
            "memory",
            "cron",
            "weather",
            "tmux",
            "skill-creator",
            "summarize",
        ]
    )
    storage_workspace: Optional[str] = None  # From ov.conf root level storage.workspace
    use_local_memory: bool = False
    read_only: bool = False

    @property
    def channels_config(self) -> ChannelsConfig:
        """Get channels config wrapper."""
        config = ChannelsConfig()
        config.channels = self.channels
        return config

    @property
    def bot_data_path(self) -> Path:
        """Get expanded bot data path: {storage_workspace}/bot."""
        workspace = self.storage_workspace or "~/.openviking/data"
        return Path(workspace).expanduser() / "bot"

    @property
    def workspace_path(self) -> Path:
        """Get expanded workspace path: {storage_workspace}/bot/workspace."""
        return self.bot_data_path / "workspace"

    @property
    def ov_data_path(self) -> Path:
        return self.bot_data_path / "ov_data"

    def _get_vlm_config(self) -> Optional[Dict[str, Any]]:
        """Get vlm config from OpenVikingConfig. Returns (vlm_config_dict)."""
        from openviking_cli.utils.config import get_openviking_config

        ov_config = get_openviking_config()

        if hasattr(ov_config, "vlm"):
            return ov_config.vlm.model_dump()
        return None

    def _match_provider(
        self, model: str | None = None
    ) -> tuple["ProviderConfig | None", str | None]:
        """Match provider config from ov.conf vlm section. Returns (config, spec_name)."""
        # Get from OpenVikingConfig vlm
        vlm_config = self._get_vlm_config()

        if vlm_config:
            provider_name = vlm_config.get("provider")
            if provider_name:
                # Build provider config from vlm
                provider_config = ProviderConfig()

                # Try to get from vlm.providers first
                if "providers" in vlm_config and provider_name in vlm_config["providers"]:
                    p_data = vlm_config["providers"][provider_name]
                    if "api_key" in p_data:
                        provider_config.api_key = p_data["api_key"]
                    if "api_base" in p_data:
                        provider_config.api_base = p_data["api_base"]
                    if "extra_headers" in p_data:
                        provider_config.extra_headers = p_data["extra_headers"]
                else:
                    # Fall back to top-level vlm fields
                    if vlm_config.get("api_key"):
                        provider_config.api_key = vlm_config["api_key"]
                    if vlm_config.get("api_base"):
                        provider_config.api_base = vlm_config["api_base"]

                if provider_config.api_key:
                    return provider_config, provider_name

        return None, None

    def get_provider(self, model: str | None = None) -> ProviderConfig | None:
        """Get matched provider config (api_key, api_base, extra_headers). Falls back to first available."""
        p, _ = self._match_provider(model)
        return p

    def get_provider_name(self, model: str | None = None) -> str | None:
        """Get the registry name of the matched provider (e.g. "deepseek", "openrouter")."""
        _, name = self._match_provider(model)
        return name

    def get_api_key(self, model: str | None = None) -> str | None:
        """Get API key for the given model. Falls back to first available key."""
        p = self.get_provider(model)
        return p.api_key if p else None

    def get_api_base(self, model: str | None = None) -> str | None:
        """Get API base URL for the given model. Applies default URLs for known gateways."""
        from vikingbot.providers.registry import find_by_name

        p, name = self._match_provider(model)
        if p and p.api_base:
            return p.api_base
        if name:
            spec = find_by_name(name)
            if spec and spec.is_gateway and spec.default_api_base:
                return spec.default_api_base
        return None

    model_config = SettingsConfigDict(env_prefix="NANOBOT_", env_nested_delimiter="__")


class SessionKey(BaseModel):
    model_config = ConfigDict(frozen=True)
    type: str
    channel_id: str
    chat_id: str

    def __hash__(self):
        return hash((self.type, self.channel_id, self.chat_id))

    def safe_name(self):
        return f"{self.type}__{self.channel_id}__{self.chat_id}"

    def channel_key(self):
        return f"{self.type}__{self.channel_id}"

    @staticmethod
    def from_safe_name(safe_name: str):
        file_name_split = safe_name.split("__")
        return SessionKey(
            type=file_name_split[0], channel_id=file_name_split[1], chat_id=file_name_split[2]
        )