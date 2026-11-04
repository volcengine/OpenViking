use super::super::prompts::input_live_lines;
use super::super::test_support::strip_ansi;
use super::{
    CustomKeyMode, IdentityMode, custom_api_key_helper_lines, custom_api_key_input_helper_lines,
    custom_key_mode_labels, custom_validation_failure_choices, edit_api_key_choice_labels,
    edit_custom_key_action_labels, identity_prompt_parts, openviking_service_api_key_helper_lines,
    openviking_service_validation_failure_choices, provider_labels,
    root_key_normal_command_notice_lines, root_key_redirect_labels,
    should_confirm_detected_user_key, should_prompt_root_identity, user_key_redirect_labels,
    validate_draft,
};
use crate::config::Config;
use crate::config_wizard::store::{
    ApiKeyRole, ConfigDraft, ConfigKind, validate_account_id_value, validate_user_id_value,
};
use crate::i18n::Language;
use crate::theme;
use colored::Colorize;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;

#[test]
fn provider_helper_copy_is_minimal_and_custom_is_clear() {
    let cloud = openviking_service_api_key_helper_lines();
    let local_custom = custom_api_key_helper_lines(true);
    let remote_custom = custom_api_key_helper_lines(false);
    let local_custom_plain: Vec<String> =
        local_custom.iter().map(|line| strip_ansi(line)).collect();
    let remote_custom_plain: Vec<String> =
        remote_custom.iter().map(|line| strip_ansi(line)).collect();

    assert_eq!(
        provider_labels(Language::En)[0],
        "OpenViking Service (VolcEngine Cloud)"
    );
    assert!(cloud.iter().any(|line| line.contains("Get your API key:")));
    assert!(cloud.iter().any(|line| {
        line.contains("Go to User Management → API Key to view and copy your key.")
    }));
    assert!(!cloud.iter().any(|line| line.contains("Server URL")));
    assert!(
        local_custom
            .iter()
            .any(|line| line.contains("Optional for local servers. Add one if auth is enabled."))
    );
    assert!(
        remote_custom
            .iter()
            .any(|line| line.contains("Required for remote custom servers."))
    );
    assert!(
        !remote_custom
            .iter()
            .any(|line| line.contains("Usually not needed locally"))
    );
    assert!(
        !local_custom_plain
            .iter()
            .chain(remote_custom_plain.iter())
            .any(|line| line.contains(';'))
    );
}

#[test]
fn custom_key_mode_choices_distinguish_no_key_user_key_and_root_key() {
    assert_eq!(
        custom_key_mode_labels(true),
        ["No key / local dev", "User API key", "Root API key"]
    );
    assert_eq!(
        custom_key_mode_labels(false),
        ["User API key", "Root API key"]
    );
}

#[test]
fn custom_key_input_copy_explains_storage_target() {
    let user_key = custom_api_key_input_helper_lines(CustomKeyMode::UserKey);
    let root_key = custom_api_key_input_helper_lines(CustomKeyMode::RootKey);

    assert!(user_key.iter().any(|line| line.contains("normal")));
    assert!(!user_key.iter().any(|line| line.contains("api_key")));
    assert!(!root_key.iter().any(|line| line.contains("root_api_key")));
    assert!(!root_key.iter().any(|line| line.contains("api_key")));
    assert!(root_key.iter().any(|line| line.contains("--sudo")));
    assert!(!root_key.iter().any(|line| line.contains("remote")));
}

#[test]
fn local_no_key_identity_prompt_shows_default_identity() {
    let (default, value_label, helper_lines) = identity_prompt_parts(IdentityMode::LocalNoKey);
    let lines = input_live_lines(
        "Create a new OpenViking config.",
        "Account ID",
        default,
        value_label,
        false,
        &helper_lines,
        None,
    );
    let text = lines.join("\n");

    assert!(text.contains("Default:"));
    assert!(text.contains("default"));
    assert!(text.contains("Local no-key identity."));
    assert!(!text.contains("Press Enter"));
}

