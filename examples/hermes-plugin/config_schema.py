"""External Desktop setup. Older hosts keep their existing CLI setup."""

from plugins.memory import config_schema as _host

CONFIG_SCHEMA = None
if getattr(_host, "PROVIDER_SETUP_API_VERSION", 0) >= 1:
    from plugins.memory.config_schema import (
        KIND_SECRET,
        KIND_SEGMENTED,
        KIND_SELECT,
        STORAGE_PROVIDER_MANAGED,
        ProviderConfigSchema,
        ProviderField,
        ProviderFieldCondition,
        ProviderFieldOption,
    )

    _SERVICE = ProviderFieldCondition("setup_type", values=("service",))
    _PROFILE = ProviderFieldCondition("setup_type", values=("profile",))
    _CUSTOM = ProviderFieldCondition("setup_type", values=("custom",))
    _MANUAL = ProviderFieldCondition("setup_type", values=("service", "custom"))
    _CUSTOM_WITH_KEY = ProviderFieldCondition("credential", values=("user", "root"))
    _CUSTOM_ROOT = ProviderFieldCondition("credential", values=("root",))

    CONFIG_SCHEMA = ProviderConfigSchema(
        name="openviking",
        label="OpenViking",
        storage=STORAGE_PROVIDER_MANAGED,
        description="Connect to OpenViking Cloud, a server, or Quick Local on the connected Hermes host.",
        submit_action="save",
        submit_label="Save setup",
        status_action="health",
        actions=(),
        fields=(
            ProviderField(
                key="usage_profile",
                label="Usage",
                kind=KIND_SELECT,
                default="personal",
                description="Personal recalls common and sender memory and preserves session settings. Shared also shares group history; confirmation is required.",
                options=(
                    ProviderFieldOption("personal", "Personal Agent"),
                    ProviderFieldOption("shared", "Shared Agent"),
                ),
            ),
            ProviderField(
                key="setup_type",
                label="Setup Type",
                kind=KIND_SEGMENTED,
                default="quick_local",
                description="Choose a managed service, an existing OpenViking CLI profile, or another server.",
                required=True,
                options=(
                    ProviderFieldOption("service", "OpenViking Cloud"),
                    ProviderFieldOption(
                        "quick_local",
                        "Quick Local",
                        "Reuse this Hermes profile's LLM and run embeddings locally on the connected Hermes host. First setup downloads packages and a model. The server stays running after Desktop closes.",
                    ),
                    ProviderFieldOption("profile", "Existing Profiles"),
                    ProviderFieldOption("custom", "Custom Server"),
                ),
            ),
            ProviderField(
                key="profile_path",
                label="OpenViking profile",
                kind=KIND_SELECT,
                description="Link this Hermes profile to an OpenViking CLI profile.",
                placeholder="No OpenViking profiles found",
                search_placeholder="Search profiles...",
                required=True,
                dynamic_options=True,
                searchable=True,
                visible_when=(_PROFILE,),
            ),
            ProviderField(
                key="profile_name",
                label="Profile Name",
                description="Saved as an OpenViking CLI profile and linked to this Hermes profile.",
                default="openviking",
                required=True,
                visible_when=(_MANUAL,),
            ),
            ProviderField(
                key="url",
                label="OpenViking URL",
                description="Local or remote OpenViking server.",
                default="http://127.0.0.1:1933",
                required=True,
                visible_when=(_CUSTOM,),
            ),
            ProviderField(
                key="credential",
                label="Credential",
                kind=KIND_SELECT,
                default="user",
                description="Choose how Hermes authenticates with this OpenViking server.",
                visible_when=(_CUSTOM,),
                options=(
                    ProviderFieldOption(
                        "none",
                        "No API key",
                        "For an explicitly unauthenticated local server.",
                    ),
                    ProviderFieldOption(
                        "user", "User API key", "Authenticate as an OpenViking user."
                    ),
                    ProviderFieldOption(
                        "root",
                        "Root API key",
                        "Authenticate as root for an account and user.",
                    ),
                ),
            ),
            ProviderField(
                key="api_key_service",
                label="OpenViking API key",
                kind=KIND_SECRET,
                description="Stored only in the OpenViking CLI profile.",
                required=True,
                help_url="https://console.volcengine.com/vikingdb/openviking/region:openviking+cn-beijing",
                help_label="Get OpenViking API key",
                visible_when=(_SERVICE,),
            ),
            ProviderField(
                key="api_key",
                label="OpenViking API key",
                kind=KIND_SECRET,
                description="Stored only in the OpenViking CLI profile.",
                required=True,
                visible_when=(_CUSTOM, _CUSTOM_WITH_KEY),
            ),
            ProviderField(
                key="account",
                label="Account",
                description="Required when authenticating with a root API key.",
                required=True,
                visible_when=(_CUSTOM, _CUSTOM_ROOT),
            ),
            ProviderField(
                key="user",
                label="User",
                description="Required when authenticating with a root API key.",
                required=True,
                visible_when=(_CUSTOM, _CUSTOM_ROOT),
            ),
            ProviderField(
                key="actor_peer_id",
                label="Agent ID",
                default="",
                description=(
                    "Optional peer ID for separate assistant context. "
                    "Leave blank to use user memory."
                ),
                visible_when=(_MANUAL,),
            ),
        ),
    )
