use super::super::credentials::root_key_used_for_normal_commands;
use super::super::test_support::strip_ansi;
use super::{
    RootAccountSummary, RootUserSummary, account_tree_labels, config_with_selected_user,
    root_accounts_from_value, root_api_key_helper_lines, root_key_account_selection_labels,
    root_key_user_selection_labels, root_users_from_value, user_key_from_response,
    user_tree_labels, validate_new_user_id,
};
use crate::config::Config;
use serde_json::json;

#[test]
fn user_management_root_key_prompt_shows_current_server_url() {
    let text = strip_ansi(&root_api_key_helper_lines("https://openviking.example.com").join("\n"));

    assert!(text.contains("Server URL:"));
    assert!(text.contains("https://openviking.example.com"));
    assert!(text.contains("Used only to list or create accounts/users"));
}

#[test]
fn new_user_id_validation_rejects_existing_users_before_admin_call() {
    let existing_users = vec![RootUserSummary {
        user_id: "alice".to_string(),
        role: "user".to_string(),
        api_key: None,
        key_prefix: None,
    }];

    assert!(validate_new_user_id("bob", &existing_users).is_ok());

    let error = validate_new_user_id("alice", &existing_users)
        .expect_err("duplicate user should be rejected");
    assert!(error.to_string().contains("already exists"));
}

#[test]
fn root_account_and_user_labels_render_like_directories() {
    let account_labels = account_tree_labels(&[
        RootAccountSummary {
            account_id: "acme".to_string(),
            user_count: 2,
        },
        RootAccountSummary {
            account_id: "solo".to_string(),
            user_count: 1,
        },
    ]);
    assert_eq!(account_labels, vec!["acme/  2 users", "solo/  1 user"]);

    let user_labels = user_tree_labels(
        "acme",
        &[
            RootUserSummary {
                user_id: "alice".to_string(),
                role: "admin".to_string(),
                api_key: Some("alice-key".to_string()),
                key_prefix: None,
            },
            RootUserSummary {
                user_id: "bob".to_string(),
                role: "user".to_string(),
                api_key: None,
                key_prefix: Some("bob-pref".to_string()),
            },
        ],
    );
    assert_eq!(user_labels, vec!["acme/alice  admin", "acme/bob  user"]);
}

#[test]
fn root_key_add_config_selection_labels_offer_user_creation() {
    let account_labels = root_key_account_selection_labels(&[RootAccountSummary {
        account_id: "acme".to_string(),
        user_count: 0,
    }]);
    assert_eq!(
        account_labels,
        vec!["acme/  0 users", "+ Create new account"]
    );

    let user_labels = root_key_user_selection_labels("acme", &[]);
    assert_eq!(user_labels, vec!["+ Create new user in acme/"]);
}

#[test]
fn root_key_config_prompts_for_user_selection_before_save() {
    let root_as_normal = Config {
        api_key: Some("root-key".to_string()),
        root_api_key: Some("root-key".to_string()),
        ..Config::default()
    };
    assert!(root_key_used_for_normal_commands(&root_as_normal));

    let separate_user_key = Config {
        api_key: Some("user-key".to_string()),
        root_api_key: Some("root-key".to_string()),
        ..Config::default()
    };
    assert!(!root_key_used_for_normal_commands(&separate_user_key));
}

#[test]
fn root_admin_response_parsers_sort_and_ignore_empty_items() {
    let accounts = root_accounts_from_value(&json!([
        {"account_id": "beta", "user_count": 0},
        {"account_id": "", "user_count": 9},
        {"account_id": "deleting-account", "user_count": 2, "status": "deleting"},
        {"account_id": "alpha", "user_count": 2}
    ]));
    assert_eq!(
        accounts,
        vec![
            RootAccountSummary {
                account_id: "alpha".to_string(),
                user_count: 2,
            },
            RootAccountSummary {
                account_id: "beta".to_string(),
                user_count: 0,
            },
        ]
    );

    let users = root_users_from_value(&json!([
        {"user_id": "zoe", "role": "admin", "api_key": "zoe-key"},
        {"user_id": "amy", "key_prefix": "amy-pref"},
        {"user_id": " ", "api_key": "ignored"}
    ]));
    assert_eq!(
        users,
        vec![
            RootUserSummary {
                user_id: "amy".to_string(),
                role: "user".to_string(),
                api_key: None,
                key_prefix: Some("amy-pref".to_string()),
            },
            RootUserSummary {
                user_id: "zoe".to_string(),
                role: "admin".to_string(),
                api_key: Some("zoe-key".to_string()),
                key_prefix: None,
            },
        ]
    );
}

#[test]
fn selected_user_config_keeps_root_key_and_stores_user_key_identity() {
    let mut config = Config::default();
    config.url = "http://127.0.0.1:1933".to_string();
    config.api_key = Some("root-key".to_string());
    config.root_api_key = Some("root-key".to_string());

    let updated = config_with_selected_user(&config, "root-key", "acme", "alice", "user-key");

    assert_eq!(updated.root_api_key.as_deref(), Some("root-key"));
    assert_eq!(updated.api_key.as_deref(), Some("user-key"));
    assert_eq!(updated.account.as_deref(), Some("acme"));
    assert_eq!(updated.user.as_deref(), Some("alice"));
}

#[test]
fn user_key_from_response_requires_non_empty_key() {
    assert_eq!(
        user_key_from_response(&json!({"user_key": " new-user-key "})),
        Some("new-user-key".to_string())
    );
    assert_eq!(
        user_key_from_response(&json!({"api_key": " alt-user-key "})),
        Some("alt-user-key".to_string())
    );
    assert_eq!(
        user_key_from_response(&json!({"result": {"user_key": " wrapped-user-key "}})),
        Some("wrapped-user-key".to_string())
    );
    assert_eq!(user_key_from_response(&json!({"user_key": "   "})), None);
    assert_eq!(user_key_from_response(&json!({})), None);
}