#[test]
fn root_key_identity_prompt_has_no_default_identity() {
    let (default, value_label, helper_lines) = identity_prompt_parts(IdentityMode::RootKey);
    let lines = input_live_lines(
        "Create a new OpenViking config.",
        "Account ID",
        default,
        value_label,
        false,
        &helper_lines,
        None,
    );
    let text = lines.join("\n");

    assert!(!text.contains("Default:"));
    assert!(!text.contains("Current:"));
    assert!(text.contains("Root API keys require an explicit account and user."));
}

#[test]
fn validation_failure_choices_are_kind_specific() {
    assert_eq!(
        openviking_service_validation_failure_choices(),
        ["Retry API key", "Cancel"]
    );
    assert_eq!(
        custom_validation_failure_choices(),
        ["Edit server URL", "Edit API key", "Cancel"]
    );
    assert!(!openviking_service_validation_failure_choices().contains(&"Edit config name"));
}

#[test]
fn root_key_notice_points_to_edit_flow_when_root_key_is_reused() {
    let config = Config {
        api_key: Some("root-key".to_string()),
        root_api_key: Some("root-key".to_string()),
        ..Config::default()
    };

    let text = strip_ansi(&root_key_normal_command_notice_lines(&config).join("\n"));

    assert!(text.contains("Root key configured."));
    assert!(text.contains("Normal commands will use this key"));
    assert!(text.contains("ov config -> Edit Config -> Set normal user API key"));
}

#[test]
fn root_key_notice_styles_sentence_text_with_body_color() {
    colored::control::set_override(true);
    let config = Config {
        api_key: Some("root-key".to_string()),
        root_api_key: Some("root-key".to_string()),
        ..Config::default()
    };
    let expected = vec![
        format!(
            "{}{}",
            theme::warning("Root key").bold(),
            theme::body(" configured.")
        ),
        format!(
            "{}{}{}{}",
            theme::strong("Normal commands"),
            theme::body(" will use this key until you set a "),
            theme::warning("separate user API key").bold(),
            theme::body(".")
        ),
        format!(
            "{}{}{}{}{}{}{}{}{}",
            theme::body("For "),
            theme::warning("least privilege").bold(),
            theme::body(", run "),
            theme::command("ov config").bold(),
            theme::body(" -> "),
            theme::strong("Edit Config"),
            theme::body(" -> "),
            theme::strong("Set normal user API key"),
            theme::body(".")
        ),
    ];
    let lines = root_key_normal_command_notice_lines(&config);
    colored::control::unset_override();

    assert_eq!(lines, expected);
}

#[test]
fn root_key_notice_is_hidden_for_separate_user_key() {
    let config = Config {
        api_key: Some("user-key".to_string()),
        root_api_key: Some("root-key".to_string()),
        ..Config::default()
    };

    assert!(root_key_normal_command_notice_lines(&config).is_empty());
}

#[test]
fn edit_api_key_choices_match_kind_and_existing_key_state() {
    assert!(edit_api_key_choice_labels(ConfigKind::Custom, false).is_empty());
    assert!(edit_api_key_choice_labels(ConfigKind::OpenVikingService, false).is_empty());
    assert_eq!(
        edit_api_key_choice_labels(ConfigKind::Custom, true),
        ["Keep existing API key", "Replace API key", "Clear API key"]
    );
    assert_eq!(
        edit_api_key_choice_labels(ConfigKind::OpenVikingService, true),
        ["Keep existing API key", "Replace API key"]
    );
}

#[test]
fn edit_custom_key_actions_preserve_separate_credential_roles() {
    assert_eq!(
        edit_custom_key_action_labels(false, false),
        ["Set normal user API key", "Set root API key"]
    );
    assert_eq!(
        edit_custom_key_action_labels(true, true),
        [
            "Keep existing API keys",
            "Set normal user API key",
            "Set root API key",
            "Use root key for normal commands",
            "Clear root API key",
            "Clear all API keys"
        ]
    );
    assert_eq!(
        edit_custom_key_action_labels(true, false),
        [
            "Keep existing API keys",
            "Set normal user API key",
            "Set root API key",
            "Clear all API keys"
        ]
    );
}

