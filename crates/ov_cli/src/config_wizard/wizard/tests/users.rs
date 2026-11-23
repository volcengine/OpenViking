use super::super::profiles::active_config_index;
use super::super::test_support::{strip_ansi, unique_dir};
use super::{
    user_management_config_context, user_management_config_from_server_url,
    user_management_config_name_choice_labels, user_management_server_url_helper_lines,
};
use crate::config::Config;
use crate::config_wizard::store::{ConfigEntry, ConfigKind, ConfigStore};
use std::fs;

#[test]
fn user_management_config_name_choices_offer_custom_for_current_name() {
    assert_eq!(
        user_management_config_name_choice_labels(Some("managed"), "managed"),
        vec![
            "Set custom local name".to_string(),
            "Use current name (managed)".to_string(),
            "Back".to_string()
        ]
    );
}

#[test]
fn user_management_targets_active_config_without_extra_picker() {
    let configs = vec![
        ConfigEntry {
            name: "dev".to_string(),
            config: Config::default(),
            is_active: false,
            kind: ConfigKind::Custom,
        },
        ConfigEntry {
            name: "prod".to_string(),
            config: Config::default(),
            is_active: true,
            kind: ConfigKind::Custom,
        },
    ];
    assert_eq!(active_config_index(&configs), Some(1));

    let configs_without_active = vec![ConfigEntry {
        name: "dev".to_string(),
        config: Config::default(),
        is_active: false,
        kind: ConfigKind::Custom,
    }];
    assert_eq!(active_config_index(&configs_without_active), None);

    let dir = unique_dir("user-management-context");
    fs::create_dir_all(&dir).expect("config dir should exist");
    let store = ConfigStore::for_config_dir(dir.clone());
    assert!(user_management_config_context(&store).unwrap().is_none());

    let inactive_config = Config {
        url: "https://inactive.example.com".to_string(),
        ..Config::default()
    };
    store
        .save_named_config("inactive", &inactive_config)
        .expect("inactive config should be saved");
    assert!(user_management_config_context(&store).unwrap().is_none());

    let active_config = Config {
        url: "https://active.example.com".to_string(),
        ..Config::default()
    };
    store
        .save_and_activate("prod", &active_config)
        .expect("active config should be saved");
    let context = user_management_config_context(&store)
        .unwrap()
        .expect("active config should be used");
    assert_eq!(context.config_name.as_deref(), Some("prod"));
    assert_eq!(context.config.url, "https://active.example.com");

    let legacy_dir = unique_dir("user-management-legacy-active");
    fs::create_dir_all(&legacy_dir).expect("config dir should exist");
    let legacy_store = ConfigStore::for_config_dir(legacy_dir.clone());
    let legacy_config = Config {
        url: "https://legacy.example.com".to_string(),
        ..Config::default()
    };
    legacy_store
        .save_and_activate("active", &legacy_config)
        .expect("legacy active config should be saved");
    let legacy_context = user_management_config_context(&legacy_store)
        .unwrap()
        .expect("legacy active config should be used");
    assert_eq!(legacy_context.config_name, None);
    assert_eq!(legacy_context.config.url, "https://legacy.example.com");

    let unnamed_dir = unique_dir("user-management-unnamed-active");
    fs::create_dir_all(&unnamed_dir).expect("config dir should exist");
    let unnamed_store = ConfigStore::for_config_dir(unnamed_dir.clone());
    let unnamed_config = Config {
        url: "https://unnamed.example.com".to_string(),
        ..Config::default()
    };
    unnamed_store
        .save_and_activate("temporary", &unnamed_config)
        .expect("active config should be saved");
    fs::remove_file(unnamed_store.saved_config_path("temporary").unwrap())
        .expect("saved profile should be removed");
    let unnamed_context = user_management_config_context(&unnamed_store)
        .unwrap()
        .expect("unnamed active config should be used");
    assert_eq!(unnamed_context.config_name, None);
    assert_eq!(unnamed_context.config.url, "https://unnamed.example.com");

    fs::remove_dir_all(dir).ok();
    fs::remove_dir_all(legacy_dir).ok();
    fs::remove_dir_all(unnamed_dir).ok();
}

#[test]
fn user_management_config_from_server_url_normalizes_without_active_config() {
    let config = user_management_config_from_server_url("127.0.0.1")
        .expect("server URL should build a temporary config");

    assert_eq!(config.url, "http://127.0.0.1:1933");
    assert!(config.api_key.is_none());
    assert!(config.root_api_key.is_none());

    let error = user_management_config_from_server_url("  ")
        .expect_err("empty server URL should be rejected")
        .to_string();
    assert!(error.contains("Server URL cannot be empty"));
}

#[test]
fn user_management_server_url_prompt_copy_does_not_require_ovcli_conf() {
    let text = strip_ansi(&user_management_server_url_helper_lines().join("\n"));

    assert!(text.contains("only needs the server URL"));
    assert!(!text.contains("current ovcli.conf"));
}

#[test]
fn user_management_save_writes_named_config_and_active_config() {
    let dir = unique_dir("user-management-save");
    fs::create_dir_all(&dir).expect("config dir should exist");
    let store = ConfigStore::for_config_dir(dir.clone());
    let config = Config {
        url: "https://openviking.example.com".to_string(),
        api_key: Some("user-key".to_string()),
        root_api_key: Some("root-key".to_string()),
        account: Some("acme".to_string()),
        user: Some("alice".to_string()),
        ..Config::default()
    };

    store
        .save_and_activate("managed", &config)
        .expect("user management config should save");

    assert!(store.saved_config_path("managed").unwrap().exists());
    let active = store
        .load_active()
        .expect("active should load")
        .expect("active config should exist");
    assert_eq!(active.api_key.as_deref(), Some("user-key"));
    assert_eq!(active.root_api_key.as_deref(), Some("root-key"));
    assert_eq!(active.account.as_deref(), Some("acme"));
    assert_eq!(active.user.as_deref(), Some("alice"));

    fs::remove_dir_all(dir).ok();
}
