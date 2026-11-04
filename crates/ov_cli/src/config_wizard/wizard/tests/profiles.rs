use super::super::test_support::{strip_ansi, unique_dir};
use super::{
    active_delete_block_helper_lines, add_config_name_label,
    add_generated_config_name_helper_lines, add_save_action_labels, allocate_config_name,
    config_select_label, current_server_url_default, edit_save_action_labels,
    local_config_name_choice_labels, next_step_copy, save_config, switch_validation_error_lines,
    validate_config_name_change, validate_new_config_name,
};
use crate::config::Config;
use crate::config_wizard::store::{ConfigEntry, ConfigKind, ConfigStore, validate_config_name};
use crate::error::Error;
use crate::theme;
use colored::Colorize;
use std::fs;

#[test]
fn switch_validation_error_uses_selected_config_kind() {
    let lines = switch_validation_error_lines(
        "cloud",
        ConfigKind::OpenVikingService,
        &Error::api_with_status("invalid key", 401),
    );
    let plain = strip_ansi(&lines.join("\n"));

    assert!(plain.contains("Target config 'cloud' failed validation."));
    assert!(plain.contains("API key"));
    assert!(!plain.contains("server URL"));
}

#[test]
fn add_config_name_copy_marks_name_optional() {
    assert_eq!(add_config_name_label(), "Config name (optional)");
}

#[test]
fn generated_config_name_is_valid_prefixed_and_non_colliding() {
    let dir = unique_dir("generated-name");
    fs::create_dir_all(&dir).expect("dir should exist");
    let store = ConfigStore::for_config_dir(dir);

    let name = allocate_config_name(&store, ConfigKind::Custom)
        .expect("generated name should be available");

    assert!(name.starts_with("custom-"));
    assert_eq!(name.len(), "custom-".len() + 6);
    validate_config_name(&name).expect("generated name should be valid");
}

#[test]
fn local_config_name_choices_offer_custom_local_name() {
    assert_eq!(
        local_config_name_choice_labels("custom-abc123"),
        vec![
            "Set custom local name".to_string(),
            "Use generated name (custom-abc123)".to_string(),
            "Back".to_string()
        ]
    );
}

#[test]
fn add_config_generated_name_copy_mentions_local_config_name() {
    let text = strip_ansi(&add_generated_config_name_helper_lines().join("\n"));

    assert!(text.contains("Choose how to name this local CLI config."));
    assert!(text.contains("ovcli.conf.<name>"));
}

#[test]
fn new_config_name_validation_rejects_existing_and_reserved_names() {
    let dir = unique_dir("new-config-name-conflict");
    fs::create_dir_all(&dir).expect("dir should exist");
    let store = ConfigStore::for_config_dir(dir.clone());
    store
        .save_named_config("taken", &Config::default())
        .expect("existing config should be saved");

    assert!(validate_new_config_name(&store, "available").is_ok());

    let duplicate = validate_new_config_name(&store, "taken")
        .expect_err("duplicate name should be rejected")
        .to_string();
    assert!(duplicate.contains("already exists"));

    let reserved = validate_new_config_name(&store, "active")
        .expect_err("reserved name should be rejected")
        .to_string();
    assert!(reserved.contains("reserved"));

    fs::remove_dir_all(dir).ok();
}

#[test]
fn edit_config_name_validation_allows_current_name_only() {
    let dir = unique_dir("edit-config-name-conflict");
    fs::create_dir_all(&dir).expect("dir should exist");
    let store = ConfigStore::for_config_dir(dir.clone());
    store
        .save_named_config("current", &Config::default())
        .expect("current config should be saved");
    store
        .save_named_config("taken", &Config::default())
        .expect("other config should be saved");

    assert!(validate_config_name_change(&store, "current", "current").is_ok());
    assert!(validate_config_name_change(&store, "current", "available").is_ok());

    let duplicate = validate_config_name_change(&store, "current", "taken")
        .expect_err("renaming to another saved config should be rejected")
        .to_string();
    assert!(duplicate.contains("already exists"));

    fs::remove_dir_all(dir).ok();
}