#[test]
fn root_key_redirect_copy_is_outcome_based() {
    assert_eq!(
        root_key_redirect_labels(),
        ["Continue as root key", "Re-enter user key", "Cancel"]
    );
}

#[test]
fn user_key_redirect_copy_is_outcome_based() {
    assert_eq!(
        user_key_redirect_labels(),
        ["Continue as user key", "Re-enter root key", "Cancel"]
    );
}

#[test]
fn root_key_route_detects_regular_user_key() {
    assert!(should_confirm_detected_user_key(
        ConfigKind::Custom,
        Some(CustomKeyMode::RootKey),
        Some(ApiKeyRole::Regular),
    ));
    assert!(!should_confirm_detected_user_key(
        ConfigKind::Custom,
        Some(CustomKeyMode::UserKey),
        Some(ApiKeyRole::Regular),
    ));
    assert!(!should_confirm_detected_user_key(
        ConfigKind::Custom,
        Some(CustomKeyMode::RootKey),
        Some(ApiKeyRole::Root),
    ));
    assert!(!should_confirm_detected_user_key(
        ConfigKind::OpenVikingService,
        Some(CustomKeyMode::RootKey),
        Some(ApiKeyRole::Regular),
    ));
}

#[test]
fn identity_validation_rejects_spaces_before_server_validation() {
    assert!(validate_user_id_value("alice").is_ok());
    assert!(validate_user_id_value("alice@example.com").is_ok());
    let error = validate_user_id_value("alice smith").expect_err("spaces should be rejected");
    assert_eq!(
        error.to_string(),
        "Configuration error: User ID can only contain letters, numbers, '_', '-', '.', and '@'"
    );
    assert!(validate_account_id_value("_system").is_err());
}

#[test]
fn replaced_root_key_requires_identity_confirmation() {
    assert!(should_prompt_root_identity(
        Some(ApiKeyRole::Root),
        true,
        Some("old-account"),
        Some("old-user"),
    ));
    assert!(should_prompt_root_identity(
        Some(ApiKeyRole::Root),
        false,
        Some("old-account"),
        None,
    ));
    assert!(!should_prompt_root_identity(
        Some(ApiKeyRole::Root),
        false,
        Some("old-account"),
        Some("old-user"),
    ));
    assert!(!should_prompt_root_identity(
        Some(ApiKeyRole::Regular),
        true,
        Some("old-account"),
        Some("old-user"),
    ));
}

#[tokio::test]
async fn validated_root_key_config_populates_root_api_key_for_sudo() {
    let url = spawn_root_probe_server("root-key").await;
    let draft = ConfigDraft {
        name: "local".to_string(),
        kind: ConfigKind::Custom,
        url,
        api_key: Some("root-key".to_string()),
        root_api_key: None,
        account: Some("admin".to_string()),
        user: Some("default".to_string()),
    };
    let mut ui = super::LiveRegion::default();

    let validated = validate_draft(&mut ui, "test", &draft)
        .await
        .expect("root key should validate");

    assert_eq!(validated.api_key_role, Some(ApiKeyRole::Root));
    assert_eq!(validated.config.api_key.as_deref(), Some("root-key"));
    assert_eq!(validated.config.root_api_key.as_deref(), Some("root-key"));
}

#[tokio::test]
async fn validated_user_key_config_preserves_separate_root_api_key_for_sudo() {
    let url = spawn_dual_key_probe_server("user-key", "root-key").await;
    let draft = ConfigDraft {
        name: "local".to_string(),
        kind: ConfigKind::Custom,
        url,
        api_key: Some("user-key".to_string()),
        root_api_key: Some("root-key".to_string()),
        account: Some("admin".to_string()),
        user: Some("default".to_string()),
    };
    let mut ui = super::LiveRegion::default();

    let validated = validate_draft(&mut ui, "test", &draft)
        .await
        .expect("separate user and root keys should validate");

    assert_eq!(validated.api_key_role, Some(ApiKeyRole::Regular));
    assert_eq!(validated.config.api_key.as_deref(), Some("user-key"));
    assert_eq!(validated.config.root_api_key.as_deref(), Some("root-key"));
    assert_eq!(validated.config.account, None);
    assert_eq!(validated.config.user, None);
}

