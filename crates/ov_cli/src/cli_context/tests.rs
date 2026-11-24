use crate::CliContext;
use crate::config::{Config, DEFAULT_CUSTOM_URL};
use crate::output::OutputFormat;

#[test]
fn cli_context_overrides_identity_from_cli_flags() {
    let config = Config {
        url: DEFAULT_CUSTOM_URL.to_string(),
        api_key: Some("test-key".to_string()),
        root_api_key: None,
        account: Some("from-config-account".to_string()),
        user: Some("from-config-user".to_string()),
        actor_peer_id: Some("from-config-peer".to_string()),
        agent_id: None,
        timeout: 60.0,
        output: "table".to_string(),
        echo_command: true,
        show_progress: false,
        verbose: false,
        upload: Default::default(),
        extra_headers: None,
        profile: false,
        gateway_token: None,
        auth_mode: None,
        ldap_username: None,
        ldap_password: None,
        oidc_token: None,
    };

    let ctx = CliContext::from_config(
        config,
        OutputFormat::Json,
        true,
        Some("from-cli-account".to_string()),
        Some("from-cli-user".to_string()),
        Some("from-cli-peer".to_string()),
        false,
        None,
        None,
        None,
    );

    assert_eq!(ctx.config.account.as_deref(), Some("from-cli-account"));
    assert_eq!(ctx.config.user.as_deref(), Some("from-cli-user"));
    assert_eq!(ctx.config.actor_peer_id.as_deref(), Some("from-cli-peer"));
    assert!(ctx.config.agent_id.is_none());
}

#[test]
fn cli_context_maps_agent_id_to_actor_peer_scope() {
    let config = Config {
        url: DEFAULT_CUSTOM_URL.to_string(),
        api_key: Some("test-key".to_string()),
        root_api_key: None,
        account: None,
        user: None,
        actor_peer_id: None,
        agent_id: Some("legacy-agent".to_string()),
        timeout: 60.0,
        output: "table".to_string(),
        echo_command: true,
        show_progress: false,
        verbose: false,
        upload: Default::default(),
        extra_headers: None,
        profile: false,
        gateway_token: None,
        auth_mode: None,
        ldap_username: None,
        ldap_password: None,
        oidc_token: None,
    };

    let ctx = CliContext::from_config(
        config,
        OutputFormat::Json,
        true,
        None,
        None,
        None,
        false,
        None,
        None,
        None,
    );
    let client = ctx.get_client();

    assert_eq!(client.actor_peer_id(), Some("legacy-agent"));
}

#[test]
fn cli_context_uses_root_api_key_with_sudo() {
    let config = Config {
        url: DEFAULT_CUSTOM_URL.to_string(),
        api_key: Some("user-key".to_string()),
        root_api_key: Some("root-key".to_string()),
        account: None,
        user: None,
        actor_peer_id: Some("peer-a".to_string()),
        agent_id: None,
        timeout: 60.0,
        output: "table".to_string(),
        echo_command: true,
        show_progress: false,
        verbose: false,
        profile: false,
        upload: Default::default(),
        extra_headers: None,
        gateway_token: None,
        auth_mode: None,
        ldap_username: None,
        ldap_password: None,
        oidc_token: None,
    };

    // Without sudo: use api_key
    let ctx = CliContext::from_config(
        config.clone(),
        OutputFormat::Json,
        true,
        None,
        None,
        None,
        false,
        None,
        None,
        None,
    );
    let client = ctx.get_client();
    assert_eq!(client.api_key(), Some("user-key"));
    assert_eq!(client.actor_peer_id(), Some("peer-a"));

    // With sudo: use root_api_key
    let ctx = CliContext::from_config(
        config,
        OutputFormat::Json,
        true,
        None,
        None,
        None,
        true,
        None,
        None,
        None,
    );
    let client = ctx.get_client();
    assert_eq!(client.api_key(), Some("root-key"));
}