#[test]
fn add_save_rejects_existing_config_name_without_overwriting() {
    let dir = unique_dir("add-save-name-conflict");
    fs::create_dir_all(&dir).expect("dir should exist");
    let store = ConfigStore::for_config_dir(dir.clone());
    let existing = Config {
        url: "https://existing.example.com".to_string(),
        ..Config::default()
    };
    store
        .save_named_config("taken", &existing)
        .expect("existing config should be saved");

    let replacement = Config {
        url: "https://replacement.example.com".to_string(),
        ..Config::default()
    };
    let error = save_config(&store, "taken", &replacement, false)
        .expect_err("add save should reject existing config name")
        .to_string();
    assert!(error.contains("already exists"));

    let saved = store
        .load_saved_config("taken")
        .expect("existing config should still load");
    assert_eq!(saved.url, "https://existing.example.com");
    assert!(store.load_active().unwrap().is_none());

    fs::remove_dir_all(dir).ok();
}

#[test]
fn config_select_label_marks_active_with_badge() {
    let config = Config {
        url: "http://127.0.0.1:1933".to_string(),
        ..Config::default()
    };
    let active = ConfigEntry {
        name: "VPS".to_string(),
        config: config.clone(),
        is_active: true,
        kind: ConfigKind::Custom,
    };
    let inactive = ConfigEntry {
        name: "local".to_string(),
        config,
        is_active: false,
        kind: ConfigKind::Custom,
    };

    let active_label = config_select_label(&active);
    assert!(active_label.contains("VPS - Custom"));
    assert!(active_label.contains("[Active]"));
    assert!(!active_label.contains("* "));

    let inactive_label = config_select_label(&inactive);
    assert_eq!(inactive_label, "local - Custom");
    assert!(!inactive_label.contains("[Active]"));
}

#[test]
fn custom_server_url_default_uses_active_config_or_loopback() {
    let dir = unique_dir("server-url-default");
    fs::create_dir_all(&dir).expect("config dir should exist");
    let store = ConfigStore::for_config_dir(dir.clone());

    assert_eq!(
        current_server_url_default(&store).expect("default should resolve"),
        "http://127.0.0.1:1933"
    );

    let active_config = Config {
        url: "https://openviking.example.com".to_string(),
        ..Config::default()
    };
    store
        .save_and_activate("prod", &active_config)
        .expect("active config should be saved");

    assert_eq!(
        current_server_url_default(&store).expect("active URL should resolve"),
        "https://openviking.example.com"
    );

    fs::remove_dir_all(dir).ok();
}

#[test]
fn active_delete_copy_mentions_switch_command() {
    let copy = active_delete_block_helper_lines().join("\n");

    assert!(copy.contains("Deleting the active config is blocked."));
    assert!(copy.contains("ov config switch"));
    assert!(copy.contains("then delete this one"));
}

#[test]
fn save_choices_allow_saving_without_activation() {
    assert_eq!(
        add_save_action_labels(),
        ["Save and activate", "Save only", "Cancel"]
    );
    assert_eq!(
        edit_save_action_labels(false),
        ["Save only", "Save and activate", "Cancel"]
    );
    assert_eq!(edit_save_action_labels(true), ["Save changes", "Cancel"]);
}

#[test]
fn success_copy_points_to_help_command() {
    assert!(next_step_copy().contains("ov --help"));
    assert!(next_step_copy().contains("get started"));
}

#[test]
fn next_step_copy_styles_sentence_text_with_body_color() {
    colored::control::set_override(true);
    let expected = format!(
        "{}{}{}",
        theme::body("Run "),
        theme::command("ov --help").bold(),
        theme::body(" to get started.")
    );
    let rendered = next_step_copy();
    colored::control::unset_override();

    assert_eq!(rendered, expected);
}