#[tokio::test]
async fn selected_root_key_regular_user_key_returns_regular_role_without_root_key() {
    let url = spawn_dual_key_probe_server("user-key", "root-key").await;
    let draft = ConfigDraft {
        name: "local".to_string(),
        kind: ConfigKind::Custom,
        url,
        api_key: Some("user-key".to_string()),
        root_api_key: Some("user-key".to_string()),
        account: None,
        user: None,
    };
    let mut ui = super::LiveRegion::default();

    let validated = validate_draft(&mut ui, "test", &draft)
        .await
        .expect("regular key should validate so the wizard can redirect");

    assert_eq!(validated.api_key_role, Some(ApiKeyRole::Regular));
    assert_eq!(validated.config.api_key.as_deref(), Some("user-key"));
    assert_eq!(validated.config.root_api_key, None);
    assert_eq!(validated.config.account, None);
    assert_eq!(validated.config.user, None);
}

#[tokio::test]
async fn separate_root_key_candidate_regular_user_key_returns_regular_root_candidate_role() {
    let url =
        spawn_two_user_key_probe_server("existing-user-key", "entered-user-key", "root-key").await;
    let draft = ConfigDraft {
        name: "local".to_string(),
        kind: ConfigKind::Custom,
        url,
        api_key: Some("existing-user-key".to_string()),
        root_api_key: Some("entered-user-key".to_string()),
        account: None,
        user: None,
    };
    let mut ui = super::LiveRegion::default();

    let validated = validate_draft(&mut ui, "test", &draft)
        .await
        .expect("regular root-key candidate should validate so the wizard can redirect");

    assert_eq!(validated.api_key_role, Some(ApiKeyRole::Regular));
    assert_eq!(validated.root_api_key_role, Some(ApiKeyRole::Regular));
    assert_eq!(
        validated.config.api_key.as_deref(),
        Some("existing-user-key")
    );
    assert_eq!(validated.config.root_api_key, None);
}

async fn spawn_root_probe_server(root_api_key: &'static str) -> String {
    let listener = TcpListener::bind("127.0.0.1:0")
        .await
        .expect("test server should bind");
    let addr = listener.local_addr().expect("test server should have addr");
    tokio::spawn(async move {
        for _ in 0..6 {
            let Ok((mut stream, _)) = listener.accept().await else {
                return;
            };
            let mut buffer = vec![0; 4096];
            let Ok(read) = stream.read(&mut buffer).await else {
                return;
            };
            let request = String::from_utf8_lossy(&buffer[..read]);
            let lower_request = request.to_ascii_lowercase();
            let root_header = format!("x-api-key: {root_api_key}");
            let response = if request.starts_with("GET /health ") {
                http_response(200, r#"{"healthy":true,"auth_mode":"api_key"}"#)
            } else if request.starts_with("GET /api/v1/system/status ") {
                if lower_request.contains(&root_header) {
                    http_response(200, r#"{"status":"ok","result":{"initialized":true}}"#)
                } else {
                    http_response(
                        401,
                        r#"{"error":{"code":"AuthenticationError","message":"invalid key"}}"#,
                    )
                }
            } else if request.starts_with("GET /api/v1/admin/accounts ") {
                if lower_request.contains(&root_header) {
                    http_response(200, r#"{"accounts":[]}"#)
                } else {
                    http_response(
                        403,
                        r#"{"error":{"code":"PermissionDenied","message":"root required"}}"#,
                    )
                }
            } else {
                http_response(404, r#"{"error":{"message":"not found"}}"#)
            };
            let _ = stream.write_all(response.as_bytes()).await;
        }
    });
    format!("http://{addr}")
}

async fn spawn_dual_key_probe_server(
    user_api_key: &'static str,
    root_api_key: &'static str,
) -> String {
    let listener = TcpListener::bind("127.0.0.1:0")
        .await
        .expect("test server should bind");
    let addr = listener.local_addr().expect("test server should have addr");
    tokio::spawn(async move {
        for _ in 0..12 {
            let Ok((mut stream, _)) = listener.accept().await else {
                return;
            };
            let mut buffer = vec![0; 4096];
            let Ok(read) = stream.read(&mut buffer).await else {
                return;
            };
            let request = String::from_utf8_lossy(&buffer[..read]);
            let lower_request = request.to_ascii_lowercase();
            let user_header = format!("x-api-key: {user_api_key}");
            let root_header = format!("x-api-key: {root_api_key}");
            let has_user_key = lower_request.contains(&user_header);
            let has_root_key = lower_request.contains(&root_header);
            let response = if request.starts_with("GET /health ") {
                http_response(200, r#"{"healthy":true,"auth_mode":"api_key"}"#)
            } else if request.starts_with("GET /api/v1/system/status ") {
                if has_user_key || has_root_key {
                    http_response(200, r#"{"status":"ok","result":{"initialized":true}}"#)
                } else {
                    http_response(
                        401,
                        r#"{"error":{"code":"AuthenticationError","message":"invalid key"}}"#,
                    )
                }
            } else if request.starts_with("GET /api/v1/admin/accounts ") {
                if has_root_key {
                    http_response(200, r#"{"accounts":[]}"#)
                } else {
                    http_response(
                        403,
                        r#"{"error":{"code":"PermissionDenied","message":"root required"}}"#,
                    )
                }
            } else {
                http_response(404, r#"{"error":{"message":"not found"}}"#)
            };
            let _ = stream.write_all(response.as_bytes()).await;
        }
    });
    format!("http://{addr}")
}

async fn spawn_two_user_key_probe_server(
    first_user_api_key: &'static str,
    second_user_api_key: &'static str,
    root_api_key: &'static str,
) -> String {
    let listener = TcpListener::bind("127.0.0.1:0")
        .await
        .expect("test server should bind");
    let addr = listener.local_addr().expect("test server should have addr");
    tokio::spawn(async move {
        for _ in 0..12 {
            let Ok((mut stream, _)) = listener.accept().await else {
                return;
            };
            let mut buffer = vec![0; 4096];
            let Ok(read) = stream.read(&mut buffer).await else {
                return;
            };
            let request = String::from_utf8_lossy(&buffer[..read]);
            let lower_request = request.to_ascii_lowercase();
            let first_user_header = format!("x-api-key: {first_user_api_key}");
            let second_user_header = format!("x-api-key: {second_user_api_key}");
            let root_header = format!("x-api-key: {root_api_key}");
            let has_regular_key = lower_request.contains(&first_user_header)
                || lower_request.contains(&second_user_header);
            let has_root_key = lower_request.contains(&root_header);
            let response = if request.starts_with("GET /health ") {
                http_response(200, r#"{"healthy":true,"auth_mode":"api_key"}"#)
            } else if request.starts_with("GET /api/v1/system/status ") {
                if has_regular_key || has_root_key {
                    http_response(200, r#"{"status":"ok","result":{"initialized":true}}"#)
                } else {
                    http_response(
                        401,
                        r#"{"error":{"code":"AuthenticationError","message":"invalid key"}}"#,
                    )
                }
            } else if request.starts_with("GET /api/v1/admin/accounts ") {
                if has_root_key {
                    http_response(200, r#"{"accounts":[]}"#)
                } else {
                    http_response(
                        403,
                        r#"{"error":{"code":"PermissionDenied","message":"root required"}}"#,
                    )
                }
            } else {
                http_response(404, r#"{"error":{"message":"not found"}}"#)
            };
            let _ = stream.write_all(response.as_bytes()).await;
        }
    });
    format!("http://{addr}")
}

fn http_response(status: u16, body: &str) -> String {
    let reason = match status {
        200 => "OK",
        401 => "Unauthorized",
        403 => "Forbidden",
        404 => "Not Found",
        _ => "Error",
    };
    format!(
        "HTTP/1.1 {status} {reason}\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
        body.len()
    )
}
